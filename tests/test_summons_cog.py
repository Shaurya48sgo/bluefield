import asyncio
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from cogs.summons import EditSummonView, MembersButton, SummonsCog

from mongo_helpers import get_test_db, mongo_available

skip_if_no_mongo = pytest.mark.skipif(not mongo_available(), reason="MongoDB not running")
skip = skip_if_no_mongo


def make_cog(db):
    db.summon_roles.drop()
    db.summon_settings.drop()
    db.blacklist.drop()
    db.audit_log.drop()
    db.guild_settings.drop()
    db.easyjoin_panels.drop()
    cog = SummonsCog(MagicMock())
    cog.bot = MagicMock()
    from cogs import common, summons

    for mod in (common, summons):
        mod.S = db["summon_roles"]
        mod.AS = db["summon_settings"]
        mod.BL = db["blacklist"]
        mod.AL = db["audit_log"]
        mod.G = db["guild_settings"]
        mod.P = db["easyjoin_panels"]
    return cog


def make_member(uid=100, manage_roles=False, administrator=False, roles=None):
    member = MagicMock()
    member.id = uid
    member.display_name = f"user{uid}"
    perms = MagicMock()
    perms.manage_roles = manage_roles
    perms.administrator = administrator
    member.guild_permissions = perms
    member.roles = roles or []
    member.guild = MagicMock()
    member.guild.id = 1
    return member


def make_guild():
    guild = MagicMock()
    guild.id = 1
    guild.name = "Test Guild"
    guild.get_member.return_value = None
    guild.get_role.return_value = None
    return guild


def make_interaction(member, guild=None):
    guild = guild or make_guild()
    interaction = MagicMock()
    interaction.user = member
    interaction.guild = guild
    interaction.channel_id = 555
    channel = MagicMock()
    channel.send = AsyncMock(return_value=AsyncMock())
    interaction.channel = channel
    interaction.response.send_message = AsyncMock()
    interaction.response.send_modal = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock(return_value=AsyncMock())
    return interaction


def make_summon(rid=1, **overrides):
    from datetime import datetime, timezone

    doc = {
        "guild_id": 1,
        "name": "Raiders",
        "color": 0x5865F2,
        "creator_id": 42,
        "co_owner_ids": [],
        "members": [42],
        "banned": [],
        "enabled": True,
        "canping": "anyone_joined",
        "ping_ids": [],
        "ping_types": [],
        "canjoin": "anyone",
        "join_ids": [],
        "invite_ids": [],
        "invite_types": [],
        "real_role_id": None,
        "member_embed_id": None,
        "created_at": datetime.now(timezone.utc),
    }
    doc.update(overrides)
    return doc


@skip
def test_create_summon_saves_and_joins_creator():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        member = make_member(uid=100)
        interaction = make_interaction(member)
        asyncio.run(cog.create_summon.callback(cog, interaction, "NewGroup", "anyone_joined", "anyone"))
        interaction.response.send_message.assert_awaited_once()
        doc = db["summon_roles"].find_one({"name": "NewGroup"})
        assert doc is not None
        assert doc["creator_id"] == 100
        assert 100 in doc["members"]
        assert doc["enabled"] is True
    finally:
        client.close()


@skip
def test_create_non_admin_at_limit_denied():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        for i in range(3):
            db["summon_roles"].insert_one(make_summon(rid=i, name=f"G{i}", creator_id=100))
        member = make_member(uid=100)
        interaction = make_interaction(member)
        asyncio.run(cog.create_summon.callback(cog, interaction, "Another", "anyone_joined", "anyone"))
        interaction.response.send_message.assert_awaited_once()
        assert db["summon_roles"].count_documents({"name": "Another"}) == 0
    finally:
        client.close()


@skip
def test_admin_bypasses_limit():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        for i in range(3):
            db["summon_roles"].insert_one(make_summon(rid=i, name=f"G{i}", creator_id=100))
        member = make_member(uid=100, manage_roles=True)
        interaction = make_interaction(member)
        asyncio.run(cog.create_summon.callback(cog, interaction, "AdminGroup", "anyone_joined", "anyone"))
        assert db["summon_roles"].count_documents({"name": "AdminGroup"}) == 1
    finally:
        client.close()


@skip
def test_create_blacklisted_rejected():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        db["blacklist"].insert_one({"guild_id": 1, "user_id": 100})
        member = make_member(uid=100)
        interaction = make_interaction(member)
        asyncio.run(cog.create_summon.callback(cog, interaction, "NewGroup", "anyone_joined", "anyone"))
        assert db["summon_roles"].count_documents({"name": "NewGroup"}) == 0
    finally:
        client.close()


@skip
def test_create_blacklist_admin_immune():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        db["blacklist"].insert_one({"guild_id": 1, "user_id": 100})
        member = make_member(uid=100, manage_roles=True)
        interaction = make_interaction(member)
        asyncio.run(cog.create_summon.callback(cog, interaction, "NewGroup", "anyone_joined", "anyone"))
        assert db["summon_roles"].count_documents({"name": "NewGroup"}) == 1
    finally:
        client.close()


@skip
def test_join_anyone_mode():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        res = db["summon_roles"].insert_one(make_summon(rid=1))
        member = make_member(uid=200)
        interaction = make_interaction(member)
        asyncio.run(cog.join.callback(cog, interaction, str(res.inserted_id)))
        doc = db["summon_roles"].find_one({"name": "Raiders"})
        assert 200 in doc["members"]
    finally:
        client.close()


@skip
def test_join_invite_only_denied_without_invite():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        res = db["summon_roles"].insert_one(make_summon(rid=1, canjoin="invited", join_ids=[]))
        member = make_member(uid=200)
        interaction = make_interaction(member)
        asyncio.run(cog.join.callback(cog, interaction, str(res.inserted_id)))
        doc = db["summon_roles"].find_one({"name": "Raiders"})
        assert 200 not in doc["members"]
    finally:
        client.close()


@skip
def test_join_invite_only_allowed_when_invited():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        res = db["summon_roles"].insert_one(make_summon(rid=1, canjoin="invited", join_ids=[200]))
        member = make_member(uid=200)
        interaction = make_interaction(member)
        asyncio.run(cog.join.callback(cog, interaction, str(res.inserted_id)))
        doc = db["summon_roles"].find_one({"name": "Raiders"})
        assert 200 in doc["members"]
    finally:
        client.close()


@skip
def test_join_banned_denied():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        res = db["summon_roles"].insert_one(make_summon(rid=1, banned=[200]))
        member = make_member(uid=200)
        interaction = make_interaction(member)
        asyncio.run(cog.join.callback(cog, interaction, str(res.inserted_id)))
        doc = db["summon_roles"].find_one({"name": "Raiders"})
        assert 200 not in doc["members"]
    finally:
        client.close()


@skip
def test_leave_removes_member():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        res = db["summon_roles"].insert_one(make_summon(rid=1, members=[42, 200]))
        member = make_member(uid=200)
        interaction = make_interaction(member)
        asyncio.run(cog.leave.callback(cog, interaction, str(res.inserted_id)))
        doc = db["summon_roles"].find_one({"name": "Raiders"})
        assert 200 not in doc["members"]
    finally:
        client.close()


@skip
def test_invite_to_requires_invite_only():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        res = db["summon_roles"].insert_one(make_summon(rid=1, canjoin="anyone"))
        member = make_member(uid=42)
        target = make_member(uid=300)
        interaction = make_interaction(member)
        asyncio.run(cog.invite_to.callback(cog, interaction, str(res.inserted_id), target))
        doc = db["summon_roles"].find_one({"name": "Raiders"})
        assert 300 not in doc["join_ids"]
    finally:
        client.close()


@skip
def test_invite_to_by_owner():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        res = db["summon_roles"].insert_one(make_summon(rid=1, canjoin="invited", creator_id=42))
        member = make_member(uid=42)
        target = make_member(uid=300)
        interaction = make_interaction(member)
        asyncio.run(cog.invite_to.callback(cog, interaction, str(res.inserted_id), target))
        doc = db["summon_roles"].find_one({"name": "Raiders"})
        assert 300 in doc["join_ids"]
    finally:
        client.close()


@skip
def test_ban_from_by_owner():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        res = db["summon_roles"].insert_one(make_summon(rid=1, members=[42, 300]))
        member = make_member(uid=42)
        target = make_member(uid=300)
        interaction = make_interaction(member)
        asyncio.run(cog.ban_from.callback(cog, interaction, str(res.inserted_id), target))
        doc = db["summon_roles"].find_one({"name": "Raiders"})
        assert 300 in doc["banned"]
        assert 300 not in doc["members"]
    finally:
        client.close()


@skip
def test_unban_from():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        res = db["summon_roles"].insert_one(make_summon(rid=1, banned=[300]))
        member = make_member(uid=42)
        target = make_member(uid=300)
        interaction = make_interaction(member)
        asyncio.run(cog.unban_from.callback(cog, interaction, str(res.inserted_id), target))
        doc = db["summon_roles"].find_one({"name": "Raiders"})
        assert 300 not in doc["banned"]
    finally:
        client.close()


@skip
def test_ban_from_denied_for_normal_member():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        res = db["summon_roles"].insert_one(make_summon(rid=1, creator_id=42))
        member = make_member(uid=200)
        target = make_member(uid=300)
        interaction = make_interaction(member)
        asyncio.run(cog.ban_from.callback(cog, interaction, str(res.inserted_id), target))
        doc = db["summon_roles"].find_one({"name": "Raiders"})
        assert 300 not in doc["banned"]
    finally:
        client.close()


@skip
def test_delete_by_owner_removes_doc():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        res = db["summon_roles"].insert_one(make_summon(rid=1, creator_id=100))
        member = make_member(uid=100)
        interaction = make_interaction(member)
        asyncio.run(cog.delete_summon.callback(cog, interaction, str(res.inserted_id)))
        assert db["summon_roles"].find_one({"name": "Raiders"}) is None
    finally:
        client.close()


@skip
def test_delete_denied_for_non_owner():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        res = db["summon_roles"].insert_one(make_summon(rid=1, creator_id=42))
        member = make_member(uid=200)
        interaction = make_interaction(member)
        asyncio.run(cog.delete_summon.callback(cog, interaction, str(res.inserted_id)))
        assert db["summon_roles"].find_one({"name": "Raiders"}) is not None
    finally:
        client.close()


@skip
def test_co_owner_can_manage():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        doc = make_summon(rid=1, creator_id=42, co_owner_ids=[200])
        res = db["summon_roles"].insert_one(doc)
        member = make_member(uid=200)
        interaction = make_interaction(member)
        # co-owner should be able to edit (opens view)
        asyncio.run(cog.edit_summon.callback(cog, interaction, str(res.inserted_id)))
        interaction.response.send_message.assert_awaited_once()
        # non-owner non-co-owner cannot
        member2 = make_member(uid=300)
        interaction2 = make_interaction(member2)
        asyncio.run(cog.edit_summon.callback(cog, interaction2, str(res.inserted_id)))
        interaction2.response.send_message.assert_awaited_once()
    finally:
        client.close()


@skip
def test_summon_cooldown_for_non_admin():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        res = db["summon_roles"].insert_one(make_summon(rid=1, creator_id=42, members=[42, 100]))
        member = make_member(uid=100)
        member.guild.get_member.side_effect = lambda uid: member if uid == 100 else None
        guild = make_guild()
        guild.get_member.side_effect = lambda uid: member if uid == 100 else None
        interaction = make_interaction(member, guild)
        asyncio.run(cog.summon.callback(cog, interaction, str(res.inserted_id)))
        # second call within 60s blocked
        interaction2 = make_interaction(member, guild)
        asyncio.run(cog.summon.callback(cog, interaction2, str(res.inserted_id)))
        assert "wait" in interaction2.response.send_message.await_args.args[0].lower()
    finally:
        client.close()


@skip
def test_summon_no_cooldown_for_admin():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        res = db["summon_roles"].insert_one(make_summon(rid=1, creator_id=42))
        member = make_member(uid=100, manage_roles=True)
        guild = make_guild()
        guild.get_member.side_effect = lambda uid: member if uid == 100 else None
        interaction = make_interaction(member, guild)
        asyncio.run(cog.summon.callback(cog, interaction, str(res.inserted_id)))
        interaction2 = make_interaction(member, guild)
        asyncio.run(cog.summon.callback(cog, interaction2, str(res.inserted_id)))
        # admin path never calls response.send_message with a cooldown block
        interaction2.response.send_message.assert_not_awaited()
    finally:
        client.close()


@skip
def test_list_groups_admin_sees_all():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        db["summon_roles"].insert_many(
            [
                make_summon(rid=1, name="A"),
                make_summon(rid=2, name="B"),
            ]
        )
        member = make_member(uid=100, manage_roles=True)
        member.guild = make_guild()
        interaction = make_interaction(member)
        asyncio.run(cog.list_groups.callback(cog, interaction))
        embed = interaction.response.send_message.await_args.kwargs["embed"]
        assert len(embed.fields) == 2
    finally:
        client.close()


@skip
def test_list_groups_member_sees_only_visible():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        db["summon_roles"].insert_many(
            [
                make_summon(rid=1, name="A", members=[100]),
                make_summon(rid=2, name="B", canjoin="invited", join_ids=[]),
            ]
        )
        member = make_member(uid=100)
        member.guild = make_guild()
        interaction = make_interaction(member)
        asyncio.run(cog.list_groups.callback(cog, interaction))
        embed = interaction.response.send_message.await_args.kwargs["embed"]
        assert len(embed.fields) == 1
        assert embed.fields[0].name.startswith("✅")
    finally:
        client.close()


@skip
def test_servercard_shows_creator_flag():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        db["summon_roles"].insert_one(make_summon(rid=1, creator_id=100, members=[100]))
        member = make_member(uid=100)
        member.guild = make_guild()
        interaction = make_interaction(member)
        asyncio.run(cog.servercard.callback(cog, interaction, member))
        embed = interaction.response.send_message.await_args.kwargs["embed"]
        assert "👑" in embed.fields[0].value
    finally:
        client.close()


@skip
def test_role_flag_adds_real_role():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        res = db["summon_roles"].insert_one(make_summon(rid=1, creator_id=42))
        ctx = MagicMock()
        ctx.guild = make_guild()
        created_role = MagicMock()
        created_role.id = 555
        created_role.mention = "<@&555>"
        ctx.guild.create_role = AsyncMock(return_value=created_role)
        ctx.send = AsyncMock()
        ctx.author = make_member(uid=42, manage_roles=True)
        asyncio.run(cog.role_cmd.callback(cog, ctx, str(res.inserted_id), "-y"))
        doc = db["summon_roles"].find_one({"_id": res.inserted_id})
        ctx.guild.create_role.assert_awaited_once()
        assert doc["real_role_id"] == 555
    finally:
        client.close()


@skip
def test_promote_and_revoke():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        res = db["summon_roles"].insert_one(make_summon(rid=1, creator_id=42))
        ctx = MagicMock()
        ctx.guild = make_guild()
        ctx.send = AsyncMock()
        ctx.author = make_member(uid=42)
        user = make_member(uid=200)
        asyncio.run(cog.promote.callback(cog, ctx, user, summon=str(res.inserted_id)))
        doc = db["summon_roles"].find_one({"_id": res.inserted_id})
        assert 200 in doc["co_owner_ids"]
        asyncio.run(cog.revoke.callback(cog, ctx, user, summon=str(res.inserted_id)))
        doc = db["summon_roles"].find_one({"_id": res.inserted_id})
        assert 200 not in doc["co_owner_ids"]
    finally:
        client.close()


@skip
def test_promote_only_owner():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        res = db["summon_roles"].insert_one(make_summon(rid=1, creator_id=42))
        ctx = MagicMock()
        ctx.guild = make_guild()
        ctx.send = AsyncMock()
        ctx.author = make_member(uid=999)
        user = make_member(uid=200)
        asyncio.run(cog.promote.callback(cog, ctx, user, summon=str(res.inserted_id)))
        doc = db["summon_roles"].find_one({"_id": res.inserted_id})
        assert 200 not in doc["co_owner_ids"]
    finally:
        client.close()


@skip
def test_audit_records_actions():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        res = db["summon_roles"].insert_one(make_summon(rid=1, creator_id=42))
        member = make_member(uid=100)
        interaction = make_interaction(member)
        asyncio.run(cog.join.callback(cog, interaction, str(res.inserted_id)))
        assert db["audit_log"].count_documents({"guild_id": 1}) >= 1
    finally:
        client.close()


@skip
def test_create_duplicate_name_rejected():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        db["summon_roles"].insert_one(make_summon(rid=1, name="Raiders"))
        member = make_member(uid=100, manage_roles=True)
        interaction = make_interaction(member)
        asyncio.run(cog.create_summon.callback(cog, interaction, "raiders", "anyone_joined", "anyone"))
        assert db["summon_roles"].count_documents({"name": "raiders"}) == 0
        msg = interaction.response.send_message.await_args.args[0]
        assert "already exists" in msg.lower()
    finally:
        client.close()


@skip
def test_create_uses_settings_limit():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        db["guild_settings"].insert_one({"guild_id": 1, "max_groups_per_member": 1})
        db["summon_roles"].insert_one(make_summon(rid=1, name="G0", creator_id=100))
        member = make_member(uid=100)
        interaction = make_interaction(member)
        asyncio.run(cog.create_summon.callback(cog, interaction, "Another", "anyone_joined", "anyone"))
        assert db["summon_roles"].count_documents({"name": "Another"}) == 0
    finally:
        client.close()


@skip
def test_prefix_summon_create():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        ctx = MagicMock()
        ctx.guild = make_guild()
        ctx.send = AsyncMock()
        ctx.author = make_member(uid=42, manage_roles=True)
        asyncio.run(cog.summon_prefix_create.callback(cog, ctx, "PrefixGroup", "chosen", "invited"))
        doc = db["summon_roles"].find_one({"name": "PrefixGroup"})
        assert doc is not None
        assert doc["canping"] == "chosen"
        assert doc["canjoin"] == "invited"
    finally:
        client.close()


@skip
def test_prefix_summon_create_invalid_canping():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        ctx = MagicMock()
        ctx.guild = make_guild()
        ctx.send = AsyncMock()
        ctx.author = make_member(uid=42, manage_roles=True)
        asyncio.run(cog.summon_prefix_create.callback(cog, ctx, "Group", "bad", "anyone"))
        assert db["summon_roles"].count_documents({"name": "Group"}) == 0
    finally:
        client.close()


@skip
def test_delete_confirm_has_simple_yes_no():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        doc = make_summon(rid=1)
        res = db["summon_roles"].insert_one(doc)
        member = make_member(uid=42, manage_roles=True)
        interaction = make_interaction(member)
        asyncio.run(cog.delete_summon.callback(cog, interaction, str(res.inserted_id)))
        view = interaction.response.send_message.await_args.kwargs["view"]
        labels = [b.label for b in view.children]
        assert labels == ["Yes, delete", "Cancel"]
        assert "Delete role too" not in labels
    finally:
        client.close()


@skip
def test_edit_view_conditional_buttons():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        # chosen/invited -> all 6 buttons
        db["summon_roles"].insert_one(make_summon(rid=1, canping="chosen", canjoin="invited"))
        view = EditSummonView(cog, 1, str(db["summon_roles"].find_one({"name": "Raiders"})["_id"]))
        labels = [b.label for b in view.children if isinstance(b, discord.ui.Button)]
        assert "🔔 Add pingers" in labels
        assert "🗑️ Remove pingers" in labels
        assert "🤝 Add inviters" in labels
        assert "🗑️ Remove inviters" in labels
        # anyone_joined/anyone -> no pinger/inviter buttons
        db["summon_roles"].drop()
        db["summon_roles"].insert_one(make_summon(rid=1, canping="anyone_joined", canjoin="anyone"))
        view2 = EditSummonView(cog, 1, str(db["summon_roles"].find_one({"name": "Raiders"})["_id"]))
        labels2 = [b.label for b in view2.children if isinstance(b, discord.ui.Button)]
        assert "🔔 Add pingers" not in labels2
        assert "🗑️ Remove pingers" not in labels2
        assert "🤝 Add inviters" not in labels2
        assert "🗑️ Remove inviters" not in labels2
    finally:
        client.close()


@skip
def test_edit_embed_shows_members_and_powers():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        db["summon_roles"].insert_one(make_summon(rid=1, creator_id=42, co_owner_ids=[200], members=[42, 200, 300]))
        doc = db["summon_roles"].find_one({"name": "Raiders"})
        guild = make_guild()
        m42 = MagicMock(); m42.id = 42; m42.mention = "<@42>"
        m200 = MagicMock(); m200.id = 200; m200.mention = "<@200>"
        m300 = MagicMock(); m300.id = 300; m300.mention = "<@300>"
        guild.get_member.side_effect = lambda uid: {42: m42, 200: m200, 300: m300}.get(uid)
        embed = cog._edit_embed(guild, doc)
        assert "👥 Members (3)" in [f.name for f in embed.fields][0]
        assert "👑" in embed.fields[0].value
        assert "🔑" in embed.fields[0].value
    finally:
        client.close()


@skip
def test_ask_mentions_remove_removes_entries():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        db["summon_roles"].insert_one(make_summon(rid=1, ping_ids=[7, 8], ping_types=["user", "user"]))
        res = db["summon_roles"].find_one({"name": "Raiders"})
        interaction = MagicMock()
        interaction.response.send_message = AsyncMock()
        interaction.followup.send = AsyncMock()
        msg = MagicMock()
        msg.author.id = 42
        msg.channel.id = 99
        msg.guild = MagicMock()
        msg.content = "<@7>"
        msg.delete = AsyncMock()
        cog.bot.wait_for = AsyncMock(return_value=msg)
        asyncio.run(cog.ask_mentions(interaction, 1, str(res["_id"]), "ping", "ping_ids", "ping_types", remove=True))
        doc = db["summon_roles"].find_one({"name": "Raiders"})
        assert doc["ping_ids"] == [8]
        assert doc["ping_types"] == ["user"]
    finally:
        client.close()


@skip
def test_ask_mentions_add_appends_entries():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        db["summon_roles"].insert_one(make_summon(rid=1, ping_ids=[7], ping_types=["user"]))
        res = db["summon_roles"].find_one({"name": "Raiders"})
        interaction = MagicMock()
        interaction.response.send_message = AsyncMock()
        interaction.followup.send = AsyncMock()
        msg = MagicMock()
        msg.author.id = 42
        msg.channel.id = 99
        msg.guild = MagicMock()
        msg.content = "<@9> <@7>"
        msg.delete = AsyncMock()
        cog.bot.wait_for = AsyncMock(return_value=msg)
        asyncio.run(cog.ask_mentions(interaction, 1, str(res["_id"]), "ping", "ping_ids", "ping_types", remove=False))
        doc = db["summon_roles"].find_one({"name": "Raiders"})
        assert set(doc["ping_ids"]) == {7, 9}
        assert len(doc["ping_ids"]) == len(doc["ping_types"])
    finally:
        client.close()


@skip
def test_name_taken_case_insensitive():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        db["summon_roles"].insert_one(make_summon(rid=1, name="Raiders"))
        assert cog._name_taken(1, "raiders") is True
        assert cog._name_taken(1, "RAIDERS") is True
        assert cog._name_taken(1, "Other") is False
        assert cog._name_taken(2, "Raiders") is False
    finally:
        client.close()


@skip
def test_name_taken_excludes_self():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        res = db["summon_roles"].insert_one(make_summon(rid=1, name="Raiders"))
        assert cog._name_taken(1, "Raiders", exclude=str(res.inserted_id)) is False
    finally:
        client.close()


@skip
def test_create_rejects_same_name_different_people():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        db["summon_roles"].insert_one(make_summon(rid=1, name="Raiders", creator_id=42))
        member = make_member(uid=200, manage_roles=True)
        interaction = make_interaction(member)
        asyncio.run(cog.create_summon.callback(cog, interaction, "Raiders", "anyone_joined", "anyone"))
        assert db["summon_roles"].count_documents({"name": "Raiders"}) == 1
        msg = interaction.response.send_message.await_args.args[0]
        assert "already exists" in msg.lower()
    finally:
        client.close()


@skip
def test_invite_autocomplete_only_invited_groups():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        db["summon_roles"].insert_many(
            [
                make_summon(rid=1, name="OpenGroup", canjoin="anyone"),
                make_summon(rid=2, name="SecretGroup", canjoin="invited"),
            ]
        )
        guild = make_guild()
        interaction = MagicMock()
        interaction.guild = guild
        result = asyncio.run(cog.invite_autocomplete(interaction, ""))
        names = [c.name for c in result]
        assert names == ["SecretGroup"]
    finally:
        client.close()


@skip
def test_easyjoin_rejects_invite_only():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        res = db["summon_roles"].insert_one(make_summon(rid=1, canjoin="invited"))
        member = make_member(uid=100, manage_roles=True)
        interaction = make_interaction(member)
        interaction.guild = make_guild()
        asyncio.run(cog.easyjoin.callback(cog, interaction, str(res.inserted_id)))
        msg = interaction.response.send_message.await_args.args[0]
        assert "anyone can join" in msg.lower()
    finally:
        client.close()


@skip
def test_easyjoin_posts_panel_and_stores_panel():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        res = db["summon_roles"].insert_one(make_summon(rid=1, canjoin="anyone"))
        member = make_member(uid=100, manage_roles=True)
        interaction = make_interaction(member)
        guild = make_guild()
        interaction.guild = guild
        msg = MagicMock()
        msg.id = 777
        interaction.response.send_message = AsyncMock(return_value=msg)
        asyncio.run(cog.easyjoin.callback(cog, interaction, str(res.inserted_id)))
        interaction.response.send_message.assert_awaited_once()
        panel = db["easyjoin_panels"].find_one({"summon_id": str(res.inserted_id)})
        assert panel is not None
        assert panel["message_id"] == 777
    finally:
        client.close()


@skip
def test_easyjoin_toggle_join_and_leave():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        res = db["summon_roles"].insert_one(make_summon(rid=1, canjoin="anyone", creator_id=42))
        member = make_member(uid=200)
        interaction = make_interaction(member)
        interaction.guild = make_guild()
        interaction.response.send_message = AsyncMock()
        asyncio.run(cog.easyjoin_toggle(interaction, str(res.inserted_id), join=True))
        doc = db["summon_roles"].find_one({"_id": res.inserted_id})
        assert 200 in doc["members"]
        asyncio.run(cog.easyjoin_toggle(interaction, str(res.inserted_id), join=False))
        doc = db["summon_roles"].find_one({"_id": res.inserted_id})
        assert 200 not in doc["members"]
    finally:
        client.close()


@skip
def test_easyjoin_join_denied_if_banned():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        res = db["summon_roles"].insert_one(make_summon(rid=1, canjoin="anyone", banned=[200]))
        member = make_member(uid=200)
        interaction = make_interaction(member)
        interaction.guild = make_guild()
        interaction.response.send_message = AsyncMock()
        asyncio.run(cog.easyjoin_toggle(interaction, str(res.inserted_id), join=True))
        doc = db["summon_roles"].find_one({"_id": res.inserted_id})
        assert 200 not in doc["members"]
    finally:
        client.close()


@skip
def test_easyjoin_autocomplete_only_open_groups():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        db["summon_roles"].insert_many(
            [
                make_summon(rid=1, name="OpenGroup", canjoin="anyone"),
                make_summon(rid=2, name="SecretGroup", canjoin="invited"),
            ]
        )
        guild = make_guild()
        interaction = MagicMock()
        interaction.guild = guild
        result = asyncio.run(cog.easyjoin_autocomplete(interaction, ""))
        names = [c.name for c in result]
        assert names == ["OpenGroup"]
    finally:
        client.close()


@skip
def test_easyjoin_toggle_updates_panel():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        res = db["summon_roles"].insert_one(make_summon(rid=1, canjoin="anyone", creator_id=42))
        db["easyjoin_panels"].insert_one({"guild_id": 1, "summon_id": str(res.inserted_id), "channel_id": 555, "message_id": 777})
        member = make_member(uid=200)
        interaction = make_interaction(member)
        guild = make_guild()
        channel = MagicMock()
        fetched = MagicMock()
        fetched.edit = AsyncMock()
        channel.fetch_message = AsyncMock(return_value=fetched)
        guild.get_channel.return_value = channel
        interaction.guild = guild
        interaction.response.send_message = AsyncMock()
        asyncio.run(cog.easyjoin_toggle(interaction, str(res.inserted_id), join=True))
        channel.fetch_message.assert_awaited_once()
        fetched.edit.assert_awaited_once()
    finally:
        client.close()


@skip
def test_easyjoin_expire_removes_panel():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        res = db["summon_roles"].insert_one(make_summon(rid=1, canjoin="anyone", creator_id=42))
        db["easyjoin_panels"].insert_one({"guild_id": 1, "summon_id": str(res.inserted_id), "channel_id": 555, "message_id": 777, "created_by": 42})
        member = make_member(uid=42)
        interaction = make_interaction(member)
        guild = make_guild()
        channel = MagicMock()
        fetched = MagicMock()
        fetched.embeds = []
        fetched.edit = AsyncMock()
        channel.fetch_message = AsyncMock(return_value=fetched)
        guild.get_channel.return_value = channel
        interaction.guild = guild
        interaction.channel = channel
        interaction.message = MagicMock()
        interaction.message.id = 777
        interaction.response.send_message = AsyncMock()
        asyncio.run(cog.easyjoin_expire(interaction, str(res.inserted_id)))
        assert db["easyjoin_panels"].count_documents({"message_id": 777}) == 0
        interaction.response.send_message.assert_awaited_once()
    finally:
        client.close()


@skip
def test_easyjoin_expire_denied_for_stranger():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        res = db["summon_roles"].insert_one(make_summon(rid=1, canjoin="anyone", creator_id=42))
        db["easyjoin_panels"].insert_one({"guild_id": 1, "summon_id": str(res.inserted_id), "channel_id": 555, "message_id": 777, "created_by": 42})
        member = make_member(uid=999)
        interaction = make_interaction(member)
        guild = make_guild()
        interaction.guild = guild
        interaction.message = MagicMock()
        interaction.message.id = 777
        interaction.response.send_message = AsyncMock()
        asyncio.run(cog.easyjoin_expire(interaction, str(res.inserted_id)))
        assert db["easyjoin_panels"].count_documents({"message_id": 777}) == 1
    finally:
        client.close()


@skip
def test_close_easyjoin_panels_on_canjoin_change():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        res = db["summon_roles"].insert_one(make_summon(rid=1, canjoin="anyone"))
        db["easyjoin_panels"].insert_one({"guild_id": 1, "summon_id": str(res.inserted_id), "channel_id": 555, "message_id": 777})
        guild = make_guild()
        channel = MagicMock()
        fetched = MagicMock()
        fetched.embeds = []
        fetched.edit = AsyncMock()
        channel.fetch_message = AsyncMock(return_value=fetched)
        guild.get_channel.return_value = channel
        asyncio.run(cog.close_easyjoin_panels(guild, str(res.inserted_id)))
        assert db["easyjoin_panels"].count_documents({"summon_id": str(res.inserted_id)}) == 0
        fetched.edit.assert_awaited_once()
    finally:
        client.close()
