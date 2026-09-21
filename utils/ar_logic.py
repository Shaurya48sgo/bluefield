"""Pure autoresponder helpers — no discord/motor imports so unit tests run anywhere."""

import re

# Reaction curation vocabulary
APPROVE = "\u2705"  # valid response
REJECT = "\u274c"  # ignored response
HINT = "\U0001f7e1"  # hint-pool response (hint mode window)
FLAG = "\U0001f6a9"  # needs review (treated as rejected)

ENDORSEMENT_EMOJIS = (APPROVE, REJECT, HINT)

STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_HINT = "hint"
STATUS_UNREVIEWED = "unreviewed"

EMOJI_TO_STATUS = {
    APPROVE: STATUS_APPROVED,
    REJECT: STATUS_REJECTED,
    HINT: STATUS_HINT,
}

MODE_WORD = "word"
MODE_CONTAINS = "contains"
MODES = (MODE_WORD, MODE_CONTAINS)

_DURATION_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([smhdSMHD])?\s*$")
_DURATION_MULTIPLIER = {"s": 1 / 60, "m": 1, "h": 60, "d": 1440}


def name_key(name):
    """Case-insensitive identity for group names / keywords."""
    return (name or "").strip().casefold()


def new_keyword(text, mode, default_enabled=False):
    """Build a keyword entry; new keywords inherit the global default."""
    return {"text": (text or "").strip(), "mode": mode, "enabled": bool(default_enabled)}


def resolve_target(groups, target):
    """Resolve an I?keyword target against loaded group docs.

    Returns (kind, hits) where kind is 'all' | 'group' | 'keyword' | 'none'
    and hits is a list of (group, [keyword, ...]) tuples. Group names win
    over keyword text on ties. Pure function — caller mutates and saves.
    """
    key = name_key(target)
    groups = list(groups or [])
    if key == "all":
        return ("all", [(g, list(g.get("keywords", []) or [])) for g in groups])
    for group in groups:
        if name_key(group.get("name", "")) == key:
            return ("group", [(group, list(group.get("keywords", []) or []))])
    hits = []
    for group in groups:
        matched = [k for k in (group.get("keywords", []) or [])
                   if name_key(k.get("text", "")) == key]
        if matched:
            hits.append((group, matched))
    if hits:
        return ("keyword", hits)
    return ("none", [])


def parse_duration(raw):
    """Parse '5m'/'1h'/'2d'/'30s'/bare-number (minutes) -> float minutes.

    Returns None for None/empty/invalid input.
    """
    if raw is None:
        return None
    match = _DURATION_RE.match(str(raw))
    if not match:
        return None
    value = float(match.group(1))
    unit = (match.group(2) or "m").lower()
    return value * _DURATION_MULTIPLIER[unit]


def matches_keyword(text, keyword, mode):
    """Case-insensitive trigger check.

    word: whole-word/phrase match (\\b boundaries). contains: substring.
    """
    if not text or not keyword or not keyword.strip():
        return False
    if mode == MODE_CONTAINS:
        return keyword.casefold() in text.casefold()
    # default: word mode. (?<!\w)/(?!\w) instead of \b so keywords starting
    # or ending in non-word chars (e.g. "$5.00") still match correctly.
    pattern = r"(?<!\w)" + re.escape(keyword.strip()) + r"(?!\w)"
    return re.search(pattern, text, re.IGNORECASE) is not None


def pick_endorsement_status(emojis_present):
    """Deterministic status when several endorsements remain (race fallback).

    Priority: approved > hint > rejected. Returns None if none present.
    """
    present = set(emojis_present or [])
    for emoji in (APPROVE, HINT, REJECT):
        if emoji in present:
            return EMOJI_TO_STATUS[emoji]
    return None


def select_pool(hint_mode, hint_window_min, last_triggered, now):
    """Which response pool fires: ('hint', restamp) or ('approved', restamp).

    First trigger after the window expires (or never triggered) -> approved.
    Triggers inside the window -> hint. Returns (pool, restamp_last_triggered).
    """
    if hint_mode and last_triggered:
        try:
            window = float(hint_window_min or 0) * 60
        except (TypeError, ValueError):
            window = 0
        if window > 0 and (now - last_triggered) <= window:
            return (STATUS_HINT, True)
    return (STATUS_APPROVED, True)
