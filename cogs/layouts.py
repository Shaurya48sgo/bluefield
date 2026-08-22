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


def _embed(title, desc, code):
    e = discord.Embed(title=title, color=_color(code), description=desc)
    return e


# ---------------------------------------------------------------------------
# Secret layouts. Fixed structure (in the description):
#   **NICKNAME** <joiner> `CODE`   <- same line, code copyable
#   message
#   -# Post #N                     <- small subtext at the bottom
# No separators between code and message.
# ---------------------------------------------------------------------------

def _s_build(code, nick, msg, post, title=None, joiner="·"):
    line = f"**{nick}** {joiner} `{code}`"
    desc = f"{line}\n\n{msg}\n\n-# Post #{post}"
    e = discord.Embed(title=title, color=_color(code), description=desc)
    return e


def _make_build(title, joiner):
    return lambda c, n, m, p: _s_build(c, n, m, p, title=title, joiner=joiner)


TITLES = [None, "Secret"]
JOINERS = ["·", "—", "|", "➜", "»", "~", ":", "@", "→", "+", "=", "//", "::", "#", "*", "-", "❯", "►", "×", "∧"]

SECRET_LAYOUTS = [
    {"name": f"{('No title' if t is None else t)} · {j}", "build": _make_build(t, j)}
    for t, j in zip(TITLES * 10, JOINERS)
]


def build_secret(layout_index, code, nickname, message, post_number):
    layout_index = max(0, min(layout_index, len(SECRET_LAYOUTS) - 1))
    return SECRET_LAYOUTS[layout_index]["build"](code, nickname, message, post_number)


# ---------------------------------------------------------------------------
# Reply layouts. Fixed: nickname = author (set by build_reply),
# description = reply code box + "replied to" target + message.
# ---------------------------------------------------------------------------

def _r_desc(r, t, p, text, sep="━━━━━━━━━━"):
    return f"```{r}```\n{sep}\nReplied to **`{t}`** · Post #{p or '?'}\n\n{text}"


REPLY_LAYOUTS = [
    {"name": "Clean reply", "build": lambda r, t, p, text: _embed(None, _r_desc(r, t, p, text), r)},
    {"name": "Titled Reply", "build": lambda r, t, p, text: _embed("Reply", _r_desc(r, t, p, text), r)},
    {"name": "Reply divider", "build": lambda r, t, p, text: _embed(None, _r_desc(r, t, p, text, "──── Replied to ────"), r)},
    {"name": "Titled divider", "build": lambda r, t, p, text: _embed("Reply", _r_desc(r, t, p, text, "──── Replied to ────"), r)},
    {"name": "Reply stars", "build": lambda r, t, p, text: _embed(None, _r_desc(r, t, p, text, "✦ Replied to ✦"), r)},
    {"name": "Titled stars", "build": lambda r, t, p, text: _embed("Reply", _r_desc(r, t, p, text, "✦ Replied to ✦"), r)},
    {"name": "Reply dashes", "build": lambda r, t, p, text: _embed(None, _r_desc(r, t, p, text, "──────────"), r)},
    {"name": "Titled dashes", "build": lambda r, t, p, text: _embed("Reply", _r_desc(r, t, p, text, "──────────"), r)},
    {"name": "Reply equals", "build": lambda r, t, p, text: _embed(None, _r_desc(r, t, p, text, "══════════"), r)},
    {"name": "Titled equals", "build": lambda r, t, p, text: _embed("Reply", _r_desc(r, t, p, text, "══════════"), r)},
    {"name": "Reply arrows", "build": lambda r, t, p, text: _embed(None, _r_desc(r, t, p, text, "➜ ───── ➜"), r)},
    {"name": "Titled arrows", "build": lambda r, t, p, text: _embed("Reply", _r_desc(r, t, p, text, "➜ ───── ➜"), r)},
    {"name": "Reply tilde", "build": lambda r, t, p, text: _embed(None, _r_desc(r, t, p, text, "~~~~~~~~~~"), r)},
    {"name": "Titled tilde", "build": lambda r, t, p, text: _embed("Reply", _r_desc(r, t, p, text, "~~~~~~~~~~"), r)},
    {"name": "Reply hearts", "build": lambda r, t, p, text: _embed(None, _r_desc(r, t, p, text, "♥ Replied to ♥"), r)},
    {"name": "Titled hearts", "build": lambda r, t, p, text: _embed("Reply", _r_desc(r, t, p, text, "♥ Replied to ♥"), r)},
    {"name": "Reply minimal", "build": lambda r, t, p, text: _embed(None, f"```{r}```\nReplied to `{t}` · #{p or '?'}\n\n{text}", r)},
    {"name": "Titled minimal", "build": lambda r, t, p, text: _embed("Reply", f"```{r}```\nReplied to `{t}` · #{p or '?'}\n\n{text}", r)},
    {"name": "Reply line", "build": lambda r, t, p, text: _embed(None, f"`{r}` ➜ `{t}` · #{p or '?'}\n\n{text}", r)},
    {"name": "Titled line", "build": lambda r, t, p, text: _embed("Reply", f"`{r}` ➜ `{t}` · #{p or '?'}\n\n{text}", r)},
]


def build_reply(layout_index, reply_code, reply_nick, target_code, target_nick, target_post, text):
    layout_index = max(0, min(layout_index, len(REPLY_LAYOUTS) - 1))
    embed = REPLY_LAYOUTS[layout_index]["build"](reply_code, target_code, target_post, text)
    if reply_nick:
        embed.set_author(name=reply_nick)
    return embed