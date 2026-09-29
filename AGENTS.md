# Working on this repo (for any coding agent or model)

Read this before changing anything. It holds the context that isn't obvious from the code: the
privacy rules, how to run and test without real data, why things are built the way they are, and
what's planned. `README.md` is the user-facing overview; this file is for whoever writes the code.

## What this is

A personal finance app for one household that runs entirely on the owner's Mac (Apple Silicon,
64 GB): import bank, card and retirement statements (CSV and PDF), categorize transactions with a
local model, keep a monthly envelope budget, track account balances and goals, and chat about the
numbers. Planned next: retirement projections, kids' college savings (529), goal-based advice,
statements arriving by forwarded email.

## Rule 1: the owner's financial data never reaches a cloud model

- Real data lives in `data/` (`data/finance.db`, `data/backups/`, `data/webview/`). It is gitignored.
  **Do not open, read, query, copy, print, screenshot or summarize anything in `data/`,** and don't run
  the real app (port 8770) in a browser you can see. Cloud-hosted coding agents must treat it as off limits
  even when a task seems to need it.
- On the owner's Mac, Claude Code enforces this with a PreToolUse hook
  (`~/.claude/hooks/block-finance-data.sh`, wired in `~/.claude/settings.json`). Other agents have no such
  hook, so follow the rule yourself.
- Importing `finance.app` (or calling `db.init()`) runs migrations against whatever `FINANCE_DATA` points
  to, defaulting to `data/`. **Always set `FINANCE_DATA` to a scratch folder or `data-demo/` when
  running code outside the test suite.**
- Inference inside the app goes only to Ollama on `127.0.0.1` (`finance/llm.py`). Never add a call to a
  hosted API, analytics, CDN fonts or any other network dependency. The server binds to 127.0.0.1 only.
- To investigate a problem with real data: ask the owner to run `.venv/bin/python scripts/diagnose.py`
  (read-only, prints counts only) and paste the output. Reproduce with synthetic files that have the same
  structure (see `tests/make_samples.py`). If a fix must be checked against real data, write a test or
  script the owner runs locally and that prints only pass/fail and counts.

## Run, demo, test

```bash
/opt/homebrew/bin/python3.12 -m venv .venv && .venv/bin/pip install -r requirements.lock   # once
.venv/bin/python -m pytest -q          # all tests use synthetic data; they must pass before any commit
./demo.sh                              # separate copy on made-up data: http://127.0.0.1:8771
./start.sh                             # the real app (owner only): http://127.0.0.1:8770
macos/build_apps.sh                    # Finance.app + Finance Demo.app in ~/Applications
```

- `demo.sh` / Finance Demo.app rebuild `data-demo/` from `demo/seed.py` on every start (six months of fake
  household data plus a two-year 401(k)). `seed.py` refuses to write to any folder not named `data-demo`.
  Use the demo for screenshots and UI checks.
- The Mac apps are py2app **alias** builds that run this folder's code with its `.venv`, so code changes
  only need the app quit and reopened. Quitting the app stops its in-process server.
- The local model is `gemma4:26b-a4b-it-q4_K_M` via Ollama (Homebrew service). `FINANCE_MODEL` overrides it.
  Tests don't need Ollama: they patch `llm.available()` to False and exercise the regex fallbacks.

## Layout

```
finance/app.py         FastAPI app: API routes, staged import (read, identify, review, commit), budget, income
finance/importers.py   CSV/PDF parsing into Parsed(txns, period, balances); sign detection; merchant keys; fingerprints
finance/identify.py    which account a statement belongs to (institution, type, last 4 digits) and what to ask
finance/categorize.py  transfer patterns, then the user's merchant rules, then the local model
finance/accounts.py    account list with balance history
finance/goals.py       goals and their progress (account inflows, or balance growth for investment accounts)
finance/assistant.py   the Ask chat: read-only tools, proposals the user must accept, the tool-calling loop
finance/llm.py         Ollama client (127.0.0.1 only)
finance/db.py          SQLite schema and migrations (migrations back the DB up first when a table is rebuilt)
finance/desktop.py     pywebview window around the in-process server
finance/static/        the page: plain HTML/CSS/JS, no build step, no external assets
demo/seed.py           made-up data for the demo copy
scripts/diagnose.py    count-only health check the owner runs
scripts/lock_installed.py  regenerates requirements.lock from the installed venv
macos/                 app bundles, icons
tests/                 pytest; make_samples.py generates fake statements in real banks' shapes
```

## Data model and conventions

- Amounts: **negative = money out, positive = money in**, from the account's point of view.
  Card exports that list purchases as positive are flipped (`apply_sign`); investment accounts never auto-flip.
- Category kinds: `expense`, `income`, `transfer`. Budget totals count expenses (refunds reduce them) and
  income; transfers are excluded. Uncategorized money out still counts toward Spent (shown as its own card).
- **Money out can never be income.** Card payments and moves between the owner's accounts are Transfer on
  both sides (`categorize.TRANSFER_RE`).
- Category sources: `user` (set by hand, never overwritten), `rule` (learned from a user choice for that
  merchant key), `pattern` (transfer regex), `model`. Rules carry a direction (`in`/`out`) and only apply
  to money moving the same way as the example.
- Account kinds: `checking`, `savings`, `credit`, `retirement`, `investment`. The last two are excluded from
  budget, categorization and chat spending totals via `db.BUDGET_ACCOUNTS`. Account names are **not** unique;
  accounts are identified by institution + last 4 digits.
- `balances` holds one point per account per date: a statement's ending balance, the day before its start
  for a beginning balance, CSV running balances, or manual entries.
- Imports are idempotent: `fingerprint` dedupes re-imported files; rows from a different file of the same
  account with the same date and amount are skipped as overlap.
- Anything the local model extracts is checked against the source text (amounts, last 4 digits and balances
  must literally appear) before it's trusted.
- The chat can't change data. Goals and budget changes it suggests become `proposals` the user accepts.
  It must not give personalized investment advice; it says so and points to a licensed professional.

## How changes are made

- Keep it simple: Python 3.12, FastAPI, SQLite, plain JS. No frontend build, no new services.
- Add or extend a test with synthetic data for every behavior change, and run the suite before committing.
- Check UI changes on the demo copy (both light and dark, and at phone width).
- Schema changes go through `db.MIGRATIONS` (add column) or a rebuild that backs up the DB first; they run
  when the app starts, on the owner's real data, so they must be safe to run twice.
- Commit to `main` and push to `git@github.com:conleydg/finance.git` (private). Keep the owner's own
  changes (e.g. the dependency lock) in separate commits from yours.

## Decisions already made (don't relitigate without asking)

- Everything local; the coding agent may be cloud-hosted but never sees real data (see Rule 1).
- UI direction: "Envelopes" (option B of four mockups): dark sidebar, a card per budget category.
- Native-feeling Mac app via pywebview rather than a browser tab or a Swift rewrite.
- Imports are reviewed before saving: the model proposes the account, the user confirms.

## Open items

- Nightly backup of the database to the owner's encrypted external drive: planned, waiting for the drive's
  name. Design: daily SQLite snapshot into `data/backups/` (keep 30), copied to the drive when mounted
  (keep a year), status shown in the sidebar.
- Statement anonymizer (local model rewrites a statement with fake names and amounts, same layout) so bugs
  that need real data can be reproduced safely: proposed, not built.
- Retirement projections, college savings, goal-based advice, email-forwarded statements: not started.
