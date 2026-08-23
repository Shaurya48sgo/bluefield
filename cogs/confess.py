from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands

from cogs.common import (
    C,
    G,
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
    SECRET_COLORS,
    build_reply,
    build_secret,
    random_nickname,
)

COLOR_EMOJIS = {
    "Purple": "🟣",
    "Blue": "🔵",
    "Green": "🟢",
    "Yellow": "🟡",
    "Red": "🔴",
    "Pink": "🩷",
    "Orange": "🟠",
    "Cyan": "🩵",
}

DEFAULT_MAX_CODES = 5


def _jump_link(guild_id, channel_id, message_id):
    return f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}"


def _next_post(guild_id):
    settings = get_guild_settings(guild_id)
    n = settings.get("secret_post_counter", 0) + 1
    set_guild_settings(guild_id, secret_post_counter=n)
    return n


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
        options = [
            discord.SelectOption(label=f"{d.get('nickname', '?')} - {d['code']}", value=d["code"]) for d in docs
        ]
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
            code = self.cog._new_code(self.guild_id, self.author.id)
            code_doc = C.find_one({"code": code})
            embed = discord.Embed(
                title="Pick a color",
                color=discord.Colour(0x9B59B6),
                description="Select the color for this code's messages (a color is pre-selected), then press **Confirm**.",
            )
            await interaction.response.send_message(
                embed=embed,
                view=ReplyColorPickView(
                    self.cog, interaction, self.guild_id, self.channel_id, self.original_code, code, code_doc.get("nickname"), self.original_message
                ),
                ephemeral=True,
            )
            return
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
        docs = list(C.find({"user_id": interaction.user.id}).sort("created_at", 1))
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


class NewSecretModal(discord.ui.Modal):
    def __init__(self, cog, interaction, guild_id, code, message, default_nick):
        super().__init__(title="Your new code")
        self.cog = cog
        self.interaction = interaction
        self.guild_id = guild_id
        self.code = code
        self.message = message
        example = random_nickname()
        self.nick_input = discord.ui.TextInput(
            label="Nickname (e.g. " + example + ")",
            max_length=32,
            required=True,
            default=default_nick or example,
        )
        self.add_item(self.nick_input)

    async def on_submit(self, interaction):
        nick = self.nick_input.value.strip()
        if not nick:
            await interaction.response.send_message("Nickname cannot be empty.", ephemeral=True)
            return
        C.update_one({"code": self.code, "user_id": interaction.user.id}, {"$set": {"nickname": nick}})
        embed = discord.Embed(
            title="Pick a color",
            color=discord.Colour(0x9B59B6),
            description="Select the color for this code's messages (a color is pre-selected).",
        )
        await interaction.response.send_message(
            embed=embed, view=ColorPickView(self.cog, interaction, self.guild_id, self.code, self.message),
            ephemeral=True,
        )


class ColorPickView(discord.ui.View):
    def __init__(self, cog, interaction, guild_id, code, message):
        super().__init__(timeout=300)
        self.cog = cog
        self.interaction = interaction
        self.guild_id = guild_id
        self.code = code
        self.message = message
        options = []
        first = True
        for name, value in SECRET_COLORS.items():
            options.append(
                discord.SelectOption(
                    label=name,
                    value=str(value),
                    emoji=COLOR_EMOJIS.get(name),
                    default=first,
                    description=f"#{value:06X}",
                )
            )
            first = False
        self.color_select = discord.ui.Select(placeholder="Color", options=options)
        self.color_select.callback = self.on_color
        self.add_item(self.color_select)

    async def on_color(self, interaction):
        color_value = int(self.color_select.values[0])
        C.update_one({"code": self.code, "user_id": interaction.user.id}, {"$set": {"color": color_value}})
        await self.cog._post_secret(interaction, self.guild_id, self.code, self.message, color_value)


class ReplyColorPickView(discord.ui.View):
    def __init__(self, cog, interaction, guild_id, channel_id, original_code, code, default_nick, original_message):
        super().__init__(timeout=300)
        self.cog = cog
        self.interaction = interaction
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.original_code = original_code
        self.code = code
        self.default_nick = default_nick
        self.original_message = original_message
        options = []
        first = True
        for name, value in SECRET_COLORS.items():
            options.append(
                discord.SelectOption(
                    label=name,
                    value=str(value),
                    emoji=COLOR_EMOJIS.get(name),
                    default=first,
                    description=f"#{value:06X}",
                )
            )
            first = False
        self.color_select = discord.ui.Select(placeholder="Color", options=options)
        self.color_select.callback = self.on_color
        self.add_item(self.color_select)
        self.confirm = discord.ui.Button(label="Confirm", style=discord.ButtonStyle.success)
        self.confirm.callback = self.on_confirm
        self.add_item(self.confirm)

    async def on_color(self, interaction):
        await interaction.response.defer()

    async def on_confirm(self, interaction):
        color_value = int(self.color_select.values[0])
        C.update_one({"code": self.code, "user_id": interaction.user.id}, {"$set": {"color": color_value}})
        await interaction.response.send_modal(
            ReplyComposeModal(
                self.cog, self.guild_id, self.channel_id, self.original_code, self.code, self.default_nick, self.original_message
            )
        )


class ReplyComposeModal(discord.ui.Modal):
    def __init__(self, cog, guild_id, channel_id, original_code, code, default_nick, original_message):
        super().__init__(title=f"Reply as code {code}")
        self.cog = cog
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.original_code = original_code
        self.code = code
        self.original_message = original_message
        example = random_nickname()
        self.nick_input = discord.ui.TextInput(
            label="Nickname (e.g. " + example + ")",
            max_length=32,
            required=True,
            default=default_nick or example,
        )
        self.text_input = discord.ui.TextInput(
            label="Your reply",
            style=discord.TextStyle.paragraph,
            max_length=2000,
            required=True,
        )
        self.add_item(self.nick_input)
        self.add_item(self.text_input)

    async def on_submit(self, interaction):
        nick = self.nick_input.value.strip()
        text = self.text_input.value.strip()
        if not nick:
            await interaction.response.send_message("Nickname cannot be empty.", ephemeral=True)
            return
        if not text:
            await interaction.response.send_message("Reply cannot be empty.", ephemeral=True)
            return
        C.update_one({"code": self.code, "user_id": interaction.user.id}, {"$set": {"nickname": nick}})
        await self.cog.post_reply(
            interaction, self.guild_id, self.channel_id, self.original_code, self.code, text, self.original_message
        )


class HacksSearchModal(discord.ui.Modal):
    def __init__(self, cog):
        super().__init__(title="Hacks search")
        self.cog = cog
        self.query_input = discord.ui.TextInput(
            label="Search", placeholder="User ID, code, or nickname", max_length=100, required=True
        )
        self.add_item(self.query_input)

    async def on_submit(self, interaction):
        query = self.query_input.value.strip()
        docs = self.cog._hacks_search(query)
        if not docs:
            await interaction.response.send_message(f"No codes found for `{query}`.", ephemeral=True)
            return
        embed = self.cog._hacks_results_embed(query, docs)
        await interaction.response.send_message(embed=embed, view=HacksSearchView(self.cog, interaction.user))


class HacksSearchView(discord.ui.View):
    def __init__(self, cog, author):
        super().__init__(timeout=300)
        self.cog = cog
        self.author = author

    async def interaction_check(self, interaction):
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("Not your search.")
            return False
        return True

    @discord.ui.button(label="🔍 Search", style=discord.ButtonStyle.primary)
    async def search_button(self, interaction, button):
        await interaction.response.send_modal(HacksSearchModal(self.cog))


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
        """Preview the unified secret & reply layout (owner only)."""
        if not is_owner(ctx.author.id):
            await ctx.send("Only the bot owner can view layouts.")
            return
        await ctx.send(
            "This is the **unified** layout used for every secret message:",
            embed=build_secret("K7QX9FD2", "ShadowFox", "Sample secret message.", 42),
        )
        await ctx.send(
            "This is the **unified** reply layout:",
            embed=build_reply(
                "REPLYCODE", "SwiftWolf", "ORIGCODE", "NightOwl", 8, 7, "Sample reply text.",
                link="https://discord.com/channels/1/2/3",
            ),
        )

    # ---------- slash: /secret say ----------

    secret = app_commands.Group(name="secret", description="Anonymous secret chat")

    def _code_label(self, doc):
        label = f"{doc.get('nickname', '?')} - {doc['code']}"
        if self._is_suspended(doc):
            label = f"⛔ {label} (suspended)"
        return label

    async def code_autocomplete(self, interaction, current):
        gid = interaction.guild.id
        uid = interaction.user.id
        docs = list(C.find({"user_id": uid}).sort("created_at", 1))
        out = []
        for d in docs:
            label = self._code_label(d)
            if current.lower() in label.lower() or current.lower() in d["code"].lower():
                out.append(app_commands.Choice(name=label, value=d["code"]))
        limit = self._max_codes(gid)
        if len(docs) < limit:
            out.append(app_commands.Choice(name="Generate new", value="GENERATE_NEW"))
        return out[:25]

    async def delete_autocomplete(self, interaction, current):
        docs = C.find({"user_id": interaction.user.id})
        out = []
        for d in docs:
            label = self._code_label(d)
            if current.lower() in label.lower() or current.lower() in d["code"].lower():
                out.append(app_commands.Choice(name=label, value=d["code"]))
            if len(out) >= 25:
                break
        return out

    async def nick_autocomplete(self, interaction, current):
        docs = C.find({"user_id": interaction.user.id}).sort("created_at", 1)
        out = []
        for d in docs:
            label = self._code_label(d)
            if current.lower() in label.lower():
                out.append(app_commands.Choice(name=label, value=d["code"]))
            if len(out) >= 25:
                break
        return out

    @secret.command(name="say")
    @app_commands.describe(message="The message to post anonymously", code="Pick one of your codes, or 'Generate new'")
    @app_commands.autocomplete(code=code_autocomplete)
    async def say(self, interaction, message: str, code: str):
        """Post anonymously using a code (or generate a new one)."""
        async def fail(text):
            embed = discord.Embed(color=discord.Colour(0xED4245), description=text)
            await interaction.response.send_message(embed=embed, ephemeral=True)

        channel = self._channel(interaction.guild)
        if channel is None:
            await fail("Anonymous chat isn't enabled in this server.")
            return
        if interaction.channel_id != channel.id:
            await fail(f"Anonymous messages only work in {channel.mention}.")
            return
        if is_blacklisted(interaction.guild.id, interaction.user.id, interaction.user):
            await fail("You are blacklisted from anonymous chat.")
            return
        if not message.strip():
            await fail("Message cannot be empty.")
            return

        uid = interaction.user.id
        gid = interaction.guild.id
        limit = self._max_codes(gid)
        docs = list(C.find({"user_id": uid}).sort("created_at", 1))

        code = code.strip().upper()
        if code == "GENERATE_NEW":
            if len(docs) >= limit:
                await fail(
                    f"You've reached the limit of **{limit}** codes. "
                    "Delete one first with `/secret code delete <code>`."
                )
                return
            code = self._new_code(gid, uid)
            docs = list(C.find({"user_id": uid}).sort("created_at", 1))
            code_doc = C.find_one({"code": code, "user_id": uid})
            modal = NewSecretModal(self, interaction, gid, code, message, code_doc.get("nickname"))
            await interaction.response.send_modal(modal)
            return
        else:
            code_doc = C.find_one({"code": code, "user_id": uid})
            if not code_doc:
                await fail("Invalid code. Pick one of your codes or 'Generate new'.")
                return
            if self._is_suspended(code_doc):
                until = code_doc.get("suspended_until")
                await fail(
                    f"⛔ This code is **suspended** until {until.strftime('%Y-%m-%d %H:%M UTC')}."
                )
                return
            await self._post_secret(interaction, gid, code, message, color=code_doc.get("color"))

    async def post_reply(self, interaction, guild_id, channel_id, original_code, code, text, original_message=None):
        target_post = None
        if original_message is not None:
            orig = M.find_one({"guild_id": guild_id, "message_id": original_message.id})
            if orig:
                target_post = orig.get("post_number")
        reply_doc = C.find_one({"code": code})
        target_doc = C.find_one({"code": original_code})
        reply_nick = reply_doc.get("nickname") if reply_doc else None
        target_nick = target_doc.get("nickname") if target_doc else None
        reply_color = reply_doc.get("color") if reply_doc else None
        link = None
        if original_message is not None:
            link = _jump_link(guild_id, channel_id, original_message.id)
        reply_post = _next_post(guild_id)
        embed = build_reply(
            code, reply_nick, original_code, target_nick, reply_post, target_post, text, link=link, color=reply_color
        )
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
        M.insert_one(
            {
                "guild_id": guild_id,
                "channel_id": channel_id,
                "message_id": sent.id,
                "code": code,
                "owner_id": interaction.user.id,
                "post_number": reply_post,
                "created_at": datetime.now(timezone.utc),
            }
        )
        owner = C.find_one({"code": original_code})
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

    async def _post_secret(self, interaction, guild_id, code, message, color=None):
        channel = self._channel(interaction.guild)
        if channel is None:
            await interaction.response.send_message("Anonymous chat isn't enabled in this server.", ephemeral=True)
            return
        uid = interaction.user.id
        post_number = _next_post(guild_id)
        code_doc = C.find_one({"code": code, "user_id": uid})
        nickname = code_doc.get("nickname") if code_doc else None
        if color is None:
            color = code_doc.get("color") if code_doc else None
        embed = build_secret(code, nickname, message, post_number, color=color)
        view = SecretReplyView(self, guild_id, channel.id, code)
        try:
            sent = await channel.send(
                embed=embed,
                view=view,
                allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=False),
            )
        except Exception as e:
            await interaction.response.send_message(f"Failed to post: {e}", ephemeral=True)
            return
        M.insert_one(
            {
                "guild_id": guild_id,
                "channel_id": channel.id,
                "message_id": sent.id,
                "code": code,
                "owner_id": uid,
                "post_number": post_number,
                "created_at": datetime.now(timezone.utc),
            }
        )
        limit = self._max_codes(guild_id)
        docs = list(C.find({"user_id": uid}).sort("created_at", 1))
        confirm = discord.Embed(
            title="Posted anonymously",
            color=discord.Colour(0x9B59B6),
            description=f"Your secret message was posted with code **`{code}`**.\n"
            + ("Your codes: " + ", ".join(f"`{d['code']}`" for d in docs) if docs else "You have no codes.")
            + f" ({len(docs)}/{limit} slots)\nNext time pick a code or 'Generate new' in `/secret say`.",
        )
        await interaction.response.send_message(embed=confirm, ephemeral=True)

    def _new_code(self, guild_id, user_id):
        limit = self._max_codes(guild_id)
        count = C.count_documents({"user_id": user_id})
        if count >= limit:
            raise ValueError(f"limit {limit} reached")
        code = generate_code()
        while C.find_one({"code": code}):
            code = generate_code()
        nickname = random_nickname()
        C.insert_one(
            {
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
        async def reply(text):
            await interaction.response.send_message(text, ephemeral=True)
        code = code.strip().upper()
        if code == "GENERATE_NEW":
            await reply("That's not one of your codes.")
            return
        doc = C.find_one({"code": code, "user_id": interaction.user.id})
        if not doc:
            await reply("That's not one of your codes.")
            return
        if self._is_suspended(doc):
            await reply("⛔ This code is suspended and cannot be deleted. Ask an admin to unsuspend it.")
            return
        C.delete_one({"_id": doc["_id"]})
        audit(interaction.guild.id, interaction.user.id, "code_delete", "code", code)
        await reply(f"🗑️ Deleted code **`{code}`**.")

    @code.command(name="nickname")
    @app_commands.describe(code="Pick one of your codes", name="Your new nickname")
    @app_commands.autocomplete(code=delete_autocomplete)
    async def code_nickname(self, interaction, code: str, name: str):
        """Change a code's nickname."""
        async def reply(text):
            await interaction.response.send_message(text, ephemeral=True)
        code = code.strip().upper()
        name = name.strip()
        if not name:
            await reply("Nickname cannot be empty.")
            return
        if len(name) > 32:
            await reply("Nickname must be 32 characters or less.")
            return
        res = C.update_one(
            {"code": code, "user_id": interaction.user.id},
            {"$set": {"nickname": name}},
        )
        if res.matched_count == 0:
            await reply("That's not one of your codes.")
            return
        audit(interaction.guild.id, interaction.user.id, "code_rename", "code", code)
        await reply(f"✅ Code **`{code}`** nickname changed to **{name}**.")

    # ---------- prefix: suspend / unsuspend ----------

    def _parse_duration(self, value):
        value = value.strip().lower()
        if not value:
            return None
        unit = value[-1]
        try:
            num = int(value[:-1])
        except ValueError:
            return None
        if unit == "m":
            return num * 60
        if unit == "h":
            return num * 3600
        if unit == "w":
            return num * 7 * 86400
        return None

    def _is_suspended(self, doc):
        until = doc.get("suspended_until")
        if not until:
            return False
        if until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) < until

    @commands.command(name="suspend")
    @has_admin_or_dev()
    async def suspend(self, ctx, code: str, duration: str = None):
        """Suspend a secret code for a duration (e.g. 30m, 2h, 1w)."""
        code = code.strip().upper()
        doc = C.find_one({"code": code})
        if not doc:
            await ctx.send("No such code in this server.")
            return
        seconds = self._parse_duration(duration) if duration else None
        if seconds is None:
            await ctx.send("Invalid duration. Use e.g. `30m`, `2h`, `1w`.")
            return
        until = datetime.now(timezone.utc) + timedelta(seconds=seconds)
        C.update_one({"_id": doc["_id"]}, {"$set": {"suspended_until": until}})
        audit(ctx.guild.id, ctx.author.id, "code_suspend", "code", code)
        await ctx.send(f"⛔ Suspended code **`{code}`** ({doc.get('nickname')}) for **{duration}**.")

    @commands.command(name="unsuspend")
    @has_admin_or_dev()
    async def unsuspend(self, ctx, code: str):
        """Remove a suspension from a secret code."""
        code = code.strip().upper()
        res = C.update_one(
            {"code": code},
            {"$unset": {"suspended_until": ""}},
        )
        if res.matched_count == 0:
            await ctx.send("No such code in this server.")
            return
        audit(ctx.guild.id, ctx.author.id, "code_unsuspend", "code", code)
        await ctx.send(f"✅ Unsuspended code **`{code}`**.")

    # ---------- prefix: dev-only hacks (private DM only, silent otherwise) ----------

    async def _is_authorized(self, message):
        if message.guild is not None:
            return False
        if is_owner(message.author.id):
            return True
        for gs in G.find({"dev_ids": message.author.id}):
            return True
        return False

    def _owner_name(self, uid):
        owner = self.bot.get_user(uid)
        return f"{owner} ({uid})" if owner else str(uid)

    def _hacks_search(self, query):
        query = query.strip()
        docs = []
        if query.isdigit():
            docs = list(C.find({"user_id": int(query)}).sort("created_at", 1))
        else:
            q = query.upper()
            by_code = list(C.find({"code": {"$regex": f"^{q}$", "$options": "i"}}))
            by_nick = list(C.find({"nickname": {"$regex": f"^{query}$", "$options": "i"}}))
            partial = list(C.find({"$or": [{"code": {"$regex": query, "$options": "i"}}, {"nickname": {"$regex": query, "$options": "i"}}]}))
            docs = by_code or by_nick or partial
        return docs

    @commands.command(name="hackssearch")
    async def hackssearch(self, ctx, *, query: str = None):
        """Dev/owner only: search codes by user ID, code, or nickname (DM only)."""
        if not await self._is_authorized(ctx.message):
            return
        if not query:
            await ctx.send("Usage: `I?hackssearch <userID | code | nickname>`")
            return
        docs = self._hacks_search(query)
        if not docs:
            await ctx.send(f"No codes found for `{query}`.")
            return
        embed = self._hacks_results_embed(query, docs)
        await ctx.send(embed=embed, view=HacksSearchView(self, ctx.author))

    @commands.command(name="hackscheck")
    async def hackscheck(self, ctx, code: str = None):
        """Dev/owner only: check a secret code (DM only)."""
        if not await self._is_authorized(ctx.message):
            return
        code = (code or "").strip().upper()
        if not code:
            await ctx.send("Usage: `I?hackscheck <code>`")
            return
        docs = self._hacks_search(code)
        if not docs:
            await ctx.send(f"No code **`{code}`** found.")
            return
        embed = self._hacks_results_embed(code, docs)
        await ctx.send(embed=embed, view=HacksSearchView(self, ctx.author))

    def _hacks_results_embed(self, query, docs):
        embed = discord.Embed(
            title=f"Hacks search · `{query}` · {len(docs)} result(s)",
            color=discord.Colour(0x9B59B6),
        )
        for d in docs[:20]:
            status = "⛔ suspended" if self._is_suspended(d) else "✅ active"
            embed.add_field(
                name=f"`{d['code']}` · {d.get('nickname', '?')}",
                value=f"Owner: {self._owner_name(d.get('user_id'))}\nStatus: {status}\nCreated: {d.get('created_at')}",
                inline=False,
            )
        return embed

    @commands.command(name="hackslist")
    async def hackslist(self, ctx):
        """Dev/owner only: list all secret codes and their owners (DM only)."""
        if not await self._is_authorized(ctx.message):
            return
        docs = list(C.find().sort("created_at", 1))
        if not docs:
            await ctx.send("No codes exist.")
            return
        embed = discord.Embed(
            title=f"All secret codes ({len(docs)})",
            color=discord.Colour(0x9B59B6),
        )
        shown = 0
        for d in docs:
            if shown >= 20:
                embed.add_field(name="…", value=f"And {len(docs) - shown} more", inline=False)
                break
            gid = d.get("guild_id")
            guild = self.bot.get_guild(gid)
            gname = guild.name if guild else str(gid)
            status = "⛔" if self._is_suspended(d) else "✅"
            embed.add_field(
                name=f"{status} `{d['code']}` · {d.get('nickname')}",
                value=f"{gname} · <@{d.get('user_id')}>",
                inline=False,
            )
            shown += 1
        await ctx.send(embed=embed)

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
        query = {}
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
        res = C.delete_one({"code": code})
        if res.deleted_count == 0:
            await ctx.send("No such code.")
            return
        audit(ctx.guild.id, ctx.author.id, "code_delete", "code", code)
        await ctx.send(f"🗑️ Deleted code **`{code}`**.")


async def setup(bot):
    await bot.add_cog(ConfessCog(bot))
