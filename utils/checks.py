from discord.ext import commands
from database import db
from config import Config


async def is_owner_or_authorized(ctx):
    if Config.OWNER_ID and ctx.author.id == Config.OWNER_ID:
        return True
    try:
        if await ctx.bot.is_owner(ctx.author):
            return True
    except Exception:
        pass
    return False


async def _base_check(ctx):
    if not ctx.guild:
        raise commands.CheckFailure("This command can only be used in a server.")
    if await is_owner_or_authorized(ctx):
        return True
    if await db.is_dev(ctx.guild.id, ctx.author.id):
        return True
    raise commands.CheckFailure("You need to be a bot dev or owner to use this command.")


def dev_only():
    return commands.check(_base_check)
