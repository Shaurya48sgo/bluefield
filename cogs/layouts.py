import random

import discord

NICKNAME_ADJ = [
    "Shadow", "Silent", "Swift", "Crimson", "Frost", "Golden", "Night", "Wild",
    "Brave", "Clever", "Hidden", "Storm", "Lucky", "Mystic", "Rapid", "Solar",
    "Cosmic", "Neon", "Phantom", "Emerald",
]

NICKNAME_NOUN = [
    "Fox", "Wolf", "Owl", "Falcon", "Tiger", "Lynx", "Raven", "Viper",
    "Bear", "Eagle", "Panther", "Phoenix", "Hawk", "Serpent", "Jaguar",
    "Griffin", "Dragon", "Lion", "Deer", "Falcon",
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
# Secret layouts. Fixed structure: nickname = author badge (set by build_secret),
# description = code-box + message + "-# Post #N" at the bottom.
# Each layout only varies the decoration around that structure.
# ---------------------------------------------------------------------------

def _body(code, msg, post):
    return f"```{code}```\n{msg}\n\n-# Post #{post}"


def _body_div(code, msg, post, div):
    return f"```{code}```\n{div}\n\n{msg}\n\n-# Post #{post}"


SECRET_LAYOUTS = [
    {"name": "Clean", "build": lambda c, m, p: _embed(None, _body(c, m, p), c)},
    {"name": "Titled Secret", "build": lambda c, m, p: _embed("Secret", _body(c, m, p), c)},
    {"name": "Divider", "build": lambda c, m, p: _embed(None, _body_div(c, m, p, "━━━━━━━━━━"), c)},
    {"name": "Titled Divider", "build": lambda c, m, p: _embed("Secret", _body_div(c, m, p, "━━━━━━━━━━"), c)},
    {"name": "Stars", "build": lambda c, m, p: _embed(None, _body_div(c, m, p, "✦ ━━━━ ✦"), c)},
    {"name": "Titled Stars", "build": lambda c, m, p: _embed("Secret", _body_div(c, m, p, "✦ ━━━━ ✦"), c)},
    {"name": "Dashes", "build": lambda c, m, p: _embed(None, _body_div(c, m, p, "──────────"), c)},
    {"name": "Titled Dashes", "build": lambda c, m, p: _embed("Secret", _body_div(c, m, p, "──────────"), c)},
    {"name": "Equals", "build": lambda c, m, p: _embed(None, _body_div(c, m, p, "══════════"), c)},
    {"name": "Titled Equals", "build": lambda c, m, p: _embed("Secret", _body_div(c, m, p, "══════════"), c)},
    {"name": "Brackets", "build": lambda c, m, p: _embed(None, _body_div(c, m, p, "──────────"), c)},
    {"name": "Double divider", "build": lambda c, m, p: _embed(None, f"```{c}```\n━━━━━━━━━━\n{'_' * 10}\n\n{m}\n\n-# Post #{p}")},
    {"name": "Arrows", "build": lambda c, m, p: _embed(None, _body_div(c, m, p, "➜ ───── ➜"), c)},
    {"name": "Titled Arrows", "build": lambda c, m, p: _embed("Secret", _body_div(c, m, p, "➜ ───── ➜"), c)},
    {"name": "Tilde", "build": lambda c, m, p: _embed(None, _body_div(c, m, p, "~~~~~~~~~~"), c)},
    {"name": "Titled Tilde", "build": lambda c, m, p: _embed("Secret", _body_div(c, m, p, "~~~~~~~~~~"), c)},
    {"name": "Hearts", "build": lambda c, m, p: _embed(None, _body_div(c, m, p, "♥ ━━━━ ♥"), c)},
    {"name": "Titled Hearts", "build": lambda c, m, p: _embed("Secret", _body_div(c, m, p, "♥ ━━━━ ♥"), c)},
    {"name": "Bold divider", "build": lambda c, m, p: _embed(None, _body_div(c, m, p, "━━━━━━━━━━"), c)},
    {"name": "Minimal", "build": lambda c, m, p: _embed(None, f"```{c}```\n{m}\n\n-# Post #{p}", c)},
]


def _embed(title, desc, code):
    e = discord.Embed(title=title, color=_color(code), description=desc)
    return e


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


def build_secret(layout_index, code, nickname, message, post_number):
    layout_index = max(0, min(layout_index, len(SECRET_LAYOUTS) - 1))
    embed = SECRET_LAYOUTS[layout_index]["build"](code, message, post_number)
    if nickname:
        embed.set_author(name=nickname)
    return embed


def build_reply(layout_index, reply_code, reply_nick, target_code, target_nick, target_post, text):
    layout_index = max(0, min(layout_index, len(REPLY_LAYOUTS) - 1))
    embed = REPLY_LAYOUTS[layout_index]["build"](reply_code, target_code, target_post, text)
    if reply_nick:
        embed.set_author(name=reply_nick)
    return embed


# ---------------------------------------------------------------------------
# V2 secret layouts: richer embeds (author icon, fields, thumbnail, footer icon)
# ---------------------------------------------------------------------------

def _v2_fields(code, msg, post, icon):
    e = discord.Embed(color=_color(code))
    e.set_author(name="Secret", icon_url=icon)
    e.add_field(name="Code", value=f"```{code}```", inline=False)
    e.add_field(name="Message", value=msg, inline=False)
    e.set_footer(text=f"-# Post #{post}")
    return e


def _v2_icon(code, msg, post, icon):
    e = discord.Embed(color=_color(code), description=f"```{code}```\n{msg}\n\n-# Post #{post}")
    e.set_author(name="Secret", icon_url=icon)
    return e


def _v2_thumb(code, msg, post, icon):
    e = discord.Embed(color=_color(code), description=f"```{code}```\n{msg}\n\n-# Post #{post}")
    e.set_thumbnail(url=icon)
    return e


def _v2_footericon(code, msg, post, icon):
    e = discord.Embed(color=_color(code), description=f"```{code}```\n{msg}")
    e.set_footer(text=f"-# Post #{post}", icon_url=icon)
    return e


def _v2_fieldcode(code, msg, post, icon):
    e = discord.Embed(color=_color(code), description=msg)
    e.set_author(name="Secret", icon_url=icon)
    e.add_field(name="Code", value=f"```{code}```", inline=False)
    e.set_footer(text=f"-# Post #{post}")
    return e


V2_ICON = "https://cdn.discordapp.com/emojis/1080190630834855956.webp?size=64"
V2_ICON2 = "https://cdn.discordapp.com/emojis/1069157221050552441.webp?size=64"
V2_ICON3 = "https://cdn.discordapp.com/emojis/1175413573943889920.webp?size=64"

SECRET_LAYOUTS_V2 = [
    {"name": "V2 Fields", "build": lambda c, m, p: _v2_fields(c, m, p, V2_ICON)},
    {"name": "V2 Icon", "build": lambda c, m, p: _v2_icon(c, m, p, V2_ICON)},
    {"name": "V2 Thumbnail", "build": lambda c, m, p: _v2_thumb(c, m, p, V2_ICON2)},
    {"name": "V2 Footer icon", "build": lambda c, m, p: _v2_footericon(c, m, p, V2_ICON3)},
    {"name": "V2 Field code", "build": lambda c, m, p: _v2_fieldcode(c, m, p, V2_ICON)},
    {"name": "V2 Icon 2", "build": lambda c, m, p: _v2_icon(c, m, p, V2_ICON2)},
    {"name": "V2 Thumb 2", "build": lambda c, m, p: _v2_thumb(c, m, p, V2_ICON3)},
    {"name": "V2 Footer icon 2", "build": lambda c, m, p: _v2_footericon(c, m, p, V2_ICON2)},
    {"name": "V2 Fields 2", "build": lambda c, m, p: _v2_fields(c, m, p, V2_ICON2)},
    {"name": "V2 Icon 3", "build": lambda c, m, p: _v2_icon(c, m, p, V2_ICON3)},
]


def build_secret_v2(layout_index, code, nickname, message, post_number):
    layout_index = max(0, min(layout_index, len(SECRET_LAYOUTS_V2) - 1))
    embed = SECRET_LAYOUTS_V2[layout_index]["build"](code, message, post_number)
    if nickname:
        embed.set_author(name=nickname)
    return embed