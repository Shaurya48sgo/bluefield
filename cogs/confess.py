from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from cogs.common import (
    C,
    I,
    M,
    audit,
    generate_code,
    get_guild_settings,
    has_admin_or_dev,
    is_blacklisted,
    set_guild_settings,
)

DEFAULT_MAX_CODES = 5


def _jump_link(guild_id, channel_id, message_id):
    return f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}"


class SecretReplyModal(discord.ui.Modal):
    def __init__(self, cog, guild_id, channel_id, original_code, code, original_message=None):
        super().__init__(title=f"Reply as code {code}")
        self.cog = cog
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.original_code = original_code
        self.code = code
        self.original_message = original_message
        self.text_input = discord.ui.TextInput(
            label="Your reply",
            style=discord.TextStyle.paragraph,
            max_length=2000,
            required=True,
        )
        self.add_item(self.text_input)

    async def on_submit(self, interaction):
        text = self.text_input.value.strip()
        if not text:
            await interaction.response.send_message("Reply cannot be empty.")
            return
        await self.cog.post_reply(
            interaction, self.guild_id, self.channel_id, self.original_code, self.code, text, self.original_message
        )


class ReplyCodeSelectView(discord.ui.View):
    def __init__(self, cog, author, guild_id, channel_id, original_code, docs, original_message=None):
        super().__init__(timeout=300)
        self.cog = cog
        self.author = author
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.original_code = original_code
        self.original_message = original_message
        self.docs = docs
        options = [discord.SelectOption(label=d["code"], value=d["code"]) for d in docs]
        limit = self.cog._max_codes(guild_id)
        if len(docs) < limit:
            options.append(discord.SelectOption(label="Generate new", value="GENERATE_NEW"))
        self.code_select = discord.ui.Select(placeholder="Pick your code", options=options)
        self.code_select.callback = self.on_select
        self.add_item(self.code_select)

    async def interaction_check(self, interaction):
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("Not your reply.")
            return False
        return True

    async def on_select(self, interaction):
        value = self.code_select.values[0]
        if value == "GENERATE_NEW":
            limit = self.cog._max_codes(self.guild_id)
            if len(self.docs) >= limit:
                await interaction.response.send_message(
                    f"You've reached the limit of **{limit}** codes. Delete one first with `/secret code delete <code>`.",
                    ephemeral=True,
                )
                return
            value = self.cog._new_code(self.guild_id, self.author.id)
            self.docs = list(C.find({"guild_id": self.guild_id, "user_id": self.author.id}).sort("created_at", 1))
        await interaction.response.send_modal(
            SecretReplyModal(
                self.cog, self.guild_id, self.channel_id, self.original_code, value, self.original_message
            )
        )


class SecretReplyButton(discord.ui.Button):
    def __init__(self, guild_id, channel_id, code):
        super().__init__(label="Reply", style=discord.ButtonStyle.secondary, custom_id=f"secret_reply:{code}")
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.code = code

    async def callback(self, interaction):
        docs = list(C.find({"guild_id": self.guild_id, "user_id": interaction.user.id}).sort("created_at", 1))
        embed = discord.Embed(
            title="Reply",
            description="Which code do you want to reply as?",
            color=discord.Colour(0x9B59B6),
        )
        await interaction.response.send_message(
            embed=embed,
            view=ReplyCodeSelectView(
                self.view.cog, interaction.user, self.guild_id, self.channel_id, self.code, docs, interaction.message
            ),
            ephemeral=True,
        )


class SecretReplyView(discord.ui.View):
    def __init__(self, cog, guild_id, channel_id, code):
        super().__init__(timeout=None)
        self.cog = cog
        self.add_item(SecretReplyButton(guild_id, channel_id, code))


class InboxView(discord.ui.View):
    def __init__(self, cog, author, guild_id):
        super().__init__(timeout=300)
        self.cog = cog
        self.author = author
        self.guild_id = guild_id

    async def interaction_check(self, interaction):
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("Not your inbox.")
            return False
        return True

    @discord.ui.button(label="Clear inbox", style=discord.ButtonStyle.danger)
    async def clear_inbox_button(self, interaction, button):
        I.delete_many({"user_id": self.author.id})
        embed = discord.Embed(title="Inbox", description="Inbox cleared.", color=discord.Colour(0x9B59B6))
        await interaction.response.edit_message(embed=embed, view=None)

    @discord.ui.button(label="Clear chat", style=discord.ButtonStyle.secondary)
    async def clear_chat_button(self, interaction, button):
        removed = await self.cog.clear_secret_chat(None, self.author.id)
        embed = discord.Embed(
            title="Inbox",
            description=f"Cleared **{removed}** of your secret messages.",
            color=discord.Colour(0x9B59B6),
        )
        await interaction.response.edit_message(embed=embed)


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

    async def code_autocomplete(self, interaction, current):
        gid = interaction.guild.id
        uid = interaction.user.id
        docs = list(C.find({"guild_id": gid, "user_id": uid}).sort("created_at", 1))
        out = []
        for d in docs:
            if current.lower() in d["code"].lower():
                out.append(app_commands.Choice(name=d["code"], value=d["code"]))
        limit = self._max_codes(gid)
        if len(docs) < limit:
            out.append(app_commands.Choice(name="Generate new", value="GENERATE_NEW"))
        return out[:25]

    async def delete_autocomplete(self, interaction, current):
        docs = C.find({"guild_id": interaction.guild.id, "user_id": interaction.user.id})
        out = []
        for d in docs:
            if current.lower() in d["code"].lower():
                out.append(app_commands.Choice(name=d["code"], value=d["code"]))
            if len(out) >= 25:
                break
        return out

    @secret.command(name="say")
    @app_commands.describe(message="The message to post anonymously", code="Pick one of your codes, or 'Generate new'")
    @app_commands.autocomplete(code=code_autocomplete)
    async def say(self, interaction, message: str, code: str):
        """Post anonymously using a code (or generate a new one)."""
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

        code = code.strip().upper()
        if code == "GENERATE_NEW":
            if len(docs) >= limit:
                await interaction.response.send_message(
                    f"You've reached the limit of **{limit}** codes. "
                    "Delete one first with `/secret code delete <code>`."
                )
                return
            code = self._new_code(gid, uid)
            docs = list(C.find({"guild_id": gid, "user_id": uid}).sort("created_at", 1))
        elif not C.find_one({"guild_id": gid, "code": code, "user_id": uid}):
            await interaction.response.send_message(
                "Invalid code. Pick one of your codes or 'Generate new'."
            )
            return

        embed = discord.Embed(
            color=discord.Colour(0x9B59B6),
            title="Secret message",
        )
        embed.add_field(name="Code", value=f"**`{code}`**", inline=False)
        embed.add_field(name="Message", value=message, inline=False)
        view = SecretReplyView(self, gid, channel.id, code)
        try:
            sent = await channel.send(
                embed=embed,
                view=view,
                allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=False),
            )
        except Exception as e:
            await interaction.response.send_message(f"Failed to post: {e}")
            return
        M.insert_one(
            {
                "guild_id": gid,
                "channel_id": channel.id,
                "message_id": sent.id,
                "code": code,
                "owner_id": uid,
                "created_at": datetime.now(timezone.utc),
            }
        )

        confirm = discord.Embed(
            title="Posted anonymously",
            color=discord.Colour(0x9B59B6),
            description=f"Your secret message was posted with code **`{code}`**.\n"
            + ("Your codes: " + ", ".join(f"`{d['code']}`" for d in docs) if docs else "You have no codes.")
            + f" ({len(docs)}/{limit} slots)\nNext time pick a code or 'Generate new' in `/secret say`.",
        )
        await interaction.response.send_message(embed=confirm, ephemeral=True)

    async def post_reply(self, interaction, guild_id, channel_id, original_code, code, text, original_message=None):
        embed = discord.Embed(
            color=discord.Colour(0x9B59B6),
            title="Reply",
        )
        embed.add_field(name="Code", value=f"**`{code}`**", inline=False)
        embed.add_field(name="Message", value=text, inline=False)
        channel = interaction.guild.get_channel(channel_id)
        if channel is None:
            await interaction.response.send_message("That channel no longer exists.")
            return
        view = SecretReplyView(self, guild_id, channel_id, code)
        try:
            kwargs = {
                "embed": embed,
                "view": view,
                "allowed_mentions": discord.AllowedMentions(everyone=False, roles=False, users=False),
            }
            if original_message is not None:
                kwargs["reference"] = original_message
            sent = await channel.send(**kwargs)
        except Exception as e:
            await interaction.response.send_message(f"Failed to post reply: {e}")
            return
        owner = C.find_one({"guild_id": guild_id, "code": original_code})
        if owner:
            I.insert_one(
                {
                    "guild_id": guild_id,
                    "user_id": owner["user_id"],
                    "code": original_code,
                    "channel_id": channel_id,
                    "message_id": sent.id,
                    "text": text,
                    "created_at": datetime.now(timezone.utc),
                }
            )
        audit(guild_id, interaction.user.id, "secret_reply", "code", code)
        await interaction.response.send_message("Reply posted.", ephemeral=True)

    async def clear_secret_chat(self, guild_id, user_id):
        removed = 0
        query = {"owner_id": user_id}
        if guild_id:
            query["guild_id"] = guild_id
        docs = list(M.find(query))
        for d in docs:
            guild = self.bot.get_guild(d["guild_id"])
            if not guild:
                continue
            channel = guild.get_channel(d["channel_id"])
            if not channel:
                continue
            try:
                msg = await channel.fetch_message(d["message_id"])
                await msg.delete()
                removed += 1
            except Exception:
                pass
        M.delete_many(query)
        return removed

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

    @code.command(name="delete")
    @app_commands.describe(code="Pick one of your codes to delete")
    @app_commands.autocomplete(code=delete_autocomplete)
    async def code_delete(self, interaction, code: str):
        """Delete one of your anonymous codes (frees a slot)."""
        code = code.strip().upper()
        if code == "GENERATE_NEW":
            await interaction.response.send_message("That's not one of your codes.")
            return
        res = C.delete_one({"guild_id": interaction.guild.id, "code": code, "user_id": interaction.user.id})
        if res.deleted_count == 0:
            await interaction.response.send_message("That's not one of your codes.")
            return
        audit(interaction.guild.id, interaction.user.id, "code_delete", "code", code)
        await interaction.response.send_message(f"🗑️ Deleted code **`{code}`**.")

    # ---------- slash: /inbox ----------

    @app_commands.command(name="inbox")
    async def inbox(self, interaction):
        """See where your codes were mentioned. Only works in DMs with the bot."""
        if interaction.guild is not None:
            await interaction.response.send_message(
                "`/inbox` only works in DMs with the bot — open a DM and run it there.",
                ephemeral=True,
            )
            return
        entries = list(I.find({"user_id": interaction.user.id}).sort("created_at", -1).limit(15))
        embed = discord.Embed(
            title="Inbox",
            color=discord.Colour(0x9B59B6),
            description=f"You have **{len(entries)}** mention(s) on your codes." if entries else "No mentions yet.",
        )
        if entries:
            for e in entries:
                link = _jump_link(e["guild_id"], e["channel_id"], e["message_id"])
                embed.add_field(
                    name=f"Code `{e['code']}`",
                    value=f"{e['text'][:200]}\n[Jump to message]({link})",
                    inline=False,
                )
        await interaction.response.send_message(embed=embed, view=InboxView(self, interaction.user, None))

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
