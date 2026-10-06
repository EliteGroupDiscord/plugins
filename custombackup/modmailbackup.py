import io
import json
import datetime
from copy import deepcopy

import discord
from discord.ext import commands

from bot import ModmailBot
from core import checks
from core.models import getLogger

logger = getLogger(__name__)

SECRET_KEYS = {"token", "connection_uri", "mongo_uri", "github_token", "database_password", "password"}
DEPLOYMENT_KEYS = {"guild_id", "modmail_guild_id", "log_url", "log_url_prefix", "database_type"}
BACKUP_VERSION = 1


def _json_default(value):
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat()
    if isinstance(value, set):
        return list(value)
    return str(value)


def _safe_dict(data):
    if not isinstance(data, dict):
        return {}
    return {str(k): v for k, v in data.items() if str(k).lower() not in SECRET_KEYS}


class ModmailBackup(commands.Cog):
    """Portable backup and restore for Modmail."""

    def __init__(self, bot: ModmailBot):
        self.bot = bot
        self.partition = bot.plugin_db.get_partition(self)

    async def _raw_config(self):
        data = await self.bot.api.get_config()
        data = _safe_dict(deepcopy(data or {}))
        data.pop("_id", None)
        data.pop("bot_id", None)
        return data

    async def _plugin_data(self):
        exported = {}
        try:
            plugins_db = self.bot.api.db.plugins
            names = await plugins_db.list_collection_names()
        except Exception:
            logger.exception("Could not enumerate plugin database collections.")
            return exported

        for name in names:
            if name == self.__class__.__name__:
                continue
            try:
                docs = await plugins_db[name].find({}).to_list(None)
                clean = []
                for doc in docs:
                    doc = deepcopy(doc)
                    doc.pop("_id", None)
                    clean.append(doc)
                exported[name] = clean
            except Exception:
                logger.exception("Could not export plugin collection %s.", name)
        return exported

    async def _make_payload(self, include_plugin_data=True):
        cfg = await self._raw_config()
        core_keys = (
            "snippets", "aliases", "command_permissions", "level_permissions",
            "override_command_level", "auto_triggers", "blocked", "blocked_roles",
            "blocked_whitelist", "notification_squad", "subscriptions", "closures", "plugins"
        )
        core = {k: deepcopy(cfg.get(k, {} if k != "plugins" else [])) for k in core_keys}
        settings = {
            k: v for k, v in cfg.items()
            if k not in core_keys and k not in SECRET_KEYS and k not in DEPLOYMENT_KEYS
        }
        return {
            "format": "modmail-portable-backup",
            "backup_version": BACKUP_VERSION,
            "created_at": discord.utils.utcnow().isoformat(),
            "bot_id": str(self.bot.user.id) if self.bot.user else None,
            "guild_id": str(getattr(self.bot, "guild_id", "")),
            "modmail_version": str(getattr(self.bot, "version", "unknown")),
            "core": core,
            "settings": settings,
            "plugin_data": await self._plugin_data() if include_plugin_data else {},
        }

    def _file(self, payload):
        raw = json.dumps(payload, indent=2, ensure_ascii=False, default=_json_default).encode()
        stamp = discord.utils.utcnow().strftime("%Y-%m-%d_%H-%M-%S")
        return discord.File(io.BytesIO(raw), filename=f"modmail-backup_{stamp}.json")

    @commands.group(name="backup", invoke_without_command=True)
    @checks.has_permissions(commands.PermissionLevel.OWNER)
    async def backup(self, ctx):
        p = ctx.prefix
        await ctx.send(
            f"**Modmail Backup**\n"
            f"`{p}backup create` - export settings, snippets, aliases and plugin data\n"
            f"`{p}backup create core` - export core data only\n"
            f"`{p}backup inspect` - inspect attached backup\n"
            f"`{p}backup restore` - restore attached backup\n"
            f"`{p}backup restore core` - restore core data only\n\n"
            "Tokens and database credentials are never exported."
        )

    @backup.command(name="create", aliases=["export", "save"])
    @checks.has_permissions(commands.PermissionLevel.OWNER)
    async def backup_create(self, ctx, mode: str = "all"):
        include_plugins = mode.lower() not in {"core", "safe"}
        status = await ctx.send("Creating backup...")
        try:
            payload = await self._make_payload(include_plugins)
            core = payload["core"]
            embed = discord.Embed(
                title="Backup Complete",
                description="Keep this attachment somewhere you control.",
                color=discord.Color.green(),
            )
            embed.add_field(name="Snippets", value=str(len(core.get("snippets", {}))))
            embed.add_field(name="Aliases", value=str(len(core.get("aliases", {}))))
            embed.add_field(name="Plugins", value=str(len(core.get("plugins", []))))
            embed.add_field(name="Plugin data", value=str(len(payload.get("plugin_data", {}))))
            embed.set_footer(text="Secrets and database credentials were excluded.")
            await ctx.send(embed=embed, file=self._file(payload))
            try:
                await status.delete()
            except discord.HTTPException:
                pass
        except Exception as exc:
            logger.exception("Backup failed.")
            await status.edit(content=f"Backup failed: `{type(exc).__name__}: {exc}`")

    async def _read_attachment(self, ctx):
        if not ctx.message.attachments:
            raise commands.BadArgument("Attach the backup JSON to the same message.")
        attachment = ctx.message.attachments[0]
        if attachment.size > 20 * 1024 * 1024:
            raise commands.BadArgument("Backup file is too large.")
        try:
            data = json.loads((await attachment.read()).decode("utf-8"))
        except Exception as exc:
            raise commands.BadArgument("The attachment is not valid JSON.") from exc
        if data.get("format") != "modmail-portable-backup":
            raise commands.BadArgument("That is not a Modmail portable backup.")
        return data

    @backup.command(name="inspect", aliases=["info"])
    @checks.has_permissions(commands.PermissionLevel.OWNER)
    async def backup_inspect(self, ctx):
        data = await self._read_attachment(ctx)
        core = data.get("core", {})
        embed = discord.Embed(title="Backup Information", color=discord.Color.blurple())
        embed.add_field(name="Created", value=str(data.get("created_at", "Unknown")), inline=False)
        embed.add_field(name="Version", value=str(data.get("modmail_version", "Unknown")))
        embed.add_field(name="Snippets", value=str(len(core.get("snippets", {}))))
        embed.add_field(name="Aliases", value=str(len(core.get("aliases", {}))))
        embed.add_field(name="Plugins", value=str(len(core.get("plugins", []))))
        embed.add_field(name="Plugin data", value=str(len(data.get("plugin_data", {}))))
        await ctx.send(embed=embed)

    async def _restore_core(self, data):
        current = await self._raw_config()
        merged = deepcopy(current)
        for section in ("settings", "core"):
            for key, value in data.get(section, {}).items():
                if key not in SECRET_KEYS and key not in DEPLOYMENT_KEYS:
                    merged[key] = value
        for key in SECRET_KEYS | DEPLOYMENT_KEYS:
            merged.pop(key, None)
        await self.bot.api.update_config(merged)

    async def _restore_plugins(self, data):
        restored = 0
        plugins_db = self.bot.api.db.plugins
        for name, docs in data.get("plugin_data", {}).items():
            if name == self.__class__.__name__ or not isinstance(docs, list):
                continue
            collection = plugins_db[name]
            await collection.delete_many({})
            if docs:
                await collection.insert_many(deepcopy(docs))
            restored += 1
        return restored

    @backup.command(name="restore", aliases=["import"])
    @checks.has_permissions(commands.PermissionLevel.OWNER)
    async def backup_restore(self, ctx, mode: str = "all"):
        data = await self._read_attachment(ctx)

        # Always make a copy of current state first.
        safety = await self._make_payload(True)
        await ctx.send("Pre-restore safety backup:", file=self._file(safety))

        await self._restore_core(data)
        restored = 0
        if mode.lower() not in {"core", "safe"}:
            restored = await self._restore_plugins(data)

        embed = discord.Embed(
            title="Restore Complete",
            description="Restart the bot before relying on the restored settings.",
            color=discord.Color.green(),
        )
        embed.add_field(name="Plugin partitions restored", value=str(restored))
        embed.set_footer(text="Token, MongoDB URI, guild IDs and log URL were not changed.")
        await ctx.send(embed=embed)


async def setup(bot: ModmailBot):
    await bot.add_cog(ModmailBackup(bot))
