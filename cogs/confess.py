import asyncio
import re
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from cogs.common import (
    C,
    audit,
    generate_code,
    get_guild_settings,
    is_blacklisted,
    set_guild_settings,
)

DEFAULT_MAX_CODES = 5


class ConfessSetupView(discord.ui.View):
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

    @discord.ui.button(label="Anonymous channel", style=discord.ButtonStyle.primary)
    async def channel_button(self, interaction, button):
        value = await self._wait_input(
            interaction, "Mention the **anonymous chat/confession channel**, or type `skip`:"
        )
        if value is None:
            return
        m = re.match(r"<#(\d+)>|(\d+)", value)
        if not m:
            await interaction.followup.send("No valid channel — leaving it unchanged.")
            return
        channel = interaction.guild.get_channel(int(m.group(1) or m.group(2)))
        if channel is None:
            await interaction.followup.send("No valid channel — leaving it unchanged.")
            return
        set_guild_settings(self.guild_id, confess_channel_id=channel.id)
        audit(self.guild_id, interaction.user.id, "settings", "guild", self.guild_id, f"confess channel -> #{channel.name}")
        await interaction.followup.send(f"✅ Anonymous channel set to {channel.mention}.")

    @discord.ui.button(label="Max codes per member", style=discord.ButtonStyle.secondary)
    async def limit_button(self, interaction, button):
        value = await self._wait_input(
            interaction, f"Type the **max codes per member** (default {DEFAULT_MAX_CODES}), or type `skip`:"
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
        set_guild_settings(self.guild_id, confess_max_codes=limit)
        audit(self.guild_id, interaction.user.id, "settings", "guild", self.guild_id, f"confess max codes -> {limit}")
        await interaction.followup.send(f"✅ Max codes per member set to **{limit}**.")

    @discord.ui.button(label="Done", style=discord.ButtonStyle.success)
    async def done_button(self, interaction, button):
        settings = get_guild_settings(self.guild_id)
        embed = discord.Embed(title="🕶️ Confession setup", color=discord.Color.blue())
        embed.add_field(name="Anonymous channel", value=f"<#{settings.get('confess_channel_id')}>" if settings.get("confess_channel_id") else "Not set")
        embed.add_field(name="Max codes per member", value=str(settings.get("confess_max_codes", DEFAULT_MAX_CODES)))
        await interaction.response.send_message(embed=embed)


class ConfessCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def _max_codes(self, guild_id):
        return get_guild_settings(guild_id).get("confess_max_codes", DEFAULT_MAX_CODES)

    def _channel(self, guild):
        cid = get_guild_settings(guild.id).get("confess_channel_id")
        return guild.get_channel(cid) if cid else None

    # ---------- prefix: setup ----------

    @commands.command(name="confessionsetup_all")
    @commands.has_permissions(administrator=True)
    async def confessionsetup_all(self, ctx):
        """Set up the anonymous chat channel and code limit."""
        await ctx.send(
            "🕶️ **Confession setup** — use the buttons below:",
            view=ConfessSetupView(self, ctx.author, ctx.guild.id),
        )

    # ---------- slash: /say ----------

    @app_commands.command(name="say")
    @app_commands.describe(code="Your anonymous code", message="The message to post anonymously")
    async def say(self, interaction, code: str, message: str):
        """Post an anonymous message in the confession channel using your code."""
        channel = self._channel(interaction.guild)
        if channel is None:
            await interaction.response.send_message("Anonymous chat isn't enabled in this server.")
            return
        if interaction.channel_id != channel.id:
            await interaction.response.send_message(f"Anonymous messages only work in {channel.mention}.")
            return
        if is_blacklisted(interaction.guild.id, interaction.user.id, interaction.user):
            await interaction.response.send_message("You are blacklisted from anonymous chat.")
            return
        code = code.strip().upper()
        doc = C.find_one({"guild_id": interaction.guild.id, "code": code, "user_id": interaction.user.id})
        if not doc:
            await interaction.response.send_message(
                "Invalid code. Use `/code new` to create one, or `/code list` to see yours."
            )
            return
        if not message.strip():
            await interaction.response.send_message("Message cannot be empty.")
            return
        await interaction.response.send_message(
            f"💬 **Code {code}**: {message}",
            allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=False),
        )

    # ---------- slash: /code ----------

    code = app_commands.Group(name="code", description="Manage your anonymous codes")

    @code.command(name="new")
    async def code_new(self, interaction):
        """Create a new anonymous code (max 5)."""
        if is_blacklisted(interaction.guild.id, interaction.user.id, interaction.user):
            await interaction.response.send_message("You are blacklisted from anonymous chat.")
            return
        count = C.count_documents({"guild_id": interaction.guild.id, "user_id": interaction.user.id})
        limit = self._max_codes(interaction.guild.id)
        if count >= limit:
            await interaction.response.send_message(
                f"You've reached the limit of **{limit}** codes. "
                "Use `/code list` then `/code delete <code>` to free a slot.",
                ephemeral=True,
            )
            return
        code = generate_code()
        while C.find_one({"guild_id": interaction.guild.id, "code": code}):
            code = generate_code()
        C.insert_one(
            {
                "guild_id": interaction.guild.id,
                "user_id": interaction.user.id,
                "code": code,
                "created_at": datetime.now(timezone.utc),
            }
        )
        audit(interaction.guild.id, interaction.user.id, "code_new", "code", code)
        await interaction.response.send_message(
            f"🕶️ Your new anonymous code: **`{code}`**\nUse `/say code:{code} message:...` to talk. "
            f"({count + 1}/{limit} slots used)",
            ephemeral=True,
        )

    @code.command(name="list")
    async def code_list(self, interaction):
        """List your anonymous codes."""
        docs = list(C.find({"guild_id": interaction.guild.id, "user_id": interaction.user.id}).sort("created_at", 1))
        limit = self._max_codes(interaction.guild.id)
        if not docs:
            await interaction.response.send_message(
                f"You have no codes yet. Use `/code new` to create one. ({0}/{limit} slots used)",
                ephemeral=True,
            )
            return
        lines = [f"`{d['code']}`" for d in docs]
        await interaction.response.send_message(
            f"🕶️ Your codes:\n" + "\n".join(lines) + f"\n({len(docs)}/{limit} slots used)",
            ephemeral=True,
        )

    @code.command(name="delete")
    @app_commands.describe(code="The code to delete")
    async def code_delete(self, interaction, code: str):
        """Delete one of your anonymous codes (frees a slot)."""
        code = code.strip().upper()
        res = C.delete_one({"guild_id": interaction.guild.id, "code": code, "user_id": interaction.user.id})
        if res.deleted_count == 0:
            await interaction.response.send_message("That's not one of your codes.")
            return
        audit(interaction.guild.id, interaction.user.id, "code_delete", "code", code)
        await interaction.response.send_message(f"🗑️ Deleted code **`{code}`**.")

    # ---------- admin: view/delete any code ----------

    @commands.command(name="confesscodes")
    @commands.has_permissions(administrator=True)
    async def confesscodes(self, ctx, user: discord.Member = None):
        """List a member's anonymous codes (admin)."""
        query = {"guild_id": ctx.guild.id}
        if user:
            query["user_id"] = user.id
        docs = list(C.find(query).sort("created_at", 1))
        if not docs:
            await ctx.send("No codes found.")
            return
        lines = [f"`{d['code']}` -> <@{d['user_id']}>" for d in docs]
        embed = discord.Embed(title="🕶️ Anonymous codes", color=discord.Color.blue())
        embed.description = "\n".join(lines)
        await ctx.send(embed=embed)

    @commands.command(name="confessdelete")
    @commands.has_permissions(administrator=True)
    async def confessdelete(self, ctx, code: str):
        """Delete any anonymous code (admin)."""
        code = code.strip().upper()
        res = C.delete_one({"guild_id": ctx.guild.id, "code": code})
        if res.deleted_count == 0:
            await ctx.send("No such code.")
            return
        audit(ctx.guild.id, ctx.author.id, "code_delete", "code", code)
        await ctx.send(f"🗑️ Deleted code **`{code}`**.")


async def setup(bot):
    await bot.add_cog(ConfessCog(bot))
