import random

import discord

NICKNAME_ADJ = [
    "Shadow", "Silent", "Swift", "Crimson", "Frost", "Golden", "Night", "Wild",
    "Brave", "Clever", "Hidden", "Storm", "Lucky", "Mystic", "Rapid", "Solar",
    "Cosmic", "Neon", "Phantom", "Emerald",
    "Azure", "Blazing", "Crystal", "Dark", "Echo", "Fierce", "Glacier", "Hollow",
    "Iron", "Jade", "Keen", "Lunar", "Mellow", "Noble", "Onyx", "Primal",
    "Quiet", "Rogue", "Silver", "Titan", "Umbra", "Velvet", "Wicked", "Xenon",
    "Young", "Zephyr", "Amber", "Bold", "Copper", "Daring", "Eager", "Feral",
    "Grave", "Hasty", "Icy", "Jolly", "Kindred", "Lone", "Marble", "Nimble",
    "Odd", "Pale", "Quaint", "Rustic", "Sleek", "Timid", "Ugly", "Vivid",
    "Wise", "Zealous", "Ash", "Bronze", "Cobalt", "Dusk", "Faint", "Gloom",
]

NICKNAME_NOUN = [
    "Fox", "Wolf", "Owl", "Falcon", "Tiger", "Lynx", "Raven", "Viper",
    "Bear", "Eagle", "Panther", "Phoenix", "Hawk", "Serpent", "Jaguar",
    "Griffin", "Dragon", "Lion", "Deer", "Falcon",
    "Shark", "Cobra", "Mantis", "Stag", "Boar", "Hyena", "Condor", "Wombat",
    "Panda", "Koala", "Gecko", "Newt", "Otter", "Badger", "Ferret", "Weasel",
    "Raccoon", "Possum", "Armadillo", "Buffalo", "Caribou", "Cheetah", "Cougar",
    "Coyote", "Dalmatian", "Dolphin", "Echidna", "Giraffe", "Gorilla", "Hippo",
    "Impala", "Jackal", "Kangaroo", "Leopard", "Manatee", "Meerkat", "Narwhal",
    "Ocelot", "Pangolin", "Quokka", "Rhino", "Salamander", "Tortoise", "Urchin",
    "Vulture", "Wolverine", "Yak", "Zebra", "Bison", "Chameleon", "Dingo",
    "Elephant", "Flamingo", "Gibbon", "Hedgehog", "Iguana", "Jellyfish", "Kingfisher",
]


def random_nickname():
    return f"{random.choice(NICKNAME_ADJ)}{random.choice(NICKNAME_NOUN)}"


def _color(code):
    palette = [
        0x9B59B6, 0x5865F2, 0x57F287, 0xFEE75C,
        0xED4245, 0xEB459E, 0xF47F17, 0x00B0F4,
    ]
    h = 0x811C9DC5
    for ch in code:
        h ^= ord(ch)
        h = ((h << 5) & 0xFFFFFFFF) + (h >> 27)
    return discord.Colour(palette[h % len(palette)])


# ---------------------------------------------------------------------------
# Unified V2 style (used everywhere):
#   **NICKNAME** · `CODE`     <- same line, code copyable
#   message
#   -# ...                    <- small subtext at the bottom
# ---------------------------------------------------------------------------

SECRET_COLORS = {
    "Purple": 0x9B59B6,
    "Blue": 0x5865F2,
    "Green": 0x57F287,
    "Yellow": 0xFEE75C,
    "Red": 0xED4245,
    "Pink": 0xEB459E,
    "Orange": 0xF47F17,
    "Cyan": 0x00B0F4,
}


def code_color(code, color=None):
    if color:
        return discord.Colour(color)
    return _color(code)


def build_secret(code, nickname, message, post_number, color=None):
    line = f"**{nickname or '?'}** · `{code}`"
    desc = f"{line}\n\n{message}\n\n-# Post #{post_number}"
    return discord.Embed(color=code_color(code, color), description=desc)


def build_reply(reply_code, reply_nick, target_code, target_nick, target_post, text, link=None, color=None):
    line = f"**{reply_nick or '?'}** · `{reply_code}`"
    footer = f"-# Replied to Post #{target_post or '?'} · **{target_nick or '?'}** (`{target_code}`)"
    if link:
        footer += f" · [jump]({link})"
    desc = f"{line}\n\n{text}\n\n{footer}"
    return discord.Embed(color=code_color(reply_code, color), description=desc)