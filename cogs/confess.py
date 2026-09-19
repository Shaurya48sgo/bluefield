from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands

from cogs.common import (
    C,
    CD,
    G,
    I,
    M,
    RC,
    RP,
    US,
    audit,
    fmt_duration,
    generate_code,
    get_extra_code_slots,
    get_guild_settings,
    has_admin_or_dev,
    has_setup_access,
    is_admin,
    is_blacklisted,
    is_dev,
    is_mod,
    add_extra_code_slots,
    is_owner,
    parse_duration,
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
DEFAULT_CONFESS_COOLDOWN = 0  # unlimited unless a server sets one via I?confesschannel
THREADS_COOLDOWN = 3600  # 1 message per hour per code slot in the threads channel


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
            discord.SelectOption(
                label=f"{d.get('slot', '?')}- {d.get('nickname', '?')} - {d['code']}",
                value=d["code"],
            )
            for d in docs
        ]
        limit = self.cog._max_codes(guild_id, self.author.id)
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
            limit = self.cog._max_codes(self.guild_id, self.author.id)
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
        docs = self.view.cog._ensure_slots(interaction.user.id)
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
    def __init__(self, cog, interaction, guild_id, code, message, default_nick, reply_to=None,
                 mention_user_id=None, mention_code=None, channel_id=None):
        super().__init__(title="Your new code")
        self.cog = cog
        self.interaction = interaction
        self.guild_id = guild_id
        self.code = code
        self.message = message
        self.reply_to = reply_to
        self.mention_user_id = mention_user_id
        self.mention_code = mention_code
        self.channel_id = channel_id
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
            embed=embed,
            view=ColorPickView(
                self.cog, interaction, self.guild_id, self.code, self.message,
                reply_to=self.reply_to,
                mention_user_id=self.mention_user_id,
                mention_code=self.mention_code,
                channel_id=self.channel_id,
            ),
            ephemeral=True,
        )


class ColorPickView(discord.ui.View):
    def __init__(self, cog, interaction, guild_id, code, message, reply_to=None,
                 mention_user_id=None, mention_code=None, channel_id=None):
        super().__init__(timeout=300)
        self.cog = cog
        self.interaction = interaction
        self.guild_id = guild_id
        self.code = code
        self.message = message
        self.reply_to = reply_to
        self.mention_user_id = mention_user_id
        self.mention_code = mention_code
        self.channel_id = channel_id
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
        code_doc = C.find_one({"code": self.code, "user_id": interaction.user.id})
        slot = self.cog._slot_of(code_doc)
        channel = None
        if self.channel_id is not None:
            channel = interaction.guild.get_channel(self.channel_id)
        if channel is None:
            channel = self.cog._channel(interaction.guild)
        in_thread = self.cog._in_thread(interaction)
        if self.reply_to is not None:
            target_doc = M.find_one({"guild_id": self.guild_id, "post_number": self.reply_to})
            if not target_doc or channel is None:
                await interaction.response.send_message("The post you were replying to no longer exists.", ephemeral=True)
                return
            target_channel = interaction.guild.get_channel(target_doc.get("channel_id")) or channel
            try:
                original_message = await target_channel.fetch_message(target_doc["message_id"])
            except Exception:
                await interaction.response.send_message("The post you were replying to was deleted.", ephemeral=True)
                return
            if not in_thread:
                remaining = self.cog._cooldown_remaining(self.guild_id, channel.id, interaction.user.id, slot)
                if remaining > 0:
                    await interaction.response.send_message(
                        embed=discord.Embed(
                            color=discord.Colour(0xED4245),
                            description=self.cog._cooldown_fail_text(self.code, slot, remaining),
                        ),
                        ephemeral=True,
                    )
                    return
            await self.cog.post_reply(
                interaction, self.guild_id, channel.id, target_doc["code"], self.code, self.message, original_message,
                mention_user_id=self.mention_user_id, mention_code=self.mention_code,
            )
            return
        await self.cog._post_secret(
            interaction, self.guild_id, self.code, self.message, color_value,
            mention_user_id=self.mention_user_id, mention_code=self.mention_code,
            channel=channel, in_thread=in_thread,
        )


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
        self.picked_color = int(self.color_select.values[0])
        await interaction.response.defer()

    async def on_confirm(self, interaction):
        color_value = getattr(self, "picked_color", None)
        if color_value is None:
            color_value = list(SECRET_COLORS.values())[0]
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


REVEAL_EXPIRY = 24 * 3600


class RevealDecisionView(discord.ui.View):
    def __init__(self, cog, proposal_id, to_user_id):
        super().__init__(timeout=24 * 3600)
        self.cog = cog
        self.proposal_id = proposal_id
        self.to_user_id = to_user_id

    async def interaction_check(self, interaction):
        if interaction.user.id != self.to_user_id:
            await interaction.response.send_message("This reveal request isn't for you.", ephemeral=True)
            return False
        return True

    def _expired(self, prop):
        created = prop.get("created_at")
        if created is None:
            return True
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - created > timedelta(seconds=REVEAL_EXPIRY)

    async def _reveal_identity(self, user, their_code, other_user, other_code, deleted):
        embed = discord.Embed(
            title="🤝 Mutual reveal accepted!",
            color=discord.Colour(0x57F287),
            description=(
                f"Your code **`{their_code}`** and **`{other_code}`** have revealed each other.\n\n"
                f"The person behind `{other_code}` is {other_user.mention} "
                f"(**{other_user.name}**)"
                + ("\n\n🗑️ Both codes have been deleted." if deleted else "")
            ),
        )
        try:
            await user.send(embed=embed)
            return True
        except Exception:
            return False

    @discord.ui.button(label="✅ Accept", style=discord.ButtonStyle.success)
    async def accept(self, interaction, button):
        prop = RP.find_one({"_id": self.proposal_id})
        if not prop or prop.get("status") != "pending":
            await interaction.response.edit_message(
                embed=discord.Embed(
                    title="Reveal request",
                    description="This request is no longer active.",
                    color=discord.Colour(0x99AAB5),
                ),
                view=None,
            )
            return
        if self._expired(prop):
            RP.update_one({"_id": prop["_id"]}, {"$set": {"status": "expired"}})
            await interaction.response.edit_message(
                embed=discord.Embed(
                    title="⌛ Reveal request expired",
                    color=discord.Colour(0x99AAB5),
                ),
                view=None,
            )
            return
        from_code_doc = C.find_one({"code": prop["from_code"]})
        to_code_doc = C.find_one({"code": prop["to_code"]})
        if not from_code_doc or not to_code_doc:
            RP.update_one({"_id": prop["_id"]}, {"$set": {"status": "invalid"}})
            await interaction.response.send_message("One of the codes no longer exists.", ephemeral=True)
            return
        RP.update_one({"_id": prop["_id"]}, {"$set": {"status": "accepted", "accepted_at": datetime.now(timezone.utc)}})
        deleted = bool(prop.get("delete"))
        if deleted:
            C.delete_many({"code": {"$in": [prop["from_code"], prop["to_code"]]}})
        from_user = self.cog.bot.get_user(prop["from_user_id"])
        to_user = interaction.user
        delivered = False
        if from_user:
            delivered = await self._reveal_identity(
                from_user, prop["from_code"], to_user, prop["to_code"], deleted
            )
        await self._reveal_identity(to_user, prop["to_code"], from_user, prop["from_code"], deleted)
        audit(interaction.guild.id if interaction.guild else 0, to_user.id, "reveal_accept", "code", prop["to_code"], f"with {prop['from_code']}")
        note = "" if delivered else "\n\n⚠️ Couldn't DM the other user — they may have DMs closed."
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="🤝 Revealed!",
                description=f"You are now revealed with **`{prop['from_code']}`**.{note}",
                color=discord.Colour(0x57F287),
            ),
            view=None,
        )

    @discord.ui.button(label="❌ Decline", style=discord.ButtonStyle.danger)
    async def decline(self, interaction, button):
        prop = RP.find_one({"_id": self.proposal_id})
        if not prop or prop.get("status") != "pending":
            await interaction.response.edit_message(
                embed=discord.Embed(
                    title="Reveal request",
                    description="This request is no longer active.",
                    color=discord.Colour(0x99AAB5),
                ),
                view=None,
            )
            return
        RP.update_one({"_id": prop["_id"]}, {"$set": {"status": "declined"}})
        from_user = self.cog.bot.get_user(prop["from_user_id"])
        if from_user:
            try:
                await from_user.send(
                    embed=discord.Embed(
                        title="Reveal declined",
                        description=f"**`{prop['to_code']}`** declined your mutual reveal request.",
                        color=discord.Colour(0xED4245),
                    )
                )
            except Exception:
                pass
        audit(interaction.guild.id if interaction.guild else 0, interaction.user.id, "reveal_decline", "code", prop["to_code"])
        await interaction.response.edit_message(
            embed=discord.Embed(title="Reveal declined.", color=discord.Colour(0xED4245)),
            view=None,
        )


class ReportActionView(discord.ui.View):
    def __init__(self, cog, code):
        super().__init__(timeout=7 * 24 * 3600)
        self.cog = cog
        self.code = code

    async def interaction_check(self, interaction):
        member = interaction.user
        allowed = (
            is_owner(member.id)
            or (interaction.guild and is_dev(interaction.guild.id, member.id))
            or (interaction.guild and is_mod(interaction.guild.id, member.id))
            or is_admin(member)
        )
        if not allowed:
            await interaction.response.send_message("Only staff can use this.", ephemeral=True)
        return allowed

    @discord.ui.button(label="⛔ Suspend 1h", style=discord.ButtonStyle.primary)
    async def suspend_1h(self, interaction, button):
        await self.cog._suspend_from_report(interaction, self.code, "1h")

    @discord.ui.button(label="⛔ Suspend 24h", style=discord.ButtonStyle.primary)
    async def suspend_24h(self, interaction, button):
        await self.cog._suspend_from_report(interaction, self.code, "24h")

    @discord.ui.button(label="✅ Dismiss", style=discord.ButtonStyle.secondary)
    async def dismiss(self, interaction, button):
        embed = discord.Embed(
            title=f"🚩 Report · `{self.code}` · dismissed",
            color=discord.Colour(0x99AAB5),
            description=f"Dismissed by {interaction.user.mention}.",
        )
        await interaction.response.edit_message(embed=embed, view=None)
        audit(interaction.guild.id, interaction.user.id, "report_dismiss", "code", self.code)


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
        self.last_report = {}

    def _max_codes(self, guild_id, user_id=None):
        base = get_guild_settings(guild_id).get("confess_max_codes", DEFAULT_MAX_CODES)
        if user_id is None:
            return base
        return (
            base
            + get_extra_code_slots(guild_id, user_id)
            + get_extra_code_slots(0, user_id)  # global bonus (vouchers / DM grants)
        )

    def _staff_uid_authorized(self, user_id):
        if is_owner(user_id):
            return True
        for _gs in G.find({"dev_ids": user_id}):
            return True
        return False

    # ---------- code slots (numbering) ----------

    def _ensure_slots(self, user_id):
        """Backfill persistent slot numbers (1, 2, 3, ...) for a user's codes.

        Deleted slots stay free, so a replacement code reuses the lowest free
        number. Returns the user's code docs sorted oldest-first.
        """
        docs = list(C.find({"user_id": user_id}).sort("created_at", 1))
        used = set()
        for d in docs:
            s = d.get("slot")
            if isinstance(s, int) and s > 0:
                used.add(s)
        nxt = 1
        for d in docs:
            s = d.get("slot")
            if isinstance(s, int) and s > 0:
                continue
            while nxt in used:
                nxt += 1
            C.update_one({"_id": d["_id"]}, {"$set": {"slot": nxt}})
            d["slot"] = nxt
            used.add(nxt)
            nxt += 1
        return docs

    def _slot_of(self, code_doc):
        s = (code_doc or {}).get("slot")
        return s if isinstance(s, int) and s > 0 else "?"

    # ---------- per-code posting cooldown (slot-based, survives code deletion) ----------

    def _cooldown_window(self, guild_id, channel_id):
        s = get_guild_settings(guild_id)
        if channel_id is not None and channel_id == s.get("secret_threads_channel_id"):
            return THREADS_COOLDOWN
        v = s.get("confess_cooldown", None)
        if v is None:
            return DEFAULT_CONFESS_COOLDOWN
        try:
            return max(0, int(v))
        except (TypeError, ValueError):
            return DEFAULT_CONFESS_COOLDOWN

    def _cooldown_remaining(self, guild_id, channel_id, user_id, slot):
        window = self._cooldown_window(guild_id, channel_id)
        if not window or not isinstance(slot, int):
            return 0
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=window)
        doc = CD.find_one(
            {
                "guild_id": guild_id,
                "user_id": user_id,
                "slot": slot,
                "channel_id": channel_id,
                "at": {"$gt": cutoff},
            }
        )
        if not doc:
            return 0
        at = doc.get("at")
        if at is None:
            return 0
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        return max(0, window - (datetime.now(timezone.utc) - at).total_seconds())

    def _record_post(self, guild_id, channel_id, user_id, slot):
        if not isinstance(slot, int):
            return
        CD.update_one(
            {"guild_id": guild_id, "user_id": user_id, "slot": slot, "channel_id": channel_id},
            {"$set": {"at": datetime.now(timezone.utc)}},
            upsert=True,
        )

    def _in_thread(self, interaction):
        ch = getattr(interaction, "channel", None)
        try:
            return isinstance(ch, discord.Thread)
        except Exception:
            return False


    @commands.command(name="codecode")
    async def codecode(self, ctx, number: int = None):
        """Create a redeemable bonus-slot voucher code (bot owner/devs). DM only."""
        if ctx.guild is not None:
            if self._staff_uid_authorized(ctx.author.id):
                await ctx.send("📩 This only works in my **DMs** — open my profile and message me.")
            return
        if not self._staff_uid_authorized(ctx.author.id):
            await ctx.send("Only the bot owner and devs can create vouchers.")
            return
        if number is None or number <= 0:
            await ctx.send("Usage: `I?codecode <number>` — slots the voucher grants.")
            return
        voucher = f"SLOT-{generate_code(8)}"
        while RC.find_one({"code": voucher}):
            voucher = f"SLOT-{generate_code(8)}"
        RC.insert_one(
            {
                "code": voucher,
                "slots": number,
                "created_by": ctx.author.id,
                "created_at": datetime.now(timezone.utc),
                "used_by": None,
                "used_at": None,
            }
        )
        audit(0, ctx.author.id, "voucher_create", "code", voucher, f"{number} slots")
        embed = discord.Embed(
            title="🎟️ Bonus-slot voucher created",
            color=discord.Colour(0x9B59B6),
            description=(
                f"**Voucher:** `{voucher}`\n"
                f"**Grants:** +{number} secret-code slot(s)\n\n"
                f"Redeem with `I?codeuse {voucher}` here in DMs."
            ),
        )
        embed.set_footer(text="One-time use · applies to ALL servers")
        await ctx.send(embed=embed)

    @commands.command(name="codeuse")
    async def codeuse(self, ctx, code: str = None):
        """Redeem a bonus-slot voucher code. DM only."""
        if ctx.guild is not None:
            await ctx.send("📩 Vouchers are redeemed in my **DMs** — open my profile and message me.")
            return
        if code is None:
            await ctx.send("Usage: `I?codeuse <voucher>` — redeem it here in DMs.")
            return
        doc = RC.find_one({"code": code.strip().upper()})
        if not doc:
            await ctx.send("Invalid or unknown voucher code.")
            return
        if doc.get("used_by"):
            await ctx.send("That voucher has already been used.")
            return
        RC.update_one(
            {"_id": doc["_id"]},
            {"$set": {"used_by": ctx.author.id, "used_at": datetime.now(timezone.utc)}},
        )
        new_total = add_extra_code_slots(0, ctx.author.id, doc["slots"])
        audit(0, ctx.author.id, "voucher_redeem", "code", doc["code"], f"+{doc['slots']} slots")
        base_default = DEFAULT_MAX_CODES
        embed = discord.Embed(
            title="✅ Voucher redeemed!",
            color=discord.Colour(0x57F287),
            description=(
                f"**+{doc['slots']}** extra secret-code slot(s) added to your account.\n"
                f"Your total bonus is now **{new_total}** slot(s) — "
                f"your limit becomes `{base_default}+{new_total}` (or higher where admins raised it) "
                "in every server."
            ),
        )
        embed.set_footer(text="Thank you for supporting the bot!")
        await ctx.send(embed=embed)

    def _channel(self, guild):
        cid = get_guild_settings(guild.id).get("confess_channel_id")
        return guild.get_channel(cid) if cid else None

    # ---------- prefix: per-channel setup ----------

    @commands.command(name="confesschannel")
    @has_setup_access()
    async def confesschannel(self, ctx, *, raw: str = None):
        """Set the anonymous chat channel with optional per-code cooldown.

        Usage: `I?confesschannel [#channel] [cooldown] [-r]`
        cooldown: 0 = no limit, or 30s/10m/2h/1d/1w (default no limit). `-r` clears it.
        """
        gid = ctx.guild.id
        tokens = (raw or "").strip().split()
        lowered = [t.lower() for t in tokens]
        if "-r" in lowered or "remove" in lowered:
            set_guild_settings(gid, confess_channel_id=None)
            audit(gid, ctx.author.id, "settings", "guild", gid, "confess channel cleared")
            await ctx.send("🗑️ Anonymous chat channel cleared.")
            return
        channel = None
        if ctx.message.channel_mentions:
            channel = ctx.message.channel_mentions[0]
        else:
            channel = ctx.channel
        window = None
        for t in tokens:
            if t.lower() in ("-y", "-r", "remove"):
                continue
            if t.startswith("<#") and t.endswith(">"):
                continue
            parsed = parse_duration(t)
            if parsed is not None:
                window = parsed
        kwargs = {"confess_channel_id": channel.id}
        if window is not None:
            kwargs["confess_cooldown"] = window
        set_guild_settings(gid, **kwargs)
        effective = window
        if effective is None:
            effective = get_guild_settings(gid).get("confess_cooldown", DEFAULT_CONFESS_COOLDOWN)
        audit(gid, ctx.author.id, "settings", "guild", gid, f"confess channel -> #{channel.name} cooldown {fmt_duration(effective)}")
        await ctx.send(
            f"✅ Anonymous chat channel set to {channel.mention}.\n"
            f"⏳ Cooldown: **1 message per {fmt_duration(effective)} per code** (slot-based, threads exempt)."
        )

    @commands.command(name="secretthreads")
    @has_setup_access()
    async def secretthreads(self, ctx, *, raw: str = None):
        """Set the secret-threads channel: posts there get an auto discussion thread.

        Usage: `I?secretthreads [#channel] [-r]`
        """
        gid = ctx.guild.id
        tokens = (raw or "").strip().split()
        lowered = [t.lower() for t in tokens]
        if "-r" in lowered or "remove" in lowered:
            set_guild_settings(gid, secret_threads_channel_id=None)
            audit(gid, ctx.author.id, "settings", "guild", gid, "secret threads channel cleared")
            await ctx.send("🗑️ Secret-threads channel cleared.")
            return
        if ctx.message.channel_mentions:
            channel = ctx.message.channel_mentions[0]
        else:
            channel = ctx.channel
        set_guild_settings(gid, secret_threads_channel_id=channel.id)
        audit(gid, ctx.author.id, "settings", "guild", gid, f"secret threads channel -> #{channel.name}")
        await ctx.send(
            f"✅ Secret-threads channel set to {channel.mention}.\n"
            f"🧵 Every secret posted there gets its own discussion thread (1 post per {fmt_duration(THREADS_COOLDOWN)} per code, no limit inside threads)."
        )

    @commands.command(name="confesslist")
    @has_setup_access()
    async def confesslist(self, ctx):
        """List the anonymous chat + secret-threads channels and their per-code cooldowns."""
        gid = ctx.guild.id
        settings = get_guild_settings(gid)
        confess_id = settings.get("confess_channel_id")
        threads_id = settings.get("secret_threads_channel_id")
        cooldown = settings.get("confess_cooldown", DEFAULT_CONFESS_COOLDOWN)

        def _mention(cid):
            if not cid:
                return None
            ch = ctx.guild.get_channel(cid)
            return ch.mention if ch else f"<#{cid}>"

        entries = []
        confess_txt = _mention(confess_id)
        if confess_txt:
            entries.append(
                f"📝 Anonymous chat: {confess_txt}\n"
                f"⏳ Cooldown: **1 message per {fmt_duration(cooldown)} per code**"
            )
        threads_txt = _mention(threads_id)
        if threads_txt:
            entries.append(
                f"🧵 Secret threads: {threads_txt}\n"
                f"⏳ Cooldown: **1 message per {fmt_duration(THREADS_COOLDOWN)} per code** (no limit inside threads)"
            )
        embed = discord.Embed(
            title=f"📋 Confess channels ({len(entries)})",
            color=discord.Colour(0x9B59B6),
            description="\n\n".join(entries) if entries else "0 — no confess channels set up yet.",
        )
        embed.set_footer(text="I?confesschannel [#channel] [cooldown] · I?secretthreads [#channel]")
        await ctx.send(embed=embed)

    @commands.command(name="codeadd")
    async def codeadd(self, ctx, target: str = None, number: int = None):
        """Grant extra secret-code slots to a user (bot owner/devs only). Works in DMs."""
        gid = ctx.guild.id if ctx.guild else 0
        allowed = is_owner(ctx.author.id) or (ctx.guild is not None and is_dev(ctx.guild.id, ctx.author.id))
        if not allowed:
            await ctx.send("Only the bot owner and devs can manage extra code slots.")
            return
        if target is None or number is None:
            await ctx.send(
                "Usage: `I?codeadd <mention/userid> <number>` (negative number removes)\n"
                + ("Grants apply to ALL servers when used in DMs." if ctx.guild is None else "")
            )
            return
        raw = str(target).strip()
        if raw.startswith("<@") and raw.endswith(">"):
            raw = raw[2:-1].lstrip("!")
        if not raw.isdigit():
            await ctx.send("Pass a valid **user mention** or **user ID**.")
            return
        user_id = int(raw)
        if number == 0:
            await ctx.send("Number can't be 0.")
            return
        old = get_extra_code_slots(gid, user_id)
        new_total = add_extra_code_slots(gid, user_id, number)
        scope = "ALL servers" if gid == 0 else "this server"
        audit(gid, ctx.author.id, "code_slots", "user", user_id, f"{number:+d} slots -> {new_total} ({scope})")
        verb = (
            f"Granted **{number}** extra code slot(s) to"
            if number > 0
            else f"Reduced <@{user_id}>'s bonus slots by **{abs(number)}** for"
        )
        await ctx.send(
            f"✅ {verb} `<@{user_id}>` ({scope}) — total bonus is now **{new_total}** slot(s)."
            + ("\n⚠️ Existing codes above the limit are untouched." if number < 0 and new_total < old else "")
        )

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
        slot = doc.get("slot", "?")
        label = f"{slot}- {doc.get('nickname', '?')} - {doc['code']}"
        if self._is_suspended(doc):
            label = f"⛔ {label} (suspended)"
        return label

    async def code_autocomplete(self, interaction, current):
        gid = interaction.guild.id
        uid = interaction.user.id
        docs = self._ensure_slots(uid)
        out = []
        for d in docs:
            label = self._code_label(d)
            if current.lower() in label.lower() or current.lower() in d["code"].lower():
                out.append(app_commands.Choice(name=label, value=d["code"]))
        limit = self._max_codes(gid, uid)
        if len(docs) < limit:
            out.append(app_commands.Choice(name="Generate new", value="GENERATE_NEW"))
        return out[:25]

    async def delete_autocomplete(self, interaction, current):
        docs = self._ensure_slots(interaction.user.id)
        out = []
        for d in docs:
            label = self._code_label(d)
            if current.lower() in label.lower() or current.lower() in d["code"].lower():
                out.append(app_commands.Choice(name=label, value=d["code"]))
            if len(out) >= 25:
                break
        return out

    async def nick_autocomplete(self, interaction, current):
        docs = self._ensure_slots(interaction.user.id)
        out = []
        for d in docs:
            label = self._code_label(d)
            if current.lower() in label.lower():
                out.append(app_commands.Choice(name=label, value=d["code"]))
            if len(out) >= 25:
                break
        return out

    def _resolve_post_channel(self, interaction):
        """Figure out where a /secret say should land.

        Returns (channel, in_thread, error). Threads are exempt from cooldowns
        and never get auto-threads created inside them.
        """
        guild = interaction.guild
        settings = get_guild_settings(guild.id)
        confess_id = settings.get("confess_channel_id")
        threads_id = settings.get("secret_threads_channel_id")
        if self._in_thread(interaction):
            parent = getattr(getattr(interaction, "channel", None), "parent", None)
            parent_id = getattr(parent, "id", None)
            if parent_id == confess_id or parent_id == threads_id:
                return (guild.get_channel(parent_id) or parent, True, None)
            if parent is not None:
                return (parent, True, None)
            return (None, True, "Couldn't figure out which thread this is.")
        cid = getattr(interaction, "channel_id", None)
        if cid is not None and (cid == confess_id or cid == threads_id):
            ch = guild.get_channel(cid)
            if ch is not None:
                return (ch, False, None)
        names = []
        if confess_id:
            names.append(f"<#{confess_id}>")
        if threads_id:
            names.append(f"<#{threads_id}> (threads)")
        if not names:
            return (None, False, "Anonymous chat isn't enabled in this server.")
        return (None, False, f"Anonymous messages only work in {' or '.join(names)}.")

    def _cooldown_fail_text(self, code, slot, remaining):
        return (
            f"⏳ Code `{self._slot_code(code, slot)}` can post again in "
            f"**{fmt_duration(remaining)}** (1 message per channel per cooldown)."
        )

    def _slot_code(self, code, slot):
        return f"{slot}- {code}" if isinstance(slot, int) else code

    async def reveal_target_autocomplete(self, interaction, current):
        docs = list(C.find({"user_id": {"$ne": interaction.user.id}}).sort("created_at", -1))
        out = []
        for d in docs:
            label = self._code_label(d)
            if current.lower() in label.lower() or current.lower() in d["code"].lower():
                out.append(app_commands.Choice(name=label, value=d["code"]))
            if len(out) >= 25:
                break
        return out

    async def mention_code_autocomplete(self, interaction, current):
        return await self.reveal_target_autocomplete(interaction, current)

    @secret.command(name="say")
    @app_commands.describe(
        message="The message to post anonymously",
        code="Pick one of your codes, or 'Generate new'",
        reply_to="Post number of the secret you're replying to (optional)",
        mention_user="Ping a user on your post (optional)",
        mention_code="Notify the owner of a code on your post (optional)",
    )
    @app_commands.autocomplete(code=code_autocomplete, mention_code=mention_code_autocomplete)
    async def say(
        self,
        interaction,
        message: str,
        code: str,
        reply_to: int = None,
        mention_user: discord.Member = None,
        mention_code: str = None,
    ):
        """Post anonymously using a code (or generate a new one)."""
        async def fail(text):
            embed = discord.Embed(color=discord.Colour(0xED4245), description=text)
            await interaction.response.send_message(embed=embed, ephemeral=True)

        channel, in_thread, channel_error = self._resolve_post_channel(interaction)
        if channel_error:
            await fail(channel_error)
            return
        if is_blacklisted(interaction.guild.id, interaction.user.id, interaction.user):
            await fail("You are blacklisted from anonymous chat.")
            return
        if not message.strip():
            await fail("Message cannot be empty.")
            return

        uid = interaction.user.id
        gid = interaction.guild.id
        limit = self._max_codes(gid, uid)
        docs = self._ensure_slots(uid)

        target_doc = None
        if reply_to is not None:
            target_doc = M.find_one({"guild_id": gid, "post_number": reply_to})
            if not target_doc:
                await fail(f"No post **#{reply_to}** exists here.")
                return

        mention_code_doc = None
        if mention_code:
            mention_code = mention_code.strip().upper()
            mention_code_doc = C.find_one({"code": mention_code})
            if not mention_code_doc:
                await fail(f"No code `{mention_code}` to mention.")
                return
            if self._is_suspended(mention_code_doc):
                await fail(f"Code `{mention_code}` is suspended.")
                return
        mention_user_id = mention_user.id if mention_user is not None else None
        mention_code_str = mention_code_doc["code"] if mention_code_doc else None

        code = code.strip().upper()
        if code == "GENERATE_NEW":
            if len(docs) >= limit:
                await fail(
                    f"You've reached the limit of **{limit}** codes. "
                    "Delete one first with `/secret code delete <code>`."
                )
                return
            code = self._new_code(gid, uid)
            code_doc = C.find_one({"code": code, "user_id": uid})
            modal = NewSecretModal(
                self, interaction, gid, code, message, code_doc.get("nickname"),
                reply_to=reply_to,
                mention_user_id=mention_user_id,
                mention_code=mention_code_str,
                channel_id=channel.id,
            )
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
            slot = self._slot_of(code_doc)
            if not in_thread:
                remaining = self._cooldown_remaining(gid, channel.id, uid, slot)
                if remaining > 0:
                    await fail(self._cooldown_fail_text(code, slot, remaining))
                    return
            if target_doc is not None:
                target_channel = interaction.guild.get_channel(target_doc.get("channel_id")) or channel
                try:
                    original_message = await target_channel.fetch_message(target_doc["message_id"])
                except Exception:
                    await fail(f"Post **#{reply_to}** no longer exists.")
                    return
                await self.post_reply(
                    interaction, gid, channel.id, target_doc["code"], code, message, original_message,
                    mention_user_id=mention_user_id, mention_code=mention_code_str,
                )
                return
            await self._post_secret(
                interaction, gid, code, message, color=code_doc.get("color"),
                mention_user_id=mention_user_id, mention_code=mention_code_str,
                channel=channel, in_thread=in_thread,
            )

    def _nodm_enabled(self, user_id):
        doc = US.find_one({"user_id": user_id})
        return bool(doc and doc.get("nodm"))

    async def _dm_reply_notice(self, target_user_id, replier_code, reply_post, text, link):
        if self._nodm_enabled(target_user_id):
            return
        user = self.bot.get_user(target_user_id)
        if user is None:
            try:
                user = await self.bot.fetch_user(target_user_id)
            except Exception:
                return
        preview = (text[:180] + "…") if len(text) > 180 else text
        embed = discord.Embed(
            title="💬 Your secret got a reply",
            color=discord.Colour(0x5865F2),
            description=(
                f"Post **#{reply_post}** from code **`{replier_code}`**:\n\n{preview}\n\n"
                f"[Jump to the reply]({link})"
            ),
        )
        embed.set_footer(text="Do /dm off to turn off these pings · /inbox to check who pinged you")
        try:
            await user.send(embed=embed)
        except Exception:
            pass

    async def _dm_mention_notice(self, target_user_id, mentioned_code, mentioned_slot,
                                   from_code, post_number, text, link):
        """DM the owner of a mentioned code. Never reveals identity in-channel."""
        if self._nodm_enabled(target_user_id):
            return
        user = self.bot.get_user(target_user_id)
        if user is None:
            try:
                user = await self.bot.fetch_user(target_user_id)
            except Exception:
                return
        preview = (text[:180] + "…") if len(text) > 180 else text
        embed = discord.Embed(
            title="🔔 One of your codes was mentioned",
            color=discord.Colour(0xEB459E),
            description=(
                f"Code **`{self._slot_code(mentioned_code, mentioned_slot)}`** was mentioned "
                f"in post **#{post_number}** by code **`{from_code}`**:\n\n{preview}\n\n"
                f"[Jump to the post]({link})"
            ),
        )
        embed.set_footer(text="Do /dm off to turn off these pings · /inbox to check who pinged you")
        try:
            await user.send(embed=embed)
        except Exception:
            pass

    def _resolve_mentions(self, mention_user_id, mention_code):
        """Returns (ping_user_ids, mention_code_doc, error_text).

        Only real user mentions (`mention_user`) produce Discord pings.
        A code mention never pings — it is shown as code text in the embed
        and the code's owner is notified via DM + inbox instead.
        """
        ping_ids = []
        if mention_user_id is not None:
            try:
                ping_ids.append(int(mention_user_id))
            except (TypeError, ValueError):
                return None, None, "Invalid user to mention."
        mdoc = None
        if mention_code:
            mdoc = C.find_one({"code": str(mention_code).strip().upper()})
            if not mdoc:
                return None, None, f"No code `{mention_code}` to mention."
            if self._is_suspended(mdoc):
                return None, None, f"Code `{mdoc['code']}` is suspended."
        return list(dict.fromkeys(ping_ids)), mdoc, None

    async def post_reply(self, interaction, guild_id, channel_id, original_code, code, text, original_message=None,
                         mention_user_id=None, mention_code=None):
        reply_doc = C.find_one({"code": code})
        slot = None
        if reply_doc is not None:
            slot = reply_doc.get("slot")
            if not isinstance(slot, int):
                self._ensure_slots(interaction.user.id)
                reply_doc = C.find_one({"code": code})
                slot = (reply_doc or {}).get("slot")
        if not self._in_thread(interaction):
            remaining = self._cooldown_remaining(guild_id, channel_id, interaction.user.id, slot)
            if remaining > 0:
                await interaction.response.send_message(
                    embed=discord.Embed(
                        color=discord.Colour(0xED4245),
                        description=self._cooldown_fail_text(code, slot, remaining),
                    ),
                    ephemeral=True,
                )
                return
        ping_ids, mention_doc, mention_error = self._resolve_mentions(mention_user_id, mention_code)
        if mention_error:
            await interaction.response.send_message(
                embed=discord.Embed(color=discord.Colour(0xED4245), description=mention_error),
                ephemeral=True,
            )
            return
        target_post = None
        if original_message is not None:
            orig = M.find_one({"guild_id": guild_id, "message_id": original_message.id})
            if orig:
                target_post = orig.get("post_number")
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
        if ping_ids:
            embed.description += "\n\n" + " ".join(f"<@{uid}>" for uid in ping_ids)
        if mention_doc is not None:
            embed.description += f"\n\n🔔 Mention: `{self._slot_code(mention_doc['code'], mention_doc.get('slot'))}`"
        channel = interaction.guild.get_channel(channel_id)
        if channel is None:
            await interaction.response.send_message("That channel no longer exists.")
            return
        view = SecretReplyView(self, guild_id, channel_id, code)
        try:
            kwargs = {
                "embed": embed,
                "view": view,
                "allowed_mentions": discord.AllowedMentions(
                    everyone=False, roles=False, users=bool(ping_ids)
                ),
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
                "slot": slot if isinstance(slot, int) else None,
                "post_number": reply_post,
                "created_at": datetime.now(timezone.utc),
            }
        )
        self._record_post(guild_id, channel_id, interaction.user.id, slot)
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
            if owner["user_id"] != interaction.user.id and link is not None:
                await self._dm_reply_notice(owner["user_id"], code, reply_post, text, link)
        if mention_doc is not None and mention_doc["user_id"] != interaction.user.id:
            I.insert_one(
                {
                    "guild_id": guild_id,
                    "user_id": mention_doc["user_id"],
                    "code": mention_doc["code"],
                    "channel_id": channel_id,
                    "message_id": sent.id,
                    "text": text,
                    "created_at": datetime.now(timezone.utc),
                }
            )
            await self._dm_mention_notice(
                mention_doc["user_id"], mention_doc["code"], mention_doc.get("slot"),
                code, reply_post, text, _jump_link(guild_id, channel_id, sent.id),
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

    async def _post_secret(self, interaction, guild_id, code, message, color=None,
                           mention_user_id=None, mention_code=None, channel=None, in_thread=False):
        if channel is None:
            channel = self._channel(interaction.guild)
        if channel is None:
            await interaction.response.send_message("Anonymous chat isn't enabled in this server.", ephemeral=True)
            return
        uid = interaction.user.id
        code_doc = C.find_one({"code": code, "user_id": uid})
        slot = None
        if code_doc is not None:
            slot = code_doc.get("slot")
            if not isinstance(slot, int):
                self._ensure_slots(uid)
                code_doc = C.find_one({"code": code, "user_id": uid})
                slot = (code_doc or {}).get("slot")
        if not in_thread:
            remaining = self._cooldown_remaining(guild_id, channel.id, uid, slot)
            if remaining > 0:
                await interaction.response.send_message(
                    embed=discord.Embed(
                        color=discord.Colour(0xED4245),
                        description=self._cooldown_fail_text(code, slot, remaining),
                    ),
                    ephemeral=True,
                )
                return
        ping_ids, mention_doc, mention_error = self._resolve_mentions(mention_user_id, mention_code)
        if mention_error:
            await interaction.response.send_message(
                embed=discord.Embed(color=discord.Colour(0xED4245), description=mention_error),
                ephemeral=True,
            )
            return
        post_number = _next_post(guild_id)
        nickname = code_doc.get("nickname") if code_doc else None
        if color is None:
            color = code_doc.get("color") if code_doc else None
        embed = build_secret(code, nickname, message, post_number, color=color)
        if ping_ids:
            embed.description += "\n\n" + " ".join(f"<@{uid}>" for uid in ping_ids)
        if mention_doc is not None:
            embed.description += f"\n\n🔔 Mention: `{self._slot_code(mention_doc['code'], mention_doc.get('slot'))}`"
        view = SecretReplyView(self, guild_id, channel.id, code)
        try:
            sent = await channel.send(
                embed=embed,
                view=view,
                allowed_mentions=discord.AllowedMentions(
                    everyone=False, roles=False, users=bool(ping_ids)
                ),
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
                "slot": slot if isinstance(slot, int) else None,
                "post_number": post_number,
                "created_at": datetime.now(timezone.utc),
            }
        )
        self._record_post(guild_id, channel.id, uid, slot)
        if mention_doc is not None and mention_doc["user_id"] != uid:
            I.insert_one(
                {
                    "guild_id": guild_id,
                    "user_id": mention_doc["user_id"],
                    "code": mention_doc["code"],
                    "channel_id": channel.id,
                    "message_id": sent.id,
                    "text": message,
                    "created_at": datetime.now(timezone.utc),
                }
            )
            await self._dm_mention_notice(
                mention_doc["user_id"], mention_doc["code"], mention_doc.get("slot"),
                code, post_number, message, _jump_link(guild_id, channel.id, sent.id),
            )
        settings = get_guild_settings(guild_id)
        if not in_thread and channel.id == settings.get("secret_threads_channel_id"):
            try:
                await sent.create_thread(
                    name=f"Post #{post_number} discussion",
                    auto_archive_duration=1440,
                )
            except Exception:
                pass
        limit = self._max_codes(guild_id, uid)
        docs = self._ensure_slots(uid)
        confirm = discord.Embed(
            title="Posted anonymously",
            color=discord.Colour(0x9B59B6),
            description=f"Your secret message was posted with code **`{self._slot_code(code, slot)}`**.\n"
            + ("Your codes: " + ", ".join(f"`{self._slot_code(d['code'], d.get('slot'))}`" for d in docs) if docs else "You have no codes.")
            + f" ({len(docs)}/{limit} slots)\nNext time pick a code or 'Generate new' in `/secret say`.",
        )
        await interaction.response.send_message(embed=confirm, ephemeral=True)

    def _new_code(self, guild_id, user_id):
        limit = self._max_codes(guild_id, user_id)
        count = C.count_documents({"user_id": user_id})
        if count >= limit:
            raise ValueError(f"limit {limit} reached")
        code = generate_code()
        while C.find_one({"code": code}):
            code = generate_code()
        nickname = random_nickname()
        used = {d.get("slot") for d in C.find({"user_id": user_id}) if isinstance(d.get("slot"), int)}
        slot = 1
        while slot in used:
            slot += 1
        C.insert_one(
            {
                "user_id": user_id,
                "code": code,
                "nickname": nickname,
                "slot": slot,
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

    # ---------- slash: /secret reveal ----------

    reveal_group = app_commands.Group(name="reveal", description="Mutually reveal identities with another code")
    secret.add_command(reveal_group)

    @reveal_group.command(name="propose")
    @app_commands.describe(
        to_code="The code you want to mutually reveal with",
        your_code="One of your codes to reveal",
        also_delete="Also delete both codes after revealing (anti-blackmail)",
    )
    @app_commands.autocomplete(to_code=reveal_target_autocomplete, your_code=delete_autocomplete)
    async def reveal_propose(self, interaction, to_code: str, your_code: str, also_delete: bool = False):
        """Propose a mutual identity reveal with another anonymous code."""
        async def reply(text):
            await interaction.response.send_message(text, ephemeral=True)

        to_code = to_code.strip().upper()
        your_code = your_code.strip().upper()
        if to_code == your_code:
            await reply("You can't reveal with your own code.")
            return
        mine = C.find_one({"code": your_code, "user_id": interaction.user.id})
        if not mine:
            await reply("That's not one of your codes.")
            return
        if self._is_suspended(mine):
            await reply("⛔ Your code is suspended.")
            return
        target = C.find_one({"code": to_code})
        if not target:
            await reply(f"No such code **`{to_code}`**.")
            return
        if target.get("user_id") == interaction.user.id:
            await reply("That's one of your own codes.")
            return
        if self._is_suspended(target):
            await reply("⛔ That code is suspended.")
            return
        if RP.find_one({"from_code": your_code, "to_code": to_code, "status": "pending"}):
            await reply("You already have a pending reveal request with this code.")
            return
        prop = RP.insert_one(
            {
                "guild_id": interaction.guild.id,
                "from_user_id": interaction.user.id,
                "from_code": your_code,
                "to_code": to_code,
                "delete": bool(also_delete),
                "status": "pending",
                "created_at": datetime.now(timezone.utc),
            }
        )
        target_user = self.bot.get_user(target.get("user_id"))
        if target_user is None:
            try:
                target_user = await self.bot.fetch_user(target.get("user_id"))
            except Exception:
                target_user = None
        if target_user is None:
            RP.delete_one({"_id": prop.inserted_id})
            await reply("Couldn't reach that user.")
            return
        delete_note = (
            "🗑️ If you accept, **both codes will be deleted** after revealing."
            if also_delete
            else "Nothing gets deleted — only identities are exchanged."
        )
        embed = discord.Embed(
            title="🤝 Mutual reveal request",
            color=discord.Colour(0x5865F2),
            description=(
                f"**`{your_code}`** wants to mutually reveal identities with your code **`{to_code}`**.\n\n"
                f"{delete_note}\n\n"
                "If you accept, both of you will get a DM showing who is behind each code."
            ),
        )
        try:
            await target_user.send(
                embed=embed,
                view=RevealDecisionView(self, prop.inserted_id, target.get("user_id")),
            )
        except Exception:
            RP.delete_one({"_id": prop.inserted_id})
            await reply("That user has DMs closed — couldn't send them the request.")
            return
        audit(interaction.guild.id, interaction.user.id, "reveal_propose", "code", your_code, f"to {to_code}")
        await reply(f"🤝 Reveal request sent to the owner of **`{to_code}`**. They have 24 hours to accept.")

    # ---------- slash: /secret report ----------

    REPORT_COOLDOWN = 60

    @secret.command(name="report")
    @app_commands.describe(code="The code you want to report", reason="Why are you reporting this?")
    @app_commands.autocomplete(code=reveal_target_autocomplete)
    async def report(self, interaction, code: str, reason: str):
        """Report an anonymous code to the staff. Anyone can use this."""
        async def reply(text):
            await interaction.response.send_message(text, ephemeral=True)

        gid = interaction.guild.id
        now = datetime.now(timezone.utc).timestamp()
        last = self.last_report.get(interaction.user.id)
        if last is not None and now - last < self.REPORT_COOLDOWN:
            await reply(f"⏳ Please wait {int(self.REPORT_COOLDOWN - (now - last))}s before reporting again.")
            return
        code = code.strip().upper()
        doc = C.find_one({"code": code})
        if not doc:
            await reply(f"No such code **`{code}`**.")
            return
        reason = reason.strip()
        if len(reason) < 5:
            await reply("Please describe the reason (at least 5 characters).")
            return
        if len(reason) > 500:
            await reply("Reason must be 500 characters or less.")
            return
        cid = get_guild_settings(gid).get("report_log_channel_id")
        channel = interaction.guild.get_channel(cid) if cid else None
        if channel is None:
            await reply("Reports aren't set up here yet — ask an admin to run `I?reports` in the reports channel.")
            return
        embed = discord.Embed(
            title=f"🚩 Report · `{code}`",
            color=discord.Colour(0xED4245),
            description=reason,
        )
        embed.add_field(name="Nickname", value=doc.get("nickname", "?"), inline=True)
        embed.add_field(
            name="Reporter",
            value=f"{interaction.user.mention} (`{interaction.user.id}`)",
            inline=True,
        )
        embed.set_footer(text=f"Submitted · {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
        try:
            await channel.send(embed=embed, view=ReportActionView(self, code))
        except Exception:
            await reply("Couldn't submit the report — the reports channel may be missing.")
            return
        self.last_report[interaction.user.id] = now
        audit(gid, interaction.user.id, "report_submit", "code", code)
        await reply(f"✅ Report on **`{code}`** submitted to the staff. Thanks for helping keep chat safe.")

    async def _suspend_from_report(self, interaction, code, duration):
        seconds = self._parse_duration(duration)
        if seconds is None:
            return
        doc = C.find_one({"code": code})
        until = datetime.now(timezone.utc) + timedelta(seconds=seconds)
        C.update_one(
            {"_id": doc["_id"]} if doc else {"code": code},
            {
                "$set": {"suspended_until": until},
                "$push": {
                    "suspend_history": {
                        "action": "suspend",
                        "by": interaction.user.id,
                        "until": until,
                        "at": datetime.now(timezone.utc),
                        "source": "report",
                    }
                },
            },
        )
        audit(interaction.guild.id, interaction.user.id, "code_suspend", "code", code, "from report")
        embed = discord.Embed(
            title="⛔ Code suspended (report)",
            color=discord.Colour(0xED4245),
            description=(
                f"**Code:** `{code}`\n"
                f"**Moderator:** {interaction.user.mention}\n"
                f"**Duration:** {duration}"
            ),
        )
        await self.send_mod_log(interaction.guild, embed)
        done = discord.Embed(
            title=f"🚩 Report · `{code}` · handled",
            color=discord.Colour(0x57F287),
            description=f"Suspended for **{duration}** by {interaction.user.mention}.",
        )
        await interaction.response.edit_message(embed=done, view=None)

    # ---------- prefix: suspend / unsuspend ----------

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
    async def suspend(self, ctx, code: str, duration: str = None):
        """Suspend a secret code for a duration (e.g. 30m, 2h, 1w). Staff/mods only."""
        if not (
            is_owner(ctx.author.id)
            or is_dev(ctx.guild.id, ctx.author.id)
            or is_mod(ctx.guild.id, ctx.author.id)
            or is_admin(ctx.author)
        ):
            await ctx.send("Only admins, devs or mods can suspend codes.")
            return
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
        C.update_one(
            {"_id": doc["_id"]},
            {
                "$set": {"suspended_until": until},
                "$push": {
                    "suspend_history": {
                        "action": "suspend",
                        "by": ctx.author.id,
                        "until": until,
                        "at": datetime.now(timezone.utc),
                    }
                },
            },
        )
        audit(ctx.guild.id, ctx.author.id, "code_suspend", "code", code)
        embed = discord.Embed(
            title="⛔ Code suspended",
            color=discord.Colour(0xED4245),
            description=(
                f"**Code:** `{code}` ({doc.get('nickname', '?')})\n"
                f"**Moderator:** {ctx.author.mention}\n"
                f"**Duration:** {duration} (until <t:{int(until.timestamp())}:f>)"
            ),
        )
        await self.send_mod_log(ctx.guild, embed)
        await ctx.send(f"⛔ Suspended code **`{code}`** ({doc.get('nickname')}) for **{duration}**.")

    @commands.command(name="unsuspend")
    async def unsuspend(self, ctx, code: str):
        """Remove a suspension from a secret code. Staff/mods only."""
        if not (
            is_owner(ctx.author.id)
            or is_dev(ctx.guild.id, ctx.author.id)
            or is_mod(ctx.guild.id, ctx.author.id)
            or is_admin(ctx.author)
        ):
            await ctx.send("Only admins, devs or mods can unsuspend codes.")
            return
        code = code.strip().upper()
        res = C.update_one(
            {"code": code},
            {
                "$unset": {"suspended_until": ""},
                "$push": {
                    "suspend_history": {
                        "action": "unsuspend",
                        "by": ctx.author.id,
                        "until": None,
                        "at": datetime.now(timezone.utc),
                    }
                },
            },
        )
        if res.matched_count == 0:
            await ctx.send("No such code in this server.")
            return
        audit(ctx.guild.id, ctx.author.id, "code_unsuspend", "code", code)
        embed = discord.Embed(
            title="✅ Code unsuspended",
            color=discord.Colour(0x57F287),
            description=f"**Code:** `{code}`\n**Moderator:** {ctx.author.mention}",
        )
        await self.send_mod_log(ctx.guild, embed)
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
        if query.strip().isdigit():
            uid = int(query.strip())
            await ctx.send(embed=self._hacks_profile_embed(uid), view=HacksSearchView(self, ctx.author))
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
                name=f"`{d.get('slot', '?')}- {d['code']}` · {d.get('nickname', '?')}",
                value=f"Owner: {self._owner_name(d.get('user_id'))}\nStatus: {status}\nCreated: {d.get('created_at')}",
                inline=False,
            )
        return embed

    def _hacks_profile_embed(self, user_id):
        codes = list(C.find({"user_id": user_id}).sort("created_at", 1))
        total_posts = M.count_documents({"owner_id": user_id})
        posts = list(M.find({"owner_id": user_id}).sort("created_at", -1).limit(5))
        embed = discord.Embed(
            title=f"👤 Hacks profile · <@{user_id}>",
            description=(
                f"**User ID:** `{user_id}`\n"
                f"**Codes:** {len(codes)} · **Secret posts/replies:** {total_posts}"
            ),
            color=discord.Colour(0x9B59B6),
        )
        for d in codes[:15]:
            if self._is_suspended(d):
                until = d.get("suspended_until")
                status = f"⛔ suspended until {until:%Y-%m-%d %H:%M UTC}" if until else "⛔ suspended"
            else:
                status = "✅ active"
            history = d.get("suspend_history", [])
            hist_note = f" · {len(history)} suspension event(s)" if history else ""
            created = d.get("created_at")
            created_note = created.strftime("%Y-%m-%d") if created else "?"
            embed.add_field(
                name=f"`{d.get('slot', '?')}- {d['code']}` · {d.get('nickname', '?')}",
                value=f"{status} · created {created_note}{hist_note}",
                inline=False,
            )
            for h in history[-3:]:
                at = h.get("at")
                at_note = at.strftime("%Y-%m-%d %H:%M") if at else "?"
                until_h = h.get("until")
                until_note = until_h.strftime("%Y-%m-%d %H:%M") if until_h else "-"
                embed.add_field(
                    name="↳ suspension",
                    value=(
                        f"{h.get('action', '?')} by <@{h.get('by', '?')}> "
                        f"at {at_note} UTC (until {until_note})"
                    ),
                    inline=False,
                )
        if not codes:
            embed.add_field(name="Codes", value="None found.", inline=False)
        if posts:
            lines = []
            for p in posts:
                link = _jump_link(p.get("guild_id"), p.get("channel_id"), p.get("message_id"))
                pn = p.get("post_number", "?")
                lines.append(f"[Post #{pn}]({link})")
            embed.add_field(name="Recent posts", value="\n".join(lines), inline=False)
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

    # ---------- slash: /dm ----------

    @app_commands.command(name="dm")
    @app_commands.describe(setting="on = get a DM when someone replies to your posts (default), off = silent")
    @app_commands.choices(
        setting=[
            app_commands.Choice(name="on — DM me on replies", value="on"),
            app_commands.Choice(name="off — don't DM me", value="off"),
        ]
    )
    async def dm(self, interaction, setting: str = None):
        """Choose whether you get a DM when someone replies to your secret posts."""
        uid = interaction.user.id
        currently_on = not bool(US.find_one({"user_id": uid}) and US.find_one({"user_id": uid}).get("nodm"))
        if setting is None:
            state = "🔔 **ON**" if currently_on else "🔕 **OFF**"
            await interaction.response.send_message(
                f"Reply DMs are {state} (default is ON).\n"
                "Use `/dm on` or `/dm off` to change it. `/inbox` always shows who replied.",
                ephemeral=True,
            )
            return
        setting = setting.strip().lower()
        if setting in ("yes", "on", "true"):
            US.update_one({"user_id": uid}, {"$unset": {"nodm": ""}}, upsert=True)
            await interaction.response.send_message(
                "🔔 Reply DMs **ON** — you'll get a DM whenever someone replies to one of your posts.\n"
                "Change anytime with `/dm off` · check history with `/inbox`.",
                ephemeral=True,
            )
        elif setting in ("no", "off", "false"):
            US.update_one({"user_id": uid}, {"$set": {"nodm": True}}, upsert=True)
            await interaction.response.send_message(
                "🔕 Reply DMs **OFF** — no more reply notifications. Replies still land in `/inbox`.\n"
                "Turn back on anytime with `/dm on`.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message("Use `/dm on` or `/dm off`.", ephemeral=True)

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
