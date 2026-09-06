"""Multi-source-server routing for modmail-dev/Modmail."""

from __future__ import annotations

import asyncio
import re
from typing import Optional

import discord
from discord.ext import commands

from bot import ModmailBot
from core import checks
from core.models import PermissionLevel, getLogger
from core.thread import Thread


log = getLogger(__name__)
TOPIC_GUILD_RE = re.compile(r"^Source Server ID:\s*(\d{17,21})\s*$", re.MULTILINE)


class SourceGuildSelect(discord.ui.Select):
    def __init__(self, guilds: list[discord.Guild]):
        options = [
            discord.SelectOption(
                label=guild.name[:100],
                value=str(guild.id),
                description=f"Server ID: {guild.id}",
            )
            for guild in guilds[:25]
        ]
        super().__init__(
            placeholder="Select the server you want to contact",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view: SourceGuildView = self.view
        if interaction.user.id != view.user_id:
            await interaction.response.send_message("This menu is not for you.", ephemeral=True)
            return
        view.guild_id = int(self.values[0])
        for item in view.children:
            item.disabled = True
        await interaction.response.edit_message(content="Server selected.", embed=None, view=view)
        view.stop()


class SourceGuildView(discord.ui.View):
    def __init__(self, user_id: int, guilds: list[discord.Guild], timeout: float):
        super().__init__(timeout=timeout)
        self.user_id = user_id
        self.guild_id: Optional[int] = None
        self.message: Optional[discord.Message] = None
        self.add_item(SourceGuildSelect(guilds))

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(
                    content="This server-selection menu expired. Send your message again to restart.",
                    embed=None,
                    view=self,
                )
            except discord.HTTPException:
                pass


class MultiServer(commands.Cog):
    """Route several community servers into one Modmail staff server."""

    def __init__(self, bot: ModmailBot):
        self.bot = bot
        self.db = bot.plugin_db.get_partition(self)
        self.source_guild_ids: set[int] = set()
        self.selection_timeout = 120
        self.user_locks: dict[int, asyncio.Lock] = {}
        self._original_process_dm = None

    async def cog_load(self) -> None:
        await self.bot.wait_until_ready()
        config = await self.db.find_one({"_id": "config"}) or {}
        self.source_guild_ids = {int(x) for x in config.get("source_guild_ids", [])}
        self.selection_timeout = max(30, min(int(config.get("selection_timeout", 120)), 600))

        # The normal GUILD_ID remains a valid source server automatically.
        if self.bot.guild_id:
            self.source_guild_ids.add(self.bot.guild_id)

        if getattr(self.bot.process_dm_modmail, "__multiserver_wrapper__", False):
            raise RuntimeError("MultiServer is already wrapping process_dm_modmail")

        self._original_process_dm = self.bot.process_dm_modmail

        async def routed_process_dm(message: discord.Message) -> None:
            await self._route_then_process(message)

        routed_process_dm.__multiserver_wrapper__ = True
        self.bot.process_dm_modmail = routed_process_dm
        log.info("MultiServer loaded with %d configured source guild(s).", len(self.source_guild_ids))

    async def cog_unload(self) -> None:
        if self._original_process_dm is not None:
            self.bot.process_dm_modmail = self._original_process_dm

    async def _configured_guilds_for(self, user_id: int) -> list[discord.Guild]:
        """Return source guilds the user is actually a member of.

        get_member() only checks discord.py's local member cache and can return
        None even when the user is in the server. Fall back to fetch_member()
        so DM routing does not depend on the member cache being populated.
        """
        guilds: list[discord.Guild] = []

        # Treat every guild the bot is currently in as eligible except the
        # central staff/modmail guild. Explicitly configured guild IDs are
        # also retained for compatibility with the management commands.
        candidate_ids = set(self.source_guild_ids)
        candidate_ids.update(
            guild.id
            for guild in self.bot.guilds
            if self.bot.modmail_guild is None or guild.id != self.bot.modmail_guild.id
        )

        for guild_id in candidate_ids:
            guild = self.bot.get_guild(guild_id)
            if guild is None:
                continue
            if self.bot.modmail_guild and guild.id == self.bot.modmail_guild.id:
                continue

            member = guild.get_member(user_id)
            if member is None:
                try:
                    member = await guild.fetch_member(user_id)
                except discord.NotFound:
                    member = None
                except discord.Forbidden:
                    log.warning(
                        "Could not check membership for user %s in guild %s (%s): forbidden.",
                        user_id,
                        guild.name,
                        guild.id,
                    )
                    member = None
                except discord.HTTPException:
                    log.exception(
                        "Could not check membership for user %s in guild %s (%s).",
                        user_id,
                        guild.name,
                        guild.id,
                    )
                    member = None

            if member is not None:
                guilds.append(guild)

        return sorted(guilds, key=lambda guild: guild.name.casefold())

    async def _stored_guild_id(self, user_id: int, thread: Optional[Thread]) -> Optional[int]:
        if thread and thread.channel and thread.channel.topic:
            match = TOPIC_GUILD_RE.search(thread.channel.topic)
            if match:
                return int(match.group(1))
        record = await self.db.find_one({"_id": f"thread:{user_id}"})
        if record and record.get("guild_id"):
            return int(record["guild_id"])
        return None

    async def _save_thread_source(self, user_id: int, guild_id: int) -> None:
        await self.db.update_one(
            {"_id": f"thread:{user_id}"},
            {"$set": {"guild_id": str(guild_id)}},
            upsert=True,
        )

    async def _choose_source(self, message: discord.Message, guilds: list[discord.Guild]) -> Optional[int]:
        if len(guilds) == 1:
            return guilds[0].id
        if not guilds:
            embed = discord.Embed(
                title="No supported server found",
                description="You must be a member of one of this bot's configured servers before contacting staff.",
                color=self.bot.error_color,
            )
            await message.channel.send(embed=embed)
            return None
        if len(guilds) > 25:
            log.warning("User %s shares more than 25 configured source guilds; showing the first 25.", message.author.id)

        embed = discord.Embed(
            title="Which server are you contacting?",
            description="Discord DMs do not identify a source server. Select the server this message concerns.",
            color=self.bot.main_color,
        )
        view = SourceGuildView(message.author.id, guilds, self.selection_timeout)
        view.message = await message.channel.send(embed=embed, view=view)
        timed_out = await view.wait()
        return None if timed_out else view.guild_id

    async def _route_then_process(self, message: discord.Message) -> None:
        lock = self.user_locks.setdefault(message.author.id, asyncio.Lock())
        try:
            async with lock:
                thread = await self.bot.threads.find(recipient=message.author)
                guild_id = await self._stored_guild_id(message.author.id, thread)

                if thread is None:
                    guilds = await self._configured_guilds_for(message.author.id)
                    guild_id = await self._choose_source(message, guilds)
                    if guild_id is None:
                        return
                    await self._save_thread_source(message.author.id, guild_id)
                elif guild_id is None:
                    # Compatibility for threads created before this plugin was installed.
                    guilds = await self._configured_guilds_for(message.author.id)
                    if len(guilds) == 1:
                        guild_id = guilds[0].id
                        await self._save_thread_source(message.author.id, guild_id)

                await self._original_process_dm(message)
        finally:
            if not lock.locked():
                self.user_locks.pop(message.author.id, None)

    @commands.Cog.listener()
    async def on_thread_ready(self, thread: Thread, *args) -> None:
        guild_id = await self._stored_guild_id(thread.id, thread)
        if guild_id is None:
            return
        guild = self.bot.get_guild(guild_id)
        guild_name = guild.name if guild else "Unknown/Unavailable Server"
        member = guild.get_member(thread.id) if guild else None
        if guild and member is None:
            try:
                member = await guild.fetch_member(thread.id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                member = None

        try:
            existing_topic = thread.channel.topic or ""
            preserved_lines = [
                line
                for line in existing_topic.splitlines()
                if not line.startswith("Source Server:")
                and not line.startswith("Source Server ID:")
            ]
            preserved_lines.extend(
                [
                    f"Source Server: {guild_name}",
                    f"Source Server ID: {guild_id}",
                ]
            )
            topic = "\n".join(line for line in preserved_lines if line).strip()
            await thread.channel.edit(topic=topic[:1024], reason="Record Modmail source server")
        except discord.HTTPException:
            log.exception("Could not add the source server to thread %s's topic.", thread.id)

        genesis = await thread.get_genesis_message()
        if genesis and genesis.embeds:
            embed = genesis.embeds[0]
            embed.add_field(name="Source Server", value=guild_name, inline=True)
            embed.add_field(name="Source Server ID", value=f"`{guild_id}`", inline=True)
            if member and member.nick:
                embed.add_field(name="Source Nickname", value=member.nick, inline=True)
            if guild and guild.icon:
                embed.set_thumbnail(url=guild.icon.url)
            try:
                await genesis.edit(embed=embed)
                return
            except discord.HTTPException:
                log.exception("Could not edit the genesis message for thread %s.", thread.id)

        fallback = discord.Embed(title="Source Server", color=self.bot.main_color)
        fallback.add_field(name="Server", value=guild_name, inline=True)
        fallback.add_field(name="Server ID", value=f"`{guild_id}`", inline=True)
        await thread.channel.send(embed=fallback)

    @commands.Cog.listener()
    async def on_thread_close(self, thread: Thread, *args) -> None:
        await self.db.delete_one({"_id": f"thread:{thread.id}"})

    async def _write_config(self) -> None:
        configurable = sorted(g for g in self.source_guild_ids if g != self.bot.guild_id)
        await self.db.update_one(
            {"_id": "config"},
            {"$set": {"source_guild_ids": [str(x) for x in configurable], "selection_timeout": self.selection_timeout}},
            upsert=True,
        )

    @commands.group(name="multiserver", aliases=("ms",), invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.OWNER)
    async def multiserver(self, ctx: commands.Context) -> None:
        """Show the configured source servers."""
        lines = []
        for guild_id in sorted(self.source_guild_ids):
            guild = self.bot.get_guild(guild_id)
            lines.append(f"• {guild.name if guild else 'Unavailable'} — `{guild_id}`")
        description = "\n".join(lines) or "No source servers are configured."
        await ctx.send(embed=discord.Embed(title="MultiServer sources", description=description, color=self.bot.main_color))

    @multiserver.command(name="add")
    @checks.has_permissions(PermissionLevel.OWNER)
    async def multiserver_add(self, ctx: commands.Context, guild_id: int) -> None:
        """Add a server that may create Modmail threads."""
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            await ctx.send("The bot is not currently in a server with that ID.")
            return
        if guild == self.bot.modmail_guild:
            await ctx.send("The central staff server cannot be added as a source server.")
            return
        self.source_guild_ids.add(guild_id)
        await self._write_config()
        await ctx.send(f"Added **{guild.name}** (`{guild.id}`) as a Modmail source server.")

    @multiserver.command(name="remove")
    @checks.has_permissions(PermissionLevel.OWNER)
    async def multiserver_remove(self, ctx: commands.Context, guild_id: int) -> None:
        """Remove a configured source server."""
        if guild_id == self.bot.guild_id:
            await ctx.send("The primary GUILD_ID cannot be removed here; change GUILD_ID in the bot environment instead.")
            return
        if guild_id not in self.source_guild_ids:
            await ctx.send("That server is not configured as a source server.")
            return
        self.source_guild_ids.remove(guild_id)
        await self._write_config()
        await ctx.send(f"Removed source server `{guild_id}`.")

    @multiserver.command(name="timeout")
    @checks.has_permissions(PermissionLevel.OWNER)
    async def multiserver_timeout(self, ctx: commands.Context, seconds: int) -> None:
        """Set the DM server-picker timeout (30-600 seconds)."""
        if not 30 <= seconds <= 600:
            await ctx.send("The timeout must be between 30 and 600 seconds.")
            return
        self.selection_timeout = seconds
        await self._write_config()
        await ctx.send(f"Server-selection timeout set to {seconds} seconds.")


async def setup(bot: ModmailBot) -> None:
    await bot.add_cog(MultiServer(bot))