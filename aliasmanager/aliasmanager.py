"""Interactive alias manager for modmail-dev/Modmail v4.x.

This plugin provides a button/modal based editor for multi-command aliases so
staff do not have to manually join commands with ``&&``.

Management:
    ?am create <name>
    ?am edit <name>
    ?am list
    ?am show <name>
    ?am delete <name>
    ?am copy <name> <new_name>
    ?am move <name> <from> <to>

Running:
    Once saved, use the alias exactly like a normal command:
    ?closeticket
"""

from __future__ import annotations

import asyncio
import copy
import re
from typing import Optional

import discord
from discord.ext import commands

from bot import ModmailBot
from core import checks
from core.models import PermissionLevel, getLogger


log = getLogger(__name__)

NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,32}$")
MAX_STEPS = 20
MAX_STEP_LENGTH = 1800


def _trim(text: str, limit: int = 1024) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


class StepModal(discord.ui.Modal, title="Add alias step"):
    command = discord.ui.TextInput(
        label="Command",
        placeholder="Example: reply Thanks for contacting us!",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=MAX_STEP_LENGTH,
    )

    def __init__(self, view: "AliasEditorView"):
        super().__init__()
        self.editor = view

    async def on_submit(self, interaction: discord.Interaction) -> None:
        value = str(self.command.value).strip()
        if not value:
            await interaction.response.send_message("The command cannot be empty.", ephemeral=True)
            return

        # Users may naturally include the bot prefix; strip it to avoid storing
        # nested prefixes.
        prefixes = await self.editor.cog._prefixes_for_interaction(interaction)
        for prefix in sorted(prefixes, key=len, reverse=True):
            if value.startswith(prefix):
                value = value[len(prefix):].lstrip()
                break

        if not value:
            await interaction.response.send_message("The command cannot be empty.", ephemeral=True)
            return

        self.editor.steps.append(value)
        await interaction.response.edit_message(
            embed=self.editor.make_embed(),
            view=self.editor,
        )


class AliasEditorView(discord.ui.View):
    def __init__(
        self,
        cog: "AliasManager",
        author_id: int,
        alias_name: str,
        steps: Optional[list[str]] = None,
        *,
        editing: bool = False,
    ):
        super().__init__(timeout=300)
        self.cog = cog
        self.author_id = author_id
        self.alias_name = alias_name.lower()
        self.steps = list(steps or [])
        self.editing = editing
        self.saved = False
        self.message: Optional[discord.Message] = None
        self._refresh_buttons()

    def _refresh_buttons(self) -> None:
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                if item.custom_id == "alias_remove_last":
                    item.disabled = not self.steps
                elif item.custom_id == "alias_save":
                    item.disabled = not self.steps
                elif item.custom_id == "alias_add":
                    item.disabled = len(self.steps) >= MAX_STEPS

    def make_embed(self) -> discord.Embed:
        action = "Editing" if self.editing else "Creating"
        embed = discord.Embed(
            title=f"{action} alias: {self.alias_name}",
            description=(
                "Add each command as its own step. You do **not** need to type `&&`.\n"
                f"Maximum: **{MAX_STEPS} steps**."
            ),
            color=self.cog.bot.main_color,
        )

        if self.steps:
            shown = []
            for index, step in enumerate(self.steps, 1):
                shown.append(f"**{index}.** `{_trim(step, 180)}`")
            embed.add_field(
                name=f"Steps ({len(self.steps)}/{MAX_STEPS})",
                value=_trim("\n".join(shown), 3900),
                inline=False,
            )
        else:
            embed.add_field(
                name="Steps",
                value="No commands added yet. Press **Add Step**.",
                inline=False,
            )

        embed.set_footer(
            text="Commands run from top to bottom and keep Modmail's normal command permissions/checks."
        )
        self._refresh_buttons()
        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Only the person who opened this editor can use it.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(
        label="Add Step",
        style=discord.ButtonStyle.primary,
        emoji="➕",
        custom_id="alias_add",
    )
    async def add_step(self, interaction: discord.Interaction, button: discord.ui.Button):
        if len(self.steps) >= MAX_STEPS:
            await interaction.response.send_message(
                f"An alias can have at most {MAX_STEPS} steps.",
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(StepModal(self))

    @discord.ui.button(
        label="Remove Last",
        style=discord.ButtonStyle.secondary,
        emoji="↩️",
        custom_id="alias_remove_last",
    )
    async def remove_last(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.steps:
            self.steps.pop()
        await interaction.response.edit_message(embed=self.make_embed(), view=self)

    @discord.ui.button(
        label="Save",
        style=discord.ButtonStyle.success,
        emoji="💾",
        custom_id="alias_save",
    )
    async def save(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.steps:
            await interaction.response.send_message(
                "Add at least one command before saving.",
                ephemeral=True,
            )
            return

        await self.cog._save_alias(
            self.alias_name,
            self.steps,
            interaction.user.id,
        )
        self.saved = True

        for item in self.children:
            item.disabled = True

        await interaction.response.edit_message(
            embed=discord.Embed(
                title="Alias saved",
                description=(
                    f"**{self.alias_name}** now has **{len(self.steps)}** step(s).\n\n"
                    f"Run it with your normal bot prefix, for example `?{self.alias_name}`."
                ),
                color=self.cog.bot.main_color,
            ),
            view=self,
        )
        self.stop()

    @discord.ui.button(
        label="Cancel",
        style=discord.ButtonStyle.danger,
        emoji="✖️",
        custom_id="alias_cancel",
    )
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="Alias editor cancelled",
                description="No changes were saved.",
                color=self.cog.bot.error_color,
            ),
            view=self,
        )
        self.stop()

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(
                    embed=discord.Embed(
                        title="Alias editor expired",
                        description="Run the create/edit command again to continue.",
                        color=self.cog.bot.error_color,
                    ),
                    view=self,
                )
            except discord.HTTPException:
                pass


class ConfirmDeleteView(discord.ui.View):
    def __init__(self, cog: "AliasManager", author_id: int, alias_name: str):
        super().__init__(timeout=60)
        self.cog = cog
        self.author_id = author_id
        self.alias_name = alias_name

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("This confirmation is not for you.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Delete", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.db.delete_one({"_id": f"alias:{self.alias_name}"})
        self.cog.aliases.pop(self.alias_name, None)
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            content=f"Deleted alias **{self.alias_name}**.",
            embed=None,
            view=self,
        )
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content="Deletion cancelled.", embed=None, view=self)
        self.stop()


class AliasManager(commands.Cog):
    """Interactive multi-command alias editor and runner."""

    def __init__(self, bot: ModmailBot):
        self.bot = bot
        self.db = bot.plugin_db.get_partition(self)
        self.aliases: dict[str, dict] = {}
        self.running: set[int] = set()

    async def cog_load(self) -> None:
        await self.bot.wait_until_ready()
        await self._load_aliases()
        log.info("AliasManager loaded with %d alias(es).", len(self.aliases))

    async def _load_aliases(self) -> None:
        self.aliases.clear()
        cursor = self.db.find({"_id": {"$regex": "^alias:"}})
        async for record in cursor:
            name = str(record["_id"])[6:].lower()
            steps = [str(x).strip() for x in record.get("steps", []) if str(x).strip()]
            if steps:
                self.aliases[name] = {
                    "steps": steps,
                    "creator_id": int(record.get("creator_id", 0) or 0),
                }

    async def _save_alias(self, name: str, steps: list[str], creator_id: int) -> None:
        cleaned = [step.strip() for step in steps if step.strip()]
        payload = {
            "steps": cleaned,
            "creator_id": str(creator_id),
        }
        await self.db.update_one(
            {"_id": f"alias:{name}"},
            {"$set": payload},
            upsert=True,
        )
        self.aliases[name] = {
            "steps": cleaned,
            "creator_id": creator_id,
        }

    async def _prefixes_for_message(self, message: discord.Message) -> list[str]:
        prefixes = await self.bot.get_prefix(message)
        if isinstance(prefixes, str):
            return [prefixes]
        return list(prefixes)

    async def _prefixes_for_interaction(self, interaction: discord.Interaction) -> list[str]:
        # Interaction messages live in the same guild/channel as the editor.
        if interaction.message is None:
            return ["?"]
        return await self._prefixes_for_message(interaction.message)

    async def _matched_alias(self, message: discord.Message) -> tuple[Optional[str], Optional[str]]:
        if not message.content or message.author.bot or message.guild is None:
            return None, None

        # Keep these aliases limited to the central staff/inbox guild.
        if self.bot.modmail_guild and message.guild.id != self.bot.modmail_guild.id:
            return None, None

        prefixes = await self._prefixes_for_message(message)
        matched_prefix = next(
            (p for p in sorted(prefixes, key=len, reverse=True) if message.content.startswith(p)),
            None,
        )
        if matched_prefix is None:
            return None, None

        body = message.content[len(matched_prefix):].strip()
        if not body:
            return None, None

        name = body.split(maxsplit=1)[0].lower()
        if name not in self.aliases:
            return None, None

        return matched_prefix, name

    async def _thread_variables(self, message: discord.Message) -> dict[str, str]:
        variables = {
            "{moderator}": str(message.author),
            "{moderatorid}": str(message.author.id),
            "{channel}": getattr(message.channel, "name", str(message.channel)),
            "{server}": message.guild.name if message.guild else "DM",
            "{serverid}": str(message.guild.id) if message.guild else "0",
            "{user}": "Unknown",
            "{userid}": "Unknown",
        }

        try:
            thread = await self.bot.threads.find(channel=message.channel)
        except Exception:
            thread = None

        if thread is not None:
            recipient = getattr(thread, "recipient", None)
            if recipient is not None:
                variables["{user}"] = str(recipient)
                variables["{userid}"] = str(recipient.id)
            else:
                # In Modmail, thread.id is the recipient/user ID for ordinary
                # one-user threads.
                thread_id = getattr(thread, "id", None)
                if thread_id:
                    variables["{userid}"] = str(thread_id)
                    user = self.bot.get_user(int(thread_id))
                    if user:
                        variables["{user}"] = str(user)

        return variables

    async def _render_step(self, step: str, message: discord.Message) -> str:
        variables = await self._thread_variables(message)
        rendered = step
        for key, value in variables.items():
            rendered = rendered.replace(key, value)
        return rendered

    async def _invoke_step(
        self,
        original: discord.Message,
        prefix: str,
        command_text: str,
    ) -> bool:
        # discord.Message.content is an ordinary mutable attribute in discord.py
        # v2. We use a shallow copy so the original staff message/log is untouched.
        synthetic = copy.copy(original)
        synthetic.content = f"{prefix}{command_text}"

        ctx = await self.bot.get_context(synthetic)
        if ctx.command is None:
            await original.channel.send(
                f"⚠️ Alias stopped: I couldn't find the command `{command_text.split(maxsplit=1)[0]}`."
            )
            return False

        try:
            await self.bot.invoke(ctx)
            return True
        except commands.CommandError as exc:
            # Usually Bot.invoke dispatches command_error itself. This catch is
            # retained for custom command implementations that bubble errors.
            log.exception("Alias step failed: %s", command_text)
            await original.channel.send(
                f"⚠️ Alias stopped while running `{_trim(command_text, 120)}`: `{exc}`"
            )
            return False

    @commands.Cog.listener("on_message")
    async def alias_listener(self, message: discord.Message) -> None:
        prefix, alias_name = await self._matched_alias(message)
        if alias_name is None or prefix is None:
            return

        # Do not let one staff member accidentally start the same alias twice
        # from duplicate/replayed events.
        if message.id in self.running:
            return
        self.running.add(message.id)

        try:
            record = self.aliases.get(alias_name)
            if not record:
                return

            steps = list(record["steps"])
            for step in steps:
                rendered = await self._render_step(step, message)
                ok = await self._invoke_step(message, prefix, rendered)
                if not ok:
                    break

                # Give Discord/Modmail a moment to finish message/thread state
                # changes before the following command.
                await asyncio.sleep(0.35)
        finally:
            self.running.discard(message.id)

    def _valid_new_name(self, name: str) -> tuple[bool, str]:
        name = name.lower()
        if not NAME_RE.fullmatch(name):
            return False, "Alias names may only contain letters, numbers, `_`, or `-` (max 32 characters)."
        if self.bot.get_command(name) is not None:
            return False, f"`{name}` conflicts with an existing bot command."
        return True, ""

    @commands.group(
        name="aliasmanager",
        aliases=("am", "aliaseditor"),
        invoke_without_command=True,
    )
    @checks.has_permissions(PermissionLevel.OWNER)
    async def aliasmanager(self, ctx: commands.Context) -> None:
        """Manage enhanced aliases."""
        embed = discord.Embed(
            title="Alias Manager",
            description=(
                "Create multi-command aliases without manually writing `&&`.\n\n"
                f"`{ctx.prefix}am create <name>` — interactive creator\n"
                f"`{ctx.prefix}am edit <name>` — edit an existing alias\n"
                f"`{ctx.prefix}am list` — list aliases\n"
                f"`{ctx.prefix}am show <name>` — show its steps\n"
                f"`{ctx.prefix}am copy <name> <new name>` — duplicate it\n"
                f"`{ctx.prefix}am move <name> <from> <to>` — reorder a step\n"
                f"`{ctx.prefix}am delete <name>` — delete it"
            ),
            color=self.bot.main_color,
        )
        await ctx.send(embed=embed)

    @aliasmanager.command(name="create", aliases=("new", "make"))
    @checks.has_permissions(PermissionLevel.OWNER)
    async def create_alias(self, ctx: commands.Context, name: str) -> None:
        name = name.lower()
        valid, error = self._valid_new_name(name)
        if not valid:
            await ctx.send(error)
            return
        if name in self.aliases:
            await ctx.send(
                f"Alias **{name}** already exists. Use `{ctx.prefix}am edit {name}` instead."
            )
            return

        view = AliasEditorView(self, ctx.author.id, name)
        view.message = await ctx.send(embed=view.make_embed(), view=view)

    @aliasmanager.command(name="edit")
    @checks.has_permissions(PermissionLevel.OWNER)
    async def edit_alias(self, ctx: commands.Context, name: str) -> None:
        name = name.lower()
        record = self.aliases.get(name)
        if record is None:
            await ctx.send(f"No enhanced alias named **{name}** exists.")
            return

        view = AliasEditorView(
            self,
            ctx.author.id,
            name,
            list(record["steps"]),
            editing=True,
        )
        view.message = await ctx.send(embed=view.make_embed(), view=view)

    @aliasmanager.command(name="list", aliases=("all",))
    @checks.has_permissions(PermissionLevel.OWNER)
    async def list_aliases(self, ctx: commands.Context) -> None:
        if not self.aliases:
            await ctx.send("No enhanced aliases have been created yet.")
            return

        lines = [
            f"• **{name}** — {len(record['steps'])} step(s)"
            for name, record in sorted(self.aliases.items())
        ]
        embed = discord.Embed(
            title=f"Enhanced Aliases ({len(lines)})",
            description=_trim("\n".join(lines), 4000),
            color=self.bot.main_color,
        )
        await ctx.send(embed=embed)

    @aliasmanager.command(name="show", aliases=("view", "info"))
    @checks.has_permissions(PermissionLevel.OWNER)
    async def show_alias(self, ctx: commands.Context, name: str) -> None:
        name = name.lower()
        record = self.aliases.get(name)
        if record is None:
            await ctx.send(f"No enhanced alias named **{name}** exists.")
            return

        lines = [f"**{i}.** `{_trim(step, 350)}`" for i, step in enumerate(record["steps"], 1)]
        embed = discord.Embed(
            title=f"Alias: {name}",
            description=_trim("\n".join(lines), 4000),
            color=self.bot.main_color,
        )
        embed.set_footer(text=f"Run with {ctx.prefix}{name}")
        await ctx.send(embed=embed)

    @aliasmanager.command(name="delete", aliases=("remove", "del"))
    @checks.has_permissions(PermissionLevel.OWNER)
    async def delete_alias(self, ctx: commands.Context, name: str) -> None:
        name = name.lower()
        if name not in self.aliases:
            await ctx.send(f"No enhanced alias named **{name}** exists.")
            return

        view = ConfirmDeleteView(self, ctx.author.id, name)
        await ctx.send(
            f"Delete enhanced alias **{name}**? This cannot be undone.",
            view=view,
        )

    @aliasmanager.command(name="copy", aliases=("duplicate",))
    @checks.has_permissions(PermissionLevel.OWNER)
    async def copy_alias(self, ctx: commands.Context, name: str, new_name: str) -> None:
        name = name.lower()
        new_name = new_name.lower()

        source = self.aliases.get(name)
        if source is None:
            await ctx.send(f"No enhanced alias named **{name}** exists.")
            return

        valid, error = self._valid_new_name(new_name)
        if not valid:
            await ctx.send(error)
            return
        if new_name in self.aliases:
            await ctx.send(f"Alias **{new_name}** already exists.")
            return

        await self._save_alias(new_name, list(source["steps"]), ctx.author.id)
        await ctx.send(f"Copied **{name}** → **{new_name}**.")

    @aliasmanager.command(name="move", aliases=("reorder",))
    @checks.has_permissions(PermissionLevel.OWNER)
    async def move_alias(
        self,
        ctx: commands.Context,
        name: str,
        from_position: int,
        to_position: int,
    ) -> None:
        name = name.lower()
        record = self.aliases.get(name)
        if record is None:
            await ctx.send(f"No enhanced alias named **{name}** exists.")
            return

        steps = list(record["steps"])
        if not (1 <= from_position <= len(steps)) or not (1 <= to_position <= len(steps)):
            await ctx.send(f"Positions must be between 1 and {len(steps)}.")
            return

        item = steps.pop(from_position - 1)
        steps.insert(to_position - 1, item)
        await self._save_alias(name, steps, ctx.author.id)
        await ctx.send(
            f"Moved step **{from_position}** to **{to_position}** in **{name}**."
        )


async def setup(bot: ModmailBot) -> None:
    await bot.add_cog(AliasManager(bot))
