import os
import re
import asyncio

import discord
from discord import app_commands
from discord.ext import commands

from cogs.common import (
    DEFAULT_PREFIX,
    G,
    audit,
    get_guild_prefix_sync,
    get_guild_settings,
    set_guild_prefix,
    set_guild_settings,
)

OWNER_ID = os.getenv("OWNER_ID")


class SetupFlow(discord.ui.View):
    def __init__(self, cog, author, guild_id):
        super().__init__(timeout=300)
        self.cog = cog
        self.author = author
        self.guild_id = guild_id
        self.busy = False

    async def interaction_check(self, interaction):
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("Only the admin who started this can set it up.")
            return False
        return True

    async def _wait_input(self, interaction, prompt):
        if self.busy:
            await interaction.response.send_message("Finish the current prompt first.")
            return None
        self.busy = True
        await interaction.response.send_message(prompt)
        try:
            msg = await self.cog.bot.wait_for(
                "message",
                check=lambda m: m.author.id == interaction.user.id and m.channel.id == interaction.channel.id,
                timeout=120,
            )
        except asyncio.TimeoutError:
            self.busy = False
            await interaction.followup.send("Timed out.")
            return None
        self.busy = False
        try:
            await msg.delete()
        except Exception:
            pass
        return msg.content.strip()

    @discord.ui.button(label="Activity log channel", style=discord.ButtonStyle.primary)
    async def activity_button(self, interaction, button):
        value = await self._wait_input(
            interaction, "Mention the **activity log channel** (who summoned what), or type `skip`:"
        )
        if value is None:
            return
        channel = interaction.guild.get_channel(_parse_channel(value))
        if channel is None:
            await interaction.followup.send("No valid channel — leaving it unchanged.")
            return
        set_guild_settings(self.guild_id, activity_log_channel_id=channel.id)
        audit(self.guild_id, interaction.user.id, "settings", "guild", self.guild_id, f"activity channel -> #{channel.name}")
        await interaction.followup.send(f"✅ Activity log channel set to {channel.mention}.")

    @discord.ui.button(label="Member log channel", style=discord.ButtonStyle.secondary)
    async def member_button(self, interaction, button):
        value = await self._wait_input(
            interaction, "Mention the **member log channel** (live member list embeds), or type `skip`:"
        )
        if value is None:
            return
        channel = interaction.guild.get_channel(_parse_channel(value))
        if channel is None:
            await interaction.followup.send("No valid channel — leaving it unchanged.")
            return
        set_guild_settings(self.guild_id, member_log_channel_id=channel.id)
        audit(self.guild_id, interaction.user.id, "settings", "guild", self.guild_id, f"member channel -> #{channel.name}")
        await interaction.followup.send(f"✅ Member log channel set to {channel.mention}.")

    @discord.ui.button(label="Max groups per member", style=discord.ButtonStyle.secondary)
    async def limit_button(self, interaction, button):
        value = await self._wait_input(
            interaction, "Type the **max groups per member** (default 3), or type `skip`:"
        )
        if value is None:
            return
        if value.lower() == "skip":
            await interaction.followup.send("Left unchanged.")
            return
        try:
            limit = max(1, int(value))
        except ValueError:
            await interaction.followup.send("That's not a number — leaving it unchanged.")
            return
        set_guild_settings(self.guild_id, max_groups_per_member=limit)
        audit(self.guild_id, interaction.user.id, "settings", "guild", self.guild_id, f"max groups -> {limit}")
        await interaction.followup.send(f"✅ Max groups per member set to **{limit}**.")

    @discord.ui.button(label="Done", style=discord.ButtonStyle.success)
    async def done_button(self, interaction, button):
        settings = get_guild_settings(self.guild_id)
        embed = discord.Embed(title="⚙️ Summon setup", color=discord.Color.blue())
        embed.add_field(name="Activity log channel", value=f"<#{settings.get('activity_log_channel_id')}>" if settings.get("activity_log_channel_id") else "Not set")
        embed.add_field(name="Member log channel", value=f"<#{settings.get('member_log_channel_id')}>" if settings.get("member_log_channel_id") else "Not set")
        embed.add_field(name="Max groups per member", value=str(settings.get("max_groups_per_member", 3)))
        await interaction.response.send_message(embed=embed)


def _parse_channel(value):
    m = re.match(r"<#(\d+)>|(\d+)", value.strip())
    if not m:
        return None
    return int(m.group(1) or m.group(2))


class HelpView(discord.ui.View):
    def __init__(self, cog, author, prefix):
        super().__init__(timeout=300)
        self.cog = cog
        self.author = author
        self.prefix = prefix
        self.page = 0

    async def interaction_check(self, interaction):
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("Not your help menu.")
            return False
        return True

    def _embed(self):
        pages = self._pages()
        p = pages[self.page]
        embed = discord.Embed(title=p["title"], color=discord.Color.blue())
        for name, value in p["fields"]:
            embed.add_field(name=name, value=value, inline=False)
        embed.set_footer(text=f"Page {self.page + 1}/{len(pages)}")
        return embed

    @discord.ui.button(label="◀️", style=discord.ButtonStyle.secondary)
    async def prev_button(self, interaction, button):
        self.page = (self.page - 1) % len(self._pages())
        await interaction.response.edit_message(embed=self._embed())

    @discord.ui.button(label="▶️", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction, button):
        self.page = (self.page + 1) % len(self._pages())
        await interaction.response.edit_message(embed=self._embed())

    def _pages(self):
        return [
            {
                "title": "🔔 Summoning",
                "fields": [
                    ("/summon <summon>", "Ping all members of a summon. Pings them, then the message becomes **[ Name ]** has been summoned !"),
                    ("/servercard [user]", "Show a member's virtual roles + special roles, with 👑 (owner), 📢 (can ping), 🤝 (can invite)."),
                    ("/list groups", "List all groups you can join or are in (✅ in, ❌ not, ⛔ banned). Admins see everything."),
                ],
            },
            {
                "title": "📦 Manage a summon",
                "fields": [
                    ("/create summon <name> <canping> <canjoin>", "Create a virtual summon (max 3 for members)."),
                    ("/edit summon <summon>", "Open the edit panel (join/ping selectors + rename + add pingers/inviters)."),
                    ("/delete summon <summon>", "Delete a summon."),
                    ("/join <summon> / /leave <summon>", "Join or leave a summon."),
                    ("/invite_to <summon> <user>", "Invite someone to an invite-only summon."),
                    ("/ban_from <summon> <user>", "Ban a user from a summon."),
                    ("/unban_from <summon> <user>", "Unban a user from a summon."),
                ],
            },
            {
                "title": "🛡️ Admin prefix commands",
                "fields": [
                    (f"{self.prefix}summon create/edit/delete", "Create, edit or delete a summon from chat."),
                    (f"{self.prefix}promote <user> <summon>", "Make someone a co-owner (owner only)."),
                    (f"{self.prefix}revoke <user> <summon>", "Remove a co-owner (owner only)."),
                    (f"{self.prefix}role <summon> -y|-r", "Add (-y) or remove (-r) a real Discord role for a summon."),
                    (f"{self.prefix}logs <summon>", "View logs about a summon."),
                    (f"{self.prefix}audit", "View recent admin actions."),
                    (f"{self.prefix}allow summon <@role>", "Allow a real role to be summoned by specific users/roles."),
                    (f"{self.prefix}blacklist <@user> / {self.prefix}unblacklist <@user>", "Block someone from summon commands (they can still join)."),
                    (f"{self.prefix}summonsetup_all", "Set up log channels + limits."),
                    (f"{self.prefix}purge", "Clean up stale summon entries."),
                    (f"{self.prefix}prefix_now <new prefix>", "Change the bot's prefix (owner only)."),
                ],
            },
        ]


class CoreCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.guild is None or message.author.bot:
            return
        content = message.content.strip()
        m = re.match(r"^!?\?prefix_now(?:\s+(\S+))?$", content, re.IGNORECASE)
        if not m:
            return
        if OWNER_ID and message.author.id != int(OWNER_ID):
            await message.channel.send("Only the bot owner can change the prefix.")
            return
        new_prefix = m.group(1)
        if not new_prefix:
            await message.channel.send(
                f"Current prefix: `{get_guild_prefix_sync(message.guild.id)}`\nUsage: `!?prefix_now <new prefix>`"
            )
            return
        set_guild_prefix(message.guild.id, new_prefix)
        audit(message.guild.id, message.author.id, "prefix", "guild", message.guild.id, f"prefix -> {new_prefix}")
        await message.channel.send(f"✅ Prefix changed to `{new_prefix}`.")

    @commands.command(name="summonsetup_all")
    @commands.has_permissions(administrator=True)
    async def summonsetup_all(self, ctx):
        """Set up log channels and other settings."""
        await ctx.send(
            "⚙️ **Summon setup** — use the buttons below:",
            view=SetupFlow(self, ctx.author, ctx.guild.id),
        )

    @commands.command(name="help")
    async def help_prefix(self, ctx, *args):
        """Show help (prefix)."""
        prefix = get_guild_prefix_sync(ctx.guild.id)
        await ctx.send(embed=HelpView(self, ctx.author, prefix)._embed(), view=HelpView(self, ctx.author, prefix))

    @app_commands.command(name="help")
    async def help_slash(self, interaction):
        """Show help."""
        prefix = get_guild_prefix_sync(interaction.guild.id)
        await interaction.response.send_message(
            embed=HelpView(self, interaction.user, prefix)._embed(),
            view=HelpView(self, interaction.user, prefix),
        )


async def setup(bot):
    await bot.add_cog(CoreCog(bot))
