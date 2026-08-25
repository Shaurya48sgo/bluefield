import os
import random
import string

from discord.ext import commands
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
DEFAULT_PREFIX = "I?"
OWNER_ID = os.getenv("OWNER_ID")

_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)
_db = _client["bluefield"]
G = _db["guild_settings"]
S = _db["summon_roles"]
AS = _db["summon_settings"]
BL = _db["blacklist"]
AL = _db["audit_log"]
C = _db["anon_codes"]
P = _db["easyjoin_panels"]
M = _db["secret_messages"]
I = _db["inbox"]
RP = _db["reveal_proposals"]
US = _db["user_settings"]
RC = _db["redeem_codes"]
PS = _db["active_punishments"]

PREFIX_CACHE = {}

try:
    S.create_index([("guild_id", 1), ("name", 1)], unique=True)
except Exception:
    pass

try:
    C.create_index([("code", 1)], unique=True)
except Exception:
    pass


def generate_code(length=8):
    chars = string.ascii_uppercase + string.digits
    return "".join(random.choice(chars) for _ in range(length))


def get_guild_prefix_sync(guild_id):
    if guild_id in PREFIX_CACHE:
        return PREFIX_CACHE[guild_id]
    try:
        doc = G.find_one({"guild_id": guild_id})
        prefix = (doc or {}).get("prefix", DEFAULT_PREFIX)
    except Exception:
        prefix = DEFAULT_PREFIX
    PREFIX_CACHE[guild_id] = prefix
    return prefix


def set_guild_prefix(guild_id, prefix):
    PREFIX_CACHE[guild_id] = prefix
    try:
        G.update_one({"guild_id": guild_id}, {"$set": {"prefix": prefix}}, upsert=True)
    except Exception:
        pass


async def get_prefix(bot, message):
    if message.guild is None:
        return DEFAULT_PREFIX
    return get_guild_prefix_sync(message.guild.id)


def get_guild_settings(guild_id):
    return G.find_one({"guild_id": guild_id}) or {}


def set_guild_settings(guild_id, **kwargs):
    G.update_one({"guild_id": guild_id}, {"$set": kwargs}, upsert=True)
    PREFIX_CACHE.pop(guild_id, None)


COLOR_NAMES = {
    "red": 0xED4245,
    "orange": 0xF47F17,
    "yellow": 0xFEE75C,
    "green": 0x57F287,
    "blue": 0x5865F2,
    "purple": 0x9B59B6,
    "pink": 0xEB459E,
}

CANPING_CHOICES = [
    ("Anyone who joined", "anyone_joined"),
    ("Chosen people/roles", "chosen"),
]

CANJOIN_CHOICES = [
    ("Anyone", "anyone"),
    ("Invited only", "invited"),
]


def parse_color(value):
    if not value:
        return None
    v = value.strip().lower()
    if v in COLOR_NAMES:
        return COLOR_NAMES[v]
    if v.startswith("#"):
        v = v[1:]
    if len(v) == 6 and all(c in "0123456789abcdef" for c in v):
        return int(v, 16)
    return None


def parse_mentions(guild, text):
    ids = []
    types = []
    for token in text.replace(",", " ").split():
        if token.startswith("<@&") and token.endswith(">") and token[3:-1].isdigit():
            ids.append(int(token[3:-1]))
            types.append("role")
        elif token.startswith("<@") and token.endswith(">") and token[2:-1].lstrip("!").isdigit():
            ids.append(int(token[2:-1].lstrip("!")))
            types.append("user")
    return ids, types


def is_admin(member):
    return bool(member.guild_permissions.administrator or member.guild_permissions.manage_roles)


def is_owner(user_id):
    return bool(OWNER_ID) and str(user_id) == str(OWNER_ID)


def get_dev_ids(guild_id):
    return get_guild_settings(guild_id).get("dev_ids", [])


def is_dev(guild_id, user_id):
    return user_id in get_dev_ids(guild_id)


def is_owner_or_dev(guild_id, user_id):
    return is_owner(user_id) or is_dev(guild_id, user_id)


def get_mod_ids(guild_id):
    return get_guild_settings(guild_id).get("mod_ids", [])


def is_mod(guild_id, user_id):
    return user_id in get_mod_ids(guild_id)


def get_setup_ids(guild_id):
    return get_guild_settings(guild_id).get("setup_ids", [])


def is_setup(guild_id, user_id):
    return user_id in get_setup_ids(guild_id)


def get_extra_code_slots(guild_id, user_id):
    return get_guild_settings(guild_id).get("extra_code_slots", {}).get(str(user_id), 0)


def add_extra_code_slots(guild_id, user_id, delta):
    """Adjust a user's extra code slots (clamped at 0); returns the new total."""
    new_val = max(0, get_extra_code_slots(guild_id, user_id) + delta)
    G.update_one({"guild_id": guild_id}, {"$set": {f"extra_code_slots.{user_id}": new_val}}, upsert=True)
    return new_val


def has_setup_access():
    """Admins, devs, bot owner, or users granted setup access via I?server <@user> -y."""

    async def predicate(ctx):
        if is_owner(ctx.author.id):
            return True
        if ctx.guild is None:
            return False
        if is_dev(ctx.guild.id, ctx.author.id):
            return True
        if is_admin(ctx.author):
            return True
        return is_setup(ctx.guild.id, ctx.author.id)

    return commands.check(predicate)


def is_bot_enabled(guild_id):
    return not get_guild_settings(guild_id).get("bot_disabled", False)


def set_bot_enabled(guild_id, enabled):
    if enabled:
        G.update_one({"guild_id": guild_id}, {"$unset": {"bot_disabled": ""}}, upsert=True)
    else:
        G.update_one({"guild_id": guild_id}, {"$set": {"bot_disabled": True}}, upsert=True)


def is_staff(member):
    """Admins, devs, mods or the bot owner."""
    if is_owner(member.id):
        return True
    if is_admin(member):
        return True
    try:
        guild_id = member.guild.id
    except Exception:
        return False
    return is_dev(guild_id, member.id) or is_mod(guild_id, member.id)


def is_privileged(member):
    if is_admin(member):
        return True
    try:
        return is_owner_or_dev(member.guild.id, member.id)
    except Exception:
        return is_owner(member.id)


def has_admin_or_dev():
    async def predicate(ctx):
        if is_owner(ctx.author.id):
            return True
        if ctx.guild and is_dev(ctx.guild.id, ctx.author.id):
            return True
        return is_admin(ctx.author)

    return commands.check(predicate)


def is_blacklisted(guild_id, user_id, member=None):
    if member is not None:
        if is_owner(member.id):
            return False
        try:
            if is_dev(guild_id, member.id):
                return False
        except Exception:
            pass
        if is_admin(member):
            return False
    try:
        return BL.find_one({"guild_id": guild_id, "user_id": user_id}) is not None
    except Exception:
        return False


def audit(guild_id, actor_id, action, target_type, target_id, details=""):
    import datetime

    doc = {
        "guild_id": guild_id,
        "actor_id": actor_id,
        "action": action,
        "target_type": target_type,
        "target_id": target_id,
        "details": details,
        "timestamp": datetime.datetime.now(datetime.timezone.utc),
    }
    try:
        AL.insert_one(doc)
    except Exception:
        pass
    return doc
