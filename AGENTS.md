# Jail Bot — Discord Bot

`discord.py` + MongoDB (`motor`, async only — no `pymongo`) with mongita-disk fallback. Prefix `I?`. Entrypoint `main.py` (`JailBot`, cogs loaded in `setup_hook`, slash tree synced there).

```bash
pip install -r requirements.txt
python main.py   # exits if DISCORD_TOKEN not in .env
```

No tests, CI, linter, formatter, or typechecker. No README. Manual testing only.

## Env / config

- `.env` (gitignored): `DISCORD_TOKEN` (required), `MONGO_URI` (default `mongodb://localhost:27017`), `DB_NAME` (default `jailbot`), `OWNER_ID` (optional; fallback is `application_info().owner`).
- `config.py`: `PREFIX="I?"`, `SHOP_ITEMS` keys are the canonical item IDs (`silence_2min`, `silence_5min`, `silence_pro_2min`, `silence_pro_5min`, `immunity`, `full_immunity`, `reverse`, `divine_eye`, `invis_pot`), `STARTING_BALANCE=500`.
- Intents `members` + `message_content` + `guilds` are set in `JailBot.__init__`; privileged ones must also be enabled in the Discord dev portal.

## Command auth split

- Slash (everyone): `cogs/jail.py` (`/jail`), `cogs/shop.py` (`/shop`, `/buy`, `/inventory`, `/balance`), `cogs/items.py` (`/use`), `cogs/currency.py` (`/daily`, `/pay`, `/leaderboard`, `/stats`).
- Prefix (dev/owner only via `utils/checks.py:_base_check`): `cogs/setup.py` (`I?jsetup_all` full wizard, `I?jsetup [1-5]` menu/jump, `I?jailconfig` view-only), `cogs/dev.py` (`I?dev`, `I?give`, `I?givemoney`, `I?resetuser`, `I?hecker @user -y|-r`, `I?resetserver` owner-only), `cogs/autoresponder.py` (`I?keysetup`, `I?keymake`, `I?keydelete`, `I?keyword`, `I?keyhint`, `I?keylist`, `I?keyhelp`). `_base_check` order: `OWNER_ID` → `bot.is_owner()` → guild devs; DMs rejected.
- `on_command_error` in `main.py` swallows `CommandNotFound`.

## Database

- 5 collections: `guilds`, `users`, `devs`, `active_items`, `jail_logs`, plus autoresponder's `ar_config` (single `global` doc: storage guild/channel) and `ar_groups` (unique `thread_id`). Prefer helpers in `database.py`; note `cogs/dev.py` (`resetuser`/`resetserver`) and `cogs/currency.py` (leaderboard) touch collections directly.
- `database.py:connect()` pings MongoDB, else falls back to `MongitaClientDisk` wrapped in `AsyncCollection` (`asyncio.to_thread`). Mongita path has no TTL/indexes and `find`/`delete_many` are `async` (unlike motor) — `currency.py:86` calls `db.users.find(...)` without `await`, which works on motor but breaks on the fallback. Await new DB calls.
- Expired protection items are swept by `_cleanup_loop` every 60s (`cleanup_expired_items`).

## Jail resolution order (`cogs/jail.py:execute_jail`)

1. Requires `setup_complete` + silence role configured; cannot jail self or bot; attacker must hold the power item.
2. **Attacker's power item is consumed even when the jail is blocked/reflected** (`remove_from_inventory` runs on every early return).
3. Full-immunity role, then `full_immunity` item: blocks everything.
4. Reverse-immunity role (reflects ALL powers, custom per-role message), then `reverse` item (**non-Pro only**).
5. `immunity` role/item: blocks non-Pro; **Pro reflects and jails the attacker instead**.
6. Hierarchy: only if `role_order` set and both users rank in it; `target_rank < author_rank` (lower index = higher rank) is blocked with **no PRO bypass in code**.
7. Success increments `total_jails_done`/`total_jailed`.

## Gotchas

- `apply_silence` sleeps `asyncio.sleep(duration)` where duration is the minute value (2/5) — effectively seconds. Re-jailing cancels the prior in-memory task (`self.active_jails`); tasks, `active_wizards`, and `menu_listeners` are all lost on restart.
- `invis_pot` only masks the attacker's mention as "Someone" in the log-channel narrative; ephemeral replies still show names. `divine_eye` is one-time (consumed), others activate 24h via `active_items`.
- `I?hecker` (infinite items) is only checked in `/use`, not in `/jail` power consumption or `/buy` balance checks.

## Autoresponder (`cogs/autoresponder.py`, pure logic in `utils/ar_logic.py`)

- One storage text channel (`I?keysetup`); one public thread per group (`I?keymake "Name" [duration]` — duration stored only, no behavior). Keywords are managed solely via buttons on the thread control message; responses are thread messages curated by reaction (✅ approved / ❌ rejected / 🟡 hint / 🚩 needs review, latest dev/owner endorsement wins, enforced in `_leave_only` + convergent `on_raw_reaction_remove`).
- WORD is the default mode (`(?<!\w)/(?!\w)` boundaries, not `\b` — handles `$5.00`-style keywords); CONTAINS is substring; everything case-insensitive. New keywords start OFF (`I?keyword`); hint mode is per-group (`I?keyhint`).
- Must use **raw** reaction/message events (thread history isn't in cache); ignore the bot's own reactions or the add/remove handlers loop. Threads may be archived — unarchive before writes (`_refresh_control` does). Every intake path shows a visible ack: curation reactions or auto-deleting notes (`_ack`, 6s).
- Tests: `tests/test_ar_logic.py` (stdlib unittest, no discord needed): `python3 -m unittest discover -s tests -v`.
