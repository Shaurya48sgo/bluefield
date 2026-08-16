import discord


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
# Secret message layouts: build(code, message, post_number) -> discord.Embed
# ---------------------------------------------------------------------------

SECRET_LAYOUTS = [
    {
        "name": "Classic fields",
        "build": lambda code, msg, post: _fields(code, msg, post),
    },
    {
        "name": "Title code",
        "build": lambda code, msg, post: _title_code(code, msg, post),
    },
    {
        "name": "Code stack",
        "build": lambda code, msg, post: _stack(code, msg, post),
    },
    {
        "name": "Post header",
        "build": lambda code, msg, post: _post_header(code, msg, post),
    },
    {
        "name": "Code footer",
        "build": lambda code, msg, post: _code_footer(code, msg, post),
    },
    {
        "name": "Post footer",
        "build": lambda code, msg, post: _post_footer(code, msg, post),
    },
    {
        "name": "Minimal",
        "build": lambda code, msg, post: _minimal(code, msg, post),
    },
    {
        "name": "Arrow",
        "build": lambda code, msg, post: _arrow(code, msg, post),
    },
    {
        "name": "Card",
        "build": lambda code, msg, post: _card(code, msg, post),
    },
    {
        "name": "Compact",
        "build": lambda code, msg, post: _compact(code, msg, post),
    },
    {
        "name": "Wide banner",
        "build": lambda code, msg, post: _banner(code, msg, post),
    },
    {
        "name": "Boxed",
        "build": lambda code, msg, post: _boxed(code, msg, post),
    },
    {
        "name": "Code line",
        "build": lambda code, msg, post: _code_line(code, msg, post),
    },
    {
        "name": "Post title",
        "build": lambda code, msg, post: _post_title(code, msg, post),
    },
    {
        "name": "Secret badge",
        "build": lambda code, msg, post: _badge(code, msg, post),
    },
    {
        "name": "Numbered stack",
        "build": lambda code, msg, post: _numbered_stack(code, msg, post),
    },
    {
        "name": "Header row",
        "build": lambda code, msg, post: _header_row(code, msg, post),
    },
    {
        "name": "Framed",
        "build": lambda code, msg, post: _framed(code, msg, post),
    },
    {
        "name": "Split",
        "build": lambda code, msg, post: _split(code, msg, post),
    },
    {
        "name": "Titled footer",
        "build": lambda code, msg, post: _titled_footer(code, msg, post),
    },
]


def _fields(code, msg, post):
    e = discord.Embed(title="Secret message", color=_color(code))
    e.add_field(name="Code", value=f"`{code}`", inline=False)
    e.add_field(name="Message", value=msg, inline=False)
    return e


def _title_code(code, msg, post):
    return discord.Embed(title=f"Secret · `{code}`", color=_color(code), description=msg)


def _stack(code, msg, post):
    return discord.Embed(title="Secret", color=_color(code), description=f"**`{code}`**\n\n{msg}")


def _post_header(code, msg, post):
    return discord.Embed(title=f"Secret #{post} · `{code}`", color=_color(code), description=msg)


def _code_footer(code, msg, post):
    e = discord.Embed(title="Secret message", color=_color(code), description=msg)
    e.set_footer(text=f"Code: {code}")
    return e


def _post_footer(code, msg, post):
    e = discord.Embed(title="Secret message", color=_color(code), description=msg)
    e.set_footer(text=f"Post #{post} · Code: {code}")
    return e


def _minimal(code, msg, post):
    return discord.Embed(color=_color(code), description=f"**`{code}`**\n{msg}")


def _arrow(code, msg, post):
    return discord.Embed(title=f"`{code}` ➜", color=_color(code), description=msg)


def _card(code, msg, post):
    e = discord.Embed(title=f"🂠 `{code}`", color=_color(code), description=msg)
    e.set_footer(text=f"#{post}")
    return e


def _compact(code, msg, post):
    e = discord.Embed(color=_color(code))
    e.add_field(name=f"`{code}`", value=msg, inline=False)
    return e


def _banner(code, msg, post):
    e = discord.Embed(title=f"Secret Message #{post}", color=_color(code), description=msg)
    e.set_author(name=f"Code: {code}")
    return e


def _boxed(code, msg, post):
    return discord.Embed(color=_color(code), description=f"`{code}`\n━━━━━━━━━━\n{msg}")


def _code_line(code, msg, post):
    return discord.Embed(title="Secret", color=_color(code), description=f"Code: **`{code}`**\n\n{msg}")


def _post_title(code, msg, post):
    return discord.Embed(title=f"Post #{post}", color=_color(code), description=f"**`{code}`**\n{msg}")


def _badge(code, msg, post):
    e = discord.Embed(color=_color(code), description=msg)
    e.set_author(name=code)
    e.set_footer(text=f"-# ||Post #{post}||")
    return e


def _numbered_stack(code, msg, post):
    e = discord.Embed(title=f"Secret #{post}", color=_color(code), description=f"**`{code}`**\n\n{msg}")
    return e


def _header_row(code, msg, post):
    return discord.Embed(color=_color(code), description=f"`{code}` · Secret · #{post}\n━━━━━━━━━━\n{msg}")


def _framed(code, msg, post):
    return discord.Embed(color=_color(code), description=f"══════════════\n**`{code}`**\n══════════════\n\n{msg}")


def _split(code, msg, post):
    e = discord.Embed(title="Secret message", color=_color(code))
    e.add_field(name="Code", value=f"`{code}`", inline=True)
    e.add_field(name="Post", value=f"#{post}", inline=True)
    e.add_field(name="Message", value=msg, inline=False)
    return e


def _titled_footer(code, msg, post):
    e = discord.Embed(title=f"Secret · `{code}`", color=_color(code), description=msg)
    e.set_footer(text=f"Post #{post}")
    return e


# ---------------------------------------------------------------------------
# Reply layouts: build(reply_code, target_code, target_post, text) -> Embed
# ---------------------------------------------------------------------------

REPLY_LAYOUTS = [
    {
        "name": "Threaded header",
        "build": lambda r, t, p, text: _r_header(r, t, p, text),
    },
    {
        "name": "Reply title",
        "build": lambda r, t, p, text: _r_title(r, t, p, text),
    },
    {
        "name": "Reply fields",
        "build": lambda r, t, p, text: _r_fields(r, t, p, text),
    },
    {
        "name": "To-from",
        "build": lambda r, t, p, text: _r_to_from(r, t, p, text),
    },
    {
        "name": "Reply stack",
        "build": lambda r, t, p, text: _r_stack(r, t, p, text),
    },
    {
        "name": "Reply footer",
        "build": lambda r, t, p, text: _r_footer(r, t, p, text),
    },
    {
        "name": "Reply post title",
        "build": lambda r, t, p, text: _r_post_title(r, t, p, text),
    },
    {
        "name": "Reply line",
        "build": lambda r, t, p, text: _r_line(r, t, p, text),
    },
    {
        "name": "Reply banner",
        "build": lambda r, t, p, text: _r_banner(r, t, p, text),
    },
    {
        "name": "Reply minimal",
        "build": lambda r, t, p, text: _r_minimal(r, t, p, text),
    },
    {
        "name": "Reply compact",
        "build": lambda r, t, p, text: _r_compact(r, t, p, text),
    },
    {
        "name": "Reply arrow",
        "build": lambda r, t, p, text: _r_arrow(r, t, p, text),
    },
    {
        "name": "Reply card",
        "build": lambda r, t, p, text: _r_card(r, t, p, text),
    },
    {
        "name": "Reply boxed",
        "build": lambda r, t, p, text: _r_boxed(r, t, p, text),
    },
    {
        "name": "Reply split",
        "build": lambda r, t, p, text: _r_split(r, t, p, text),
    },
    {
        "name": "Reply titled footer",
        "build": lambda r, t, p, text: _r_titled_footer(r, t, p, text),
    },
    {
        "name": "Reply badge",
        "build": lambda r, t, p, text: _r_badge(r, t, p, text),
    },
    {
        "name": "Reply numbered",
        "build": lambda r, t, p, text: _r_numbered(r, t, p, text),
    },
    {
        "name": "Reply wide",
        "build": lambda r, t, p, text: _r_wide(r, t, p, text),
    },
    {
        "name": "Reply framed",
        "build": lambda r, t, p, text: _r_framed(r, t, p, text),
    },
]


def _r_header(r, t, p, text):
    return discord.Embed(
        color=_color(r),
        description=f"Secret code **`{r}`**\n──── Replied to **`{t}`** · Post #{p or '?'} ────\n\n{text}",
    )


def _r_title(r, t, p, text):
    return discord.Embed(
        title=f"Reply from `{r}`",
        color=_color(r),
        description=f"To `{t}` · Post #{p or '?'}\n\n{text}",
    )


def _r_fields(r, t, p, text):
    e = discord.Embed(title=f"Reply · `{r}`", color=_color(r))
    e.add_field(name="To", value=f"`{t}` · #{p or '?'}", inline=False)
    e.add_field(name="Message", value=text, inline=False)
    return e


def _r_to_from(r, t, p, text):
    return discord.Embed(
        color=_color(r),
        description=f"From **`{r}`**\nTo **`{t}`** · Post #{p or '?'}\n\n{text}",
    )


def _r_stack(r, t, p, text):
    return discord.Embed(
        title="Reply",
        color=_color(r),
        description=f"**`{r}`** ➜ **`{t}`** (#{p or '?'})\n\n{text}",
    )


def _r_footer(r, t, p, text):
    e = discord.Embed(title="Reply", color=_color(r), description=text)
    e.set_footer(text=f"{r} replied to {t} · Post #{p or '?'}")
    return e


def _r_post_title(r, t, p, text):
    return discord.Embed(
        title=f"Reply to Post #{p or '?'}",
        color=_color(r),
        description=f"`{r}` ➜ `{t}`\n\n{text}",
    )


def _r_line(r, t, p, text):
    return discord.Embed(
        color=_color(r),
        description=f"`{r}` replied to `{t}` (Post #{p or '?'})\n{text}",
    )


def _r_banner(r, t, p, text):
    e = discord.Embed(title="Reply", color=_color(r), description=text)
    e.set_author(name=f"{r} ➜ {t} · #{p or '?'}")
    return e


def _r_minimal(r, t, p, text):
    return discord.Embed(color=_color(r), description=f"**`{r}`** ➜ `{t}`\n{text}")


def _r_compact(r, t, p, text):
    e = discord.Embed(color=_color(r))
    e.add_field(name=f"`{r}` ➜ `{t}`", value=f"Post #{p or '?'}\n{text}", inline=False)
    return e


def _r_arrow(r, t, p, text):
    return discord.Embed(color=_color(r), description=f"`{r}` → `{t}` · #{p or '?'}\n\n{text}")


def _r_card(r, t, p, text):
    e = discord.Embed(title=f"`{r}` → `{t}`", color=_color(r), description=text)
    e.set_footer(text=f"Post #{p or '?'}")
    return e


def _r_boxed(r, t, p, text):
    return discord.Embed(
        color=_color(r),
        description=f"`{r}` ➜ `{t}`\n━━━━━━━━━━\n{text}",
    )


def _r_split(r, t, p, text):
    e = discord.Embed(title="Reply", color=_color(r))
    e.add_field(name="From", value=f"`{r}`", inline=True)
    e.add_field(name="To", value=f"`{t}`", inline=True)
    e.add_field(name="Post", value=f"#{p or '?'}", inline=True)
    e.add_field(name="Message", value=text, inline=False)
    return e


def _r_titled_footer(r, t, p, text):
    e = discord.Embed(title=f"`{r}` ➜ `{t}`", color=_color(r), description=text)
    e.set_footer(text=f"Post #{p or '?'}")
    return e


def _r_badge(r, t, p, text):
    e = discord.Embed(title="Reply", color=_color(r), description=text)
    e.set_author(name=f"{r} · to {t} · #{p or '?'}")
    return e


def _r_numbered(r, t, p, text):
    return discord.Embed(
        title=f"Reply #{p or '?'}",
        color=_color(r),
        description=f"`{r}` replied to `{t}`\n\n{text}",
    )


def _r_wide(r, t, p, text):
    return discord.Embed(
        color=_color(r),
        description=f"`{r}` — `{t}` — #{p or '?'}\n━━━━━━━━━━\n{text}",
    )


def _r_framed(r, t, p, text):
    return discord.Embed(
        color=_color(r),
        description=f"══════════════\n`{r}` ➜ `{t}` · Post #{p or '?'}\n══════════════\n\n{text}",
    )


def build_secret(layout_index, code, message, post_number):
    layout_index = max(0, min(layout_index, len(SECRET_LAYOUTS) - 1))
    return SECRET_LAYOUTS[layout_index]["build"](code, message, post_number)


def build_reply(layout_index, reply_code, target_code, target_post, text):
    layout_index = max(0, min(layout_index, len(REPLY_LAYOUTS) - 1))
    return REPLY_LAYOUTS[layout_index]["build"](reply_code, target_code, target_post, text)
