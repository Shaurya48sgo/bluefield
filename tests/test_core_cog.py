import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from mongo_helpers import get_test_db, mongo_available

skip = pytest.mark.skipif(not mongo_available(), reason="MongoDB not running")


def make_cog(db):
    db.guild_settings.drop()
    db.audit_log.drop()
    from cogs.core import CoreCog

    cog = CoreCog(MagicMock())
    from cogs import common, core

    for m in (common, core):
        m.G = db["guild_settings"]
        m.AL = db["audit_log"]
    return cog


import contextlib


@contextlib.contextmanager
def bot_owner_uid(uid="100"):
    from cogs import common

    old = common.OWNER_ID
    common.OWNER_ID = uid
    try:
        yield
    finally:
        common.OWNER_ID = old


def make_admin(uid=100):
    member = MagicMock()
    member.id = uid
    perms = MagicMock()
    perms.administrator = True
    perms.manage_roles = False
    member.guild_permissions = perms
    return member


def make_user(uid):
    member = MagicMock()
    member.id = uid
    perms = MagicMock()
    perms.administrator = False
    perms.manage_roles = False
    member.guild_permissions = perms
    return member


def make_ctx(author, uid_target=None):
    guild = MagicMock()
    guild.id = 1
    guild.owner_id = 0
    channel = MagicMock()
    channel.id = 888
    channel.mention = "<#888>"
    channel.send = AsyncMock()
    ctx = MagicMock()
    ctx.author = author
    ctx.guild = guild
    ctx.channel = channel
    ctx.send = AsyncMock()
    if uid_target is not None:
        target = make_user(uid_target)
        target.mention = f"<@{uid_target}>"
        return ctx, target
    return ctx


@skip
def test_mod_denied_for_plain_admin():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        ctx, target = make_ctx(make_admin(999), 500)
        asyncio.run(cog.mod.callback(cog, ctx, target, "-y"))
        msg = ctx.send.await_args.args[0]
        assert "server owner or devs" in msg
        assert db["guild_settings"].find_one({"guild_id": 1}) is None
    finally:
        client.close()


@skip
def test_mod_add_remove_updates_setting_and_logs():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        with bot_owner_uid("100"):
            ctx, target = make_ctx(make_admin(100), 500)
            asyncio.run(cog.mod.callback(cog, ctx, target, "-y"))
            assert db["guild_settings"].find_one({"guild_id": 1})["mod_ids"] == [500]
            ctx.send.assert_awaited_once()

            ctx2, _target = make_ctx(make_admin(100), 500)
            asyncio.run(cog.mod.callback(cog, ctx2, _target, "-r"))
            assert db["guild_settings"].find_one({"guild_id": 1})["mod_ids"] == []
    finally:
        client.close()


@skip
def test_mod_duplicate_add_and_remove_missing():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        with bot_owner_uid("100"):
            ctx, target = make_ctx(make_admin(100), 500)
            asyncio.run(cog.mod.callback(cog, ctx, target, "-y"))
            # duplicate add
            ctx3, target3 = make_ctx(make_admin(100), 500)
            asyncio.run(cog.mod.callback(cog, ctx3, target3, "-y"))
            msg = ctx3.send.await_args.args[0]
            assert "already a mod" in msg
            # remove when not a mod
            db["guild_settings"].update_one({"guild_id": 1}, {"$set": {"mod_ids": []}})
            ctx4, target4 = make_ctx(make_admin(100), 501)
            asyncio.run(cog.mod.callback(cog, ctx4, target4, "-r"))
            msg = ctx4.send.await_args.args[0]
            assert "not a mod" in msg
    finally:
        client.close()


@skip
def test_mods_lists_mods():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        db["guild_settings"].insert_one({"guild_id": 1, "mod_ids": [11, 22]})
        with bot_owner_uid("100"):
            ctx = make_ctx(make_admin(100))
            asyncio.run(cog.mods.callback(cog, ctx))
        msg = ctx.send.await_args.args[0]
        assert "<@11>" in msg and "<@22>" in msg
    finally:
        client.close()


@skip
def test_modlog_and_reports_set_channels():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        ctx = make_ctx(make_admin(100))
        asyncio.run(cog.modlog.callback(cog, ctx))
        settings = db["guild_settings"].find_one({"guild_id": 1})
        assert settings["mod_log_channel_id"] == 888
        ctx2 = make_ctx(make_admin(100))
        asyncio.run(cog.reports.callback(cog, ctx2))
        settings = db["guild_settings"].find_one({"guild_id": 1})
        assert settings["report_log_channel_id"] == 888
    finally:
        client.close()


@skip
def test_server_status_disable_enable():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        with bot_owner_uid("100"):
            ctx = make_ctx(make_admin(100))
            asyncio.run(cog.server_cmd.callback(cog, ctx, None))
            embed = ctx.send.await_args.kwargs["embed"]
            assert "ENABLED" in embed.description

            ctx2 = make_ctx(make_admin(100))
            asyncio.run(cog.server_cmd.callback(cog, ctx2, "disable"))
            assert db["guild_settings"].find_one({"guild_id": 1})["bot_disabled"] is True

            ctx3 = make_ctx(make_admin(100))
            asyncio.run(cog.server_cmd.callback(cog, ctx3, None))
            assert "DISABLED" in ctx3.send.await_args.kwargs["embed"].description

            # double disable rejected
            ctx4 = make_ctx(make_admin(100))
            asyncio.run(cog.server_cmd.callback(cog, ctx4, "disable"))
            assert "already" in ctx4.send.await_args.args[0]

            ctx5 = make_ctx(make_admin(100))
            asyncio.run(cog.server_cmd.callback(cog, ctx5, "enable"))
            assert "bot_disabled" not in db["guild_settings"].find_one({"guild_id": 1})
    finally:
        client.close()


@skip
def test_guild_gate_check_blocks_and_exempts_server_cmd():
    client, db = get_test_db()
    try:
        import main as main_mod

        db.guild_settings.drop()
        from cogs import common

        common.G = db["guild_settings"]

        disabled_ctx = MagicMock()
        disabled_ctx.guild.id = 1
        disabled_ctx.command.name = "help"
        assert main_mod.is_bot_enabled(1) is True
        assert asyncio.run(main_mod.guild_gate_check(disabled_ctx)) is True

        common.set_bot_enabled(1, False)
        assert asyncio.run(main_mod.guild_gate_check(disabled_ctx)) is False

        server_ctx = MagicMock()
        server_ctx.guild.id = 1
        server_ctx.command.name = "server"
        assert asyncio.run(main_mod.guild_gate_check(server_ctx)) is True

        dm_ctx = MagicMock()
        dm_ctx.guild = None
        assert asyncio.run(main_mod.guild_gate_check(dm_ctx)) is True

        class FakeInteraction:
            guild_id = 1
            response = MagicMock()
            response.send_message = AsyncMock()

        fi = FakeInteraction()
        assert asyncio.run(main_mod.tree_interaction_check(fi)) is False
        fi.response.send_message.assert_awaited_once()

        class FakeDM:
            guild_id = None

        assert asyncio.run(main_mod.tree_interaction_check(FakeDM())) is True
    finally:
        client.close()


@skip
def test_server_grant_revoke_setup_access():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        with bot_owner_uid("100"):
            guild = MagicMock()
            guild.id = 1
            author = make_admin(100)
            target_user = make_user(777)
            target_user.mention = "<@777>"
            channel = MagicMock()
            channel.id = 888
            channel.send = AsyncMock()
            ctx = MagicMock()
            ctx.author = author
            ctx.guild = guild
            ctx.channel = channel
            ctx.send = AsyncMock()
            ctx.message.mentions = [target_user]

            asyncio.run(cog.server_cmd.callback(cog, ctx, None, "-y"))
            assert db["guild_settings"].find_one({"guild_id": 1})["setup_ids"] == [777]
            assert "can now run" in ctx.send.await_args.args[0]

            # duplicate
            asyncio.run(cog.server_cmd.callback(cog, ctx, None, "-y"))
            assert "already" in ctx.send.await_args.args[0]

            # revoke
            asyncio.run(cog.server_cmd.callback(cog, ctx, None, "-r"))
            assert db["guild_settings"].find_one({"guild_id": 1})["setup_ids"] == []
    finally:
        client.close()


@skip
def test_setup_access_predicate_and_denied_granter():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        # plain member cannot grant
        ctx = make_ctx(make_user(555))
        granter_target = make_user(999)
        ctx.message.mentions = [granter_target]
        asyncio.run(cog.server_cmd.callback(cog, ctx, None, "-y"))
        assert "server owner or devs" in ctx.send.await_args.args[0]
        assert db["guild_settings"].find_one({"guild_id": 1}) is None

        # predicate: setup member passes on channel commands
        db["guild_settings"].insert_one({"guild_id": 1, "setup_ids": [777]})
        from cogs.common import has_setup_access

        check = has_setup_access()
        setup_member = make_user(777)
        setup_member.guild = MagicMock()
        setup_member.guild.id = 1
        sctx = MagicMock()
        sctx.author = setup_member
        sctx.guild = setup_member.guild
        assert asyncio.run(check.predicate(sctx)) is True

        plain = make_user(888)
        plain.guild = MagicMock()
        plain.guild.id = 1
        pctx = MagicMock()
        pctx.author = plain
        pctx.guild = plain.guild
        assert asyncio.run(check.predicate(pctx)) is False
    finally:
        client.close()
