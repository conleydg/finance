"""Fill a database with six months of made-up household finances.

Everything here is invented: merchants are real chain names so the categories
look natural, but amounts, dates and accounts are random. Run through demo.sh,
which points FINANCE_DATA at data-demo/ so the real database is never touched.
"""
import random
from datetime import date, timedelta

from finance import db, importers
from finance.categorize import apply_rules

MONTHS = 6
rng = random.Random(42)

# (description, category, low, high, times per month)
CARD_SPEND = [
    ("WHOLEFDS MKT #10234 BOSTON MA", "Groceries", 60, 190, 3),
    ("TRADER JOE'S #512", "Groceries", 40, 120, 3),
    ("COSTCO WHSE #0345 WALTHAM MA", "Groceries", 120, 320, 1),
    ("SQ *BLUE BOTTLE COFFEE", "Dining", 5, 9, 6),
    ("PANERA BREAD #601234", "Dining", 12, 38, 2),
    ("THE CHEESECAKE FACTORY #0112", "Dining", 60, 140, 1),
    ("DOORDASH*THAI BASIL", "Dining", 28, 65, 2),
    ("SHELL OIL 57442 NEWTON MA", "Fuel", 38, 62, 3),
    ("UBER *TRIP HELP.UBER.COM", "Transportation", 12, 40, 2),
    ("AMAZON MKTPLACE PMTS AMZN.COM/BILL WA", "Shopping", 15, 120, 4),
    ("TARGET 00012345 NEWTON MA", "Shopping", 25, 140, 2),
    ("CVS/PHARMACY #0123", "Healthcare", 8, 45, 1),
    ("REGAL CINEMAS FENWAY", "Entertainment", 30, 60, 1),
    ("CHEWY.COM", "Pets", 45, 80, 1),
    ("GREAT CLIPS #4410", "Personal Care", 22, 40, 1),
]
CARD_MONTHLY = [
    ("NETFLIX.COM LOS GATOS CA", "Subscriptions", 15.49, 3),
    ("SPOTIFY USA", "Subscriptions", 11.99, 7),
    ("VERIZON WIRELESS PAYMENTS", "Utilities", 95.00, 9),
    ("PLANET FITNESS", "Personal Care", 24.99, 15),
]
CHECKING_MONTHLY = [
    ("GREENWOOD APTS RENT WEB PMT", "Housing", -2150.00, 1),
    ("EVERSOURCE ENERGY BILL PAY", "Utilities", None, 7),      # varies by season
    ("NATIONAL GRID GAS BILL PAY", "Utilities", None, 12),
    ("GEICO AUTO INS", "Insurance", -156.30, 28),
    ("KUMON NORTH CENTER TUITION", "Kids & Education", -190.00, 22),
    ("ONLINE TRANSFER TO SAV XXXX1234", "Transfer", -500.00, 16),
]
BUDGETS = {"Housing": 2300, "Groceries": 900, "Dining": 350, "Utilities": 300, "Fuel": 160,
           "Shopping": 300, "Entertainment": 100, "Transportation": 80, "Travel": 250}


def month_starts(n: int) -> list[date]:
    today = date.today().replace(day=1)
    out = []
    for i in range(n - 1, -1, -1):
        y, m = today.year, today.month - i
        while m <= 0:
            y, m = y - 1, m + 12
        out.append(date(y, m, 1))
    return out


def day_in(month: date, day: int) -> date:
    nxt = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
    return min(month.replace(day=day), nxt - timedelta(days=1))


def build() -> list[tuple[str, str, float, str]]:
    """Returns (account, date, amount, description, category) rows."""
    rows = []
    today = date.today()
    for month in month_starts(MONTHS):
        winter = month.month in (12, 1, 2, 3)
        card_total = 0.0
        # Checking: payroll, bills, transfers
        for d in (1, 15):
            rows.append(("Demo Checking", day_in(month, d), 4210.55, "ACME CORP PAYROLL PPD ID: 123456", "Income"))
        for desc, cat, amt, d in CHECKING_MONTHLY:
            if amt is None:
                amt = -round(rng.uniform(140, 230) if (winter and "GAS" in desc) else rng.uniform(70, 150), 2)
            rows.append(("Demo Checking", day_in(month, d), amt, desc, cat))
        # Card: everyday spending
        for desc, cat, lo, hi, n in CARD_SPEND:
            for _ in range(n):
                amt = -round(rng.uniform(lo, hi), 2)
                rows.append(("Demo Card", day_in(month, rng.randint(1, 28)), amt, desc, cat))
                card_total += -amt
        for desc, cat, amt, d in CARD_MONTHLY:
            rows.append(("Demo Card", day_in(month, d), -amt, desc, cat))
            card_total += amt
        # One-offs to make months differ
        if month.month in (7, 8, 12):
            rows.append(("Demo Card", day_in(month, 12), -round(rng.uniform(320, 640), 2), "DELTA AIR LINES ATLANTA", "Travel"))
        if rng.random() < 0.5:
            rows.append(("Demo Card", day_in(month, 20), round(rng.uniform(15, 60), 2), "AMAZON MKTPLACE PMTS AMZN.COM/BILL WA", "Shopping"))
        # Pay the card from checking
        pay = round(card_total, 2)
        rows.append(("Demo Checking", day_in(month, 25), -pay, "DEMO CARD EPAYMENT ACH PMT", "Transfer"))
        rows.append(("Demo Card", day_in(month, 25), pay, "ONLINE PAYMENT - THANK YOU", "Transfer"))
        # Savings side of the transfer, plus interest
        rows.append(("Demo Savings", day_in(month, 16), 500.00, "Online Banking transfer from CHK 5678", "Transfer"))
        rows.append(("Demo Savings", day_in(month, 28), round(rng.uniform(9, 14), 2), "Interest Earned", "Income"))
    # Leave a couple uncategorized so that flow is visible too
    rows = [r for r in rows if r[1] <= today]
    rows.sort(key=lambda r: r[1])
    for i in (-3, -8):
        a, d, amt, desc, _ = rows[i]
        rows[i] = (a, d, amt, desc, None)
    return rows


def main() -> None:
    # This wipes the database it points at, so refuse anything but the demo folder.
    if db.DATA_DIR.name != "data-demo":
        raise SystemExit(f"Refusing to seed {db.DATA_DIR}: demo data only goes in a folder named data-demo")
    db.init()
    with db.connect() as con:
        for t in ("transactions", "imports", "rules", "budgets"):
            con.execute(f"DELETE FROM {t}")
        cats = {c["name"]: c["id"] for c in db.categories(con)}
        rows = build()
        by_acct: dict[str, list] = {}
        for acct, d, amt, desc, cat in rows:
            by_acct.setdefault(acct, []).append((d.isoformat(), desc, amt, cat))
        for acct, items in by_acct.items():
            aid = db.account_id(con, acct)
            imp = con.execute("INSERT INTO imports(filename, account_id, kind, parser, rows_found, rows_added) "
                              "VALUES (?, ?, 'csv', 'demo', ?, ?)", (f"demo-{acct.split()[-1].lower()}.csv", aid,
                                                                     len(items), len(items))).lastrowid
            txns = [importers.Txn(d, desc, amt) for d, desc, amt, _ in items]
            for (d, desc, amt, cat), fp in zip(items, importers.fingerprints(aid, txns)):
                con.execute("INSERT INTO transactions(account_id, import_id, date, description, merchant, amount, "
                            "category_id, category_source, fingerprint) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (aid, imp, d, desc, importers.merchant_key(desc), amt,
                             cats[cat] if cat else None, "model" if cat else None, fp))
        con.executemany("INSERT INTO budgets VALUES (?, ?)", [(cats[k], v) for k, v in BUDGETS.items()])
        apply_rules(con)
        n = con.execute("SELECT count(*) FROM transactions").fetchone()[0]
    print(f"Demo database ready: {n} made-up transactions over {MONTHS} months at {db.DB_PATH}")


if __name__ == "__main__":
    main()
