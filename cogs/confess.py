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
    is_owner,
    set_guild_settings,
)
from cogs.layouts import (
    REPLY_LAYOUTS,
    SECRET_LAYOUTS,
    SECRET_LAYOUTS_V2,
    build_reply,
    build_secret,
    build_secret_v2,
    random_nickname,
)

DEFAULT_MAX_CODES = 5


def _jump_link(guild_id, channel_id, message_id):
    return f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}"


def _next_post(guild_id):
    settings = get_guild_settings(guild_id)
    n = settings.get("secret_post_counter", 0) + 1
    set_guild_settings(guild_id, secret_post_counter=n)
    return n


def _layout_index(guild_id, key, default):
    idx = get_guild_settings(guild_id).get(key, default)
    try:
        return int(idx)
    except Exception:
        return default


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


class SecretRenameModal(discord.ui.Modal):
    def __init__(self, cog, guild_id, code, current_nick):
        super().__init__(title="Change nickname")
        self.cog = cog
        self.guild_id = guild_id
        self.code = code
        self.nick_input = discord.ui.TextInput(
            label="New nickname",
            max_length=32,
            required=True,
            default=current_nick or "",
        )
        self.add_item(self.nick_input)

    async def on_submit(self, interaction):
        nick = self.nick_input.value.strip()
        if not nick:
            await interaction.response.send_message("Nickname cannot be empty.")
            return
        res = C.update_one(
            {"guild_id": self.guild_id, "code": self.code, "user_id": interaction.user.id},
            {"$set": {"nickname": nick}},
        )
        if res.matched_count == 0:
            await interaction.response.send_message("That's not your code.")
            return
        audit(self.guild_id, interaction.user.id, "code_rename", "code", self.code)
        await interaction.response.send_message(f"✅ Nickname changed to **{nick}**.", ephemeral=True)


class SecretRenameButton(discord.ui.Button):
    def __init__(self, guild_id, code):
        super().__init__(label="✏️ Rename", style=discord.ButtonStyle.secondary, custom_id=f"secret_rename:{code}")
        self.guild_id = guild_id
        self.code = code

    async def callback(self, interaction):
        doc = C.find_one({"guild_id": self.guild_id, "code": self.code, "user_id": interaction.user.id})
        if not doc:
            await interaction.response.send_message("You can only rename your own codes.", ephemeral=True)
            return
        await interaction.response.send_modal(
            SecretRenameModal(self.view.cog, self.guild_id, self.code, doc.get("nickname"))
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
        self.add_item(SecretRenameButton(guild_id, code))


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

    @commands.command(name="layout")
    async def layout(self, ctx):
        """Preview all 20 secret message layouts (owner only)."""
        if not is_owner(ctx.author.id):
            await ctx.send("Only the bot owner can view layouts.")
            return
        code = "X7KQ9FD2"
        msg = "Sample secret message to preview this layout."
        lines = []
        for i, layout in enumerate(SECRET_LAYOUTS):
            embed = build_secret(i, code, "SampleNick", msg, 42)
            embed.title = f"Secret Layout {i + 1} · {layout['name']}"
            await ctx.send(embed=embed)
            lines.append(f"{i + 1}. {layout['name']}")
        await ctx.send("Reply with `I?layoutset <n>` to choose a secret layout.")

    @commands.command(name="layoutv2")
    async def layoutv2(self, ctx):
        """Preview all V2 secret message layouts (owner only)."""
        if not is_owner(ctx.author.id):
            await ctx.send("Only the bot owner can view layouts.")
            return
        code = "X7KQ9FD2"
        msg = "Sample secret message to preview this layout."
        for i, layout in enumerate(SECRET_LAYOUTS_V2):
            embed = build_secret_v2(i, code, "SampleNick", msg, 42)
            embed.title = f"V2 Layout {i + 1} · {layout['name']}"
            await ctx.send(embed=embed)
        await ctx.send("Reply with `I?layoutv2set <n>` to choose a V2 secret layout.")

    @commands.command(name="layoutv2set")
    async def layoutv2set(self, ctx, num: int = None):
        """Choose a V2 secret message layout (owner only)."""
        if not is_owner(ctx.author.id):
            await ctx.send("Only the bot owner can set layouts.")
            return
        if num is None or num < 1 or num > len(SECRET_LAYOUTS_V2):
            await ctx.send(f"Pick a number 1-{len(SECRET_LAYOUTS_V2)}.")
            return
        set_guild_settings(ctx.guild.id, secret_layout_v2=num - 1, secret_layout_v2_enabled=True)
        audit(ctx.guild.id, ctx.author.id, "settings", "guild", ctx.guild.id, f"secret layout v2 -> {num}")
        await ctx.send(f"✅ V2 Secret layout set to **{num} · {SECRET_LAYOUTS_V2[num - 1]['name']}**.")

    @commands.command(name="layoutr")
    async def layoutr(self, ctx):
        """Preview all 20 reply layouts (owner only)."""
        if not is_owner(ctx.author.id):
            await ctx.send("Only the bot owner can view reply layouts.")
            return
        r = "REPLYCODE"
        t = "ORIGCODE"
        text = "Sample reply text to preview this layout."
        lines = []
        for i, layout in enumerate(REPLY_LAYOUTS):
            embed = build_reply(i, r, t, 42, text)
            embed.title = f"Reply Layout {i + 1} · {layout['name']}"
            await ctx.send(embed=embed)
            lines.append(f"{i + 1}. {layout['name']}")
        await ctx.send("Reply with `I?layoutrset <n>` to choose a reply layout.")

    @commands.command(name="layoutset")
    async def layoutset(self, ctx, num: int = None):
        """Choose a secret message layout (owner only)."""
        if not is_owner(ctx.author.id):
            await ctx.send("Only the bot owner can set layouts.")
            return
        if num is None or num < 1 or num > len(SECRET_LAYOUTS):
            await ctx.send(f"Pick a number 1-{len(SECRET_LAYOUTS)}.")
            return
        set_guild_settings(ctx.guild.id, secret_layout=num - 1)
        audit(ctx.guild.id, ctx.author.id, "settings", "guild", ctx.guild.id, f"secret layout -> {num}")
        await ctx.send(f"✅ Secret layout set to **{num} · {SECRET_LAYOUTS[num - 1]['name']}**.")

    @commands.command(name="layoutrset")
    async def layoutrset(self, ctx, num: int = None):
        """Choose a reply layout (owner only)."""
        if not is_owner(ctx.author.id):
            await ctx.send("Only the bot owner can set layouts.")
            return
        if num is None or num < 1 or num > len(REPLY_LAYOUTS):
            await ctx.send(f"Pick a number 1-{len(REPLY_LAYOUTS)}.")
            return
        set_guild_settings(ctx.guild.id, reply_layout=num - 1)
        audit(ctx.guild.id, ctx.author.id, "settings", "guild", ctx.guild.id, f"reply layout -> {num}")
        await ctx.send(f"✅ Reply layout set to **{num} · {REPLY_LAYOUTS[num - 1]['name']}**.")

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

        post_number = _next_post(gid)
        code_doc = C.find_one({"guild_id": gid, "code": code, "user_id": uid})
        nickname = code_doc.get("nickname") if code_doc else None
        settings = get_guild_settings(gid)
        if settings.get("secret_layout_v2_enabled"):
            layout_idx = _layout_index(gid, "secret_layout_v2", 0)
            embed = build_secret_v2(layout_idx, code, nickname, message, post_number)
        else:
            layout_idx = _layout_index(gid, "secret_layout", 2)
            embed = build_secret(layout_idx, code, nickname, message, post_number)
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
                "post_number": post_number,
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
        target_post = None
        if original_message is not None:
            orig = M.find_one({"guild_id": guild_id, "message_id": original_message.id})
            if orig:
                target_post = orig.get("post_number")
        reply_doc = C.find_one({"guild_id": guild_id, "code": code})
        target_doc = C.find_one({"guild_id": guild_id, "code": original_code})
        reply_nick = reply_doc.get("nickname") if reply_doc else None
        target_nick = target_doc.get("nickname") if target_doc else None
        layout_idx = _layout_index(guild_id, "reply_layout", 0)
        embed = build_reply(layout_idx, code, reply_nick, original_code, target_nick, target_post, text)
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
        nickname = random_nickname()
        C.insert_one(
            {
                "guild_id": guild_id,
                "user_id": user_id,
                "code": code,
                "nickname": nickname,
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

    @code.command(name="nickname")
    @app_commands.describe(code="Pick one of your codes", name="Your new nickname")
    @app_commands.autocomplete(code=delete_autocomplete)
    async def code_nickname(self, interaction, code: str, name: str):
        """Change a code's nickname."""
        code = code.strip().upper()
        name = name.strip()
        if not name:
            await interaction.response.send_message("Nickname cannot be empty.")
            return
        if len(name) > 32:
            await interaction.response.send_message("Nickname must be 32 characters or less.")
            return
        res = C.update_one(
            {"guild_id": interaction.guild.id, "code": code, "user_id": interaction.user.id},
            {"$set": {"nickname": name}},
        )
        if res.matched_count == 0:
            await interaction.response.send_message("That's not one of your codes.")
            return
        audit(interaction.guild.id, interaction.user.id, "code_rename", "code", code)
        await interaction.response.send_message(f"✅ Code **`{code}`** nickname changed to **{name}**.")

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
