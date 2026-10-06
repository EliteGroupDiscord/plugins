"""Portable backup/restore for modmail-dev/Modmail."""
from __future__ import annotations
import io, json
from copy import deepcopy
import discord
from discord.ext import commands
from bot import ModmailBot
from core import checks
from core.models import PermissionLevel, getLogger

log = getLogger(__name__)
FORMAT = "midnight-modmail-backup"
PROTECTED = {"token","connection_uri","mongo_uri","github_token","password",
             "guild_id","modmail_guild_id","log_url","log_url_prefix"}

class ModmailBackup(commands.Cog):
    def __init__(self, bot: ModmailBot):
        self.bot = bot
        self.db = bot.plugin_db.get_partition(self)

    async def _config(self):
        getter = getattr(self.bot.api, "get_config", None)
        if getter is None:
            raise RuntimeError("This Modmail build has no api.get_config method.")
        data = deepcopy(await getter() or {})
        data.pop("_id", None)
        data.pop("bot_id", None)
        return {str(k): v for k,v in data.items() if str(k).lower() not in PROTECTED}

    async def _plugin_data(self):
        out = {}
        try:
            pdb = self.bot.api.db.plugins
            names = await pdb.list_collection_names()
        except Exception:
            return out
        for name in names:
            if name == self.__class__.__name__:
                continue
            try:
                docs = await pdb[name].find({}).to_list(length=None)
                for d in docs:
                    d.pop("_id", None)
                out[name] = docs
            except Exception:
                log.exception("Failed exporting plugin partition %s", name)
        return out

    async def _payload(self, plugins=True):
        return {
            "format": FORMAT,
            "created_at": discord.utils.utcnow().isoformat(),
            "config": await self._config(),
            "plugin_data": await self._plugin_data() if plugins else {}
        }

    def _file(self, data):
        raw = json.dumps(data, indent=2, ensure_ascii=False, default=str).encode()
        stamp = discord.utils.utcnow().strftime("%Y-%m-%d_%H-%M-%S")
        return discord.File(io.BytesIO(raw), filename=f"modmail-backup-{stamp}.json")

    @commands.group(name="backup", invoke_without_command=True)
    @checks.has_permissions(PermissionLevel.OWNER)
    async def backup(self, ctx):
        await ctx.send(
            f"`{ctx.prefix}backup create` — create full portable backup\n"
            f"`{ctx.prefix}backup create core` — config only\n"
            f"`{ctx.prefix}backup inspect` — inspect attached backup\n"
            f"`{ctx.prefix}backup restore` — restore attached backup"
        )

    @backup.command(name="create", aliases=("save","export"))
    @checks.has_permissions(PermissionLevel.OWNER)
    async def create(self, ctx, mode: str = "all"):
        msg = await ctx.send("Creating backup...")
        try:
            data = await self._payload(mode.lower() != "core")
            cfg = data["config"]
            e = discord.Embed(title="Backup Complete",
                              description="Keep the attached JSON somewhere you control.",
                              color=discord.Color.green())
            e.add_field(name="Snippets", value=str(len(cfg.get("snippets", {}))))
            e.add_field(name="Aliases", value=str(len(cfg.get("aliases", {}))))
            e.add_field(name="Settings", value=str(len(cfg)))
            e.add_field(name="Plugin partitions", value=str(len(data["plugin_data"])))
            await ctx.send(embed=e, file=self._file(data))
            try: await msg.delete()
            except discord.HTTPException: pass
        except Exception as exc:
            log.exception("Backup failed")
            await msg.edit(content=f"Backup failed: `{type(exc).__name__}: {exc}`")

    async def _read(self, ctx):
        if not ctx.message.attachments:
            raise commands.BadArgument("Attach the backup JSON to this command.")
        try:
            data = json.loads((await ctx.message.attachments[0].read()).decode())
        except Exception as exc:
            raise commands.BadArgument("Invalid backup JSON.") from exc
        if data.get("format") != FORMAT:
            raise commands.BadArgument("That is not a backup made by this plugin.")
        return data

    @backup.command(name="inspect", aliases=("info",))
    @checks.has_permissions(PermissionLevel.OWNER)
    async def inspect(self, ctx):
        data = await self._read(ctx)
        cfg = data.get("config", {})
        e = discord.Embed(title="Backup Information", color=self.bot.main_color)
        e.add_field(name="Created", value=data.get("created_at","Unknown"), inline=False)
        e.add_field(name="Snippets", value=str(len(cfg.get("snippets", {}))))
        e.add_field(name="Aliases", value=str(len(cfg.get("aliases", {}))))
        e.add_field(name="Settings", value=str(len(cfg)))
        e.add_field(name="Plugin partitions", value=str(len(data.get("plugin_data", {}))))
        await ctx.send(embed=e)

    @backup.command(name="restore", aliases=("import",))
    @checks.has_permissions(PermissionLevel.OWNER)
    async def restore(self, ctx, mode: str = "all"):
        data = await self._read(ctx)
        # Safety backup first.
        await ctx.send("Pre-restore safety backup:", file=self._file(await self._payload(True)))
        safe = {k:v for k,v in data.get("config",{}).items() if str(k).lower() not in PROTECTED}
        updater = getattr(self.bot.api, "update_config", None)
        if updater is None:
            raise RuntimeError("This Modmail build has no api.update_config method.")
        await updater(safe)

        restored = 0
        if mode.lower() != "core":
            try:
                pdb = self.bot.api.db.plugins
                for name, docs in data.get("plugin_data",{}).items():
                    if name == self.__class__.__name__ or not isinstance(docs, list):
                        continue
                    col = pdb[name]
                    await col.delete_many({})
                    if docs: await col.insert_many(deepcopy(docs))
                    restored += 1
            except Exception:
                log.exception("Some plugin data could not be restored.")

        await ctx.send(f"Restore complete. Plugin partitions restored: **{restored}**. Restart Modmail.")

async def setup(bot: ModmailBot) -> None:
    await bot.add_cog(ModmailBackup(bot))
