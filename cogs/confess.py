from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from cogs.common import (
    C,
    audit,
    generate_code,
    get_guild_settings,
    has_admin_or_dev,
    is_blacklisted,
    set_guild_settings,
)

DEFAULT_MAX_CODES = 5


class ConfessCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def _max_codes(self, guild_id):
        return get_guild_settings(guild_id).get("confess_max_codes", DEFAULT_MAX_CODES)

    def _channel(self, guild):
        cid = get_guild_settings(guild.id).get("confess_channel_id")
        return guild.get_channel(cid) if cid else None

    # ---------- prefix: per-channel setup ----------

    @commands.command(name="confesschannel")
    @has_admin_or_dev()
    async def confesschannel(self, ctx):
        """Make the current channel the anonymous chat channel."""
        set_guild_settings(ctx.guild.id, confess_channel_id=ctx.channel.id)
        audit(ctx.guild.id, ctx.author.id, "settings", "guild", ctx.guild.id, f"confess channel -> #{ctx.channel.name}")
        await ctx.send(f"✅ Anonymous chat channel set to {ctx.channel.mention}.")

    @commands.command(name="confessmax")
    @has_admin_or_dev()
    async def confessmax(self, ctx, limit: int = None):
        """Set max codes per member."""
        if limit is None or limit < 1:
            await ctx.send("Usage: `I?confessmax <n>`")
            return
        set_guild_settings(ctx.guild.id, confess_max_codes=limit)
        audit(ctx.guild.id, ctx.author.id, "settings", "guild", ctx.guild.id, f"confess max codes -> {limit}")
        await ctx.send(f"✅ Max codes per member set to **{limit}**.")

    # ---------- slash: /secret say ----------

    secret = app_commands.Group(name="secret", description="Anonymous secret chat")

    @secret.command(name="say")
    @app_commands.describe(message="The message to post anonymously", code="Your code (optional if you have none yet)")
    async def say(self, interaction, message: str, code: str = None):
        """Post anonymously. Your first code is auto-created; codes are listed for you."""
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
        if not message.strip():
            await interaction.response.send_message("Message cannot be empty.")
            return

        uid = interaction.user.id
        gid = interaction.guild.id
        limit = self._max_codes(gid)
        docs = list(C.find({"guild_id": gid, "user_id": uid}).sort("created_at", 1))

        if code:
            code = code.strip().upper()
            if not C.find_one({"guild_id": gid, "code": code, "user_id": uid}):
                await interaction.response.send_message(
                    "Invalid code. Use `/secret code list` to see yours, or leave code blank."
                )
                return
        elif docs:
            code = docs[-1]["code"]
        else:
            code = self._new_code(gid, uid)
            docs = list(C.find({"guild_id": gid, "user_id": uid}).sort("created_at", 1))

        embed = discord.Embed(
            color=discord.Colour(0x9B59B6),
            description=f"```\n{code}\n```\n{message}",
        )
        try:
            await channel.send(
                embed=embed,
                allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=False),
            )
        except Exception as e:
            await interaction.response.send_message(f"Failed to post: {e}")
            return

        confirm = discord.Embed(
            title="🕶️ Posted anonymously",
            color=discord.Colour(0x9B59B6),
            description=f"Your secret message was posted with code **`{code}`**.\n"
            + ("Your codes: " + ", ".join(f"`{d['code']}`" for d in docs) if docs else "You have no codes.")
            + f" ({len(docs)}/{limit} slots)\nNext time use `/secret say code:<code> message:...` to pick another.",
        )
        await interaction.response.send_message(embed=confirm, ephemeral=True)

    def _new_code(self, guild_id, user_id):
        limit = self._max_codes(guild_id)
        count = C.count_documents({"guild_id": guild_id, "user_id": user_id})
        if count >= limit:
            raise ValueError(f"limit {limit} reached")
        code = generate_code()
        while C.find_one({"guild_id": guild_id, "code": code}):
            code = generate_code()
        C.insert_one(
            {
                "guild_id": guild_id,
                "user_id": user_id,
                "code": code,
                "created_at": datetime.now(timezone.utc),
            }
        )
        audit(guild_id, user_id, "code_new", "code", code)
        return code

    # ---------- slash: /secret code ----------

    code = app_commands.Group(name="code", description="Manage your anonymous codes")
    secret.add_command(code)

    @code.command(name="new")
    async def code_new(self, interaction):
        """Generate a new code (until your limit is used up)."""
        if is_blacklisted(interaction.guild.id, interaction.user.id, interaction.user):
            await interaction.response.send_message("You are blacklisted from anonymous chat.")
            return
        gid = interaction.guild.id
        uid = interaction.user.id
        limit = self._max_codes(gid)
        count = C.count_documents({"guild_id": gid, "user_id": uid})
        if count >= limit:
            docs = list(C.find({"guild_id": gid, "user_id": uid}).sort("created_at", 1))
            await interaction.response.send_message(
                f"You've reached the limit of **{limit}** codes.\nYour codes: "
                + (", ".join(f"`{d['code']}`" for d in docs) if docs else "none")
                + "\nUse `/secret code delete <code>` to free a slot.",
                ephemeral=True,
            )
            return
        code = self._new_code(gid, uid)
        docs = list(C.find({"guild_id": gid, "user_id": uid}).sort("created_at", 1))
        await interaction.response.send_message(
            f"🕶️ New code: **`{code}`** ({count + 1}/{limit})\nYour codes: "
            + ", ".join(f"`{d['code']}`" for d in docs)
            + "\nUse `/secret say` to talk.",
            ephemeral=True,
        )

    @code.command(name="list")
    async def code_list(self, interaction):
        """List all your anonymous codes."""
        docs = list(C.find({"guild_id": interaction.guild.id, "user_id": interaction.user.id}).sort("created_at", 1))
        limit = self._max_codes(interaction.guild.id)
        if not docs:
            await interaction.response.send_message(
                f"You have no codes yet. Use `/secret code new` to create one. ({0}/{limit} slots used)",
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
    @has_admin_or_dev()
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
    @has_admin_or_dev()
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
