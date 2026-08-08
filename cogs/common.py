import os

from pymongo import MongoClient

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
DEFAULT_PREFIX = "!?"

_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)
_db = _client["bluefield"]
G = _db["guild_settings"]
S = _db["summon_roles"]
AS = _db["summon_settings"]
BL = _db["blacklist"]
AL = _db["audit_log"]

PREFIX_CACHE = {}

try:
    S.create_index([("guild_id", 1), ("name", 1)], unique=True)
except Exception:
    pass


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


def is_blacklisted(guild_id, user_id, member=None):
    if member is not None and is_admin(member):
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
