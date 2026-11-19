"""Regression coverage for pausing and resuming a kid's allowance."""

import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db  # noqa: E402


class AllowanceTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._real_path = db.DB_PATH
        db.DB_PATH = os.path.join(self._tmp.name, "test.db")
        db.init_db(seed=False)

        import bank
        self.bank = bank
        self.conn = db.get_conn()
        self.bank.add_kid(self.conn, "Alex", "💎")
        self.bank.add_kid(self.conn, "Jordan", "🦄")
        rows = self.conn.execute("SELECT id, name FROM kids ORDER BY id").fetchall()
        self.alex_id = rows[0]["id"]
        self.jordan_id = rows[1]["id"]

    def tearDown(self):
        self.conn.close()
        db.DB_PATH = self._real_path
        self._tmp.cleanup()

    def kid(self, kid_id):
        return self.conn.execute("SELECT * FROM kids WHERE id=?", (kid_id,)).fetchone()

    def make_allowance_due(self, kid_id, days=14):
        now = self.bank.now_ts(self.conn)
        self.conn.execute(
            "UPDATE kids SET allowance_anchor_ts=?, allowance_periods_paid=0, "
            "allowance_period_days=? WHERE id=?",
            (now - (days + 1) * 86400, days, kid_id),
        )
        self.conn.commit()


class AllowanceToggleTests(AllowanceTestCase):
    def test_disabled_allowance_never_accrues_or_shows_a_next_date(self):
        self.make_allowance_due(self.alex_id)
        self.bank.set_allowance(self.conn, self.alex_id, 500, 14, enabled=False)

        state = self.bank.all_kids_state(self.conn)[0]
        self.assertFalse(state["allowance_enabled"])
        self.assertEqual(state["pending_allowance_cents"], 0)
        self.assertIsNone(state["next_allowance_ts"])

    def test_collect_does_not_pay_a_disabled_allowance(self):
        self.make_allowance_due(self.alex_id)
        wallet_before = self.kid(self.alex_id)["wallet_cents"]
        self.bank.set_allowance(self.conn, self.alex_id, 500, 14, enabled=False)

        self.assertEqual(self.bank.collect_rewards(self.conn, self.alex_id, "allowance"), 0)
        self.assertEqual(self.kid(self.alex_id)["wallet_cents"], wallet_before)

    def test_zero_amount_is_distinct_from_disabled(self):
        self.bank.set_allowance(self.conn, self.alex_id, 0, 14, enabled=True)
        state = self.bank.all_kids_state(self.conn)[0]
        self.assertTrue(state["allowance_enabled"])
        self.assertEqual(state["allowance_cents"], 0)

    def test_reenabled_allowance_joins_enabled_siblings_schedule(self):
        now = self.bank.now_ts(self.conn)
        shared_anchor = now - 5 * 86400
        self.conn.execute(
            "UPDATE kids SET allowance_anchor_ts=?, allowance_periods_paid=3, "
            "allowance_period_days=7, allowance_enabled=1 WHERE id=?",
            (shared_anchor, self.jordan_id),
        )
        self.conn.commit()
        self.bank.set_allowance(self.conn, self.alex_id, 500, 14, enabled=False)

        # Re-enabling copies the active sibling's cadence and phase, even if the
        # disabled row still contains an old cadence.
        self.bank.set_allowance(self.conn, self.alex_id, 500, 14, enabled=True)
        alex = self.kid(self.alex_id)
        jordan = self.kid(self.jordan_id)
        self.assertTrue(alex["allowance_enabled"])
        self.assertEqual(alex["allowance_period_days"], jordan["allowance_period_days"])
        self.assertEqual(alex["allowance_anchor_ts"], jordan["allowance_anchor_ts"])
        self.assertEqual(alex["allowance_periods_paid"], jordan["allowance_periods_paid"])

        states = self.bank.all_kids_state(self.conn)
        self.assertEqual(states[0]["next_allowance_ts"], states[1]["next_allowance_ts"])

    def test_reenable_without_an_enabled_sibling_starts_a_fresh_period(self):
        self.bank.set_allowance(self.conn, self.alex_id, 500, 14, enabled=False)
        self.bank.set_allowance(self.conn, self.jordan_id, 500, 14, enabled=False)
        before = self.bank.now_ts(self.conn)

        self.bank.set_allowance(self.conn, self.alex_id, 500, 10, enabled=True)
        alex = self.kid(self.alex_id)
        self.assertGreaterEqual(alex["allowance_anchor_ts"], before)
        self.assertEqual(alex["allowance_periods_paid"], 0)
        self.assertEqual(alex["allowance_period_days"], 10)
        self.assertEqual(self.bank.pending_allowance(alex, self.bank.now_ts(self.conn))[0], 0)


class AllowanceApiTests(AllowanceTestCase):
    def setUp(self):
        super().setUp()
        import app
        app.app.config["TESTING"] = True
        self.bank.set_dad_pin(self.conn, "1234")
        self.client = app.app.test_client()
        self.client.environ_base["HTTP_X_DAD_PIN"] = "1234"

    def test_api_can_disable_and_reenable_allowance(self):
        disabled = self.client.post(
            "/api/admin/kid/allowance",
            json={"kid_id": self.alex_id, "amount_cents": 500,
                  "period_days": 14, "enabled": False},
        )
        self.assertTrue(disabled.get_json()["ok"])
        self.assertFalse(self.kid(self.alex_id)["allowance_enabled"])

        enabled = self.client.post(
            "/api/admin/kid/allowance",
            json={"kid_id": self.alex_id, "amount_cents": 500,
                  "period_days": 14, "enabled": True},
        )
        self.assertTrue(enabled.get_json()["ok"])
        self.assertTrue(self.kid(self.alex_id)["allowance_enabled"])


class AllowanceUiContractTests(unittest.TestCase):
    def test_dashboard_and_dad_controls_expose_allowance_toggle(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "static", "app.js"), encoding="utf-8") as fh:
            javascript = fh.read()
        with open(os.path.join(root, "static", "style.css"), encoding="utf-8") as fh:
            stylesheet = fh.read()

        self.assertIn("☹️ No allowance", javascript)
        self.assertIn('class="alw-enabled"', javascript)
        self.assertIn("allowance_enabled", javascript)
        self.assertIn(".next-allow.no-allowance", stylesheet)


class AllowanceMigrationTests(unittest.TestCase):
    def test_existing_kids_are_enabled_when_column_is_added(self):
        with tempfile.TemporaryDirectory() as tmp:
            real = db.DB_PATH
            db.DB_PATH = os.path.join(tmp, "old.db")
            try:
                conn = sqlite3.connect(db.DB_PATH)
                conn.execute(
                    """CREATE TABLE kids (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL,
                        color TEXT NOT NULL DEFAULT '#4f9dde',
                        emoji TEXT NOT NULL DEFAULT '🐣',
                        wallet_cents INTEGER NOT NULL DEFAULT 0,
                        savings_cents INTEGER NOT NULL DEFAULT 0,
                        allowance_cents INTEGER,
                        allowance_period_days INTEGER,
                        allowance_anchor_ts REAL NOT NULL,
                        allowance_periods_paid INTEGER NOT NULL DEFAULT 0,
                        savings_accrued_ts REAL NOT NULL,
                        created_ts REAL NOT NULL
                    )"""
                )
                conn.execute(
                    """INSERT INTO kids
                       (name, allowance_cents, allowance_period_days,
                        allowance_anchor_ts, savings_accrued_ts, created_ts)
                       VALUES ('Existing Kid', 300, 14, 1000, 1000, 1000)"""
                )
                conn.commit()
                conn.close()

                db.init_db(seed=True)
                conn = db.get_conn()
                row = conn.execute("SELECT name, allowance_enabled FROM kids").fetchone()
                self.assertEqual(row["name"], "Existing Kid")
                self.assertTrue(row["allowance_enabled"])
                conn.close()
            finally:
                db.DB_PATH = real


if __name__ == "__main__":
    unittest.main()
