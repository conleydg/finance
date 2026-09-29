# Finance

A personal finance app that runs entirely on this Mac. Import bank and card statements, let a local model categorize them, and see a monthly budget.

- **Stack:** Python 3.12, FastAPI, SQLite, plain HTML/JS. No build step.
- **Privacy:** the server binds to 127.0.0.1, data lives in `data/finance.db` (gitignored), and the only model it talks to is Ollama on 127.0.0.1. The page loads nothing from the internet.

## Run

```bash
./start.sh          # http://127.0.0.1:8770
```

Or use it as a Mac app: its own window (WebKit via pywebview) with the server running inside it, so Cmd+Q stops everything.

```bash
macos/build_apps.sh   # creates ~/Applications/Finance.app and Finance Demo.app; drag to the Dock
```

The apps are py2app alias builds: they run the code in this folder with its `.venv`, so code changes only need the app reopened, not rebuilt.

### Demo copy

```bash
./demo.sh                    # http://127.0.0.1:8771, six months of made-up data
                             # or open Finance Demo.app
```

The demo lives in `data-demo/`, is rebuilt on every start, and shows a "Demo data" badge. `demo/seed.py` refuses to write anywhere but a folder named `data-demo`. Use it for screenshots, trying changes, and anything a cloud tool looks at; never point those at the real app.

First-time setup:

```bash
/opt/homebrew/bin/python3.12 -m venv .venv && .venv/bin/pip install -r requirements.lock
```

`requirements.txt` lists direct dependencies; `requirements.lock` captures their exact installed versions and transitive dependencies for Python 3.12 on Apple Silicon macOS. It includes the existing Mac app build and test dependencies. The snapshot does not pin Ollama itself or download its model. It has version pins, not artifact hashes, and has not been verified by a clean reinstall.

Check package consistency without starting the app:

```bash
.venv/bin/python -m pip check
```

After intentionally changing dependencies and validating the environment, refresh the lock and review its diff:

```bash
.venv/bin/python scripts/lock_installed.py requirements.txt requirements.lock
```

The script reads installed package metadata only. It does not open the finance database or import application modules. Keep dependency updates separate from changes to personal data.

The model defaults to `gemma4:26b-a4b-it-q4_K_M`; override with `FINANCE_MODEL=...`. `FINANCE_DATA=/some/dir` points at a different data folder (handy for trying things out).

## What it does

**Import** (drop any mix of CSV and PDF statements on the Import page)
- The local model reads each file first and identifies the institution, account type and last four digits of the account number (checked against the text). The app matches it to an existing account and only asks what's missing: which account, whether you're still contributing, a balance's date. Nothing is saved until you press Save.
- CSV: finds the header row even under a summary block; handles one amount column or separate debit/credit columns, and running-balance columns.
- PDF: text is extracted with pdfplumber and each page is read by the local model. Amounts, balances and account digits the model returns must literally appear in the text. If Ollama is down, regex fallbacks are used. Scanned (image-only) PDFs aren't supported yet.
- Signs: money out is negative. Card exports that list purchases as positive (Amex, Discover) are detected and flipped.
- Overlapping statements are safe: rows another file of the same account already added (same date and amount) are skipped and listed. Re-importing a file adds nothing. Any import can be removed.
- Balance-only statements ("Total account value as of ...") add a point to that account's balance history.

**Accounts**
- Types: checking, savings, credit, retirement, investment. Retirement and investment activity stays out of the budget. Names don't have to be unique; accounts are told apart by institution and account number.
- Balance history per account from statements (ending balances), CSV running balances or manual entries, with a small chart.

**Categorize**
- Card payments and transfers between your own accounts are recognized by pattern and counted as Transfer on both sides.
- Then your merchant rules, then the local model in batches of 40. Money out is never counted as income.
- Changing a category remembers it for that merchant (for money moving the same direction) and re-applies it to earlier transactions you haven't set by hand. The tag next to each category shows where it came from: `you`, `rule` or `AI`.

**Budget**
- Per month: an envelope card per category with what's left, income, spending and what you kept, and a three-month average. Refunds reduce spending; transfers are excluded; uncategorized spending still counts and has its own card.
- Click Income to see what's counted as income and any money in that isn't, with one-click fixes.

**Ask and Goals**
- Ask is a chat with the local model. It looks up your numbers through read-only tools (spending by month, transaction search, top merchants, account balances) plus a snapshot of averages, budgets, accounts and goals.
- When you mention a goal, or ask how to reach one, it suggests saving the goal or changing budgets. Suggestions appear as cards under the reply; nothing changes until you accept. It won't recommend specific investments.
- Goals track what you'd saved when you set them plus money flowing into a linked account (or that account's balance growth, for retirement and investment accounts), the monthly amount needed, and where your recent pace lands you.

**Diagnose**
- `.venv/bin/python scripts/diagnose.py` prints a count-only health check (no descriptions, amounts or names) that's safe to share when something looks off.

## Tests

```bash
.venv/bin/python -m pytest -q
```

Tests use synthetic statements generated by `tests/make_samples.py` (fake data in real banks' export shapes). Nothing in the repo contains real financial data.

## For coding agents

See [AGENTS.md](AGENTS.md) (also loaded by Claude Code through `CLAUDE.md`): privacy rules, conventions, decisions and open items.

## Not yet

Nightly backup to an encrypted drive, a statement anonymizer for reproducing bugs safely, retirement projections, college savings, goal-based advice, and statements by forwarded email.
