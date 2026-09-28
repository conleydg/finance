import json
from datetime import date

import pytest


@pytest.fixture()
def con(tmp_path, monkeypatch):
    monkeypatch.setenv("FINANCE_DATA", str(tmp_path))
    import importlib

    from finance import db
    importlib.reload(db)
    db.init()
    c = db.connect()
    yield c
    c.close()
    importlib.reload(db)


def test_goal_progress_counts_account_inflows(con):
    from finance import db, goals
    acct = db.account_id(con, "Savings")
    gid = goals.create(con, "Car", 1000, None, 100, acct)
    today = date.today().isoformat()
    con.execute("INSERT INTO transactions(account_id, date, description, merchant, amount, fingerprint) "
                "VALUES (?, ?, 'transfer in', 'TRANSFER', 250, 'a')", (acct, today))
    g = [x for x in goals.list_goals(con) if x["id"] == gid][0]
    assert g["saved"] == 350 and g["remaining"] == 650


def test_saved_update_rebases(con):
    from finance import goals
    gid = goals.create(con, "Trip", 4000, "2099-07-31")
    goals.update(con, gid, {"saved_so_far": 1500, "target_amount": 5000})
    g = goals.list_goals(con)[0]
    assert (g["saved"], g["target_amount"]) == (1500, 5000)
    assert g["needed_per_month"] > 0


def test_budget_proposal_applies_only_on_accept(con):
    from finance import assistant
    res = assistant.p_budgets(con, [{"category": "dining", "monthly_amount": 200}], "test")
    p = res["proposal"]
    assert p["payload"]["changes"][0]["category"] == "Dining"
    mid = con.execute("INSERT INTO chat_messages(role, content) VALUES ('assistant', 'x')").lastrowid
    pid = con.execute("INSERT INTO proposals(message_id, kind, payload) VALUES (?, ?, ?)",
                      (mid, p["kind"], json.dumps(p["payload"]))).lastrowid
    con.commit()
    assert con.execute("SELECT count(*) FROM budgets").fetchone()[0] == 0
    assistant.resolve(pid, accept=True)
    assert con.execute("SELECT monthly_amount FROM budgets").fetchone()[0] == 200


def test_bad_proposals_are_rejected(con):
    from finance import assistant
    assert "error" in assistant.p_budgets(con, [{"category": "Nonexistent", "monthly_amount": 5}])
    assert "error" in assistant.p_goal(con, "Car", 0)
    assert assistant.p_goal(con, "Car", 5000, "2027-06")["proposal"]["payload"]["target_date"] == "2027-06-30"
