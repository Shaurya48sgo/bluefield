import asyncio
import re
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands
from bson import ObjectId

from cogs.common import (
    AS,
    AL,
    BL,
    P,
    S,
    CANJOIN_CHOICES,
    CANPING_CHOICES,
    audit,
    get_guild_settings,
    has_admin_or_dev,
    is_admin,
    is_blacklisted,
    is_privileged,
    parse_mentions,
)

MAX_FREE_ROLES = 3
SUMMON_COOLDOWN = 60


def _mention(uid):
    return f"<@{uid}>"


class MembersButton(discord.ui.View):
    def __init__(self, cog, guild_id, summon_id):
        super().__init__(timeout=180)
        self.cog = cog
        self.guild_id = guild_id
        self.summon_id = summon_id

    @discord.ui.button(label="👥 Members", style=discord.ButtonStyle.secondary)
    async def members_button(self, interaction, button):
        doc = S.find_one({"_id": ObjectId(self.summon_id), "guild_id": self.guild_id})
        if not doc:
            await interaction.response.send_message("This summon no longer exists.")
            return
        guild = self.cog.bot.get_guild(self.guild_id)
        embed = discord.Embed(
            title=f"👥 Members of **{doc['name']}**",
            color=discord.Colour(doc.get("color", 0)),
        )
        member_ids = doc.get("members", [])
        names = []
        for uid in member_ids:
            m = guild.get_member(uid) if guild else None
            names.append(m.mention if m else _mention(uid))
        embed.description = (" ".join(names)) if names else "No members yet."
        embed.set_footer(text=f"{len(member_ids)} members")
        await interaction.response.send_message(embed=embed, ephemeral=True)


class EasyJoinView(discord.ui.View):
    def __init__(self, cog, summon_id):
        super().__init__(timeout=None)
        self.cog = cog
        self.summon_id = str(summon_id)

    @discord.ui.button(label="✅ Join", style=discord.ButtonStyle.success, custom_id="easyjoin_join")
    async def join_button(self, interaction, button):
        await self.cog.easyjoin_toggle(interaction, self.summon_id, join=True)

    @discord.ui.button(label="❌ Leave", style=discord.ButtonStyle.danger, custom_id="easyjoin_leave")
    async def leave_button(self, interaction, button):
        await self.cog.easyjoin_toggle(interaction, self.summon_id, join=False)

    @discord.ui.button(label="⏹️ Expire", style=discord.ButtonStyle.secondary, custom_id="easyjoin_expire")
    async def expire_button(self, interaction, button):
        await self.cog.easyjoin_expire(interaction, self.summon_id)


class RenameModal(discord.ui.Modal):
    def __init__(self, cog, guild_id, summon_id):
        super().__init__(title="Rename summon")
        self.cog = cog
        self.guild_id = guild_id
        self.summon_id = summon_id
        self.name_input = discord.ui.TextInput(label="New name", max_length=100)
        self.add_item(self.name_input)

    async def on_submit(self, interaction):
        name = self.name_input.value.strip()
        if not name:
            await interaction.response.send_message("Name cannot be empty.")
            return
        if await self.cog._name_taken(self.guild_id, name, exclude=self.summon_id):
            await interaction.response.send_message(
                "A summon with that name already exists. Choose a different name."
            )
            return
        S.update_one({"_id": ObjectId(self.summon_id)}, {"$set": {"name": name}})
        doc = S.find_one({"_id": ObjectId(self.summon_id)})
        if doc and doc.get("real_role_id"):
            role = interaction.guild.get_role(doc["real_role_id"])
            if role:
                try:
                    await role.edit(name=name)
                except Exception:
                    pass
        audit(self.guild_id, interaction.user.id, "rename", "summon", self.summon_id, f"renamed to {name}")
        await interaction.response.send_message(f"✅ Renamed to **{name}**!")
        await self.cog.refresh_member_embed(interaction.guild, S.find_one({"_id": ObjectId(self.summon_id)}))
        await self.cog._edit_refresh(interaction, self.summon_id)


class EditSummonView(discord.ui.View):
    def __init__(self, cog, guild_id, summon_id):
        super().__init__(timeout=300)
        self.cog = cog
        self.guild_id = guild_id
        self.summon_id = summon_id
        doc = S.find_one({"_id": ObjectId(summon_id), "guild_id": guild_id}) or {}

        self.canjoin_select = discord.ui.Select(
            placeholder="Who can join?",
            options=[
                discord.SelectOption(label="Anyone", value="anyone", default=doc.get("canjoin", "anyone") == "anyone"),
                discord.SelectOption(label="Invited only", value="invited", default=doc.get("canjoin") == "invited"),
            ],
        )
        self.canping_select = discord.ui.Select(
            placeholder="Who can ping?",
            options=[
                discord.SelectOption(label="Anyone who joined", value="anyone_joined", default=doc.get("canping", "anyone_joined") == "anyone_joined"),
                discord.SelectOption(label="Chosen people/roles", value="chosen", default=doc.get("canping") == "chosen"),
            ],
        )
        self.canjoin_select.callback = self.on_canjoin
        self.canping_select.callback = self.on_canping
        self.add_item(self.canjoin_select)
        self.add_item(self.canping_select)
        if doc.get("canping") != "chosen":
            self.remove_item(self.add_pingers_button)
            self.remove_item(self.remove_pingers_button)
        if doc.get("canjoin") != "invited":
            self.remove_item(self.add_inviters_button)
            self.remove_item(self.remove_inviters_button)

    @discord.ui.button(label="✏️ Rename", style=discord.ButtonStyle.primary)
    async def rename_button(self, interaction, button):
        await interaction.response.send_modal(RenameModal(self.cog, self.guild_id, self.summon_id))

    @discord.ui.button(label="🔔 Add pingers", style=discord.ButtonStyle.secondary)
    async def add_pingers_button(self, interaction, button):
        await self.cog.ask_mentions(interaction, self.guild_id, self.summon_id, "ping", "ping_ids", "ping_types")

    @discord.ui.button(label="🗑️ Remove pingers", style=discord.ButtonStyle.danger)
    async def remove_pingers_button(self, interaction, button):
        await self.cog.ask_mentions(interaction, self.guild_id, self.summon_id, "ping", "ping_ids", "ping_types", remove=True)

    @discord.ui.button(label="🤝 Add inviters", style=discord.ButtonStyle.secondary)
    async def add_inviters_button(self, interaction, button):
        await self.cog.ask_mentions(interaction, self.guild_id, self.summon_id, "invite", "invite_ids", "invite_types")

    @discord.ui.button(label="🗑️ Remove inviters", style=discord.ButtonStyle.danger)
    async def remove_inviters_button(self, interaction, button):
        await self.cog.ask_mentions(interaction, self.guild_id, self.summon_id, "invite", "invite_ids", "invite_types", remove=True)

    async def interaction_check(self, interaction):
        doc = S.find_one({"_id": ObjectId(self.summon_id), "guild_id": self.guild_id})
        if not doc:
            await interaction.response.send_message("This summon no longer exists.")
            return False
        uid = interaction.user.id
        allowed = is_admin(interaction.user) or doc.get("creator_id") == uid or uid in doc.get("co_owner_ids", [])
        if not allowed:
            await interaction.response.send_message(
                "Only the owner, co-owners, and admins can edit this."
            )
            return False
        return True

    async def _refresh(self, interaction, text):
        await interaction.response.edit_message(
            content=text,
            embed=self.cog._edit_embed(interaction.guild, S.find_one({"_id": ObjectId(self.summon_id)})),
            view=EditSummonView(self.cog, self.guild_id, self.summon_id),
        )

    async def on_canjoin(self, interaction):
        value = self.canjoin_select.values[0]
        S.update_one({"_id": ObjectId(self.summon_id)}, {"$set": {"canjoin": value}})
        audit(self.guild_id, interaction.user.id, "edit", "summon", self.summon_id, f"canjoin -> {value}")
        if value != "anyone":
            await self.cog.close_easyjoin_panels(interaction.guild, self.summon_id)
        await self._refresh(interaction, f"✅ Join setting updated to **{value}**.")

    async def on_canping(self, interaction):
        value = self.canping_select.values[0]
        S.update_one({"_id": ObjectId(self.summon_id)}, {"$set": {"canping": value}})
        audit(self.guild_id, interaction.user.id, "edit", "summon", self.summon_id, f"canping -> {value}")
        await self._refresh(interaction, f"✅ Ping setting updated to **{value}**.")


class DeleteConfirmView(discord.ui.View):
    def __init__(self, cog, author, summon):
        super().__init__(timeout=120)
        self.cog = cog
        self.author = author
        self.summon = summon

    async def interaction_check(self, interaction):
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("Only the requester can confirm this.")
            return False
        return True

    async def _remove(self, interaction):
        doc = S.find_one({"_id": self.summon["_id"]})
        if not doc:
            await interaction.response.send_message("This summon no longer exists.")
            return
        embed_id = doc.get("member_embed_id")
        try:
            S.delete_one({"_id": self.summon["_id"]})
        except Exception as e:
            await interaction.response.send_message(f"Failed to remove from MongoDB: {e}")
            return
        msg = f"🗑️ Deleted summon **{self.summon.get('name')}**."
        if doc.get("real_role_id"):
            role = interaction.guild.get_role(doc["real_role_id"])
            if role:
                try:
                    await role.delete(reason=f"Summon deleted by {interaction.user}")
                    msg += " The Discord role was also deleted."
                except discord.Forbidden:
                    msg += " (Couldn't delete the Discord role: missing Manage Roles permission.)"
        audit(interaction.guild.id, interaction.user.id, "delete", "summon", str(self.summon["_id"]), self.summon.get("name"))
        await interaction.response.send_message(msg)
        await self.cog.delete_member_embed(interaction.guild, embed_id)
        await self.cog.close_easyjoin_panels(interaction.guild, str(self.summon["_id"]))

    @discord.ui.button(label="Yes, delete", style=discord.ButtonStyle.danger)
    async def yes_button(self, interaction, button):
        await self._remove(interaction)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def no_button(self, interaction, button):
        await interaction.response.send_message("Deletion cancelled.")


class SummonsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.last_summon = {}

    # ---------- helpers ----------

    def _name_taken(self, guild_id, name, exclude=None):
        query = {
            "guild_id": guild_id,
            "name": {"$regex": f"^{re.escape(name.strip())}$", "$options": "i"},
        }
        if exclude:
            try:
                query["_id"] = {"$ne": ObjectId(exclude)}
            except Exception:
                pass
        return S.find_one(query) is not None

    def _resolve(self, guild_id, value):
        value = value.strip()
        try:
            doc = S.find_one({"_id": ObjectId(value), "guild_id": guild_id})
            if doc:
                return doc
        except Exception:
            pass
        return S.find_one(
            {"guild_id": guild_id, "name": {"$regex": f"^{re.escape(value)}$", "$options": "i"}}
        )

    def _can_summon(self, member, doc):
        if is_privileged(member):
            return True
        if doc.get("enabled", True) is False:
            return False
        if doc.get("canping") == "anyone_joined":
            return member.id in doc.get("members", [])
        for rid, rtype in zip(doc.get("ping_ids", []), doc.get("ping_types", [])):
            if rtype == "user" and member.id == rid:
                return True
            if rtype == "role":
                r = member.guild.get_role(rid)
                if r and r in member.roles:
                    return True
        return False

    def _can_invite(self, member, doc):
        if is_privileged(member):
            return True
        if doc.get("creator_id") == member.id or member.id in doc.get("co_owner_ids", []):
            return True
        for rid, rtype in zip(doc.get("invite_ids", []), doc.get("invite_types", [])):
            if rtype == "user" and member.id == rid:
                return True
            if rtype == "role":
                r = member.guild.get_role(rid)
                if r and r in member.roles:
                    return True
        return False

    def _can_manage(self, member, doc):
        return is_privileged(member) or doc.get("creator_id") == member.id

    def _count_created(self, guild_id, creator_id):
        return S.count_documents({"guild_id": guild_id, "creator_id": creator_id})

    async def log_activity(self, guild, text):
        settings = get_guild_settings(guild.id)
        cid = settings.get("activity_log_channel_id")
        if not cid:
            return
        channel = guild.get_channel(cid)
        if channel:
            try:
                await channel.send(text)
            except Exception:
                pass

    async def delete_member_embed(self, guild, embed_id):
        if not embed_id:
            return
        settings = get_guild_settings(guild.id)
        cid = settings.get("member_log_channel_id")
        if not cid:
            return
        channel = guild.get_channel(cid)
        if not channel:
            return
        try:
            msg = await channel.fetch_message(embed_id)
            await msg.delete()
        except Exception:
            pass

    async def _edit_refresh(self, interaction, summon_id):
        try:
            doc = S.find_one({"_id": ObjectId(summon_id), "guild_id": interaction.guild.id})
            if not doc:
                return
            await interaction.edit_original_response(
                embed=self._edit_embed(interaction.guild, doc),
                view=EditSummonView(self, interaction.guild.id, summon_id),
            )
        except Exception:
            pass

    def _edit_embed(self, guild, doc):
        embed = discord.Embed(
            title=f"⚙️ Edit summon — {doc['name']}",
            color=discord.Colour(doc.get("color", 0)),
        )
        member_ids = doc.get("members", [])
        lines = []
        for uid in member_ids:
            m = guild.get_member(uid)
            name = (m.mention if m else _mention(uid))
            flags = self._member_flags(guild, doc, uid)
            lines.append(f"{flags} {name}")
        embed.add_field(name=f"👥 Members ({len(member_ids)})", value="\n".join(lines) or "No members yet.", inline=False)
        if doc.get("canping") == "chosen":
            pings = self._format_ids(guild, doc.get("ping_ids", []), doc.get("ping_types", []))
            embed.add_field(name="🔔 Chosen pingers", value=pings, inline=True)
        else:
            embed.add_field(name="🔔 Can ping", value="Anyone who joined", inline=True)
        if doc.get("canjoin") == "invited":
            invits = self._format_ids(guild, doc.get("invite_ids", []), doc.get("invite_types", []))
            embed.add_field(name="🤝 Can invite", value=invits, inline=True)
        else:
            embed.add_field(name="🚪 Can join", value="Anyone", inline=True)
        embed.set_footer(text="👑 owner · 🔑 co-owner · 📢 can ping · 🤝 can invite")
        return embed

    def _member_flags(self, guild, doc, uid):
        flags = ""
        if doc.get("creator_id") == uid:
            flags += "👑 "
        if uid in doc.get("co_owner_ids", []):
            flags += "🔑 "
        member = guild.get_member(uid)
        if member and self._can_summon(member, doc):
            flags += "📢 "
        if member and self._can_invite(member, doc):
            flags += "🤝 "
        return flags.strip()

    def _format_ids(self, guild, ids, types):
        parts = []
        for rid, rtype in zip(ids, types):
            if rtype == "user":
                m = guild.get_member(rid)
                parts.append(m.mention if m else _mention(rid))
            else:
                r = guild.get_role(rid)
                parts.append(r.mention if r else f"<@&{rid}>")
        return " ".join(parts) if parts else "none"

    async def refresh_member_embed(self, guild, doc):
        settings = get_guild_settings(guild.id)
        cid = settings.get("member_log_channel_id")
        if not cid:
            return
        channel = guild.get_channel(cid)
        if not channel:
            return
        embed = discord.Embed(
            title=f"👥 {doc['name']}",
            color=discord.Colour(doc.get("color", 0)),
        )
        member_ids = doc.get("members", [])
        names = []
        for uid in member_ids:
            m = guild.get_member(uid)
            names.append(m.mention if m else _mention(uid))
        embed.description = (" ".join(names)) if names else "No members yet."
        embed.set_footer(text=f"{len(member_ids)} members")
        try:
            if doc.get("member_embed_id"):
                try:
                    msg = await channel.fetch_message(doc["member_embed_id"])
                    await msg.edit(embed=embed)
                    return
                except Exception:
                    pass
            msg = await channel.send(embed=embed)
            S.update_one({"_id": doc["_id"]}, {"$set": {"member_embed_id": msg.id}})
        except Exception:
            pass

    async def ask_mentions(self, interaction, guild_id, summon_id, what, field_ids, field_types, remove=False):
        verb = "remove from" if remove else "add to"
        await interaction.response.send_message(
            f"Mention the users/roles to **{verb}** the **{what}ers** (e.g. `@User1 @Role1`):"
        )
        try:
            msg = await self.bot.wait_for(
                "message",
                check=lambda m: m.author.id == interaction.user.id and m.channel.id == interaction.channel.id,
                timeout=120,
            )
        except asyncio.TimeoutError:
            await interaction.followup.send("Timed out. Click the button again.")
            return
        ids, types = parse_mentions(msg.guild, msg.content)
        if not ids:
            await interaction.followup.send("No valid mentions found.")
            return
        try:
            if remove:
                doc = S.find_one({"_id": ObjectId(summon_id)}) or {}
                cur_ids = doc.get(field_ids, [])
                cur_types = doc.get(field_types, [])
                rem = set(zip(ids, types))
                kept = [(i, t) for i, t in zip(cur_ids, cur_types) if (i, t) not in rem]
                S.update_one(
                    {"_id": ObjectId(summon_id)},
                    {"$set": {field_ids: [i for i, _ in kept], field_types: [t for _, t in kept]}},
                )
            else:
                doc = S.find_one({"_id": ObjectId(summon_id)}) or {}
                cur_ids = doc.get(field_ids, [])
                cur_types = doc.get(field_types, [])
                existing = set(zip(cur_ids, cur_types))
                for i, t in zip(ids, types):
                    if (i, t) not in existing:
                        cur_ids.append(i)
                        cur_types.append(t)
                S.update_one(
                    {"_id": ObjectId(summon_id)},
                    {"$set": {field_ids: cur_ids, field_types: cur_types}},
                )
        except Exception as e:
            await interaction.followup.send(f"Failed to save: {e}")
            return
        try:
            await msg.delete()
        except discord.HTTPException:
            pass
        audit(guild_id, interaction.user.id, "edit", "summon", summon_id, f"{verb} {len(ids)} {what}ers")
        await interaction.followup.send(f"✅ {verb.title()} {len(ids)} {what}ers.")
        await self._edit_refresh(interaction, summon_id)

    # ---------- autocomplete ----------

    async def summon_autocomplete(self, interaction, current):
        docs = S.find({"guild_id": interaction.guild.id, "enabled": True})
        out = []
        for d in docs:
            if current.lower() in d["name"].lower():
                out.append(app_commands.Choice(name=d["name"], value=str(d["_id"])))
            if len(out) >= 25:
                break
        return out

    async def invite_autocomplete(self, interaction, current):
        docs = S.find({"guild_id": interaction.guild.id, "enabled": True, "canjoin": "invited"})
        out = []
        for d in docs:
            if current.lower() in d["name"].lower():
                out.append(app_commands.Choice(name=d["name"], value=str(d["_id"])))
            if len(out) >= 25:
                break
        return out

    async def easyjoin_autocomplete(self, interaction, current):
        docs = S.find({"guild_id": interaction.guild.id, "enabled": True, "canjoin": "anyone"})
        out = []
        for d in docs:
            if current.lower() in d["name"].lower():
                out.append(app_commands.Choice(name=d["name"], value=str(d["_id"])))
            if len(out) >= 25:
                break
        return out

    # ---------- slash: group easyjoin ----------

    group = app_commands.Group(name="group", description="Group tools")

    @group.command(name="easyjoin")
    @app_commands.describe(summon="The open-join summon to create buttons for")
    @app_commands.autocomplete(summon=easyjoin_autocomplete)
    async def easyjoin(self, interaction, summon: str):
        """Post a Join/Leave button panel for an open-join summon."""
        doc = self._resolve(interaction.guild.id, summon)
        if not doc:
            await interaction.response.send_message("That summon doesn't exist.")
            return
        if doc.get("canjoin") != "anyone":
            await interaction.response.send_message(
                "Easyjoin only works for summons that anyone can join."
            )
            return
        if not doc.get("enabled", True):
            await interaction.response.send_message("That summon is disabled.")
            return
        view = EasyJoinView(self, str(doc["_id"]))
        msg = await interaction.response.send_message(
            embed=self._easyjoin_embed(interaction.guild, doc),
            view=view,
        )
        P.insert_one(
            {
                "guild_id": interaction.guild.id,
                "summon_id": str(doc["_id"]),
                "channel_id": interaction.channel_id,
                "message_id": msg.id,
                "created_by": interaction.user.id,
                "created_at": datetime.now(timezone.utc),
            }
        )
        audit(interaction.guild.id, interaction.user.id, "easyjoin", "summon", str(doc["_id"]), doc["name"])

    def _easyjoin_embed(self, guild, doc):
        member_count = len([uid for uid in doc.get("members", []) if guild.get_member(uid)])
        embed = discord.Embed(
            title=f"🎮 {doc['name']}",
            description=f"👥 **{member_count}** members\nClick **Join** to join or **Leave** to leave.",
            color=discord.Colour(doc.get("color", 0)),
        )
        return embed

    async def easyjoin_toggle(self, interaction, summon_id, join: bool):
        doc = S.find_one({"_id": ObjectId(summon_id), "guild_id": interaction.guild.id})
        if not doc:
            await interaction.response.send_message("This summon no longer exists.")
            return
        member = interaction.user
        if join:
            if doc.get("canjoin") != "anyone":
                await interaction.response.send_message("This summon is no longer open to join.")
                return
            if member.id in doc.get("banned", []):
                await interaction.response.send_message("You're banned from this summon.")
                return
            if member.id in doc.get("members", []):
                await interaction.response.send_message("You're already in this summon.")
                return
            S.update_one({"_id": doc["_id"]}, {"$addToSet": {"members": member.id}})
            doc = S.find_one({"_id": doc["_id"]})
            if doc.get("real_role_id"):
                role = interaction.guild.get_role(doc["real_role_id"])
                if role:
                    try:
                        await member.add_roles(role, reason=f"Easyjoin {doc['name']}")
                    except Exception:
                        pass
            audit(interaction.guild.id, member.id, "easyjoin_join", "summon", summon_id, doc["name"])
            reply = f"✅ Joined **{doc['name']}**!"
        else:
            if member.id not in doc.get("members", []):
                await interaction.response.send_message("You're not in this summon.")
                return
            S.update_one({"_id": doc["_id"]}, {"$pull": {"members": member.id}})
            doc = S.find_one({"_id": doc["_id"]})
            if doc.get("real_role_id"):
                role = interaction.guild.get_role(doc["real_role_id"])
                if role:
                    try:
                        await member.remove_roles(role, reason=f"Easyleave {doc['name']}")
                    except Exception:
                        pass
            audit(interaction.guild.id, member.id, "easyjoin_leave", "summon", summon_id, doc["name"])
            reply = f"👋 Left **{doc['name']}**!"
        await self.refresh_member_embed(interaction.guild, doc)
        try:
            await self.update_easyjoin_panel(interaction.guild, summon_id)
        except Exception:
            pass
        await interaction.response.send_message(reply, ephemeral=True)

    async def update_easyjoin_panel(self, guild, summon_id):
        panels = list(P.find({"guild_id": guild.id, "summon_id": summon_id}))
        doc = S.find_one({"_id": ObjectId(summon_id)})
        if not doc:
            await self.close_easyjoin_panels(guild, summon_id)
            return
        if doc.get("canjoin") != "anyone":
            await self.close_easyjoin_panels(guild, summon_id)
            return
        embed = self._easyjoin_embed(guild, doc)
        for panel in panels:
            channel = guild.get_channel(panel["channel_id"])
            if not channel:
                continue
            try:
                msg = await channel.fetch_message(panel["message_id"])
                await msg.edit(embed=embed)
            except Exception:
                pass

    async def close_easyjoin_panels(self, guild, summon_id):
        panels = list(P.find({"guild_id": guild.id, "summon_id": summon_id}))
        for panel in panels:
            channel = guild.get_channel(panel["channel_id"])
            if channel:
                try:
                    msg = await channel.fetch_message(panel["message_id"])
                    embed = msg.embeds[0] if msg.embeds else None
                    closed = discord.Embed(
                        title=f"🔒 {embed.title if embed else 'Panel'}",
                        description="This panel is closed — the summon is no longer open to join.",
                        color=discord.Color.dark_grey(),
                    )
                    await msg.edit(embed=closed, view=None)
                except Exception:
                    pass
            P.delete_one({"_id": panel["_id"]})

    async def easyjoin_expire(self, interaction, summon_id):
        doc = S.find_one({"_id": ObjectId(summon_id), "guild_id": interaction.guild.id})
        panel = P.find_one(
            {"guild_id": interaction.guild.id, "summon_id": summon_id, "message_id": interaction.message.id}
        )
        if not panel:
            await interaction.response.send_message("This panel is already closed.")
            return
        allowed = is_privileged(interaction.user)
        if doc:
            allowed = allowed or doc.get("creator_id") == interaction.user.id or interaction.user.id in doc.get("co_owner_ids", [])
        if panel.get("created_by") == interaction.user.id:
            allowed = True
        if not allowed:
            await interaction.response.send_message(
                "Only the summon owner, co-owners, admins, or the panel creator can close this."
            )
            return
        P.delete_one({"_id": panel["_id"]})
        try:
            msg = await interaction.channel.fetch_message(interaction.message.id)
            embed = msg.embeds[0] if msg.embeds else None
            closed = discord.Embed(
                title=f"🔒 {embed.title if embed else 'Panel'}",
                description="This panel was closed by " + interaction.user.mention,
                color=discord.Color.dark_grey(),
            )
            await msg.edit(embed=closed, view=None)
        except Exception:
            pass
        audit(interaction.guild.id, interaction.user.id, "easyjoin_expire", "summon", summon_id)
        await interaction.response.send_message("Panel closed.", ephemeral=True)

    # ---------- slash: summon ----------

    def _can_summon_real(self, member, role):
        if is_privileged(member):
            return True
        doc = AS.find_one({"guild_id": member.guild.id, "role_id": role.id})
        if not doc:
            return False
        if member.id in doc.get("allowed_users", []):
            return True
        for rid in doc.get("allowed_roles", []):
            r = member.guild.get_role(rid)
            if r and r in member.roles:
                return True
        return False

    @app_commands.command(name="summon")
    @app_commands.describe(summon="The summon to ping")
    @app_commands.autocomplete(summon=summon_autocomplete)
    async def summon(self, interaction, summon: str):
        """Ping all members of a summon."""
        doc = self._resolve(interaction.guild.id, summon)
        real_role = None
        if not doc:
            real_role = _resolve_real_role(interaction.guild, summon)
            if real_role is None:
                await interaction.response.send_message("That summon doesn't exist.")
                return
        member = interaction.user
        if is_blacklisted(interaction.guild.id, member.id, member):
            await interaction.response.send_message(
                "You are blacklisted from summon commands."
            )
            return
        if not is_privileged(member):
            now = datetime.now(timezone.utc).timestamp()
            last = self.last_summon.get(member.id, 0)
            if now - last < SUMMON_COOLDOWN:
                await interaction.response.send_message(
                    f"⏳ Please wait {int(SUMMON_COOLDOWN - (now - last))}s before summoning again.",
                )
                return
            if doc:
                allowed = self._can_summon(member, doc)
            else:
                allowed = self._can_summon_real(member, real_role)
            if not allowed:
                await interaction.response.send_message(
                    "You are not allowed to summon this."
                )
                return
            self.last_summon[member.id] = now

        guild = interaction.guild
        allowed = discord.AllowedMentions(users=True, roles=True, everyone=False)
        if real_role is not None:
            member_ids = [m for m in real_role.members if not m.bot]
            bold = f"**[ {real_role.name} ]** has been summoned !"
            await interaction.response.defer()
            if member_ids:
                chunks = [member_ids[i : i + 90] for i in range(0, len(member_ids), 90)]
                msgs = []
                for c in chunks:
                    msg = await interaction.channel.send(
                        " ".join(m.mention for m in c), allowed_mentions=allowed
                    )
                    msgs.append(msg)
                asyncio.create_task(self._cleanup_pings(msgs))
            await interaction.followup.send(bold)
            audit(guild.id, member.id, "summon", "role", real_role.id, real_role.name)
            await self.log_activity(
                guild,
                f"🔔 {member.mention} summoned **{real_role.name}** ({len(member_ids)} members)",
            )
            return

        member_ids = [uid for uid in doc.get("members", []) if guild.get_member(uid)]
        bold = f"**[ {doc['name']} ]** has been summoned !"
        await interaction.response.defer()
        if not member_ids:
            await interaction.followup.send(bold, view=MembersButton(self, guild.id, str(doc["_id"])))
        else:
            chunks = [member_ids[i : i + 90] for i in range(0, len(member_ids), 90)]
            msgs = []
            for c in chunks:
                msg = await interaction.channel.send(
                    " ".join(_mention(uid) for uid in c), allowed_mentions=allowed
                )
                msgs.append(msg)
            await interaction.followup.send(
                bold,
                view=MembersButton(self, guild.id, str(doc["_id"])),
            )
            asyncio.create_task(self._cleanup_pings(msgs))
        audit(guild.id, member.id, "summon", "summon", str(doc["_id"]), doc["name"])
        await self.log_activity(
            guild,
            f"🔔 {member.mention} summoned **{doc['name']}** ({len(member_ids)} members)",
        )

    async def _cleanup_pings(self, msgs):
        await asyncio.sleep(10)
        for m in msgs:
            try:
                await m.delete()
            except Exception:
                pass

    # ---------- slash: create / edit / delete ----------

    create = app_commands.Group(name="create", description="Create things")
    edit = app_commands.Group(name="edit", description="Edit things")
    delete = app_commands.Group(name="delete", description="Delete things")
    list_group = app_commands.Group(name="list", description="List things")

    @create.command(name="summon")
    @app_commands.choices(
        canping=[app_commands.Choice(name=n, value=v) for n, v in CANPING_CHOICES],
        canjoin=[app_commands.Choice(name=n, value=v) for n, v in CANJOIN_CHOICES],
    )
    @app_commands.describe(
        name="Name of the summon",
        canping="Who can ping this summon",
        canjoin="Who can join this summon",
    )
    async def create_summon(self, interaction, name: str, canping: str, canjoin: str):
        """Create a new virtual summon."""
        member = interaction.user
        if is_blacklisted(interaction.guild.id, member.id, member):
            await interaction.response.send_message(
                "You are blacklisted from summon commands."
            )
            return
        name = name.strip()
        if not name:
            await interaction.response.send_message("Name cannot be empty.")
            return
        if self._name_taken(interaction.guild.id, name):
            await interaction.response.send_message(
                "A summon with that name already exists."
            )
            return
        if not is_privileged(member):
            limit = get_guild_settings(interaction.guild.id).get("max_groups_per_member", MAX_FREE_ROLES)
            if self._count_created(interaction.guild.id, member.id) >= limit:
                await interaction.response.send_message(
                    f"You've reached the limit of **{limit}** summons. Admins can create more.",
                )
                return
        doc = {
            "guild_id": interaction.guild.id,
            "name": name,
            "color": discord.Colour.random().value,
            "creator_id": member.id,
            "co_owner_ids": [],
            "members": [member.id],
            "banned": [],
            "enabled": True,
            "canping": canping,
            "ping_ids": [],
            "ping_types": [],
            "canjoin": canjoin,
            "join_ids": [],
            "invite_ids": [],
            "invite_types": [],
            "real_role_id": None,
            "member_embed_id": None,
            "created_at": datetime.now(timezone.utc),
        }
        try:
            res = S.insert_one(doc)
        except Exception as e:
            await interaction.response.send_message(f"Failed to save to MongoDB: {e}")
            return
        audit(interaction.guild.id, member.id, "create", "summon", str(res.inserted_id), doc["name"])
        await interaction.response.send_message(
            f"✅ Created summon **{doc['name']}**! You're the owner. "
            f"Use `/servercard` to view it or `/invite_to` to add people.",
        )
        await self.refresh_member_embed(interaction.guild, doc)

    @edit.command(name="summon")
    @app_commands.describe(summon="The summon to edit")
    @app_commands.autocomplete(summon=summon_autocomplete)
    async def edit_summon(self, interaction, summon: str):
        """Edit a summon's settings."""
        doc = self._resolve(interaction.guild.id, summon)
        if not doc:
            await interaction.response.send_message("That summon doesn't exist.")
            return
        if not self._can_manage(interaction.user, doc) and interaction.user.id not in doc.get("co_owner_ids", []):
            await interaction.response.send_message(
                "Only the owner, co-owners, and admins can edit this."
            )
            return
        await interaction.response.send_message(
            embed=self._edit_embed(interaction.guild, doc),
            view=EditSummonView(self, interaction.guild.id, str(doc["_id"])),
        )

    @delete.command(name="summon")
    @app_commands.describe(summon="The summon to delete")
    @app_commands.autocomplete(summon=summon_autocomplete)
    async def delete_summon(self, interaction, summon: str):
        """Delete a summon."""
        doc = self._resolve(interaction.guild.id, summon)
        if not doc:
            await interaction.response.send_message("That summon doesn't exist.")
            return
        if not self._can_manage(interaction.user, doc):
            await interaction.response.send_message(
                "Only the owner and admins can delete this."
            )
            return
        if is_admin(interaction.user):
            await interaction.response.send_message(
                f"Delete summon **{doc['name']}**?",
                view=DeleteConfirmView(self, interaction.user, doc),
            )
        else:
            try:
                S.delete_one({"_id": doc["_id"]})
            except Exception as e:
                await interaction.response.send_message(f"Failed to delete: {e}")
                return
            msg = f"🗑️ Deleted summon **{doc['name']}**."
            if doc.get("real_role_id"):
                role = interaction.guild.get_role(doc["real_role_id"])
                if role:
                    try:
                        await role.delete(reason=f"Summon deleted by owner {interaction.user}")
                        msg += " The Discord role was also deleted."
                    except discord.Forbidden:
                        pass
            audit(interaction.guild.id, interaction.user.id, "delete", "summon", str(doc["_id"]), doc["name"])
            await interaction.response.send_message(msg)
            await self.delete_member_embed(interaction.guild, doc.get("member_embed_id"))
            await self.close_easyjoin_panels(interaction.guild, str(doc["_id"]))

    # ---------- slash: join / leave / invite / ban ----------

    @app_commands.command(name="join")
    @app_commands.describe(summon="The summon to join")
    @app_commands.autocomplete(summon=summon_autocomplete)
    async def join(self, interaction, summon: str):
        """Join a summon."""
        doc = self._resolve(interaction.guild.id, summon)
        if not doc:
            await interaction.response.send_message("That summon doesn't exist.")
            return
        member = interaction.user
        if member.id in doc.get("banned", []):
            await interaction.response.send_message("You're banned from this summon.")
            return
        if member.id in doc.get("members", []):
            await interaction.response.send_message("You're already in this summon.")
            return
        if doc.get("canjoin") == "invited":
            if member.id not in doc.get("join_ids", []):
                await interaction.response.send_message(
                    "This summon is invite-only."
                )
                return
        S.update_one({"_id": doc["_id"]}, {"$addToSet": {"members": member.id}})
        doc = S.find_one({"_id": doc["_id"]})
        if doc.get("real_role_id"):
            role = interaction.guild.get_role(doc["real_role_id"])
            if role:
                try:
                    await member.add_roles(role, reason=f"Joined summon {doc['name']}")
                except Exception:
                    pass
        audit(interaction.guild.id, member.id, "join", "summon", str(doc["_id"]), doc["name"])
        await interaction.response.send_message(f"✅ Joined **{doc['name']}**!")
        await self.refresh_member_embed(interaction.guild, doc)

    @app_commands.command(name="leave")
    @app_commands.describe(summon="The summon to leave")
    @app_commands.autocomplete(summon=summon_autocomplete)
    async def leave(self, interaction, summon: str):
        """Leave a summon."""
        doc = self._resolve(interaction.guild.id, summon)
        if not doc:
            await interaction.response.send_message("That summon doesn't exist.")
            return
        member = interaction.user
        if member.id not in doc.get("members", []):
            await interaction.response.send_message("You're not in this summon.")
            return
        S.update_one({"_id": doc["_id"]}, {"$pull": {"members": member.id}})
        doc = S.find_one({"_id": doc["_id"]})
        if doc.get("real_role_id"):
            role = interaction.guild.get_role(doc["real_role_id"])
            if role:
                try:
                    await member.remove_roles(role, reason=f"Left summon {doc['name']}")
                except Exception:
                    pass
        audit(interaction.guild.id, member.id, "leave", "summon", str(doc["_id"]), doc["name"])
        await interaction.response.send_message(f"👋 Left **{doc['name']}**!")
        await self.refresh_member_embed(interaction.guild, doc)

    @app_commands.command(name="invite_to")
    @app_commands.describe(summon="The summon to invite to", user="The user to invite")
    @app_commands.autocomplete(summon=invite_autocomplete)
    async def invite_to(self, interaction, summon: str, user: discord.Member):
        """Invite someone to a summon."""
        doc = self._resolve(interaction.guild.id, summon)
        if not doc:
            await interaction.response.send_message("That summon doesn't exist.")
            return
        if doc.get("canjoin") != "invited":
            await interaction.response.send_message(
                "This summon is open for anyone to join — no invite needed."
            )
            return
        if not self._can_invite(interaction.user, doc):
            await interaction.response.send_message(
                "You're not allowed to invite to this summon."
            )
            return
        if user.id in doc.get("banned", []):
            await interaction.response.send_message(
                f"{user.mention} is banned from this summon."
            )
            return
        S.update_one({"_id": doc["_id"]}, {"$addToSet": {"join_ids": user.id}})
        audit(interaction.guild.id, interaction.user.id, "invite", "summon", str(doc["_id"]), f"{doc['name']} -> {user.id}")
        await interaction.response.send_message(
            f"✅ {user.mention} can now join **{doc['name']}**!"
        )

    @app_commands.command(name="ban_from")
    @app_commands.describe(summon="The summon to ban from", user="The user to ban")
    @app_commands.autocomplete(summon=summon_autocomplete)
    async def ban_from(self, interaction, summon: str, user: discord.Member):
        """Ban a user from a summon."""
        doc = self._resolve(interaction.guild.id, summon)
        if not doc:
            await interaction.response.send_message("That summon doesn't exist.")
            return
        if not (is_admin(interaction.user) or doc.get("creator_id") == interaction.user.id or interaction.user.id in doc.get("co_owner_ids", [])):
            await interaction.response.send_message(
                "Only the owner, co-owners, and admins can ban."
            )
            return
        if user.id == doc.get("creator_id"):
            await interaction.response.send_message("You can't ban the owner.")
            return
        S.update_one(
            {"_id": doc["_id"]},
            {"$addToSet": {"banned": user.id}, "$pull": {"members": user.id, "join_ids": user.id}},
        )
        doc = S.find_one({"_id": doc["_id"]})
        if doc.get("real_role_id"):
            role = interaction.guild.get_role(doc["real_role_id"])
            if role:
                try:
                    await user.remove_roles(role, reason=f"Banned from summon {doc['name']}")
                except Exception:
                    pass
        audit(interaction.guild.id, interaction.user.id, "ban", "summon", str(doc["_id"]), f"{doc['name']} -> {user.id}")
        await interaction.response.send_message(f"⛔ Banned {user.mention} from **{doc['name']}**.")
        await self.refresh_member_embed(interaction.guild, doc)

    @app_commands.command(name="unban_from")
    @app_commands.describe(summon="The summon to unban from", user="The user to unban")
    @app_commands.autocomplete(summon=summon_autocomplete)
    async def unban_from(self, interaction, summon: str, user: discord.Member):
        """Unban a user from a summon."""
        doc = self._resolve(interaction.guild.id, summon)
        if not doc:
            await interaction.response.send_message("That summon doesn't exist.")
            return
        if not (is_admin(interaction.user) or doc.get("creator_id") == interaction.user.id or interaction.user.id in doc.get("co_owner_ids", [])):
            await interaction.response.send_message(
                "Only the owner, co-owners, and admins can unban."
            )
            return
        S.update_one({"_id": doc["_id"]}, {"$pull": {"banned": user.id}})
        audit(interaction.guild.id, interaction.user.id, "unban", "summon", str(doc["_id"]), f"{doc['name']} -> {user.id}")
        await interaction.response.send_message(f"✅ Unbanned {user.mention} from **{doc['name']}**.")

    # ---------- slash: list groups ----------

    @list_group.command(name="groups")
    async def list_groups(self, interaction):
        """List all groups you can join or are in."""
        docs = list(S.find({"guild_id": interaction.guild.id, "enabled": True}))
        if not docs:
            await interaction.response.send_message("No summons exist in this server yet.")
            return
        member = interaction.user
        admin = is_admin(member)
        embed = discord.Embed(title=f"Groups in {interaction.guild.name}", color=discord.Color.blue())
        shown = 0
        for d in docs:
            in_group = member.id in d.get("members", [])
            banned = member.id in d.get("banned", [])
            if admin:
                visible = True
            else:
                joinable = d.get("canjoin") == "anyone" or member.id in d.get("join_ids", [])
                visible = in_group or joinable
            if not visible:
                continue
            mark = "✅" if in_group else ("⛔" if banned else "❌")
            embed.add_field(
                name=f"{mark} {d['name']} ({len(d.get('members', []))} members)",
                value=f"Join: {d.get('canjoin')} | Ping: {d.get('canping')}",
                inline=False,
            )
            shown += 1
        if not shown:
            await interaction.response.send_message("No groups available.")
            return
        await interaction.response.send_message(embed=embed)

    # ---------- slash: servercard ----------

    @app_commands.command(name="servercard")
    @app_commands.describe(user="The user to show (default: you)")
    async def servercard(self, interaction, user: discord.Member = None):
        """Show a member's virtual roles and special roles."""
        target = user or interaction.user
        guild = interaction.guild
        docs = list(S.find({"guild_id": guild.id, "enabled": True}))
        embed = discord.Embed(
            title=f"🪪 Servercard — {target.display_name}",
            color=discord.Color.blue(),
        )

        virtual = []
        special = []
        for d in docs:
            if target.id in d.get("members", []):
                flags = ""
                if d.get("creator_id") == target.id:
                    flags += "👑 "
                if self._can_summon(target, d):
                    flags += "📢 "
                if self._can_invite(target, d):
                    flags += "🤝 "
                virtual.append(f"{flags}{d['name']} ({len(d.get('members', []))} members)")
        if virtual:
            embed.add_field(name="📦 Virtual roles", value="\n".join(virtual) or "None", inline=False)

        real_grants = list(AS.find({"guild_id": guild.id}))
        for g in real_grants:
            role = guild.get_role(g.get("role_id"))
            if role and role in target.roles:
                special.append(role.mention)
        if special:
            embed.add_field(name="⭐ Special roles", value=" ".join(special), inline=False)

        if not virtual and not special:
            embed.description = "No roles to show."
        await interaction.response.send_message(embed=embed)

    # ---------- prefix: allow (real role grant) ----------

    @commands.group(name="allow", invoke_without_command=True)
    @has_admin_or_dev()
    async def allow(self, ctx):
        await ctx.send("Usage: `!?allow summon @role`")

    @allow.command(name="summon")
    @has_admin_or_dev()
    async def allow_summon(self, ctx, role: discord.Role):
        doc = AS.find_one({"guild_id": ctx.guild.id, "role_id": role.id})
        if not doc:
            AS.insert_one({"guild_id": ctx.guild.id, "role_id": role.id, "allowed_ids": []})
        await ctx.send(
            f"⚙️ Setting up **{role.mention}**.\n\n"
            "Who can **summon** this real role?",
            view=AllowWhoView(self, ctx.author, role),
        )

    @commands.group(name="summon", invoke_without_command=True)
    @has_admin_or_dev()
    async def summon_prefix(self, ctx):
        """Admin prefix commands to manage summons."""
        await ctx.send(
            "Usage:\n"
            "`!?summon create <name> <canping> <canjoin>`\n"
            "`!?summon edit <summon>`\n"
            "`!?summon delete <summon>`\n"
            "canping: `anyone_joined` | `chosen` · canjoin: `anyone` | `invited`"
        )

    @summon_prefix.command(name="create")
    @has_admin_or_dev()
    async def summon_prefix_create(self, ctx, name: str, canping: str = "anyone_joined", canjoin: str = "anyone"):
        """Create a summon (admin)."""
        canping = canping.lower()
        canjoin = canjoin.lower()
        if canping not in [v for _, v in CANPING_CHOICES]:
            await ctx.send("Invalid canping. Use `anyone_joined` or `chosen`.")
            return
        if canjoin not in [v for _, v in CANJOIN_CHOICES]:
            await ctx.send("Invalid canjoin. Use `anyone` or `invited`.")
            return
        if self._name_taken(ctx.guild.id, name.strip()):
            await ctx.send("A summon with that name already exists.")
            return
        doc = {
            "guild_id": ctx.guild.id,
            "name": name.strip(),
            "color": discord.Colour.random().value,
            "creator_id": ctx.author.id,
            "co_owner_ids": [],
            "members": [ctx.author.id],
            "banned": [],
            "enabled": True,
            "canping": canping,
            "ping_ids": [],
            "ping_types": [],
            "canjoin": canjoin,
            "join_ids": [],
            "invite_ids": [],
            "invite_types": [],
            "real_role_id": None,
            "member_embed_id": None,
            "created_at": datetime.now(timezone.utc),
        }
        res = S.insert_one(doc)
        audit(ctx.guild.id, ctx.author.id, "create", "summon", str(res.inserted_id), doc["name"])
        await ctx.send(f"✅ Created summon **{doc['name']}**.")
        await self.refresh_member_embed(ctx.guild, doc)

    @summon_prefix.command(name="edit")
    @has_admin_or_dev()
    async def summon_prefix_edit(self, ctx, *, summon: str):
        """Edit a summon (admin)."""
        doc = self._resolve(ctx.guild.id, summon)
        if not doc:
            await ctx.send("That summon doesn't exist.")
            return
        await ctx.send(
            embed=self._edit_embed(ctx.guild, doc),
            view=EditSummonView(self, ctx.guild.id, str(doc["_id"])),
        )

    @summon_prefix.command(name="delete")
    @has_admin_or_dev()
    async def summon_prefix_delete(self, ctx, *, summon: str):
        """Delete a summon (admin)."""
        doc = self._resolve(ctx.guild.id, summon)
        if not doc:
            await ctx.send("That summon doesn't exist.")
            return
        await ctx.send(
            f"Delete summon **{doc['name']}**?",
            view=DeleteConfirmView(self, ctx.author, doc),
        )

    # ---------- prefix: promote / revoke / logs / audit / purge ----------

    @commands.command(name="promote")
    @has_admin_or_dev()
    async def promote(self, ctx, user: discord.Member, *, summon: str):
        """Make someone a co-owner of a summon (owner only)."""
        doc = self._resolve(ctx.guild.id, summon)
        if not doc:
            await ctx.send("That summon doesn't exist.")
            return
        if doc.get("creator_id") != ctx.author.id:
            await ctx.send("Only the owner can promote.")
            return
        if user.id == doc.get("creator_id"):
            await ctx.send("The owner is already an owner.")
            return
        S.update_one({"_id": doc["_id"]}, {"$addToSet": {"co_owner_ids": user.id}})
        audit(ctx.guild.id, ctx.author.id, "promote", "summon", str(doc["_id"]), f"{user.id} -> {doc['name']}")
        await ctx.send(f"✅ {user.mention} is now a co-owner of **{doc['name']}**.")

    @commands.command(name="revoke")
    @has_admin_or_dev()
    async def revoke(self, ctx, user: discord.Member, *, summon: str):
        """Remove co-owner status (owner only)."""
        doc = self._resolve(ctx.guild.id, summon)
        if not doc:
            await ctx.send("That summon doesn't exist.")
            return
        if doc.get("creator_id") != ctx.author.id:
            await ctx.send("Only the owner can revoke.")
            return
        S.update_one({"_id": doc["_id"]}, {"$pull": {"co_owner_ids": user.id}})
        audit(ctx.guild.id, ctx.author.id, "revoke", "summon", str(doc["_id"]), f"{user.id} -> {doc['name']}")
        await ctx.send(f"✅ {user.mention} is no longer a co-owner of **{doc['name']}**.")

    @commands.command(name="role")
    @has_admin_or_dev()
    async def role_cmd(self, ctx, summon: str, flag: str = None):
        """Add (-y) or remove (-r) a real Discord role for a summon."""
        doc = self._resolve(ctx.guild.id, summon)
        if not doc:
            await ctx.send("That summon doesn't exist.")
            return
        flag = (flag or "").lower()
        if flag == "-y":
            if doc.get("real_role_id") and ctx.guild.get_role(doc["real_role_id"]):
                await ctx.send("This summon already has a real role.")
                return
            try:
                role = await ctx.guild.create_role(
                    name=doc["name"],
                    colour=discord.Colour(doc.get("color", 0)),
                    mentionable=True,
                    reason=f"Summon role created by {ctx.author}",
                )
            except discord.Forbidden:
                await ctx.send("I need the **Manage Roles** permission to create roles.")
                return
            for uid in doc.get("members", []):
                m = ctx.guild.get_member(uid)
                if m:
                    try:
                        await m.add_roles(role, reason=f"Added to summon {doc['name']}")
                    except Exception:
                        pass
            S.update_one({"_id": doc["_id"]}, {"$set": {"real_role_id": role.id}})
            audit(ctx.guild.id, ctx.author.id, "role_add", "summon", str(doc["_id"]), doc["name"])
            await ctx.send(f"✅ Created real role {role.mention} for **{doc['name']}**.")
        elif flag == "-r":
            role = ctx.guild.get_role(doc.get("real_role_id")) if doc.get("real_role_id") else None
            if role:
                try:
                    await role.delete(reason=f"Summon role removed by {ctx.author}")
                except discord.Forbidden:
                    await ctx.send("I need the **Manage Roles** permission to delete roles.")
                    return
            S.update_one({"_id": doc["_id"]}, {"$set": {"real_role_id": None}})
            audit(ctx.guild.id, ctx.author.id, "role_remove", "summon", str(doc["_id"]), doc["name"])
            await ctx.send(f"🗑️ Removed the real role for **{doc['name']}**.")
        else:
            await ctx.send("Usage: `!?role <summon> -y|-r` (-y add real role, -r remove)")

    @commands.command(name="logs")
    async def logs(self, ctx, *, summon: str):
        """View logs about a summon (owner/co-owner/admin)."""
        doc = self._resolve(ctx.guild.id, summon)
        if not doc:
            await ctx.send("That summon doesn't exist.")
            return
        if not (is_admin(ctx.author) or doc.get("creator_id") == ctx.author.id or ctx.author.id in doc.get("co_owner_ids", [])):
            await ctx.send("Only the owner, co-owners, and admins can view logs.")
            return
        entries = list(AL.find({"guild_id": ctx.guild.id, "target_id": str(doc["_id"])}).sort("timestamp", -1).limit(15))
        embed = discord.Embed(title=f"📜 Logs — {doc['name']}", color=discord.Color.blue())
        if not entries:
            embed.description = "No logs yet."
        for e in entries:
            actor = ctx.guild.get_member(e.get("actor_id"))
            ts = e.get("timestamp")
            embed.add_field(
                name=f"{e.get('action')} — {actor.display_name if actor else e.get('actor_id')}",
                value=f"{e.get('details', '')}\n{ts.strftime('%Y-%m-%d %H:%M') if ts else ''}",
                inline=False,
            )
        await ctx.send(embed=embed)

    @commands.command(name="audit")
    @has_admin_or_dev()
    async def audit_cmd(self, ctx, limit: int = 15):
        """View recent admin actions (owner/admin)."""
        entries = list(AL.find({"guild_id": ctx.guild.id}).sort("timestamp", -1).limit(limit))
        embed = discord.Embed(title="🛡️ Audit log", color=discord.Color.blue())
        if not entries:
            embed.description = "No logs yet."
        for e in entries:
            actor = ctx.guild.get_member(e.get("actor_id"))
            ts = e.get("timestamp")
            embed.add_field(
                name=f"{e.get('action')} — {actor.display_name if actor else e.get('actor_id')}",
                value=f"{e.get('details', '')}\n{ts.strftime('%Y-%m-%d %H:%M') if ts else ''}",
                inline=False,
            )
        await ctx.send(embed=embed)

    @commands.command(name="purge")
    @has_admin_or_dev()
    async def purge(self, ctx):
        """Remove stale summon entries (deleted real roles / missing members)."""
        removed = 0
        for d in list(S.find({"guild_id": ctx.guild.id})):
            if d.get("real_role_id") and not ctx.guild.get_role(d["real_role_id"]):
                S.update_one({"_id": d["_id"]}, {"$set": {"real_role_id": None}})
                removed += 1
        await ctx.send(f"✅ Cleaned up {removed} stale reference(s).")

    # ---------- prefix: blacklist ----------

    @commands.command(name="blacklist")
    @has_admin_or_dev()
    async def blacklist(self, ctx, user: discord.Member):
        BL.update_one(
            {"guild_id": ctx.guild.id, "user_id": user.id},
            {"$set": {"guild_id": ctx.guild.id, "user_id": user.id}},
            upsert=True,
        )
        audit(ctx.guild.id, ctx.author.id, "blacklist", "user", user.id)
        await ctx.send(f"⛔ Blacklisted {user.mention} (they can still join).")

    @commands.command(name="unblacklist")
    @has_admin_or_dev()
    async def unblacklist(self, ctx, user: discord.Member):
        BL.delete_one({"guild_id": ctx.guild.id, "user_id": user.id})
        audit(ctx.guild.id, ctx.author.id, "unblacklist", "user", user.id)
        await ctx.send(f"✅ Unblacklisted {user.mention}.")


class AllowWhoView(discord.ui.View):
    def __init__(self, cog, author, target_role):
        super().__init__(timeout=180)
        self.cog = cog
        self.author = author
        self.target_role = target_role

    async def interaction_check(self, interaction):
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("Only the admin who started this can configure it.")
            return False
        return True

    @discord.ui.button(label="A role", style=discord.ButtonStyle.primary)
    async def role_button(self, interaction, button):
        await interaction.response.send_modal(AllowRoleModal(self.cog, self.author, self.target_role))

    @discord.ui.button(label="A specific person", style=discord.ButtonStyle.secondary)
    async def person_button(self, interaction, button):
        await interaction.response.send_modal(AllowPersonModal(self.cog, self.author, self.target_role))


class AllowRoleModal(discord.ui.Modal):
    def __init__(self, cog, author, target_role):
        super().__init__(title="Allow a role to summon")
        self.cog = cog
        self.author = author
        self.target_role = target_role
        self.role_input = discord.ui.TextInput(label="Role allowed to use /summon", placeholder="@RoleName or role ID", max_length=100)
        self.add_item(self.role_input)

    async def on_submit(self, interaction):
        value = self.role_input.value.strip()
        role = _resolve_real_role(interaction.guild, value)
        if role is None:
            await interaction.response.send_message("Could not find that role.")
            return
        _add_allow(interaction.guild.id, self.target_role.id, "role", role.id)
        audit(interaction.guild.id, interaction.user.id, "allow", "role", self.target_role.id, f"{role.name} -> {self.target_role.name}")
        await interaction.response.send_message(
            f"✅ {role.mention} can now use `/summon` for {self.target_role.mention}.", ephemeral=False
        )


class AllowPersonModal(discord.ui.Modal):
    def __init__(self, cog, author, target_role):
        super().__init__(title="Allow a person to summon")
        self.cog = cog
        self.author = author
        self.target_role = target_role
        self.user_input = discord.ui.TextInput(label="User allowed to use /summon", placeholder="@User or user ID", max_length=100)
        self.add_item(self.user_input)

    async def on_submit(self, interaction):
        value = self.user_input.value.strip()
        member = _resolve_real_member(interaction.guild, value)
        if member is None:
            await interaction.response.send_message("Could not find that user.")
            return
        _add_allow(interaction.guild.id, self.target_role.id, "user", member.id)
        audit(interaction.guild.id, interaction.user.id, "allow", "role", self.target_role.id, f"{member.id} -> {self.target_role.name}")
        await interaction.response.send_message(
            f"✅ {member.mention} can now use `/summon` for {self.target_role.mention}.", ephemeral=False
        )


def _resolve_real_role(guild, value):
    value = value.strip()
    if value.startswith("<@&") and value.endswith(">"):
        rid = value[3:-1]
        return guild.get_role(int(rid)) if rid.isdigit() else None
    if value.isdigit():
        return guild.get_role(int(value))
    return discord.utils.get(guild.roles, name=value)


def _resolve_real_member(guild, value):
    value = value.strip()
    if value.startswith("<@") and value.endswith(">"):
        uid = value[2:-1].lstrip("!")
        return guild.get_member(int(uid)) if uid.isdigit() else None
    if value.isdigit():
        return guild.get_member(int(value))
    return discord.utils.get(guild.members, name=value)


def _add_allow(guild_id, role_id, allowed_type, allowed_id):
    AS.update_one(
        {"guild_id": guild_id, "role_id": role_id},
        {"$addToSet": {f"allowed_{allowed_type}s": allowed_id}},
        upsert=True,
    )


async def setup(bot):
    await bot.add_cog(SummonsCog(bot))
