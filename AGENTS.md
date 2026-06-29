# Jail Bot — Discord Bot

`discord.py` + MongoDB (`motor`) with **mongita fallback** (no MongoDB needed). Prefix `I?`.

## Quick start

```bash
pip install -r requirements.txt
python main.py   # dies if DISCORD_TOKEN not in .env
```

## Important constraints

- **No tests, CI, formatter, linter, or typechecker.** Manual testing only.
- **`motor` async only** — no `pymongo` sync calls.
- **mongita fallback:** if MongoDB can't be reached, `database.py` wraps `MongitaClientDisk` in `AsyncCollection` adapter.
- **`.env` required:** `DISCORD_TOKEN`, `MONGO_URI` (default `localhost:27017`), `DB_NAME` (default `jailbot`). `OWNER_ID` optional — fallback: `bot.application_info().owner.id`. `.env` is gitignored.
- **Prefix commands** are dev/owner only — `utils/checks.py:_base_check` checks owner then guild devs.
- **Intents required:** `members`, `message_content`, `guilds` (set in `JailBot.__init__`).
- **`help_command=None`** — no built-in help; all command info is in tables below.
- **Owner DM on ready:** `on_ready` fetches owner and sends "Bot is online and ready." DM.
- **Installed skills** in `.agents/skills/` — loaded via `opencode.json` and `skills-lock.json`.

## Project structure

```
main.py              # Entrypoint — JailBot class, cogs loaded in setup_hook
config.py            # Bot config, SHOP_ITEMS dict, currency name, STARTING_BALANCE=500
database.py          # DB CRUD + mongita AsyncCollection adapter
cogs/
├── jail.py          # /jail slash command + silence logic + narrative messages
├── setup.py         # I?jsetup_all (full wizard) & I?jsetup (menu wizard)
├── shop.py          # /shop, /buy, /inventory, /balance
├── items.py         # /use (immunity, full_immunity, reverse, divine_eye, invis_pot)
├── dev.py           # I?dev, I?give, I?givemoney, I?resetuser, I?resetserver
└── currency.py      # /daily, /pay, /leaderboard, /stats
utils/
├── checks.py        # dev_only(), is_owner_or_authorized()
└── helpers.py       # create_embed, get_role/channel/member_from_mention, etc.
```

## Slash commands (everyone, ephemeral)

| Command | Description |
|---|---|
| `/jail user:{@Member} power:{2min\|5min\|2min PRO\|5min PRO}` | Uses inventory item to jail target. Checks immunity/reverse/hierarchy. Public narrative in log channel. |
| `/use item:{Immunity\|Full Immunity\|Reverse\|Divine Eye\|Invis Pot}` | Activate a defense item (24h duration, Divine Eye is one-time). |
| `/shop` | Browse purchasable items. |
| `/buy item:{...} [quantity]` | Buy item(s) with coins. |
| `/inventory` | View your items. |
| `/balance` | Check coins. |
| `/daily` | Claim 100 coins (24h cooldown). |
| `/pay user:{@Member} amount:{int}` | Transfer coins. |
| `/leaderboard` | Top 10 richest users. |
| `/stats` | Times jailed / jails done / items owned. |

## Prefix commands (dev/owner only)

| Command | Description |
|---|---|
| `I?jsetup_all` | Full interactive wizard: silence role → log channel → reverse immunity roles (with custom messages) → full immunity roles → role hierarchy. Skip/Skip All buttons on every step. |
| `I?jsetup [N]` | Menu-based setup. `I?jsetup` shows numbered menu, `I?jsetup 3` jumps to step 3. |
| `I?dev add @user` / `I?dev remove @user` / `I?dev list` | Manage devs (owner only). |
| `I?hecker @user -y` / `I?hecker @user -r` | Grant/revoke infinite items (dev/owner only). |
| `I?give @user item_id [qty]` | Grant items. |
| `I?givemoney @user amount` | Grant coins. |
| `I?resetuser @user` | Wipe user data. |
| `I?resetserver` | Wipe all server data (owner only). |

## Database

5 collections: `guilds`, `users`, `devs`, `active_items`, `jail_logs`. All CRUD through `database.py` helpers. No direct collection access outside `database.py`.

## Key mechanics

- **Items are consumed from inventory** — `/buy` first, then `/jail` or `/use`.
- **"Already silenced"** check is role-based — target has the silence role → blocked.
- **Immunity** deflects Pro jails back onto the immunity user.
- **Full Immunity** blocks everything (both Pro and non-Pro).
- **Reverse** sends non-Pro jails back to attacker; Pro jails jail the reverse user instead.
- **Role-based immunity** (from setup): reverse immunity roles reflect jail back; full immunity roles block it entirely.
- **Hierarchy:** lower level needs PRO power to jail a higher level. Levels set via setup wizard.
- **Invis Pot** hides attacker name as "Someone" in public narrative messages.
- **Divine Eye** reveals all active protection items server-wide.
- **Role removal:** per-task `asyncio.sleep(duration*60)` removes silence role after jail expires.
- **Expired item cleanup:** background loop every 60s.
- **Public narratives** sent to configured log channel based on jail outcome and power type.
