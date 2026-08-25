import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from cogs.confess import ConfessCog, ReplyCodeSelectView, SecretReplyButton, SecretReplyView

from mongo_helpers import get_test_db, mongo_available

skip_if_no_mongo = pytest.mark.skipif(not mongo_available(), reason="MongoDB not running")
skip = skip_if_no_mongo


def make_cog(db):
    db.anon_codes.drop()
    db.guild_settings.drop()
    db.audit_log.drop()
    db.blacklist.drop()
    db.secret_messages.drop()
    db.inbox.drop()
    db.reveal_proposals.drop()
    db.user_settings.drop()
    cog = ConfessCog(MagicMock())
    cog.bot = MagicMock()
    from cogs import common, confess

    for mod in (common, confess):
        mod.C = db["anon_codes"]
        mod.G = db["guild_settings"]
        mod.AL = db["audit_log"]
        mod.BL = db["blacklist"]
        mod.M = db["secret_messages"]
        mod.I = db["inbox"]
        mod.RP = db["reveal_proposals"]
        mod.US = db["user_settings"]
    return cog


def make_member(uid=100, manage_roles=False, administrator=False):
    member = MagicMock()
    member.id = uid
    perms = MagicMock()
    perms.manage_roles = manage_roles
    perms.administrator = administrator
    member.guild_permissions = perms
    member.guild = MagicMock()
    member.guild.id = 1
    return member


def make_guild():
    guild = MagicMock()
    guild.id = 1
    channels = {}

    def get_channel(cid):
        if cid not in channels:
            channels[cid] = _make_channel(cid)
        return channels[cid]

    guild.get_channel.side_effect = get_channel
    return guild


def _make_channel(cid):
    channel = MagicMock()
    channel.id = cid
    channel.mention = f"<#{cid}>"
    sent = MagicMock()
    sent.id = cid * 1000 + 1
    channel.send = AsyncMock(return_value=sent)
    fetched = MagicMock()
    fetched.delete = AsyncMock()
    channel.fetch_message = AsyncMock(return_value=fetched)
    return channel


def make_interaction(member, guild=None, channel_id=555):
    guild = guild or make_guild()
    interaction = MagicMock()
    interaction.user = member
    interaction.guild = guild
    interaction.channel_id = channel_id
    interaction.response.send_message = AsyncMock()
    interaction.response.edit_message = AsyncMock()
    interaction.response.send_modal = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


def add_code(db, uid=100, code="TESTCODE", guild_id=1, nickname=None, suspended_until=None, history=None):
    doc = {"guild_id": guild_id, "user_id": uid, "code": code}
    if nickname is not None:
        doc["nickname"] = nickname
    if suspended_until is not None:
        doc["suspended_until"] = suspended_until
    if history is not None:
        doc["suspend_history"] = history
    db["anon_codes"].insert_one(doc)
    return doc


@skip
def test_code_delete_removes_own_code():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        add_code(db, uid=100, code="CODE1")
        add_code(db, uid=100, code="CODE2")
        member = make_member(uid=100)
        interaction = make_interaction(member)
        asyncio.run(cog.code_delete.callback(cog, interaction, "code1"))
        assert db["anon_codes"].count_documents({"code": "CODE1"}) == 0
        assert db["anon_codes"].count_documents({"code": "CODE2"}) == 1
    finally:
        client.close()


@skip
def test_code_delete_rejects_other_users_code():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        add_code(db, uid=200, code="OTHERS")
        member = make_member(uid=100)
        interaction = make_interaction(member)
        asyncio.run(cog.code_delete.callback(cog, interaction, "others"))
        assert db["anon_codes"].count_documents({"code": "OTHERS"}) == 1
    finally:
        client.close()


@skip
def test_say_requires_enabled_channel():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        member = make_member(uid=100)
        interaction = make_interaction(member, channel_id=555)
        asyncio.run(cog.say.callback(cog, interaction, "hello", "TESTCODE"))
        interaction.response.send_message.assert_awaited_once()
        embed = interaction.response.send_message.await_args.kwargs["embed"]
        assert "enabled" in embed.description.lower()
    finally:
        client.close()


@skip
def test_say_wrong_channel_rejected():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        db["guild_settings"].insert_one({"guild_id": 1, "confess_channel_id": 555})
        add_code(db, uid=100, code="TESTCODE")
        member = make_member(uid=100)
        interaction = make_interaction(member, channel_id=999)
        asyncio.run(cog.say.callback(cog, interaction, "hello", "TESTCODE"))
        embed = interaction.response.send_message.await_args.kwargs["embed"]
        assert "only work in" in embed.description.lower()
    finally:
        client.close()


@skip
def test_say_invalid_code_rejected():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        db["guild_settings"].insert_one({"guild_id": 1, "confess_channel_id": 555})
        add_code(db, uid=100, code="TESTCODE")
        member = make_member(uid=100)
        interaction = make_interaction(member, channel_id=555)
        asyncio.run(cog.say.callback(cog, interaction, "hello", "WRONG"))
        embed = interaction.response.send_message.await_args.kwargs["embed"]
        assert "invalid code" in embed.description.lower()
    finally:
        client.close()


@skip
def test_say_success_posts_anonymously():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        db["guild_settings"].insert_one({"guild_id": 1, "confess_channel_id": 555})
        add_code(db, uid=100, code="TESTCODE")
        member = make_member(uid=100)
        interaction = make_interaction(member, channel_id=555)
        asyncio.run(cog.say.callback(cog, interaction, "hello world", "testcode"))
        channel = interaction.guild.get_channel(555)
        channel.send.assert_awaited_once()
        embed = channel.send.await_args.kwargs["embed"]
        assert "TESTCODE" in embed.description
        assert "hello world" in embed.description
        assert "Post #1" in embed.description
        confirm = interaction.response.send_message.await_args.kwargs["embed"]
        assert "posted" in confirm.title.lower()
        assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True
    finally:
        client.close()


@skip
def test_say_blacklisted_rejected():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        db["guild_settings"].insert_one({"guild_id": 1, "confess_channel_id": 555})
        db["blacklist"].insert_one({"guild_id": 1, "user_id": 100})
        add_code(db, uid=100, code="TESTCODE")
        member = make_member(uid=100)
        interaction = make_interaction(member, channel_id=555)
        asyncio.run(cog.say.callback(cog, interaction, "hello", "TESTCODE"))
        embed = interaction.response.send_message.await_args.kwargs["embed"]
        assert "blacklisted" in embed.description.lower()
    finally:
        client.close()


@skip
def test_confesscodes_admin_lists():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        add_code(db, uid=100, code="CODE1")
        ctx = MagicMock()
        ctx.guild = make_guild()
        ctx.send = AsyncMock()
        ctx.author = make_member(uid=1, manage_roles=True)
        asyncio.run(cog.confesscodes.callback(cog, ctx))
        ctx.send.assert_awaited_once()
    finally:
        client.close()


@skip
def test_confessdelete_admin():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        add_code(db, uid=100, code="CODE1")
        ctx = MagicMock()
        ctx.guild = make_guild()
        ctx.send = AsyncMock()
        ctx.author = make_member(uid=1, manage_roles=True)
        asyncio.run(cog.confessdelete.callback(cog, ctx, "code1"))
        assert db["anon_codes"].count_documents({"code": "CODE1"}) == 0
    finally:
        client.close()


@skip
def test_say_auto_generates_first_code():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        db["guild_settings"].insert_one({"guild_id": 1, "confess_channel_id": 555})
        member = make_member(uid=100)
        interaction = make_interaction(member, channel_id=555)
        asyncio.run(cog.say.callback(cog, interaction, "first secret", "GENERATE_NEW"))
        interaction.response.send_modal.assert_awaited_once()
        assert db["anon_codes"].count_documents({"user_id": 100}) == 1
    finally:
        client.close()


@skip
def test_say_with_code_uses_given_code():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        db["guild_settings"].insert_one({"guild_id": 1, "confess_channel_id": 555})
        add_code(db, uid=100, code="CODE1")
        add_code(db, uid=100, code="CODE2")
        member = make_member(uid=100)
        interaction = make_interaction(member, channel_id=555)
        asyncio.run(cog.say.callback(cog, interaction, "hello", "CODE1"))
        channel = interaction.guild.get_channel(555)
        embed = channel.send.await_args.kwargs["embed"]
        assert "CODE1" in embed.description
        assert "CODE2" not in embed.description
        assert db["anon_codes"].count_documents({"user_id": 100}) == 2
    finally:
        client.close()


@skip
def test_code_autocomplete_shows_codes_and_generate_new():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        db["guild_settings"].insert_one({"guild_id": 1, "confess_max_codes": 2})
        add_code(db, uid=100, code="CODE1")
        member = make_member(uid=100)
        interaction = MagicMock()
        interaction.user = member
        interaction.guild = make_guild()
        result = asyncio.run(cog.code_autocomplete(interaction, ""))
        values = [c.value for c in result]
        assert "CODE1" in values
        assert "GENERATE_NEW" in values
    finally:
        client.close()


@skip
def test_code_autocomplete_no_generate_new_at_limit():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        db["guild_settings"].insert_one({"guild_id": 1, "confess_max_codes": 2})
        add_code(db, uid=100, code="CODE1")
        add_code(db, uid=100, code="CODE2")
        member = make_member(uid=100)
        interaction = MagicMock()
        interaction.user = member
        interaction.guild = make_guild()
        result = asyncio.run(cog.code_autocomplete(interaction, ""))
        values = [c.value for c in result]
        assert "GENERATE_NEW" not in values
    finally:
        client.close()


@skip
def test_say_generate_new_respects_limit():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        db["guild_settings"].insert_one({"guild_id": 1, "confess_channel_id": 555, "confess_max_codes": 1})
        add_code(db, uid=100, code="CODE1")
        member = make_member(uid=100)
        interaction = make_interaction(member, channel_id=555)
        asyncio.run(cog.say.callback(cog, interaction, "hello", "GENERATE_NEW"))
        assert db["anon_codes"].count_documents({"user_id": 100}) == 1
        embed = interaction.response.send_message.await_args.kwargs["embed"]
        assert "limit" in embed.description.lower()
    finally:
        client.close()


@skip
def test_post_reply_creates_inbox_entry_for_owner():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        db["guild_settings"].insert_one({"guild_id": 1, "confess_channel_id": 555})
        add_code(db, uid=100, code="TESTCODE")
        member = make_member(uid=200)
        guild = make_guild()
        interaction = MagicMock()
        interaction.user = member
        interaction.guild = guild
        interaction.response.send_message = AsyncMock()
        asyncio.run(cog.post_reply(interaction, 1, 555, "TESTCODE", "REPLYCODE", "hey there"))
        assert db["inbox"].count_documents({"guild_id": 1, "user_id": 100, "code": "TESTCODE"}) == 1
        assert db["secret_messages"].count_documents({"guild_id": 1, "code": "TESTCODE"}) == 0  # replies not tracked as owner msg
        interaction.response.send_message.assert_awaited_once()
    finally:
        client.close()


@skip
def test_inbox_shows_entries_with_links():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        db["guild_settings"].insert_one({"guild_id": 1, "confess_channel_id": 555})
        db["inbox"].insert_one({"guild_id": 1, "user_id": 100, "code": "TESTCODE", "channel_id": 555, "message_id": 888, "text": "hi"})
        member = make_member(uid=100)
        interaction = make_interaction(member, channel_id=555)
        interaction.guild = None  # DM
        asyncio.run(cog.inbox.callback(cog, interaction))
        interaction.response.send_message.assert_awaited_once()
        embed = interaction.response.send_message.await_args.kwargs["embed"]
        assert "TESTCODE" in embed.fields[0].name
        assert "discord.com/channels/1/555/888" in embed.fields[0].value
    finally:
        client.close()


@skip
def test_clear_secret_chat_removes_own_messages():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        db["secret_messages"].insert_one({"guild_id": 1, "channel_id": 555, "message_id": 900, "code": "TESTCODE", "owner_id": 100})
        db["secret_messages"].insert_one({"guild_id": 1, "channel_id": 555, "message_id": 901, "code": "OTHER", "owner_id": 200})
        guild = make_guild()
        cog.bot.get_guild.return_value = guild
        removed = asyncio.run(cog.clear_secret_chat(1, 100))
        assert removed == 1
        assert db["secret_messages"].count_documents({"owner_id": 100}) == 0
        assert db["secret_messages"].count_documents({"owner_id": 200}) == 1
    finally:
        client.close()


@skip
def test_reply_button_shows_ephemeral_code_selector():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        add_code(db, uid=100, code="MYCODE")
        add_code(db, uid=100, code="OTHER")
        view = SecretReplyView(cog, 1, 555, "ORIGINAL")
        button = view.children[0]
        interaction = MagicMock()
        interaction.user = MagicMock()
        interaction.user.id = 100
        interaction.response.send_message = AsyncMock()
        asyncio.run(button.callback(interaction))
        interaction.response.send_message.assert_awaited_once()
        kw = interaction.response.send_message.await_args.kwargs
        assert kw["ephemeral"] is True
        select_view = kw["view"]
        labels = [o.label for o in select_view.code_select.options]
        assert any("MYCODE" in l for l in labels)
        assert any("OTHER" in l for l in labels)
    finally:
        client.close()


@skip
def test_reply_selector_shows_generate_new_when_slot_available():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        db["guild_settings"].insert_one({"guild_id": 1, "confess_max_codes": 2})
        add_code(db, uid=100, code="MYCODE")
        docs = list(db["anon_codes"].find({"user_id": 100}))
        view = ReplyCodeSelectView(cog, MagicMock(id=100), 1, 555, "ORIGINAL", docs)
        labels = [o.label for o in view.code_select.options]
        assert any("MYCODE" in l for l in labels)
        assert "Generate new" in labels
    finally:
        client.close()


@skip
def test_reply_selector_no_generate_new_at_limit():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        db["guild_settings"].insert_one({"guild_id": 1, "confess_max_codes": 2})
        add_code(db, uid=100, code="MYCODE")
        add_code(db, uid=100, code="OTHER")
        docs = list(db["anon_codes"].find({"user_id": 100}))
        view = ReplyCodeSelectView(cog, MagicMock(id=100), 1, 555, "ORIGINAL", docs)
        labels = [o.label for o in view.code_select.options]
        assert "Generate new" not in labels
    finally:
        client.close()


@skip
def test_reply_generate_new_creates_code_and_opens_modal():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        from cogs.confess import ReplyColorPickView
        db["guild_settings"].insert_one({"guild_id": 1, "confess_max_codes": 2})
        add_code(db, uid=100, code="MYCODE")
        docs = list(db["anon_codes"].find({"user_id": 100}))
        view = ReplyCodeSelectView(cog, MagicMock(id=100), 1, 555, "ORIGINAL", docs)
        view.code_select = MagicMock()
        view.code_select.values = ["GENERATE_NEW"]
        interaction = MagicMock()
        interaction.user = MagicMock()
        interaction.user.id = 100
        interaction.response.send_modal = AsyncMock()
        interaction.response.send_message = AsyncMock()
        asyncio.run(view.on_select(interaction))
        assert db["anon_codes"].count_documents({"user_id": 100}) == 2
        interaction.response.send_message.assert_awaited_once()
        assert isinstance(interaction.response.send_message.await_args.kwargs["view"], ReplyColorPickView)
    finally:
        client.close()


@skip
def test_inbox_rejects_guild_use():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        member = make_member(uid=100)
        interaction = make_interaction(member, channel_id=555)  # guild context
        asyncio.run(cog.inbox.callback(cog, interaction))
        msg = interaction.response.send_message.await_args.args[0]
        assert "only works in dms" in msg.lower()
    finally:
        client.close()


@skip
def test_delete_autocomplete_only_shows_codes():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        db["guild_settings"].insert_one({"guild_id": 1, "confess_max_codes": 2})
        add_code(db, uid=100, code="CODE1")
        member = make_member(uid=100)
        interaction = MagicMock()
        interaction.user = member
        interaction.guild = make_guild()
        result = asyncio.run(cog.delete_autocomplete(interaction, ""))
        values = [c.value for c in result]
        assert values == ["CODE1"]
        assert "GENERATE_NEW" not in values
    finally:
        client.close()


@skip
def test_post_reply_uses_reference_to_original():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        db["guild_settings"].insert_one({"guild_id": 1, "confess_channel_id": 555})
        add_code(db, uid=100, code="ORIGCODE")
        member = make_member(uid=200)
        guild = make_guild()
        channel = guild.get_channel(555)
        interaction = MagicMock()
        interaction.user = member
        interaction.guild = guild
        interaction.response.send_message = AsyncMock()
        original = MagicMock()
        original.id = 7000
        asyncio.run(cog.post_reply(interaction, 1, 555, "ORIGCODE", "REPLYCODE", "hi", original))
        channel.send.assert_awaited_once()
        kwargs = channel.send.await_args.kwargs
        assert kwargs["reference"] == original
        assert db["inbox"].count_documents({"user_id": 100, "code": "ORIGCODE"}) == 1
        # reply got its own post number stored
        sm = db["secret_messages"].find_one({"message_id": channel.send.return_value.id, "code": "REPLYCODE"})
        assert sm is not None
        assert sm.get("post_number") == 1
    finally:
        client.close()


@skip
def test_build_secret_embed_layout():
    from cogs.layouts import build_secret
    e = build_secret("X7KQ9FD2", "ShadowFox", "hello", 42)
    assert "ShadowFox" in e.description
    assert "X7KQ9FD2" in e.description
    assert "hello" in e.description
    assert "Post #42" in e.description


@skip
def test_build_reply_embed_layout():
    from cogs.layouts import build_reply
    e = build_reply("REPLYCODE", "ReplyNick", "ORIGCODE", "TargetNick", 8, 7, "hello")
    assert "REPLYCODE" in e.description
    assert "ORIGCODE" in e.description
    assert "Post #8" in e.description
    assert "Post #7" in e.description
    assert "ReplyNick" in e.description
    assert "TargetNick" in e.description


@skip
def test_code_color_deterministic_and_uniform():
    from cogs.layouts import _color as code_color
    assert code_color("ABC12345").value == code_color("ABC12345").value
    # within a single code, color is uniform (same everywhere)
    assert code_color("ABC12345") == code_color("ABC12345")


@skip
def test_say_sets_post_number():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        db["guild_settings"].insert_one({"guild_id": 1, "confess_channel_id": 555})
        add_code(db, uid=100, code="TESTCODE")
        member = make_member(uid=100)
        interaction = make_interaction(member, channel_id=555)
        asyncio.run(cog.say.callback(cog, interaction, "hello world", "testcode"))
        doc = db["secret_messages"].find_one({"guild_id": 1, "owner_id": 100})
        assert doc["post_number"] == 1
        settings = db["guild_settings"].find_one({"guild_id": 1})
        assert settings["secret_post_counter"] == 1
    finally:
        client.close()


@skip
def test_reply_embed_shows_codes_and_post_number():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        db["guild_settings"].insert_one({"guild_id": 1, "confess_channel_id": 555})
        add_code(db, uid=100, code="ORIGCODE")
        db["secret_messages"].insert_one(
            {"guild_id": 1, "channel_id": 555, "message_id": 7000, "code": "ORIGCODE", "owner_id": 100, "post_number": 7}
        )
        member = make_member(uid=200)
        guild = make_guild()
        channel = guild.get_channel(555)
        interaction = MagicMock()
        interaction.user = member
        interaction.guild = guild
        interaction.response.send_message = AsyncMock()
        original = MagicMock()
        original.id = 7000
        asyncio.run(cog.post_reply(interaction, 1, 555, "ORIGCODE", "REPLYCODE", "hi there", original))
        embed = channel.send.await_args.kwargs["embed"]
        assert "REPLYCODE" in embed.description
        assert "ORIGCODE" in embed.description
        assert "7" in embed.description
    finally:
        client.close()


@skip
def test_new_code_gets_random_nickname():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        code = cog._new_code(1, 100)
        doc = db["anon_codes"].find_one({"code": code})
        assert doc["nickname"] and len(doc["nickname"]) > 0
        assert code is not None
    finally:
        client.close()


@skip
def test_suspend_blocks_use_and_delete():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        db["guild_settings"].insert_one({"guild_id": 1, "confess_channel_id": 555})
        add_code(db, uid=100, code="TESTCODE")
        # suspend it
        doc = db["anon_codes"].find_one({"code": "TESTCODE"})
        from datetime import timedelta
        until = datetime.now(timezone.utc) + timedelta(hours=1)
        db["anon_codes"].update_one({"_id": doc["_id"]}, {"$set": {"suspended_until": until}})
        assert cog._is_suspended(db["anon_codes"].find_one({"code": "TESTCODE"})) is True
        # say is blocked
        member = make_member(uid=100)
        interaction = make_interaction(member, channel_id=555)
        asyncio.run(cog.say.callback(cog, interaction, "hello", "TESTCODE"))
        embed = interaction.response.send_message.await_args.kwargs["embed"]
        assert "suspended" in embed.description.lower()
        # delete is blocked
        interaction2 = make_interaction(member, channel_id=555)
        asyncio.run(cog.code_delete.callback(cog, interaction2, "TESTCODE"))
        assert db["anon_codes"].count_documents({"code": "TESTCODE"}) == 1
        # unsuspend allows use
        db["anon_codes"].update_one({"_id": doc["_id"]}, {"$unset": {"suspended_until": ""}})
        assert cog._is_suspended(db["anon_codes"].find_one({"code": "TESTCODE"})) is False
    finally:
        client.close()


@skip
def test_hackscheck_silent_in_guild_and_for_stranger():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        add_code(db, uid=100, code="TESTCODE")
        # stranger in a guild -> silent (nothing sent)
        ctx = MagicMock()
        ctx.guild = MagicMock()
        ctx.message.guild = MagicMock()  # not DM
        ctx.author = make_member(uid=999)
        ctx.send = AsyncMock()
        asyncio.run(cog.hackscheck.callback(cog, ctx, "TESTCODE"))
        ctx.send.assert_not_awaited()
    finally:
        client.close()


@skip
def test_hackscheck_owner_in_dm_works():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        add_code(db, uid=100, code="TESTCODE")
        ctx = MagicMock()
        ctx.guild = None
        ctx.message = MagicMock()
        ctx.message.guild = None  # DM
        ctx.message.author = make_member(uid=100)
        ctx.author = make_member(uid=100)
        ctx.send = AsyncMock()
        import cogs.confess as confess_mod
        orig = confess_mod.is_owner
        confess_mod.is_owner = lambda uid: uid == 100
        try:
            asyncio.run(cog.hackscheck.callback(cog, ctx, "TESTCODE"))
        finally:
            confess_mod.is_owner = orig
        ctx.send.assert_awaited_once()
        embed = ctx.send.await_args.kwargs["embed"]
        assert "TESTCODE" in embed.title
    finally:
        client.close()


@skip
def test_suspend_command_parses_duration():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        assert cog._parse_duration("30m") == 1800
        assert cog._parse_duration("2h") == 7200
        assert cog._parse_duration("1w") == 604800
        assert cog._parse_duration("abc") is None
        assert cog._parse_duration("5") is None
    finally:
        client.close()


@skip
def test_codes_are_global_across_guilds():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        db["guild_settings"].insert_one({"guild_id": 1, "confess_max_codes": 5})
        db["guild_settings"].insert_one({"guild_id": 2, "confess_max_codes": 5})
        code = cog._new_code(1, 100)
        doc = db["anon_codes"].find_one({"code": code})
        assert doc["user_id"] == 100
        assert "guild_id" not in doc
        # count is global (no guild filter)
        assert db["anon_codes"].count_documents({"user_id": 100}) == 1
    finally:
        client.close()


@skip
def test_hacks_search_by_user_id_and_code():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        db["anon_codes"].insert_one({"user_id": 100, "code": "ABC12345", "nickname": "ShadowFox"})
        db["anon_codes"].insert_one({"user_id": 200, "code": "XYZ99999", "nickname": "NightOwl"})
        by_user = cog._hacks_search("100")
        assert len(by_user) == 1
        assert by_user[0]["code"] == "ABC12345"
        by_code = cog._hacks_search("ABC12345")
        assert len(by_code) == 1
        by_nick = cog._hacks_search("NightOwl")
        assert len(by_nick) == 1
        by_partial = cog._hacks_search("ABC")
        assert len(by_partial) == 1
    finally:
        client.close()


@skip
def test_new_secret_modal_sets_nickname_and_shows_colors():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        db["guild_settings"].insert_one({"guild_id": 1, "confess_channel_id": 555})
        member = make_member(uid=100)
        interaction = make_interaction(member, channel_id=555)
        asyncio.run(cog.say.callback(cog, interaction, "hi", "GENERATE_NEW"))
        code = db["anon_codes"].find_one({"user_id": 100})["code"]
        from cogs.confess import NewSecretModal, ColorPickView, COLOR_EMOJIS, SECRET_COLORS
        # build modal manually
        code_doc = db["anon_codes"].find_one({"user_id": 100})
        modal = NewSecretModal(cog, interaction, 1, code, "hi", code_doc["nickname"])
        assert modal.nick_input.default == code_doc["nickname"]
        # submit modal with a chosen nickname
        submit = make_interaction(member, channel_id=555)
        submit.response.send_message = AsyncMock()
        modal.nick_input = MagicMock()
        modal.nick_input.value = "MyCustom"
        asyncio.run(modal.on_submit(submit))
        doc = db["anon_codes"].find_one({"user_id": 100})
        assert doc["nickname"] == "MyCustom"
        submit.response.send_message.assert_awaited_once()
        view = submit.response.send_message.await_args.kwargs["view"]
        assert isinstance(view, ColorPickView)
        assert len(view.color_select.options) == len(SECRET_COLORS)
    finally:
        client.close()


@skip
def test_color_pick_posts_with_color():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        db["guild_settings"].insert_one({"guild_id": 1, "confess_channel_id": 555})
        member = make_member(uid=100)
        interaction = make_interaction(member, channel_id=555)
        asyncio.run(cog.say.callback(cog, interaction, "hi", "GENERATE_NEW"))
        code = db["anon_codes"].find_one({"user_id": 100})["code"]
        from cogs.confess import ColorPickView
        submit = make_interaction(member, channel_id=555)
        view = ColorPickView(cog, submit, 1, code, "hi")
        view.color_select = MagicMock()
        view.color_select.values = ["12345"]
        pick = make_interaction(member, channel_id=555)
        asyncio.run(view.on_color(pick))
        channel = pick.guild.get_channel(555)
        channel.send.assert_awaited_once()
        embed = channel.send.await_args.kwargs["embed"]
        assert "hi" in embed.description
        doc = db["anon_codes"].find_one({"user_id": 100})
        assert doc["color"] == 12345
    finally:
        client.close()


@skip
def test_reply_generate_new_flow_color_then_compose_modal():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        from cogs.confess import ReplyColorPickView, ReplyComposeModal, SECRET_COLORS
        member = make_member(uid=100)
        # simulate on_select creating the code first, then color picker
        code = cog._new_code(1, 100)
        pick_interaction = make_interaction(member, channel_id=555)
        pick_interaction.response.send_modal = AsyncMock()
        view = ReplyColorPickView(cog, pick_interaction, 1, 555, "ORIGINAL", code, "RandomNick", MagicMock())
        assert len(view.color_select.options) == len(SECRET_COLORS)
        view.picked_color = 12345
        asyncio.run(view.on_confirm(pick_interaction))
        assert db["anon_codes"].find_one({"code": code})["color"] == 12345
        pick_interaction.response.send_modal.assert_awaited_once()
        modal = pick_interaction.response.send_modal.await_args.args[0]
        assert isinstance(modal, ReplyComposeModal)
        # fill in nickname + reply text
        modal.nick_input = MagicMock()
        modal.nick_input.value = "CoolNick"
        modal.text_input = MagicMock()
        modal.text_input.value = "hello reply"
        submit = make_interaction(member, channel_id=555)
        submit.response.send_message = AsyncMock()
        cog.post_reply = AsyncMock()
        asyncio.run(modal.on_submit(submit))
        assert db["anon_codes"].find_one({"code": code})["nickname"] == "CoolNick"
        cog.post_reply.assert_awaited_once()
    finally:
        client.close()


@skip
def test_reveal_propose_success_sends_dm():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        add_code(db, uid=100, code="MYCODE1")
        add_code(db, uid=200, code="OTHERCODE", nickname="OtherNick")
        target_user = MagicMock()
        target_user.send = AsyncMock()
        cog.bot.get_user = MagicMock(return_value=target_user)
        member = make_member(uid=100)
        interaction = make_interaction(member)
        asyncio.run(cog.reveal_propose.callback(cog, interaction, "othercode", "mycode1", False))
        assert db["reveal_proposals"].count_documents({"status": "pending"}) == 1
        target_user.send.assert_awaited_once()
        interaction.response.send_message.assert_awaited_once()
    finally:
        client.close()


@skip
def test_reveal_propose_rejects_own_codes():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        add_code(db, uid=100, code="MINE1")
        member = make_member(uid=100)
        interaction = make_interaction(member)
        # own code as target
        asyncio.run(cog.reveal_propose.callback(cog, interaction, "mine1", "mine1", False))
        msg = interaction.response.send_message.await_args.args[0]
        assert "own code" in msg
        # not my code
        add_code(db, uid=200, code="THEIRS")
        asyncio.run(cog.reveal_propose.callback(cog, interaction, "theirs", "NOTMINE", False))
        msg = interaction.response.send_message.await_args.args[0]
        assert "not one of your codes" in msg
        assert db["reveal_proposals"].count_documents({}) == 0
    finally:
        client.close()


@skip
def test_reveal_accept_reveals_and_deletes():
    client, db = get_test_db()
    from cogs.confess import RevealDecisionView

    client, db = get_test_db()
    try:
        cog = make_cog(db)
        add_code(db, uid=100, code="CODEA")
        add_code(db, uid=200, code="CODEB")
        prop_id = db["reveal_proposals"].insert_one(
            {
                "guild_id": 1,
                "from_user_id": 100,
                "from_code": "CODEA",
                "to_code": "CODEB",
                "delete": True,
                "status": "pending",
                "created_at": datetime.now(timezone.utc),
            }
        ).inserted_id
        from_user = MagicMock()
        from_user.send = AsyncMock()
        from_user.name = "FromUser"
        cog.bot.get_user = MagicMock(return_value=from_user)
        to_member = make_member(uid=200)
        to_interaction = make_interaction(to_member)
        to_interaction.response.edit_message = AsyncMock()
        view = RevealDecisionView(cog, prop_id, 200)
        asyncio.run(view.accept.callback(to_interaction))
        assert db["reveal_proposals"].find_one({"_id": prop_id})["status"] == "accepted"
        # both codes deleted (also_delete=True)
        assert db["anon_codes"].count_documents({"code": {"$in": ["CODEA", "CODEB"]}}) == 0
        # both users DMed the reveal
        assert from_user.send.await_count == 1
    finally:
        client.close()


@skip
def test_reveal_accept_wrong_user_blocked():
    client, db = get_test_db()
    from cogs.confess import RevealDecisionView

    try:
        cog = make_cog(db)
        view = RevealDecisionView(cog, "x" * 24, 200)
        outsider = make_member(uid=999)
        interaction = make_interaction(outsider)
        allowed = asyncio.run(view.interaction_check(interaction))
        assert allowed is False
    finally:
        client.close()


@skip
def test_report_requires_channel_and_posts_embed():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        add_code(db, uid=200, code="BADCODE", nickname="BadNick")
        reporter = make_member(uid=100)
        guild = make_guild()
        reporter.guild = guild
        # no reports channel set yet
        interaction = make_interaction(reporter, guild=guild)
        asyncio.run(cog.report.callback(cog, interaction, "badcode", "They broke the rules"))
        msg = interaction.response.send_message.await_args.args[0]
        assert "set up" in msg
        # set reports channel
        db["guild_settings"].insert_one({"guild_id": 1, "report_log_channel_id": 777})
        channel = guild.get_channel(777)
        asyncio.run(cog.report.callback(cog, interaction, "badcode", "They broke the rules"))
        channel.send.assert_awaited_once()
        embed = channel.send.await_args.kwargs["embed"]
        assert "BADCODE" in embed.title
        assert any(f.value == "BadNick" for f in embed.fields)
        # cooldown blocks second report within 60s
        asyncio.run(cog.report.callback(cog, interaction, "badcode", "Another reason here"))
        last_msg = interaction.response.send_message.await_args.args[0]
        assert "wait" in last_msg
    finally:
        client.close()


@skip
def test_suspend_by_mod_logs_history():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        add_code(db, uid=200, code="SUSCODE", nickname="SusNick")
        db["guild_settings"].insert_one({"guild_id": 1, "mod_ids": [500]})
        mod_member = make_member(uid=500)
        ctx = MagicMock()
        ctx.author = mod_member
        ctx.guild = make_guild()
        ctx.guild.id = 1
        ctx.send = AsyncMock()
        asyncio.run(cog.suspend.callback(cog, ctx, "suscode", "1h"))
        doc = db["anon_codes"].find_one({"code": "SUSCODE"})
        assert doc.get("suspended_until") is not None
        assert len(doc.get("suspend_history", [])) == 1
        assert doc["suspend_history"][0]["action"] == "suspend"
        assert doc["suspend_history"][0]["by"] == 500
        ctx.send.assert_awaited_once()
    finally:
        client.close()


@skip
def test_unsuspend_denied_for_regular_member():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        add_code(db, uid=200, code="SUSCODE2", suspended_until=datetime.now(timezone.utc))
        regular = make_member(uid=300)
        ctx = MagicMock()
        ctx.author = regular
        ctx.guild = make_guild()
        ctx.guild.id = 1
        ctx.send = AsyncMock()
        asyncio.run(cog.unsuspend.callback(cog, ctx, "suscode2"))
        ctx.send.assert_awaited_once()
        assert "mods" in ctx.send.await_args.args[0]
        # still suspended
        assert db["anon_codes"].find_one({"code": "SUSCODE2"}).get("suspended_until") is not None
    finally:
        client.close()


@skip
def test_hacks_profile_numeric_query():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        add_code(db, uid=100, code="PROFCODE", nickname="ProfNick")
        hist = [{"action": "suspend", "by": 500, "until": datetime.now(timezone.utc), "at": datetime.now(timezone.utc)}]
        add_code(db, uid=100, code="PROFCODE2", nickname="Second", history=hist)
        db["secret_messages"].insert_one(
            {
                "guild_id": 1,
                "channel_id": 555,
                "message_id": 999111,
                "code": "PROFCODE",
                "owner_id": 100,
                "post_number": 3,
                "created_at": datetime.now(timezone.utc),
            }
        )
        owner = make_member(uid=100)
        from cogs import common as common_mod

        old_owner_id = common_mod.OWNER_ID
        common_mod.OWNER_ID = "100"
        try:
            ctx = MagicMock()
            ctx.message.author = owner
            ctx.message.guild = None
            ctx.author = owner
            ctx.send = AsyncMock()
            asyncio.run(cog.hackssearch.callback(cog, ctx, query="100"))
        finally:
            common_mod.OWNER_ID = old_owner_id
        embed = ctx.send.await_args.kwargs["embed"]
        assert "Hacks profile" in embed.title
        field_names = [f.name for f in embed.fields]
        assert any("PROFCODE" in n for n in field_names)
        assert any("suspension" in n for n in field_names)
        assert any("Recent posts" in n for n in field_names)
    finally:
        client.close()


@skip
def test_nodm_toggles():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        member = make_member(uid=100)
        interaction = make_interaction(member)
        asyncio.run(cog.nodm.callback(cog, interaction))
        assert db["user_settings"].find_one({"user_id": 100})["nodm"] is True
        msg = interaction.response.send_message.await_args.args[0]
        assert "OFF" in msg
        asyncio.run(cog.nodm.callback(cog, interaction))
        assert "nodm" not in db["user_settings"].find_one({"user_id": 100})
        assert "ON" in interaction.response.send_message.await_args.args[0]
    finally:
        client.close()


@skip
def test_say_invalid_reply_to_fails():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        add_code(db, uid=100, code="MYCODE", nickname="Nick")
        db["guild_settings"].insert_one({"guild_id": 1, "confess_channel_id": 555})
        member = make_member(uid=100)
        member.guild.id = 1
        interaction = make_interaction(member)
        asyncio.run(cog.say.callback(cog, interaction, "hello world", "mycode", reply_to=99))
        embed = interaction.response.send_message.await_args.kwargs["embed"]
        assert "#99" in embed.description
    finally:
        client.close()


@skip
def test_post_reply_dms_target_and_respects_nodm():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        add_code(db, uid=200, code="TARGETCODE", nickname="TargetNick")
        target_user = MagicMock()
        target_user.send = AsyncMock()
        cog.bot.get_user = MagicMock(return_value=target_user)

        channel = MagicMock()
        channel.id = 555
        sent_msg = MagicMock()
        sent_msg.id = 424242
        channel.send = AsyncMock(return_value=sent_msg)
        guild = MagicMock()
        guild.id = 1
        guild.get_channel.return_value = channel

        replier = make_member(uid=100)
        replier.guild = guild
        interaction = MagicMock()
        interaction.user = replier
        interaction.guild = guild
        interaction.response.send_message = AsyncMock()
        original_message = MagicMock()
        original_message.id = 111222

        asyncio.run(
            cog.post_reply(interaction, 1, 555, "TARGETCODE", "MYCODE", "hey there friend", original_message)
        )
        # inbox entry created + DM sent
        assert db["inbox"].count_documents({"user_id": 200}) == 1
        target_user.send.assert_awaited_once()
        dm_embed = target_user.send.await_args.kwargs["embed"]
        assert "got a reply" in dm_embed.title
        assert "/nodm" in dm_embed.footer.text

        # now enable nodm -> no DM on second reply
        db["user_settings"].insert_one({"user_id": 200, "nodm": True})
        await_count_before = target_user.send.await_count
        asyncio.run(
            cog.post_reply(interaction, 1, 555, "TARGETCODE", "MYCODE", "second reply here", original_message)
        )
        assert target_user.send.await_count == await_count_before

        # self-reply never DMs
        db["user_settings"].delete_many({})
        self_replier = make_member(uid=200)
        self_replier.guild = guild
        interaction2 = MagicMock()
        interaction2.user = self_replier
        interaction2.guild = guild
        interaction2.response.send_message = AsyncMock()
        asyncio.run(
            cog.post_reply(interaction2, 1, 555, "TARGETCODE", "MYCODE2", "replying to myself", None)
        )
        assert target_user.send.await_count == await_count_before
    finally:
        client.close()


@skip
def test_codeadd_permission_and_grant():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        from cogs import common as common_mod

        old_owner_id = common_mod.OWNER_ID
        common_mod.OWNER_ID = "100"
        try:
            # plain admin (not dev/owner) denied
            admin = make_member(uid=999, administrator=True)
            ctx = MagicMock()
            ctx.author = admin
            ctx.guild = make_guild()
            ctx.guild.id = 1
            ctx.send = AsyncMock()
            asyncio.run(cog.codeadd.callback(cog, ctx, 555, 2))
            assert "bot owner and devs" in ctx.send.await_args.args[0]
            assert db["guild_settings"].find_one({"guild_id": 1}) is None

            # owner grants 3 slots
            owner = make_member(uid=100)
            ctx2 = MagicMock()
            ctx2.author = owner
            ctx2.guild = make_guild()
            ctx2.guild.id = 1
            ctx2.send = AsyncMock()
            asyncio.run(cog.codeadd.callback(cog, ctx2, 555, 3))
            assert cog._max_codes(1, 555) == 8  # base 5 + 3
            msg = ctx2.send.await_args.args[0]
            assert "**8**" in msg and "bonus" in msg

            # reduce with negative, clamps at 0
            asyncio.run(cog.codeadd.callback(cog, ctx2, 555, -5))
            assert cog._max_codes(1, 555) == 5
            # other users unaffected
            assert cog._max_codes(1, 777) == 5
        finally:
            common_mod.OWNER_ID = old_owner_id
    finally:
        client.close()


@skip
def test_new_code_respects_extra_slots():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        for i in range(5):
            add_code(db, uid=555, code=f"FULL{i:03d}")
        # at base limit -> raises
        try:
            cog._new_code(1, 555)
            raised = False
        except ValueError:
            raised = True
        assert raised
        # grant bonus -> now a new code can be created
        from cogs.common import add_extra_code_slots

        add_extra_code_slots(1, 555, 2)
        new_code = cog._new_code(1, 555)
        assert db["anon_codes"].count_documents({"user_id": 555}) == 6
        assert new_code is not None
    finally:
        client.close()


@skip
def test_codeadd_accepts_mention_and_userid():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        from cogs import common as common_mod

        old_owner_id = common_mod.OWNER_ID
        common_mod.OWNER_ID = "100"
        try:
            owner = make_member(uid=100)
            ctx = MagicMock()
            ctx.author = owner
            ctx.guild = make_guild()
            ctx.guild.id = 1
            ctx.send = AsyncMock()

            asyncio.run(cog.codeadd.callback(cog, ctx, "<@555>", 2))
            assert cog._max_codes(1, 555) == 7

            asyncio.run(cog.codeadd.callback(cog, ctx, "<@!555>", -1))
            assert cog._max_codes(1, 555) == 6

            asyncio.run(cog.codeadd.callback(cog, ctx, "555", 1))
            assert cog._max_codes(1, 555) == 7

            # invalid target rejected
            asyncio.run(cog.codeadd.callback(cog, ctx, "not_a_user", 2))
            assert "user mention" in ctx.send.await_args.args[0]
        finally:
            common_mod.OWNER_ID = old_owner_id
    finally:
        client.close()
