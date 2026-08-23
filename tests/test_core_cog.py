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
def test_mod_add_remove_updates_setting_and_logs():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
        ctx, target = make_ctx(make_admin(100), 500)
        asyncio.run(cog.mod.callback(cog, ctx, target, "-y"))
        assert db["guild_settings"].find_one({"guild_id": 1})["mod_ids"] == [500]
        ctx.send.assert_awaited_once()

        ctx2, target2 = make_ctx(make_admin(100), 500)
        asyncio.run(cog.mod.callback(cog, ctx2, target2, "-r"))
        assert db["guild_settings"].find_one({"guild_id": 1})["mod_ids"] == []
    finally:
        client.close()


@skip
def test_mod_duplicate_add_and_remove_missing():
    client, db = get_test_db()
    try:
        cog = make_cog(db)
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
