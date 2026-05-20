import discord
from discord.ext import commands

from core import checks
from core.models import PermissionLevel


class Nuke(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="nuke")
    @commands.guild_only()
    @checks.has_permissions(PermissionLevel.ADMINISTRATOR)
    async def nuke(self, ctx):
        """Delete and recreate the current channel."""
        channel = ctx.channel

        if not channel.permissions_for(ctx.guild.me).manage_channels:
            return await ctx.send("❌ I need Manage Channels permission.")

        position = channel.position
        overwrites = channel.overwrites
        category = channel.category

        new_channel = await channel.clone(
            name=channel.name,
            reason=f"Channel nuked by {ctx.author}",
        )

        await new_channel.edit(
            position=position,
            category=category,
            overwrites=overwrites,
            reason=f"Channel nuked by {ctx.author}",
        )

        await channel.delete(reason=f"Channel nuked by {ctx.author}")

        await new_channel.send(f"💥 Channel nuked by **{ctx.author}**.")


async def setup(bot):
    await bot.add_cog(Nuke(bot))
