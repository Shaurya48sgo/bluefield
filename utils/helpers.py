import discord
from database import db
from datetime import datetime, timezone


async def get_role_from_mention(guild, mention):
    if mention.startswith("<@&") and mention.endswith(">"):
        role_id = int(mention[3:-1])
    else:
        try:
            role_id = int(mention)
        except ValueError:
            return None
    return guild.get_role(role_id)


async def get_channel_from_mention(guild, mention):
    if mention.startswith("<#") and mention.endswith(">"):
        channel_id = int(mention[2:-1])
    else:
        try:
            channel_id = int(mention)
        except ValueError:
            return None
    return guild.get_channel(channel_id)


async def get_member_from_mention(guild, mention):
    if mention.startswith("<@") and mention.endswith(">"):
        if mention.startswith("<@!"):
            user_id = int(mention[3:-1])
        else:
            user_id = int(mention[2:-1])
    else:
        try:
            user_id = int(mention)
        except ValueError:
            return None
    return guild.get_member(user_id)


def format_time(seconds):
    if seconds < 60:
        return f"{seconds}s"
    elif seconds < 3600:
        return f"{seconds // 60}min"
    else:
        return f"{seconds // 3600}h {(seconds % 3600) // 60}min"


def create_embed(title, description, color=discord.Color.blue(), **kwargs):
    embed = discord.Embed(title=title, description=description, color=color, **kwargs)
    embed.set_footer(text="Blue Field")
    return embed


async def has_active_item(guild_id, user_id, item_type):
    items = await db.get_active_items(guild_id, user_id)
    now = datetime.now(timezone.utc)
    for item in items:
        if item.get("type") == item_type and item.get("expires", datetime.min) > now:
            return True
    return False


async def get_active_item_expiry(guild_id, user_id, item_type):
    items = await db.get_active_items(guild_id, user_id)
    for item in items:
        if item.get("type") == item_type:
            return item.get("expires")
    return None


def replace_placeholders(text, attacker_name, target_name):
    return text.replace("[user]", attacker_name).replace("[target]", target_name)
