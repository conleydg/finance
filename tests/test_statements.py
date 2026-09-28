"""Overlapping retirement statements: balances, overlap skipping, and keeping them out of the budget."""
import importlib

import pytest

from tests import make_samples


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("FINANCE_DATA", str(tmp_path))
    from finance import db, importers
    importlib.reload(db)
    monkeypatch.setattr(importers.llm, "available", lambda: False)   # regex path: no model in tests
    db.init()
    make_samples.main()
    yield
    importlib.reload(db)


def _import(name, account, kind):
    from finance import app, db, importers
    parsed = importers.parse_pdf((make_samples.SAMPLES / name).read_bytes())
    with db.connect() as con:
        acct = db.account_id(con, account, kind)
        return app._save(con, acct, name, parsed)


def test_overlapping_401k_statements(env):
    from finance import db
    a = _import("401k_2026Q2.pdf", "Example 401(k)", "retirement")
    assert (a["added"], a["skipped"]) == (12, 0)
    assert a["balances_added"] == 2                       # Mar 31 and Jun 30
    b = _import("401k_2026JunAug.pdf", "Example 401(k)", "retirement")
    assert (b["added"], b["skipped"]) == (8, 4)           # June's four rows were already there
    assert b["balances_added"] == 2 and b["balance_conflicts"] == []
    again = _import("401k_2026Q2.pdf", "Example 401(k)", "retirement")
    assert again["added"] == 0 and again["balances_confirmed"] == 2
    with db.connect() as con:
        pts = [tuple(r) for r in con.execute("SELECT date, balance FROM balances ORDER BY date")]
    assert pts == [("2026-03-31", 41210.55), ("2026-05-31", 44610.20), ("2026-06-30", 45902.10),
                   ("2026-08-31", 49120.33)]


def test_retirement_activity_stays_out_of_budget(env):
    from finance import app
    _import("401k_2026Q2.pdf", "Example 401(k)", "retirement")
    b = app.budget("2026-06")
    assert b["income"] == 0 and b["spent"] == 0 and b["uncategorized"]["count"] == 0
    assert app.months() == []


def test_statement_period_from_text(env):
    from finance import importers
    p = importers.parse_pdf((make_samples.SAMPLES / "401k_2026JunAug.pdf").read_bytes())
    assert (p.period_start, p.period_end, p.beginning_balance, p.ending_balance) == \
        ("2026-06-01", "2026-08-31", 44610.20, 49120.33)


def _stage(name):
    from finance import db, identify, importers
    parsed = importers.parse_pdf((make_samples.SAMPLES / name).read_bytes())
    ident = identify.identify(parsed, name)
    with db.connect() as con:
        return parsed, ident, identify.match(con, ident)


def test_identify_and_match_by_account_number(env):
    from finance import app, db
    parsed, ident, m = _stage("401k_2026Q2.pdf")
    assert (ident["account_type"], ident["last4"], m["confidence"]) == ("retirement", "4821", "new")
    with db.connect() as con:
        acct = db.create_account(con, "Example 401(k)", "retirement", ident["institution"], ident["last4"])
        app._save(con, acct, "401k_2026Q2.pdf", parsed)
    _, _, m2 = _stage("401k_2026JunAug.pdf")
    assert (m2["account_id"], m2["confidence"]) == (acct, "sure")


def test_balance_only_statements_and_same_names(env):
    from finance import app, db, identify
    ids = {}
    for name, _ in make_samples.BALANCE_ONLY:
        parsed, ident, m = _stage(name)
        assert parsed.txns == [] and parsed.ending_balance and parsed.period_end
        assert identify.questions(ident, parsed, identify.contributions(parsed), m)[0]["id"] == "contributing"
        with db.connect() as con:
            acct = m["account_id"] or db.create_account(con, "IRA", "retirement", ident["institution"], ident["last4"])
            ids.setdefault(ident["last4"], acct)
            assert acct == ids[ident["last4"]]
            assert app._save(con, acct, name, parsed)["balances_added"] == 1
    assert set(ids) == {"3307", "9150"} and len(set(ids.values())) == 2
    with db.connect() as con:
        assert con.execute("SELECT count(*) FROM accounts WHERE name = 'IRA'").fetchone()[0] == 2
        pts = [tuple(r) for r in con.execute("SELECT account_id, date, balance FROM balances ORDER BY account_id, date")]
    assert pts == [(ids["3307"], "2026-06-30", 52310.44), (ids["3307"], "2026-09-30", 53904.12),
                   (ids["9150"], "2026-06-30", 8120.00)]
