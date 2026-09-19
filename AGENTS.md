# AGENTS.md — bluefield

Single-package discord.py bot (no README, no CI, no lint/typecheck config). Entrypoint: `main.py`.

## Setup & run

- `pip install -r requirements.txt` (needs `discord.py>=2.3`, `pymongo`, `pytest`, `python-dotenv`; Python 3.12).
- Copy `.env.example` → `.env` and set `DISCORD_TOKEN`, `OWNER_ID`, `MONGO_URI` (defaults to `mongodb://localhost:27017`). Bot exits at startup if `DISCORD_TOKEN` is missing.
- Run: `python main.py`. MongoDB must be reachable — `cogs/common.py` creates a `MongoClient` and unique indexes at import time.
- No formatter/linter/typechecker configured; no CI workflows.
- Commits auto-push: `.git/hooks/post-commit` pushes the current branch to `origin` on every commit (creds in `~/.git-credentials`), so just commit — no separate push needed.

## Architecture

- `main.py` — bot wiring only: prefix via `get_prefix`, global `guild_gate_check` + `tree.interaction_check` gating on `is_bot_enabled` (`I?server` stays usable), slash-command sync on `on_ready`/`on_guild_join`, persistent views re-registered in `setup_hook`, loads extensions `cogs.core`, `cogs.summons`, `cogs.confess`.
- `cogs/common.py` — shared Mongo handles (`bluefield` DB: `G S AS BL AL C P M I RP US RC PS CD`), prefix cache, duration/color parsing, role helpers (`is_admin` = administrator OR manage_roles; `is_owner`/`is_dev`/`is_mod`/`is_setup`/`is_staff`/`is_privileged`), `audit()` swallows DB errors.
- `cogs/summons.py` — virtual summon groups (`summon_roles`); slash `/summon /create /edit /delete /join /leave /invite_to /ban_from /unban_from /list /servercard /easyjoin` plus EasyJoin button panels (`easyjoin_panels`).
- `cogs/confess.py` — anonymous secrets (`anon_codes`, `secret_messages`, `secret_cooldowns`, `reveal_proposals`, `redeem_codes`, ...); slash `/secret say`, `/dm`, `/inbox`; prefix `I?confesschannel / secretthreads / confessmax / codeadd / suspend / unsuspend`.
- `cogs/core.py` — staff/punishments (`I?punishment / punishroles / B / smodrole`, `I?dev / mod / server / modlog / reports`, `I?emojies` owner/dev emoji checklist backed by `REQUIRED_EMOJIS`, `HelpView`).
- `cogs/layouts.py` — pure embed builders (`build_secret`, `build_reply`); safe to unit-test without Discord/Mongo.

## Testing

- `pytest tests/` (or `pytest tests/test_<core|summons|confess>_cog.py` for one suite). `tests/conftest.py` only fixes `sys.path`; no fixtures/plugins.
- **MongoDB required**: every test is `skipif(not mongo_available())` — without Mongo at `MONGO_URI` the suite passes vacuously (all skipped). Tests use the `bluefield_test` DB and rebind module-level collection handles (`common.G = db[...]`, etc. in each `make_cog`).
- Tests use `MagicMock`/`AsyncMock` for Discord objects; follow the existing `make_cog` + `make_member`/`make_guild` pattern when adding tests.

## Gotchas

- `cogs/common.py` connects to Mongo at import; importing any cog without a reachable DB logs/retries slowly (3s `serverSelectionTimeoutMS`) and index creation is best-effort. `audit()` and prefix lookup degrade silently on DB failure.
- `PREFIX_CACHE` in `common.py`: `set_guild_settings()` invalidates it, but direct `G.update_one` on prefix does not — use `set_guild_prefix()` / `set_guild_settings()`, and rebind `PREFIX_CACHE`-aware handles carefully in tests.
- Persistent views (`EasyJoinView`, `SecretReplyView`) are rehydrated from `easyjoin_panels`/`secret_messages` in `setup_hook` — keep their `custom_id`s stable or restart loses buttons.
- Punishment loop (`CoreCog.punishment_loop`, 45s) deliberately never removes roles — it only cleans stale `active_punishments` entries and logs expiry to the mod-log.
- Secret-chat cooldowns are per code *slot* (survive code deletion) via `secret_cooldowns`; threads channel is fixed 1h per code, main channel default unlimited unless set via `I?confesschannel [cooldown]`.
- There is no `cogs/__init__.py` (namespace package) — `from cogs.common import ...` and `bot.load_extension("cogs.core")` rely on that; don't add one unless you verify extension loading still works.
