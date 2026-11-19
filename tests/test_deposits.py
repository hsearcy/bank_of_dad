"""
Regression coverage for money going IN with a reason (deposits/gifts) and for
editing those rows later — mirroring what the Spend flow already guarantees.

Runs entirely against a throwaway SQLite file; the real data/bank.db is never
touched (db.DB_PATH is repointed before init).
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db  # noqa: E402


class BankTestCase(unittest.TestCase):
    """Fresh, seedless DB per test, plus a kid to move money around."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._real_path = db.DB_PATH
        db.DB_PATH = os.path.join(self._tmp.name, "test.db")
        db.init_db(seed=False)

        import bank
        self.bank = bank
        self.conn = db.get_conn()
        self.bank.add_kid(self.conn, "Testy", "🧪")
        self.kid_id = self.conn.execute("SELECT id FROM kids").fetchone()["id"]

    def tearDown(self):
        self.conn.close()
        db.DB_PATH = self._real_path
        self._tmp.cleanup()

    # -- helpers ----------------------------------------------------------
    def wallet(self):
        return self.conn.execute(
            "SELECT wallet_cents w FROM kids WHERE id=?", (self.kid_id,)
        ).fetchone()["w"]

    def savings(self):
        return self.conn.execute(
            "SELECT savings_cents s FROM kids WHERE id=?", (self.kid_id,)
        ).fetchone()["s"]

    def txs(self):
        return self.bank.transactions(self.conn, self.kid_id)

    def latest(self, kind):
        return next(t for t in self.txs() if t["kind"] == kind)


class GiftNoteTests(BankTestCase):
    def test_gift_records_the_reason(self):
        start = self.wallet()
        self.assertTrue(self.bank.gift(self.conn, self.kid_id, 250, "chores 🧹"))
        self.assertEqual(self.wallet(), start + 250)
        t = self.latest("gift")
        self.assertEqual(t["note"], "chores 🧹")
        self.assertEqual(t["amount_cents"], 250)

    def test_gift_note_is_trimmed(self):
        self.bank.gift(self.conn, self.kid_id, 100, "  birthday 🎂  ")
        self.assertEqual(self.latest("gift")["note"], "birthday 🎂")

    def test_blank_reason_falls_back_to_from_dad(self):
        for blank in ("", "   ", None):
            self.bank.gift(self.conn, self.kid_id, 100, blank)
            self.assertEqual(self.latest("gift")["note"], "From Dad")

    def test_gift_default_note_when_omitted(self):
        self.bank.gift(self.conn, self.kid_id, 100)
        self.assertEqual(self.latest("gift")["note"], "From Dad")

    def test_zero_or_negative_gift_is_rejected(self):
        start = self.wallet()
        self.assertFalse(self.bank.gift(self.conn, self.kid_id, 0, "nothing"))
        self.assertEqual(self.wallet(), start)
        self.assertEqual([t for t in self.txs() if t["kind"] == "gift"], [])

    def test_gift_amount_is_always_a_deposit(self):
        """A negative amount must never silently drain the wallet."""
        start = self.wallet()
        self.bank.gift(self.conn, self.kid_id, -500, "oops")
        self.assertEqual(self.wallet(), start + 500)
        self.assertEqual(self.latest("gift")["amount_cents"], 500)

    def test_gift_appears_in_history_and_snapshots_networth(self):
        before = self.conn.execute(
            "SELECT COUNT(*) c FROM history WHERE kid_id=?", (self.kid_id,)
        ).fetchone()["c"]
        self.bank.gift(self.conn, self.kid_id, 300, "tooth fairy 🦷")
        row = self.conn.execute(
            "SELECT * FROM history WHERE kid_id=? ORDER BY id DESC LIMIT 1",
            (self.kid_id,),
        ).fetchone()
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) c FROM history WHERE kid_id=?",
                              (self.kid_id,)).fetchone()["c"], before + 1)
        self.assertEqual(row["wallet_cents"], self.wallet())


class DepositEditTests(BankTestCase):
    """Deposits must be editable exactly like spends are."""

    def gift_tx(self, cents=500, note="chores 🧹"):
        self.bank.gift(self.conn, self.kid_id, cents, note)
        return self.latest("gift")

    def test_edit_deposit_reason(self):
        t = self.gift_tx()
        wallet_before = self.wallet()
        ok, _ = self.bank.edit_transaction(self.conn, t["id"], None, "extra chores 🧹")
        self.assertTrue(ok)
        self.assertEqual(self.latest("gift")["note"], "extra chores 🧹")
        self.assertEqual(self.wallet(), wallet_before)   # note-only edit moves no money

    def test_edit_deposit_amount_adjusts_wallet(self):
        t = self.gift_tx(cents=500)
        wallet_before = self.wallet()
        ok, _ = self.bank.edit_transaction(self.conn, t["id"], 800, "chores 🧹")
        self.assertTrue(ok)
        self.assertEqual(self.wallet(), wallet_before + 300)
        self.assertEqual(self.latest("gift")["amount_cents"], 800)

    def test_edit_deposit_amount_down_adjusts_wallet(self):
        t = self.gift_tx(cents=500)
        wallet_before = self.wallet()
        self.bank.edit_transaction(self.conn, t["id"], 200, "chores 🧹")
        self.assertEqual(self.wallet(), wallet_before - 300)

    def test_edit_deposit_amount_and_reason_together(self):
        t = self.gift_tx(cents=500, note="chores 🧹")
        self.bank.edit_transaction(self.conn, t["id"], 125, "  helped with dishes 🍽️ ")
        edited = self.latest("gift")
        self.assertEqual(edited["amount_cents"], 125)
        self.assertEqual(edited["note"], "helped with dishes 🍽️")

    def test_deposit_stays_positive_after_edit(self):
        """Deposits are stored positive; spends negative. Editing must not flip."""
        t = self.gift_tx(cents=500)
        self.bank.edit_transaction(self.conn, t["id"], -700, "still a deposit")
        self.assertEqual(self.latest("gift")["amount_cents"], 700)

    def test_delete_deposit_refunds_the_wallet(self):
        wallet_before = self.wallet()
        t = self.gift_tx(cents=500)
        self.assertEqual(self.wallet(), wallet_before + 500)
        ok, _ = self.bank.delete_transaction(self.conn, t["id"])
        self.assertTrue(ok)
        self.assertEqual(self.wallet(), wallet_before)
        self.assertEqual([x for x in self.txs() if x["kind"] == "gift"], [])

    def test_deposit_kinds_are_amount_editable(self):
        self.assertIn("gift", self.bank._AMOUNT_EDITABLE)
        self.assertIn("spend", self.bank._AMOUNT_EDITABLE)

    def test_spend_editing_still_works(self):
        """The behaviour we mirrored must not regress."""
        self.bank.gift(self.conn, self.kid_id, 1000, "seed")
        self.bank.spend(self.conn, self.kid_id, 300, "ice cream 🍦")
        t = self.latest("spend")
        self.assertEqual(t["amount_cents"], -300)
        wallet_before = self.wallet()
        self.bank.edit_transaction(self.conn, t["id"], 500, "big ice cream 🍦")
        edited = self.latest("spend")
        self.assertEqual(edited["amount_cents"], -500)     # spends stay negative
        self.assertEqual(edited["note"], "big ice cream 🍦")
        self.assertEqual(self.wallet(), wallet_before - 200)


class GiftApiTests(BankTestCase):
    def setUp(self):
        super().setUp()
        import app
        app.app.config["TESTING"] = True
        self.bank.set_dad_pin(self.conn, "1234")
        self.client = app.app.test_client()
        self.client.environ_base["HTTP_X_DAD_PIN"] = "1234"

    def post(self, path, body):
        return self.client.post(path, json=body)

    def test_api_gift_stores_note(self):
        r = self.post("/api/admin/gift",
                      {"kid_id": self.kid_id, "amount_cents": 250, "note": "chores 🧹"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()["ok"])
        self.assertEqual(self.latest("gift")["note"], "chores 🧹")

    def test_api_gift_accepts_reason_alias(self):
        """`reason` is what /api/spend calls it — both spellings must work."""
        self.post("/api/admin/gift",
                  {"kid_id": self.kid_id, "amount_cents": 250, "reason": "birthday 🎂"})
        self.assertEqual(self.latest("gift")["note"], "birthday 🎂")

    def test_api_gift_without_note_defaults(self):
        self.post("/api/admin/gift", {"kid_id": self.kid_id, "amount_cents": 250})
        self.assertEqual(self.latest("gift")["note"], "From Dad")

    def test_api_gift_zero_is_not_ok(self):
        r = self.post("/api/admin/gift", {"kid_id": self.kid_id, "amount_cents": 0})
        self.assertFalse(r.get_json()["ok"])

    def test_api_edit_deposit_note_and_amount(self):
        self.post("/api/admin/gift",
                  {"kid_id": self.kid_id, "amount_cents": 500, "note": "chores 🧹"})
        tx_id = self.latest("gift")["id"]
        wallet_before = self.wallet()
        r = self.post("/api/admin/tx/edit",
                      {"tx_id": tx_id, "amount_cents": 900, "note": "lots of chores 🧹"})
        self.assertTrue(r.get_json()["ok"])
        edited = self.latest("gift")
        self.assertEqual(edited["note"], "lots of chores 🧹")
        self.assertEqual(edited["amount_cents"], 900)
        self.assertEqual(self.wallet(), wallet_before + 400)

    def test_api_gift_requires_pin_when_set(self):
        self.client.environ_base.pop("HTTP_X_DAD_PIN", None)
        self.bank.set_dad_pin(self.conn, "1234")
        r = self.post("/api/admin/gift",
                      {"kid_id": self.kid_id, "amount_cents": 250, "note": "sneaky"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual([t for t in self.txs() if t["kind"] == "gift"], [])
        r = self.client.post("/api/admin/gift",
                             json={"kid_id": self.kid_id, "amount_cents": 250,
                                   "note": "allowed"},
                             headers={"X-Dad-Pin": "1234"})
        self.assertTrue(r.get_json()["ok"])
        self.assertEqual(self.latest("gift")["note"], "allowed")

    def test_transactions_endpoint_returns_deposit_notes(self):
        self.post("/api/admin/gift",
                  {"kid_id": self.kid_id, "amount_cents": 250, "note": "chores 🧹"})
        r = self.client.get(f"/api/transactions?kid_id={self.kid_id}")
        notes = [t["note"] for t in r.get_json()["transactions"] if t["kind"] == "gift"]
        self.assertIn("chores 🧹", notes)


class MigrationTests(unittest.TestCase):
    """Existing databases (and their transaction rows) must survive startup."""

    def test_existing_data_is_preserved_on_init(self):
        with tempfile.TemporaryDirectory() as tmp:
            real = db.DB_PATH
            db.DB_PATH = os.path.join(tmp, "old.db")
            try:
                db.init_db(seed=False)
                import bank
                conn = db.get_conn()
                bank.add_kid(conn, "Old Kid", "🧓")
                kid_id = conn.execute("SELECT id FROM kids").fetchone()["id"]
                bank.gift(conn, kid_id, 400, "legacy gift")
                wallet = conn.execute("SELECT wallet_cents w FROM kids WHERE id=?",
                                      (kid_id,)).fetchone()["w"]
                conn.close()

                db.init_db(seed=True)   # simulate an app restart

                conn = db.get_conn()
                self.assertEqual(conn.execute("SELECT COUNT(*) c FROM kids")
                                 .fetchone()["c"], 1)
                t = bank.transactions(conn, kid_id)[0]
                self.assertEqual(t["note"], "legacy gift")
                self.assertEqual(conn.execute(
                    "SELECT wallet_cents w FROM kids WHERE id=?",
                    (kid_id,)).fetchone()["w"], wallet)
                conn.close()
            finally:
                db.DB_PATH = real


if __name__ == "__main__":
    unittest.main()
