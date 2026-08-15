import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from cogs.confess import ConfessCog, SecretReplyButton, SecretReplyView

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
    interaction.followup.send = AsyncMock()
    return interaction


def add_code(db, uid=100, code="TESTCODE", guild_id=1):
    db["anon_codes"].insert_one({"guild_id": guild_id, "user_id": uid, "code": code})


@skip
def test_code_new_creates_code():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        member = make_member(uid=100)
        interaction = make_interaction(member)
        asyncio.run(cog.code_new.callback(cog, interaction))
        assert db["anon_codes"].count_documents({"guild_id": 1, "user_id": 100}) == 1
        interaction.response.send_message.assert_awaited_once()
    finally:
        client.close()


@skip
def test_code_new_limit_reached():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        db["guild_settings"].insert_one({"guild_id": 1, "confess_max_codes": 2})
        for i in range(2):
            add_code(db, uid=100, code=f"CODE{i}")
        member = make_member(uid=100)
        interaction = make_interaction(member)
        asyncio.run(cog.code_new.callback(cog, interaction))
        assert db["anon_codes"].count_documents({"guild_id": 1, "user_id": 100}) == 2
        msg = interaction.response.send_message.await_args.args[0]
        assert "limit" in msg.lower()
    finally:
        client.close()


@skip
def test_code_list_shows_codes():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        add_code(db, uid=100, code="CODE1")
        add_code(db, uid=100, code="CODE2")
        member = make_member(uid=100)
        interaction = make_interaction(member)
        asyncio.run(cog.code_list.callback(cog, interaction))
        msg = interaction.response.send_message.await_args.args[0]
        assert "CODE1" in msg
        assert "CODE2" in msg
    finally:
        client.close()


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
        assert db["anon_codes"].count_documents({"guild_id": 1, "code": "CODE1"}) == 0
        assert db["anon_codes"].count_documents({"guild_id": 1, "code": "CODE2"}) == 1
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
        assert db["anon_codes"].count_documents({"guild_id": 1, "code": "OTHERS"}) == 1
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
        msg = interaction.response.send_message.await_args.args[0]
        assert "enabled" in msg.lower()
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
        msg = interaction.response.send_message.await_args.args[0]
        assert "only work in" in msg.lower()
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
        msg = interaction.response.send_message.await_args.args[0]
        assert "invalid code" in msg.lower()
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
        fields = {f.name: f.value for f in embed.fields}
        assert "TESTCODE" in fields["Code"]
        assert "hello world" in fields["Message"]
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
        msg = interaction.response.send_message.await_args.args[0]
        assert "blacklisted" in msg.lower()
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
        assert db["anon_codes"].count_documents({"guild_id": 1, "code": "CODE1"}) == 0
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
        channel = interaction.guild.get_channel(555)
        channel.send.assert_awaited_once()
        embed = channel.send.await_args.kwargs["embed"]
        fields = {f.name: f.value for f in embed.fields}
        assert "first secret" in fields["Message"]
        assert db["anon_codes"].count_documents({"guild_id": 1, "user_id": 100}) == 1
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
        fields = {f.name: f.value for f in embed.fields}
        assert "CODE1" in fields["Code"]
        assert "CODE2" not in fields["Code"]
        assert db["anon_codes"].count_documents({"guild_id": 1, "user_id": 100}) == 2
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
        assert db["anon_codes"].count_documents({"guild_id": 1, "user_id": 100}) == 1
        msg = interaction.response.send_message.await_args.args[0]
        assert "limit" in msg.lower()
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
        assert "MYCODE" in labels
        assert "OTHER" in labels
    finally:
        client.close()
