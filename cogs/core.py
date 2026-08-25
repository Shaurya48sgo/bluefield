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
    get_dev_ids,
    get_mod_ids,
    has_admin_or_dev,
    is_dev,
    is_owner,
    set_guild_prefix,
    set_guild_settings,
)

OWNER_ID = os.getenv("OWNER_ID")


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
                    ("/easyjoin <summon>", "Post Join/Leave/Members buttons for an open-join summon (reaction-role style)."),
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
                    (f"{self.prefix}activitychannel", "Run IN a channel to make it the activity log channel."),
                    (f"{self.prefix}memberchannel", "Run IN a channel to make it the member log channel."),
                    (f"{self.prefix}confesschannel", "Run IN a channel to make it the anonymous chat channel."),
                    (f"{self.prefix}groupmax <n>", "Set max groups per member."),
                    (f"{self.prefix}confessmax <n>", "Set max codes per member."),
                    (f"{self.prefix}layout", "Preview the unified secret & reply layout (owner)."),
                    (f"{self.prefix}dev <@user> -y|-r", "Add/remove a dev (owner/server owner only). Devs have owner powers except managing devs."),
                    (f"{self.prefix}purge", "Clean up stale summon entries."),
                    (f"{self.prefix}prefix_now <new prefix>", "Change the bot's prefix (owner only)."),
                    (f"{self.prefix}colors", "DM the owner every usable embed colour, numbered and shown in its own colour (owner only)."),
                    (f"{self.prefix}mod <@user> -y|-r", "Add/remove a secret-chat mod. Mods can suspend/unsuspend codes."),
                    (f"{self.prefix}mods", "List this server's mods."),
                    (f"{self.prefix}modlog", "Run IN a channel to make it the mod-log channel."),
                    (f"{self.prefix}reports", "Run IN a channel to make it the code-reports channel."),
                    (f"{self.prefix}suspend <code> <duration>", "Suspend a code, e.g. `30m`, `2h`, `1w` (admins/devs/mods)."),
                    (f"{self.prefix}unsuspend <code>", "Remove a suspension (admins/devs/mods)."),
                ],
            },
            {
                "title": "🕶️ Anonymous chat",
                "fields": [
                    ("/secret say <message> [code]", "Post an anonymous message. Codes have a Reply button; replies land in your /inbox."),
                    ("/secret code delete <code>", "Delete one of your codes (pick from the list) to free a slot. New codes are generated via `/secret say` or the Reply button."),
                    ("/secret reveal propose <to_code> <your_code> [also_delete]", "Propose mutually revealing identities. If they accept, both of you get DMs showing who's who — optionally deleting both codes (anti-blackmail)."),
                    ("/secret report <code> <reason>", "Report a code to the staff. Anyone can use it; reports go to the reports channel."),
                    ("/inbox", "DM-only: see where your codes were mentioned (jump links) + Clear inbox / Clear chat buttons."),
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
        m = re.match(r"^I\?prefix_now(?:\s+(\S+))?$", content, re.IGNORECASE)
        if not m:
            return
        if not is_owner(message.author.id):
            await message.channel.send("Only the bot owner can change the prefix.")
            return
        new_prefix = m.group(1)
        if not new_prefix:
            await message.channel.send(
                f"Current prefix: `{get_guild_prefix_sync(message.guild.id)}`\nUsage: `I?prefix_now <new prefix>`"
            )
            return
        set_guild_prefix(message.guild.id, new_prefix)
        audit(message.guild.id, message.author.id, "prefix", "guild", message.guild.id, f"prefix -> {new_prefix}")
        await message.channel.send(f"✅ Prefix changed to `{new_prefix}`.")

    @commands.command(name="dev")
    async def dev(self, ctx, user: discord.Member = None, flag: str = None):
        """Add (-y) or remove (-r) a dev (owner/server owner only)."""
        allowed = is_owner(ctx.author.id) or (ctx.author.id == ctx.guild.owner_id)
        if not allowed:
            await ctx.send("Only the bot owner or server owner can manage devs.")
            return
        if user is None:
            devs = get_dev_ids(ctx.guild.id)
            await ctx.send("Devs: " + (" ".join(f"<@{d}>" for d in devs) if devs else "none") + "\nUsage: `I?dev <user> -y|-r`")
            return
        devs = list(get_dev_ids(ctx.guild.id))
        flag = (flag or "").lower()
        if flag == "-y":
            if user.id in devs:
                await ctx.send(f"{user.mention} is already a dev.")
                return
            devs.append(user.id)
            set_guild_settings(ctx.guild.id, dev_ids=devs)
            audit(ctx.guild.id, ctx.author.id, "dev_add", "user", user.id)
            await ctx.send(f"✅ {user.mention} is now a dev.")
        elif flag == "-r":
            if user.id not in devs:
                await ctx.send(f"{user.mention} is not a dev.")
                return
            devs.remove(user.id)
            set_guild_settings(ctx.guild.id, dev_ids=devs)
            audit(ctx.guild.id, ctx.author.id, "dev_remove", "user", user.id)
            await ctx.send(f"✅ {user.mention} is no longer a dev.")
        else:
            await ctx.send("Usage: `I?dev <user> -y|-r` (-y add, -r remove)")

    @commands.command(name="colors")
    async def colors(self, ctx):
        """DM the owner every usable embed colour, numbered and shown in its own colour."""
        if not is_owner(ctx.author.id):
            await ctx.send("Only the bot owner can use this.")
            return
        seen = {}
        for name in sorted(dir(discord.Colour)):
            if name.startswith("_") or name == "random":
                continue
            try:
                val = getattr(discord.Colour, name)()
            except Exception:
                continue
            if not isinstance(val, discord.Colour):
                continue
            if val.value in seen:
                seen[val.value] += "/" + name
                continue
            seen[val.value] = name
        items = sorted(seen.items(), key=lambda t: t[1])
        embeds = [
            discord.Embed(title=f"{i}. {name} — #{value:06X}", colour=discord.Colour(value))
            for i, (value, name) in enumerate(items, 1)
        ]
        for i in range(0, len(embeds), 10):
            await ctx.author.send(embeds=embeds[i : i + 10])
        audit(ctx.guild.id, ctx.author.id, "colors", "guild", ctx.guild.id)
        await ctx.send(f"📩 DM'd you **{len(embeds)}** embed colours.")

    async def send_mod_log(self, guild, embed):
        cid = get_guild_settings(guild.id).get("mod_log_channel_id")
        if not cid:
            return
        channel = guild.get_channel(cid)
        if not channel:
            return
        try:
            await channel.send(embed=embed)
        except Exception:
            pass

    @commands.command(name="mod")
    async def mod(self, ctx, user: discord.Member = None, flag: str = None):
        """Add (-y) or remove (-r) a secret-chat mod (bot owner/server owner/devs only)."""
        allowed = is_owner(ctx.author.id) or ctx.author.id == ctx.guild.owner_id or is_dev(ctx.guild.id, ctx.author.id)
        if not allowed:
            await ctx.send("Only the bot owner, server owner or devs can manage mods.")
            return
        if user is None:
            mods = get_mod_ids(ctx.guild.id)
            await ctx.send(
                "Mods: " + (" ".join(f"<@{m}>" for m in mods) if mods else "none")
                + "\nUsage: `I?mod <user> -y|-r`"
            )
            return
        mods = list(get_mod_ids(ctx.guild.id))
        flag = (flag or "").lower()
        if flag == "-y":
            if user.id in mods:
                await ctx.send(f"{user.mention} is already a mod.")
                return
            mods.append(user.id)
            set_guild_settings(ctx.guild.id, mod_ids=mods)
            audit(ctx.guild.id, ctx.author.id, "mod_add", "user", user.id)
            embed = discord.Embed(
                title="🛡️ Mod added",
                color=discord.Colour(0x5865F2),
                description=f"**Mod:** {user.mention}\n**Added by:** {ctx.author.mention}",
            )
            await self.send_mod_log(ctx.guild, embed)
            await ctx.send(f"✅ {user.mention} is now a mod (can suspend/unsuspend codes).")
        elif flag == "-r":
            if user.id not in mods:
                await ctx.send(f"{user.mention} is not a mod.")
                return
            mods.remove(user.id)
            set_guild_settings(ctx.guild.id, mod_ids=mods)
            audit(ctx.guild.id, ctx.author.id, "mod_remove", "user", user.id)
            embed = discord.Embed(
                title="🛡️ Mod removed",
                color=discord.Colour(0x5865F2),
                description=f"**Mod:** {user.mention}\n**Removed by:** {ctx.author.mention}",
            )
            await self.send_mod_log(ctx.guild, embed)
            await ctx.send(f"✅ {user.mention} is no longer a mod.")
        else:
            await ctx.send("Usage: `I?mod <user> -y|-r` (-y add, -r remove)")

    @commands.command(name="mods")
    @has_admin_or_dev()
    async def mods(self, ctx):
        """List the mods of this server."""
        mods = get_mod_ids(ctx.guild.id)
        if not mods:
            await ctx.send("No mods set. Use `I?mod <user> -y` to add one.")
            return
        await ctx.send("🛡️ Mods: " + " ".join(f"<@{m}>" for m in mods))

    @commands.command(name="modlog")
    @has_admin_or_dev()
    async def modlog(self, ctx):
        """Make the current channel the mod-log channel."""
        set_guild_settings(ctx.guild.id, mod_log_channel_id=ctx.channel.id)
        audit(ctx.guild.id, ctx.author.id, "settings", "guild", ctx.guild.id, f"mod log channel -> #{ctx.channel.name}")
        await ctx.send(f"✅ Mod-log channel set to {ctx.channel.mention}.")

    @commands.command(name="reports")
    @has_admin_or_dev()
    async def reports(self, ctx):
        """Make the current channel the code-reports channel."""
        set_guild_settings(ctx.guild.id, report_log_channel_id=ctx.channel.id)
        audit(ctx.guild.id, ctx.author.id, "settings", "guild", ctx.guild.id, f"reports channel -> #{ctx.channel.name}")
        await ctx.send(f"✅ Code reports will be submitted to {ctx.channel.mention}.")

    @commands.command(name="activitychannel")
    @has_admin_or_dev()
    async def activitychannel(self, ctx):
        """Make the current channel the activity log channel."""
        set_guild_settings(ctx.guild.id, activity_log_channel_id=ctx.channel.id)
        audit(ctx.guild.id, ctx.author.id, "settings", "guild", ctx.guild.id, f"activity channel -> #{ctx.channel.name}")
        await ctx.send(f"✅ Activity log channel set to {ctx.channel.mention}.")

    @commands.command(name="memberchannel")
    @has_admin_or_dev()
    async def memberchannel(self, ctx):
        """Make the current channel the member log channel."""
        set_guild_settings(ctx.guild.id, member_log_channel_id=ctx.channel.id)
        audit(ctx.guild.id, ctx.author.id, "settings", "guild", ctx.guild.id, f"member channel -> #{ctx.channel.name}")
        await ctx.send(f"✅ Member log channel set to {ctx.channel.mention}.")

    @commands.command(name="groupmax")
    @has_admin_or_dev()
    async def groupmax(self, ctx, limit: int = None):
        """Set max groups per member."""
        if limit is None or limit < 1:
            await ctx.send("Usage: `I?groupmax <n>`")
            return
        set_guild_settings(ctx.guild.id, max_groups_per_member=limit)
        audit(ctx.guild.id, ctx.author.id, "settings", "guild", ctx.guild.id, f"max groups -> {limit}")
        await ctx.send(f"✅ Max groups per member set to **{limit}**.")

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
