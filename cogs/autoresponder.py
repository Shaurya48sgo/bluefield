import asyncio
import random
import time

import discord
from discord.ext import commands

from config import Config
from database import db
from utils import ar_logic as logic
from utils.ar_logic import (
    APPROVE,
    ENDORSEMENT_EMOJIS,
    FLAG,
    HINT,
    REJECT,
)
from utils.checks import dev_only
from utils.helpers import create_embed

RESPONSE_COOLDOWN_SEC = 5
BACKFILL_HISTORY_LIMIT = 20
TEXT_LIMIT = 1500
THREAD_ARCHIVE_MINUTES = 1440

_STATUS_TO_EMOJI = {
    logic.STATUS_APPROVED: APPROVE,
    logic.STATUS_HINT: HINT,
    logic.STATUS_REJECTED: REJECT,
}


async def _is_manager(bot, guild, user_id):
    """Owner / bot-owner / guild-dev check for prefix cmds, buttons and reactions."""
    if guild is None or not user_id:
        return False
    if Config.OWNER_ID and user_id == Config.OWNER_ID:
        return True
    member = guild.get_member(user_id)
    if member is None:
        try:
            member = await guild.fetch_member(user_id)
        except Exception:
            member = None
    if member is not None:
        try:
            if await bot.is_owner(member):
                return True
        except Exception:
            pass
    try:
        return await db.is_dev(guild.id, user_id)
    except Exception:
        return False


def _control_embed(group):
    keywords = group.get("keywords", []) or []
    if keywords:
        kw_lines = [
            f"`{k.get('text', '')}` — {k.get('mode', logic.MODE_WORD).upper()} — "
            f"{'ON' if k.get('enabled') else 'OFF'}"
            for k in keywords
        ]
    else:
        kw_lines = ["_No keywords yet — use ➕ below to add one._"]

    counts = {logic.STATUS_APPROVED: 0, logic.STATUS_HINT: 0,
              logic.STATUS_UNREVIEWED: 0, logic.STATUS_REJECTED: 0}
    for resp in group.get("responses", []) or []:
        counts[resp.get("status", logic.STATUS_UNREVIEWED)] = \
            counts.get(resp.get("status"), 0) + 1

    hint = "ON" if group.get("hint_mode") else "OFF"
    window = group.get("hint_window_min", 5)
    duration = group.get("duration_min")
    duration_txt = f"{duration:g} min" if duration else "—"

    embed = create_embed(
        f"🔑 {group.get('name', 'Autoresponder')}",
        "Type candidate responses in this thread — the bot reacts ✅ ❌ 🟡.\n"
        "A dev/owner endorsement picks each message's status. "
        "Manage keywords with the buttons below.",
        discord.Color.purple(),
    )
    embed.add_field(name="Keywords", value="\n".join(kw_lines)[:1024] or "—", inline=False)
    embed.add_field(
        name="Responses",
        value=(f"✅ Approved: **{counts[logic.STATUS_APPROVED]}**\n"
               f"🟡 Hint pool: **{counts[logic.STATUS_HINT]}**\n"
               f"🚩 Needs review: **{counts[logic.STATUS_UNREVIEWED]}**\n"
               f"❌ Rejected: **{counts[logic.STATUS_REJECTED]}**"),
        inline=True,
    )
    embed.add_field(
        name="Settings",
        value=(f"Hint mode: **{hint}** ({window} min window)\n"
               f"Duration: **{duration_txt}** (stored, no behavior yet)"),
        inline=True,
    )
    return embed


class _KeywordModalBase(discord.ui.Modal):
    def __init__(self, cog, thread_id, title):
        super().__init__(title=title, timeout=300)
        self.cog = cog
        self.thread_id = thread_id

    async def _guard(self, interaction):
        group = self.cog._groups.get(self.thread_id)
        if group is None:
            await interaction.response.send_message(
                "This thread is no longer managed.", ephemeral=True)
            return None
        if not await _is_manager(self.cog.bot, interaction.guild, interaction.user.id):
            await interaction.response.send_message(
                "Only the bot owner/devs can manage keywords.", ephemeral=True)
            return None
        return group

    def _find(self, group, text):
        key = logic.name_key(text)
        for kw in group.get("keywords", []) or []:
            if logic.name_key(kw.get("text", "")) == key:
                return kw
        return None


class AddKeywordModal(_KeywordModalBase):
    def __init__(self, cog, thread_id):
        super().__init__(cog, thread_id, title="Add keyword")
        self.kw_input = discord.ui.TextInput(
            label="Keyword", placeholder="meow", max_length=100, required=True)
        self.mode_input = discord.ui.TextInput(
            label="Mode: word or contains", placeholder="word",
            default=logic.MODE_WORD, max_length=8, required=True)
        self.add_item(self.kw_input)
        self.add_item(self.mode_input)

    async def on_submit(self, interaction):
        group = await self._guard(interaction)
        if group is None:
            return
        text = (self.kw_input.value or "").strip()
        mode = (self.mode_input.value or "").strip().lower()
        if not text:
            await interaction.response.send_message("Keyword cannot be empty.", ephemeral=True)
            return
        if mode not in logic.MODES:
            await interaction.response.send_message(
                "Mode must be `word` or `contains`.", ephemeral=True)
            return
        if self._find(group, text):
            await interaction.response.send_message(
                f"`{text}` is already a keyword (matching is case-insensitive).",
                ephemeral=True)
            return
        group.setdefault("keywords", []).append(
            logic.new_keyword(text, mode, self.cog._default_enabled))
        await db.save_ar_group(group)
        await self.cog._refresh_control(self.thread_id)
        state = "ON" if self.cog._default_enabled else "OFF"
        await interaction.response.send_message(
            f"Added `{text}` ({mode.upper()}, {state} by default).",
            ephemeral=True)


class RenameKeywordModal(_KeywordModalBase):
    def __init__(self, cog, thread_id):
        super().__init__(cog, thread_id, title="Rename keyword")
        self.old_input = discord.ui.TextInput(
            label="Current keyword", max_length=100, required=True)
        self.new_input = discord.ui.TextInput(
            label="New keyword", max_length=100, required=True)
        self.add_item(self.old_input)
        self.add_item(self.new_input)

    async def on_submit(self, interaction):
        group = await self._guard(interaction)
        if group is None:
            return
        target = self._find(group, self.old_input.value or "")
        if target is None:
            await interaction.response.send_message("Keyword not found.", ephemeral=True)
            return
        new_text = (self.new_input.value or "").strip()
        if not new_text:
            await interaction.response.send_message("New keyword cannot be empty.", ephemeral=True)
            return
        if self._find(group, new_text):
            await interaction.response.send_message(
                "Another keyword already has that name.", ephemeral=True)
            return
        target["text"] = new_text
        await db.save_ar_group(group)
        await self.cog._refresh_control(self.thread_id)
        await interaction.response.send_message(f"Renamed to `{new_text}`.", ephemeral=True)


class DeleteKeywordModal(_KeywordModalBase):
    def __init__(self, cog, thread_id):
        super().__init__(cog, thread_id, title="Delete keyword")
        self.kw_input = discord.ui.TextInput(
            label="Keyword to delete", max_length=100, required=True)
        self.add_item(self.kw_input)

    async def on_submit(self, interaction):
        group = await self._guard(interaction)
        if group is None:
            return
        target = self._find(group, self.kw_input.value or "")
        if target is None:
            await interaction.response.send_message("Keyword not found.", ephemeral=True)
            return
        group["keywords"] = [k for k in group.get("keywords", []) if k is not target]
        await db.save_ar_group(group)
        await self.cog._refresh_control(self.thread_id)
        await interaction.response.send_message(
            f"Deleted `{target.get('text', '')}`.", ephemeral=True)


class FlipModeModal(_KeywordModalBase):
    def __init__(self, cog, thread_id):
        super().__init__(cog, thread_id, title="Flip match mode")
        self.kw_input = discord.ui.TextInput(
            label="Keyword", max_length=100, required=True)
        self.add_item(self.kw_input)

    async def on_submit(self, interaction):
        group = await self._guard(interaction)
        if group is None:
            return
        target = self._find(group, self.kw_input.value or "")
        if target is None:
            await interaction.response.send_message("Keyword not found.", ephemeral=True)
            return
        target["mode"] = (logic.MODE_CONTAINS
                          if target.get("mode") == logic.MODE_WORD
                          else logic.MODE_WORD)
        await db.save_ar_group(group)
        await self.cog._refresh_control(self.thread_id)
        await interaction.response.send_message(
            f"`{target.get('text', '')}` is now {target['mode'].upper()}.", ephemeral=True)


class ArControlView(discord.ui.View):
    """Single persistent view shared by every group control message.

    The group is resolved from the thread the button was pressed in, so
    custom_ids stay fixed and one add_view() call covers all groups.
    """

    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    async def _guard(self, interaction):
        channel = interaction.channel
        tid = channel.id if channel else None
        group = self.cog._groups.get(tid)
        if group is None:
            await interaction.response.send_message(
                "This thread is no longer managed.", ephemeral=True)
            return None
        if not await _is_manager(self.cog.bot, interaction.guild, interaction.user.id):
            await interaction.response.send_message(
                "Only the bot owner/devs can manage keywords.", ephemeral=True)
            return None
        return tid

    @discord.ui.button(label="Add keyword", emoji="➕",
                       style=discord.ButtonStyle.green, custom_id="ark:add")
    async def add_kw(self, interaction, button):
        tid = await self._guard(interaction)
        if tid is not None:
            await interaction.response.send_modal(AddKeywordModal(self.cog, tid))

    @discord.ui.button(label="Rename", emoji="✏️",
                       style=discord.ButtonStyle.blurple, custom_id="ark:rename")
    async def rename_kw(self, interaction, button):
        tid = await self._guard(interaction)
        if tid is not None:
            await interaction.response.send_modal(RenameKeywordModal(self.cog, tid))

    @discord.ui.button(label="Delete", emoji="🗑️",
                       style=discord.ButtonStyle.red, custom_id="ark:del")
    async def delete_kw(self, interaction, button):
        tid = await self._guard(interaction)
        if tid is not None:
            await interaction.response.send_modal(DeleteKeywordModal(self.cog, tid))

    @discord.ui.button(label="Flip mode", emoji="🔀",
                       style=discord.ButtonStyle.grey, custom_id="ark:flip")
    async def flip_kw(self, interaction, button):
        tid = await self._guard(interaction)
        if tid is not None:
            await interaction.response.send_modal(FlipModeModal(self.cog, tid))


class AutoresponderCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._groups = {}
        self._order = []
        self._storage_guild_id = None
        self._storage_channel_id = None
        self._default_enabled = False
        self._locks = {}
        self._cooldowns = {}

    async def cog_load(self):
        await self._reload()
        self.bot.add_view(ArControlView(self))
        await self._backfill()

    # ---------- cache / storage helpers ----------

    async def _reload(self):
        try:
            cfg = await db.get_ar_config()
        except Exception:
            cfg = None
        if cfg:
            self._storage_guild_id = cfg.get("storage_guild_id")
            self._storage_channel_id = cfg.get("storage_channel_id")
            self._default_enabled = bool(cfg.get("default_enabled", False))
        try:
            groups = await db.list_ar_groups()
        except Exception:
            groups = []
        self._groups = {g["thread_id"]: g for g in groups if g.get("thread_id")}
        self._order = [g["thread_id"] for g in groups if g.get("thread_id")]

    def _lock_for(self, thread_id):
        lock = self._locks.get(thread_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[thread_id] = lock
        return lock

    async def _get_thread(self, thread_id):
        channel = self.bot.get_channel(thread_id)
        if channel is not None:
            return channel
        try:
            return await self.bot.fetch_channel(thread_id)
        except Exception:
            return None

    async def _ack(self, thread_id, text):
        """Short-lived visible acknowledgment; threads stay clean."""
        try:
            thread = await self._get_thread(thread_id)
            if thread is None:
                return
            try:
                if getattr(thread, "archived", False):
                    await thread.edit(archived=False)
            except Exception:
                pass
            await thread.send(text, delete_after=6)
        except Exception:
            pass

    async def _refresh_control(self, thread_id):
        """Re-render the control message; re-create it if deleted."""
        group = await db.get_ar_group_by_thread(thread_id)
        if group is None:
            self._groups.pop(thread_id, None)
            return
        self._groups[thread_id] = group
        thread = await self._get_thread(thread_id)
        if thread is None:
            return
        try:
            if getattr(thread, "archived", False):
                await thread.edit(archived=False)
        except Exception:
            pass
        embed = _control_embed(group)
        view = ArControlView(self)
        control_id = group.get("control_msg_id")
        if control_id:
            try:
                msg = await thread.fetch_message(control_id)
                await msg.edit(embed=embed, view=view)
                return
            except discord.NotFound:
                pass
            except Exception:
                return
        try:
            sent = await thread.send(embed=embed, view=view)
            group["control_msg_id"] = sent.id
            await db.save_ar_group(group)
            self._groups[thread_id] = group
        except Exception:
            pass

    async def _backfill(self):
        """React to + track recent untracked thread messages (e.g. sent while offline)."""
        for tid in self._order:
            try:
                await self._backfill_thread(tid)
            except Exception:
                continue

    async def _backfill_thread(self, thread_id):
        group = self._groups.get(thread_id)
        if group is None:
            return
        thread = await self._get_thread(thread_id)
        if thread is None:
            return
        tracked = {r.get("msg_id") for r in group.get("responses", []) or []}
        changed = False
        try:
            history = [m async for m in thread.history(limit=BACKFILL_HISTORY_LIMIT)]
        except Exception:
            return
        for msg in history:
            if msg.author.bot or msg.id == group.get("control_msg_id"):
                continue
            if msg.id in tracked:
                continue
            for emo in ENDORSEMENT_EMOJIS:
                try:
                    await msg.add_reaction(emo)
                except Exception:
                    pass
            group.setdefault("responses", []).append({
                "msg_id": msg.id,
                "text": (msg.content or "")[:TEXT_LIMIT],
                "status": logic.STATUS_UNREVIEWED,
            })
            changed = True
        if changed:
            try:
                await db.save_ar_group(group)
            except Exception:
                pass

    # ---------- prefix commands (dev/owner only) ----------

    @commands.command(name="keysetup")
    @dev_only()
    async def keysetup(self, ctx, channel: str = None):
        """Set the storage channel holding all group threads. Usage: I?keysetup [#channel]"""
        target = None
        if channel:
            cleaned = channel.strip()
            if cleaned.startswith("<#") and cleaned.endswith(">"):
                cleaned = cleaned[2:-1]
            try:
                target = ctx.guild.get_channel(int(cleaned))
            except (ValueError, AttributeError):
                target = None
            if target is None:
                await ctx.send(embed=create_embed(
                    "Error", "Pass a channel mention or ID, or run this inside the channel.",
                    discord.Color.red()))
                return
        else:
            target = ctx.channel
        if not isinstance(target, discord.TextChannel):
            await ctx.send(embed=create_embed(
                "Error", "Storage must be a regular text channel (threads hang off it).",
                discord.Color.red()))
            return
        await db.set_ar_config({
            "storage_guild_id": ctx.guild.id,
            "storage_channel_id": target.id,
        })
        await self._reload()
        await ctx.send(embed=create_embed(
            "Storage Set",
            f"Group threads will live in {target.mention}.\n\n"
            "Bot needs there: View Channel, Send Messages, Create Public Threads, "
            "Send in Threads, Add Reactions, Read History, Manage Messages.",
            discord.Color.green()))

    @commands.command(name="keymake")
    @dev_only()
    async def keymake(self, ctx, name: str, duration: str = None):
        """Create a keyword group + thread. Usage: I?keymake "Cat Words" [5m]"""
        if not self._storage_guild_id or not self._storage_channel_id:
            await ctx.send(embed=create_embed(
                "Error", "No storage channel set. Run `I?keysetup` first.",
                discord.Color.red()))
            return
        clean = (name or "").strip()
        if not clean or len(clean) > 80:
            await ctx.send(embed=create_embed(
                "Error", "Group name must be 1–80 characters.", discord.Color.red()))
            return
        try:
            existing = await db.get_ar_group_by_name(clean)
        except Exception:
            existing = None
        if existing:
            await ctx.send(embed=create_embed(
                "Error", f"A group named **{existing.get('name')}** already exists.",
                discord.Color.red()))
            return
        duration_min = None
        if duration is not None:
            duration_min = logic.parse_duration(duration)
            if duration_min is None:
                await ctx.send(embed=create_embed(
                    "Error", "Duration looks like `5m`, `1h`, `2d`, `30s` (bare number = minutes).",
                    discord.Color.red()))
                return
        guild = self.bot.get_guild(self._storage_guild_id)
        if guild is None:
            await ctx.send(embed=create_embed(
                "Error", "I'm no longer in the storage server. Re-run `I?keysetup`.",
                discord.Color.red()))
            return
        channel = guild.get_channel(self._storage_channel_id)
        if not isinstance(channel, discord.TextChannel):
            await ctx.send(embed=create_embed(
                "Error", "Storage channel is gone. Re-run `I?keysetup`.",
                discord.Color.red()))
            return
        try:
            thread = await channel.create_thread(
                name=clean[:100],
                auto_archive_duration=THREAD_ARCHIVE_MINUTES,
                type=discord.ChannelType.public_thread,
                reason=f"Autoresponder group by {ctx.author}",
            )
        except discord.Forbidden:
            await ctx.send(embed=create_embed(
                "Error", "Missing permission: Create Public Threads in the storage channel.",
                discord.Color.red()))
            return
        except Exception as exc:
            await ctx.send(embed=create_embed(
                "Error", f"Could not create the thread: {exc}", discord.Color.red()))
            return
        doc = {
            "name": clean,
            "storage_channel_id": channel.id,
            "thread_id": thread.id,
            "control_msg_id": None,
            "keywords": [],
            "responses": [],
            "hint_mode": False,
            "hint_window_min": 5,
            "duration_min": duration_min,
            "last_triggered": None,
        }
        try:
            await db.insert_ar_group(doc)
        except Exception as exc:
            await ctx.send(embed=create_embed(
                "Error", f"Thread created but saving failed: {exc}", discord.Color.red()))
            return
        await self._reload()
        await self._refresh_control(thread.id)
        dur_txt = f" ({duration_min:g} min stored)" if duration_min else ""
        await ctx.send(embed=create_embed(
            "Group Created",
            f"**{clean}**{dur_txt} → <#{thread.id}>\n"
            "Add keywords with the ➕ button in the thread, type response candidates "
            "there, then enable with `I?keyword`.",
            discord.Color.green()))

    @commands.command(name="keydelete")
    @dev_only()
    async def keydelete(self, ctx, *, name: str = None):
        """Delete a group + its thread. Usage: I?keydelete "Cat Words\""""
        if not name or not name.strip():
            await ctx.send(embed=create_embed(
                "Error", "Usage: `I?keydelete \"Group Name\"`", discord.Color.red()))
            return
        try:
            group = await db.get_ar_group_by_name(name)
        except Exception:
            group = None
        if group is None:
            await ctx.send(embed=create_embed(
                "Error", "No group with that name.", discord.Color.red()))
            return
        thread = await self._get_thread(group["thread_id"])
        if thread is not None:
            try:
                await thread.delete()
            except Exception:
                pass
        try:
            await db.delete_ar_group(group["thread_id"])
        except Exception:
            pass
        await self._reload()
        await ctx.send(embed=create_embed(
            "Group Deleted", f"**{group.get('name')}** and its thread are gone.",
            discord.Color.orange()))

    @commands.command(name="keyword")
    @dev_only()
    async def keyword(self, ctx, target: str = None, flag: str = None):
        """Enable/disable keywords. Usage: I?keyword all -y | I?keyword "Cat Words" -r | I?keyword meow -r"""
        if not target or (flag or "").lower() not in ("-y", "-r"):
            await ctx.send(embed=create_embed(
                "Error", "Usage: `I?keyword all -y|-r`, `I?keyword \"Group Name\" -y|-r`, "
                         "or `I?keyword <keyword> -y|-r`",
                discord.Color.red()))
            return
        enable = flag.lower() == "-y"
        state = "ON" if enable else "OFF"
        try:
            groups = await db.list_ar_groups()
        except Exception:
            groups = []
        kind, hits = logic.resolve_target(groups, target)
        if kind == "all":
            # Global default: everything existing flips AND future keywords inherit it,
            # even when zero groups/keywords exist right now.
            try:
                await db.set_ar_config({"default_enabled": enable})
            except Exception:
                pass
            self._default_enabled = enable
            total = 0
            for group, kws in hits:
                for kw in kws:
                    kw["enabled"] = enable
                    total += 1
                try:
                    await db.save_ar_group(group)
                except Exception:
                    continue
            await self._reload()
            for group, _ in hits:
                await self._refresh_control(group["thread_id"])
            await ctx.send(embed=create_embed(
                "Keywords Updated",
                f"Turned **{state}** {total} keyword(s) in {len(hits)} group(s).\n"
                f"New keywords will also start **{state}**.",
                discord.Color.green()))
            return
        if kind == "none":
            await ctx.send(embed=create_embed(
                "Error", "No group or keyword with that name.", discord.Color.red()))
            return
        total = 0
        for group, kws in hits:
            for kw in kws:
                kw["enabled"] = enable
                total += 1
            try:
                await db.save_ar_group(group)
            except Exception:
                continue
        await self._reload()
        for group, _ in hits:
            await self._refresh_control(group["thread_id"])
        scope = ("every keyword in the group" if kind == "group"
                 else f"keyword in {len(hits)} group(s)")
        await ctx.send(embed=create_embed(
            "Keywords Updated",
            f"Turned **{state}** {total} keyword(s) ({scope}).",
            discord.Color.green()))

    @commands.command(name="keyhint")
    @dev_only()
    async def keyhint(self, ctx, name: str = None, flag: str = None, window: str = None):
        """Toggle hint mode. Usage: I?keyhint "Cat Words" -y [5m]"""
        if not name or (flag or "").lower() not in ("-y", "-r"):
            await ctx.send(embed=create_embed(
                "Error", "Usage: `I?keyhint \"Group Name\" -y|-r [window, e.g. 5m]`",
                discord.Color.red()))
            return
        try:
            group = await db.get_ar_group_by_name(name)
        except Exception:
            group = None
        if group is None:
            await ctx.send(embed=create_embed(
                "Error", "No group with that name.", discord.Color.red()))
            return
        group["hint_mode"] = flag.lower() == "-y"
        if window is not None:
            parsed = logic.parse_duration(window)
            if parsed is None or parsed <= 0:
                await ctx.send(embed=create_embed(
                    "Error", "Window looks like `5m`, `1h` (must be > 0).",
                    discord.Color.red()))
                return
            group["hint_window_min"] = parsed
        try:
            await db.save_ar_group(group)
        except Exception as exc:
            await ctx.send(embed=create_embed(
                "Error", f"Saving failed: {exc}", discord.Color.red()))
            return
        await self._reload()
        await self._refresh_control(group["thread_id"])
        state = "ON" if group["hint_mode"] else "OFF"
        await ctx.send(embed=create_embed(
            "Hint Mode Updated",
            f"**{group.get('name')}**: hint mode **{state}** "
            f"({group.get('hint_window_min', 5):g} min window).",
            discord.Color.green()))

    @commands.command(name="keylist")
    @dev_only()
    async def keylist(self, ctx, *, name: str = None):
        """List groups/keywords. Usage: I?keylist ["Group Name"]"""
        try:
            groups = await db.list_ar_groups()
        except Exception:
            groups = []
        if name and name.strip():
            single = await db.get_ar_group_by_name(name)
            if single is None:
                await ctx.send(embed=create_embed(
                    "Error", "No group with that name.", discord.Color.red()))
                return
            await ctx.send(embed=self._detail_embed(single))
            return
        if not groups:
            await ctx.send(embed=create_embed(
                "Keywords", "No groups yet. Create one with `I?keymake`.",
                discord.Color.blue()))
            return
        # Chunk fields across embeds (25 fields / ~6000 chars per embed).
        embeds, current, count, size = [], None, 0, 0
        for group in groups:
            kws = group.get("keywords", []) or []
            if kws:
                value = "\n".join(
                    f"`{k.get('text', '')}` {k.get('mode', 'word').upper()} "
                    f"{'ON' if k.get('enabled') else 'OFF'}" for k in kws)
            else:
                value = "_no keywords_"
            entry = len(group.get("name", "")) + len(value)
            if current is None or count >= 25 or size + entry > 5000:
                current = create_embed("Keywords", "All groups (names are case-insensitive).",
                                       discord.Color.blue())
                embeds.append(current)
                count, size = 0, 0
            current.add_field(name=f"🔑 {group.get('name')}"
                            + (" (hint ON)" if group.get("hint_mode") else ""),
                              value=value[:1024], inline=False)
            count += 1
            size += entry
        for embed in embeds:
            await ctx.send(embed=embed)

    def _detail_embed(self, group):
        kws = group.get("keywords", []) or []
        kw_txt = ("\n".join(
            f"`{k.get('text', '')}` — {k.get('mode', 'word').upper()} — "
            f"{'ON' if k.get('enabled') else 'OFF'}" for k in kws)
            if kws else "_no keywords_")
        counts = {}
        for resp in group.get("responses", []) or []:
            status = resp.get("status", logic.STATUS_UNREVIEWED)
            counts[status] = counts.get(status, 0) + 1
        embed = create_embed(f"🔑 {group.get('name')}", f"<#{group.get('thread_id')}>",
                             discord.Color.blue())
        embed.add_field(name="Keywords", value=kw_txt[:1024], inline=False)
        embed.add_field(
            name="Responses",
            value=(f"✅ {counts.get(logic.STATUS_APPROVED, 0)} · "
                   f"🟡 {counts.get(logic.STATUS_HINT, 0)} · "
                   f"🚩 {counts.get(logic.STATUS_UNREVIEWED, 0)} · "
                   f"❌ {counts.get(logic.STATUS_REJECTED, 0)}"),
            inline=False)
        duration = group.get("duration_min")
        embed.add_field(
            name="Settings",
            value=(f"Hint mode: **{'ON' if group.get('hint_mode') else 'OFF'}** "
                   f"({group.get('hint_window_min', 5):g} min)\n"
                   f"Duration: **{f'{duration:g} min' if duration else '—'}**"),
            inline=False)
        return embed

    @commands.command(name="keyhelp")
    @dev_only()
    async def keyhelp(self, ctx):
        """Big autoresponder help menu. Usage: I?keyhelp"""
        e1 = create_embed(
            "🔑 Autoresponder Help (1/3) — Groups",
            "**Setup (run once):** `I?keysetup [#channel]` — picks the storage "
            "channel in your personal server that holds every group thread.\n\n"
            "**Create:** `I?keymake \"Cat Words\" [5m]` — makes the thread + control "
            "message. The optional duration is only stored for now (no behavior).\n"
            "**Delete:** `I?keydelete \"Cat Words\"` — removes the group and its thread.\n"
            "**List:** `I?keylist` (all) or `I?keylist \"Cat Words\"` (detail).\n\n"
            "Group names are case-insensitive everywhere.",
            discord.Color.blue())
        e2 = create_embed(
            "🔑 Autoresponder Help (2/3) — Keywords",
            "Keywords are **made and edited only inside the group thread**, via the "
            "buttons on its first message: ➕ add · ✏️ rename · 🗑️ delete · 🔀 flip mode.\n\n"
            "**WORD** (default): `meow` fires on `meow`, not on `meowww`.\n"
            "**CONTAINS**: `meow` also fires on `meowww`, `meowing`, `kmeown`.\n"
            "Matching is always case-insensitive. New keywords inherit the global "
            "default (`I?keyword all -y` makes them start ON, otherwise OFF).\n\n"
            "**Switches:** `I?keyword \"Cat Words\" -y|-r` flips every keyword in the "
            "group; `I?keyword meow -r` flips just that one keyword; "
            "`I?keyword all -y|-r` flips everything and sets the default for "
            "future keywords too.",
            discord.Color.blue())
        e3 = create_embed(
            "🔑 Autoresponder Help (3/3) — Responses & hints",
            "Type response candidates as plain messages **in the group thread**. The bot "
            "reacts ✅ ❌ 🟡 to each; a dev/owner endorsement decides:\n"
            "✅ approved (sent) · ❌ rejected (ignored) · 🟡 hint pool · "
            "🚩 needs review (ignored until endorsed).\n"
            "Only one endorsement counts — the latest wins, others are cleared. "
            "Deleting a thread message removes it from the pool. The bot acknowledges "
            "everything: reactions on new candidates plus short-lived notes for "
            "approvals, removals and edits.\n\n"
            "**Trigger:** an enabled keyword anywhere in a message → one random response "
            "from the pool (first matching group wins, no ping, 5s per-group cooldown). "
            "The storage server itself never triggers.\n\n"
            "**Hint mode:** `I?keyhint \"Cat Words\" -y [5m]` — first trigger after the "
            "window gives a normal response; repeats inside the window give hint-pool "
            "responses.",
            discord.Color.blue())
        for embed in (e1, e2, e3):
            await ctx.send(embed=embed)

    # ---------- trigger engine ----------

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or message.guild is None:
            return
        if not self._groups:
            return
        channel = message.channel
        ch_id = channel.id
        if self._storage_channel_id and (
                ch_id == self._storage_channel_id or ch_id in self._groups):
            await self._ingest_thread_message(message)
            return
        if (isinstance(channel, discord.Thread)
                and channel.parent_id == self._storage_channel_id):
            return  # other threads in the storage channel never trigger
        await self._maybe_trigger(message)

    async def _ingest_thread_message(self, message):
        group = self._groups.get(message.channel.id)
        if group is None or message.id == group.get("control_msg_id"):
            return
        reacted = 0
        for emo in ENDORSEMENT_EMOJIS:
            try:
                await message.add_reaction(emo)
                reacted += 1
            except Exception:
                continue
        if reacted == 0:
            # Reactions are the normal ack — fall back to a note if they failed.
            await self._ack(message.channel.id,
                            "👀 Noted — I'll use this as a response candidate.")
        existing = next((r for r in group.get("responses", []) or []
                         if r.get("msg_id") == message.id), None)
        if existing is not None:
            existing["text"] = (message.content or "")[:TEXT_LIMIT]
        else:
            group.setdefault("responses", []).append({
                "msg_id": message.id,
                "text": (message.content or "")[:TEXT_LIMIT],
                "status": logic.STATUS_UNREVIEWED,
            })
        try:
            await db.save_ar_group(group)
        except Exception:
            return
        await self._refresh_control(message.channel.id)

    async def _maybe_trigger(self, message):
        content = message.content or ""
        if not content.strip():
            return
        if content.strip().lower().startswith(Config.PREFIX.lower()):
            return
        now = time.time()
        for tid in self._order:
            group = self._groups.get(tid)
            if group is None:
                continue
            enabled = [k for k in group.get("keywords", []) or [] if k.get("enabled")]
            if not enabled:
                continue
            if now - self._cooldowns.get(tid, 0) < RESPONSE_COOLDOWN_SEC:
                continue
            hit = any(logic.matches_keyword(content, k.get("text", ""),
                                            k.get("mode", logic.MODE_WORD))
                      for k in enabled)
            if not hit:
                continue
            pool, _ = logic.select_pool(group.get("hint_mode"),
                                        group.get("hint_window_min", 5),
                                        group.get("last_triggered"), now)
            candidates = [r for r in group.get("responses", []) or []
                          if r.get("status") == pool and (r.get("text") or "").strip()]
            if not candidates and pool == logic.STATUS_HINT:
                pool = logic.STATUS_APPROVED
                candidates = [r for r in group.get("responses", []) or []
                              if r.get("status") == pool and (r.get("text") or "").strip()]
            if not candidates:
                continue
            try:
                await message.reply(random.choice(candidates)["text"][:2000],
                                    mention_author=False)
            except discord.HTTPException:
                try:
                    await message.channel.send(
                        random.choice(candidates)["text"][:2000])
                except Exception:
                    return
            except Exception:
                return
            self._cooldowns[tid] = now
            group["last_triggered"] = now
            try:
                await db.save_ar_group(group)
            except Exception:
                pass
            return

    # ---------- reaction curation ----------

    async def _try_remove_user_reaction(self, payload, guild):
        if guild is None:
            return
        member = guild.get_member(payload.user_id)
        if member is None:
            return
        try:
            channel = self.bot.get_channel(payload.channel_id)
            if channel is None:
                channel = await self.bot.fetch_channel(payload.channel_id)
            msg = await channel.fetch_message(payload.message_id)
            await msg.remove_reaction(payload.emoji, member)
        except Exception:
            pass

    async def _leave_only(self, msg, keep_emoji, keeper_id):
        """Enforce a single endorsement: clear other reactions (best effort)."""
        for reaction in msg.reactions:
            emo = str(reaction.emoji)
            if emo not in ENDORSEMENT_EMOJIS and emo != FLAG:
                continue
            try:
                users = [u async for u in reaction.users()]
            except Exception:
                continue
            for user in users:
                if user.bot and emo != FLAG:
                    continue
                if emo == keep_emoji and user.id == keeper_id and not user.bot:
                    continue
                try:
                    await msg.remove_reaction(reaction.emoji, user)
                except Exception:
                    continue

    async def _managed_context(self, payload):
        """Common gate for raw reaction events. Returns (group, guild) or (None, None)."""
        if self.bot.user and payload.user_id == self.bot.user.id:
            return None, None
        if payload.guild_id != self._storage_guild_id:
            return None, None
        group = self._groups.get(payload.channel_id)
        if group is None or payload.message_id == group.get("control_msg_id"):
            return None, None
        return group, self.bot.get_guild(payload.guild_id)

    async def _fetch_tracked_message(self, thread_id, message_id):
        thread = await self._get_thread(thread_id)
        if thread is None:
            return None, None
        try:
            return thread, await thread.fetch_message(message_id)
        except discord.NotFound:
            return thread, None
        except Exception:
            return None, None

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        emo = str(payload.emoji)
        if emo not in ENDORSEMENT_EMOJIS:
            return
        group, guild = await self._managed_context(payload)
        if group is None:
            return
        if guild is None or not await _is_manager(self.bot, guild, payload.user_id):
            await self._try_remove_user_reaction(payload, guild)
            return
        async with self._lock_for(payload.channel_id):
            fresh = await db.get_ar_group_by_thread(payload.channel_id)
            if fresh is None:
                self._groups.pop(payload.channel_id, None)
                return
            self._groups[payload.channel_id] = fresh
            group = fresh
            _, msg = await self._fetch_tracked_message(
                payload.channel_id, payload.message_id)
            if msg is None:
                group["responses"] = [r for r in group.get("responses", []) or []
                                      if r.get("msg_id") != payload.message_id]
                try:
                    await db.save_ar_group(group)
                except Exception:
                    pass
                await self._refresh_control(payload.channel_id)
                return
            resp = next((r for r in group.get("responses", []) or []
                         if r.get("msg_id") == payload.message_id), None)
            if resp is None:
                resp = {"msg_id": payload.message_id, "text": "",
                        "status": logic.STATUS_UNREVIEWED}
                group.setdefault("responses", []).append(resp)
            resp["status"] = logic.EMOJI_TO_STATUS[emo]
            resp["text"] = (msg.content or "")[:TEXT_LIMIT]
            try:
                await db.save_ar_group(group)
            except Exception:
                return
            await self._leave_only(msg, emo, payload.user_id)
            await self._refresh_control(payload.channel_id)
            await self._ack(
                payload.channel_id,
                f"{emo} Marked **{resp['status']}** by <@{payload.user_id}>.")

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload):
        emo = str(payload.emoji)
        if emo not in ENDORSEMENT_EMOJIS:
            return
        group, _ = await self._managed_context(payload)
        if group is None:
            return
        async with self._lock_for(payload.channel_id):
            fresh = await db.get_ar_group_by_thread(payload.channel_id)
            if fresh is None:
                self._groups.pop(payload.channel_id, None)
                return
            self._groups[payload.channel_id] = fresh
            group = fresh
            resp = next((r for r in group.get("responses", []) or []
                         if r.get("msg_id") == payload.message_id), None)
            if resp is None:
                return
            _, msg = await self._fetch_tracked_message(
                payload.channel_id, payload.message_id)
            if msg is None:
                group["responses"] = [r for r in group.get("responses", []) or []
                                      if r.get("msg_id") != payload.message_id]
                try:
                    await db.save_ar_group(group)
                except Exception:
                    pass
                await self._refresh_control(payload.channel_id)
                return
            # Convergent: whatever non-bot endorsements remain decide the status.
            remaining = []
            for reaction in msg.reactions:
                if str(reaction.emoji) not in ENDORSEMENT_EMOJIS:
                    continue
                try:
                    users = [u async for u in reaction.users()]
                except Exception:
                    continue
                if any(not u.bot for u in users):
                    remaining.append(str(reaction.emoji))
            if remaining:
                status = logic.pick_endorsement_status(remaining)
                resp["status"] = status
                try:
                    await db.save_ar_group(group)
                except Exception:
                    return
                await self._leave_only(msg, _STATUS_TO_EMOJI[status], payload.user_id)
                await self._refresh_control(payload.channel_id)
                await self._ack(payload.channel_id,
                                f"ℹ️ Endorsement changed — now **{status}**.")
            else:
                resp["status"] = logic.STATUS_UNREVIEWED
                try:
                    await db.save_ar_group(group)
                except Exception:
                    return
                try:
                    await msg.add_reaction(FLAG)
                except Exception:
                    pass
                await self._refresh_control(payload.channel_id)
                await self._ack(payload.channel_id,
                                "🚩 Endorsement removed — response needs review.")

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload):
        group = self._groups.get(payload.channel_id)
        if group is None or payload.guild_id != self._storage_guild_id:
            return
        if payload.message_id == group.get("control_msg_id"):
            await self._refresh_control(payload.channel_id)  # re-create it
            return
        before = len(group.get("responses", []) or [])
        group["responses"] = [r for r in group.get("responses", []) or []
                              if r.get("msg_id") != payload.message_id]
        if len(group["responses"]) == before:
            return
        try:
            await db.save_ar_group(group)
        except Exception:
            return
        await self._refresh_control(payload.channel_id)
        await self._ack(payload.channel_id, "🗑️ Removed from responses (message deleted).")

    @commands.Cog.listener()
    async def on_raw_message_edit(self, payload):
        group = self._groups.get(payload.channel_id)
        if group is None or payload.guild_id != self._storage_guild_id:
            return
        if payload.message_id == group.get("control_msg_id"):
            return
        resp = next((r for r in group.get("responses", []) or []
                     if r.get("msg_id") == payload.message_id), None)
        if resp is None:
            return
        _, msg = await self._fetch_tracked_message(
            payload.channel_id, payload.message_id)
        if msg is None:
            return
        resp["text"] = (msg.content or "")[:TEXT_LIMIT]
        try:
            await db.save_ar_group(group)
        except Exception:
            pass
        await self._ack(payload.channel_id, "✏️ Response text updated.")


async def setup(bot):
    await bot.add_cog(AutoresponderCog(bot))
