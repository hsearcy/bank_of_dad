"""Characterize the existing Python domain using synthetic state and a fixed clock.

These assertions document behavior, including quirks; they do not prescribe fixes.
"""
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bank
import db

DAY = 86400
T0 = 1_700_000_000.0


class HostedContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.old_path = db.DB_PATH
        self.addCleanup(setattr, db, "DB_PATH", self.old_path)
        db.DB_PATH = os.path.join(self.tmp.name, "contract.db")
        db.init_db(seed=False)
        self.conn = db.get_conn()
        self.addCleanup(self.conn.close)
        self.clock = patch("bank.time.time", return_value=T0)
        self.mock_time = self.clock.start()
        self.addCleanup(self.clock.stop)
        bank.add_kid(self.conn, "Alex")
        bank.add_kid(self.conn, "Jordan")
        self.a, self.b = [r[0] for r in self.conn.execute("SELECT id FROM kids ORDER BY id")]

    def at(self, days):
        self.mock_time.return_value = T0 + days * DAY

    def kid(self, kid_id=None):
        return self.conn.execute("SELECT * FROM kids WHERE id=?", (kid_id or self.a,)).fetchone()

    def state(self, kid_id=None):
        return bank.kid_state(self.conn, self.kid(kid_id), bank.now_ts(self.conn))

    def test_allowance_resume_copies_first_enabled_sibling_paid_counter(self):
        bank.add_kid(self.conn, "Casey")
        c = self.conn.execute("SELECT max(id) FROM kids").fetchone()[0]
        bank.set_allowance(self.conn, self.a, 200, 7)
        self.at(21)
        self.assertEqual(bank.collect_rewards(self.conn, self.a, "allowance"), 600)
        bank.set_allowance(self.conn, self.b, 100, 14, False)
        bank.set_allowance(self.conn, c, 900, 3)
        bank.set_allowance(self.conn, self.b, 100, 99, True)
        resumed = self.kid(self.b)
        self.assertEqual((resumed["allowance_period_days"], resumed["allowance_anchor_ts"],
                          resumed["allowance_periods_paid"]), (7, T0, 3))
        self.assertEqual(self.state(self.b)["pending_allowance_cents"], 0)
        self.at(28)
        self.assertEqual(bank.collect_rewards(self.conn, self.b, "allowance"), 100)
        self.assertEqual(bank.collect_rewards(self.conn, self.b, "allowance"), 0)

    def test_zero_allowance_collection_does_not_advance_paid_periods(self):
        bank.set_allowance(self.conn, self.a, 0, 7)
        self.at(21)
        self.assertEqual(bank.collect_rewards(self.conn, self.a, "allowance"), 0)
        self.assertEqual(self.kid()["allowance_periods_paid"], 0)
        bank.set_allowance(self.conn, self.a, 100, 7)
        self.assertEqual(self.state()["pending_allowance_cents"], 300)

    def test_deleting_allowance_does_not_restore_owed_periods(self):
        bank.set_allowance(self.conn, self.a, 100, 7)
        self.at(7)
        self.assertEqual(bank.collect_rewards(self.conn, self.a, "allowance"), 100)
        tx = bank.transactions(self.conn, self.a)[0]
        self.assertEqual(bank.delete_transaction(self.conn, tx["id"]), (True, "Deleted"))
        self.assertEqual(self.kid()["allowance_periods_paid"], 1)
        self.assertEqual(self.state()["pending_allowance_cents"], 0)

    def test_zero_rounded_interest_does_not_reset_accrual_clock(self):
        bank.set_rates(self.conn, savings_weekly_pct=0.5)
        bank.move_to_savings(self.conn, self.a, 100)
        self.at(7)
        self.assertEqual(bank.collect_rewards(self.conn, self.a, "interest"), 0)
        self.assertEqual(self.kid()["savings_accrued_ts"], T0)
        self.at(14)
        self.assertEqual(bank.collect_rewards(self.conn, self.a, "interest"), 1)

    def test_legacy_history_without_pool_breakdown_falls_back_to_wallet(self):
        self.conn.execute("INSERT INTO history(kid_id, ts, networth_cents) VALUES(?,?,?)",
                          (self.a, T0 - 1, 321))
        self.conn.commit()
        self.assertEqual(self.state()["history"], [
            {"ts": T0 - 1, "v": 321, "wallet": 321, "savings": 0, "invest": 0}])

    def test_interest_is_lazy_simple_until_collection_then_compounds(self):
        bank.set_rates(self.conn, savings_weekly_pct=10)
        bank.move_to_savings(self.conn, self.a, 500)
        self.at(14)
        self.assertEqual(self.state()["pending_interest_cents"], 100)
        self.assertEqual(self.state()["pending_interest_cents"], 100)
        self.assertEqual(self.kid()["savings_cents"], 500)
        self.assertEqual(bank.collect_rewards(self.conn, self.a, "interest"), 100)
        self.assertEqual(self.kid()["savings_cents"], 600)
        self.at(21)
        self.assertEqual(self.state()["pending_interest_cents"], 60)
        self.assertEqual(bank.collect_rewards(self.conn, self.a, "interest"), 60)
        self.assertEqual(bank.collect_rewards(self.conn, self.a, "interest"), 0)

    def test_save_and_withdraw_capitalize_interest_without_interest_transaction(self):
        bank.set_rates(self.conn, savings_weekly_pct=10)
        bank.move_to_savings(self.conn, self.a, 200)
        self.at(7)
        bank.move_to_savings(self.conn, self.a, 100)
        self.assertEqual((self.kid()["wallet_cents"], self.kid()["savings_cents"]), (200, 320))
        self.at(14)
        self.assertEqual(bank.withdraw_savings(self.conn, self.a, 9999), (True, "Done!"))
        self.assertEqual((self.kid()["wallet_cents"], self.kid()["savings_cents"]), (552, 0))
        self.assertEqual([t["kind"] for t in bank.transactions(self.conn, self.a)],
                         ["withdraw", "save", "save"])
        self.assertEqual(bank.withdraw_savings(self.conn, self.a, 1), (False, "Nothing to take out"))

    def test_wallet_caps_save_bond_and_spend(self):
        self.assertEqual(bank.move_to_savings(self.conn, self.a, 9999), (True, "Saved!"))
        self.assertEqual(self.kid()["wallet_cents"], 0)
        self.assertEqual(bank.spend(self.conn, self.a, 1), (False, "No coins to spend"))
        bank.withdraw_savings(self.conn, self.a, 300)
        self.assertEqual(bank.spend(self.conn, self.a, 999, "  toy  "), (True, "Spent!"))
        self.assertEqual(bank.transactions(self.conn, self.a)[0]["amount_cents"], -300)
        bank.withdraw_savings(self.conn, self.a, 999)
        self.assertEqual(bank.buy_bond(self.conn, self.a, "1mo", 999), (True, "Locked in!"))
        self.assertEqual(self.conn.execute("SELECT principal_cents FROM bonds").fetchone()[0], 200)

    def test_bond_boundaries_payout_gain_and_fixed_purchase_rate(self):
        bank.gift(self.conn, self.a, 500)
        bank.buy_bond(self.conn, self.a, "1mo", 400)
        bank.buy_bond(self.conn, self.a, "3mo", 600)
        bank.set_rates(self.conn, returns={"1mo": 999, "3mo": 0})
        self.at(30 - 1 / DAY)
        self.assertEqual(self.state()["pending_bonds_cents"], 0)
        self.assertEqual(bank.collect_rewards(self.conn, self.a, "invest"), 0)
        self.at(30)
        state = self.state()
        self.assertEqual((state["owned_cents"], state["invest_cents"], state["pending_bonds_cents"]), (1000, 1000, 100))
        self.assertEqual([b["ready"] for b in state["bonds"]], [True, False])
        self.assertEqual(bank.collect_rewards(self.conn, self.a, "invest"), 100)
        self.assertEqual((self.state()["wallet_cents"], self.state()["invest_cents"]), (500, 600))
        self.at(90)
        self.assertEqual(bank.collect_rewards(self.conn, self.a, "invest"), 600)
        self.assertEqual((self.state()["wallet_cents"], self.state()["invest_cents"]), (1700, 0))
        self.assertEqual(bank.collect_rewards(self.conn, self.a, "invest"), 0)

    def test_payout_deletion_relocks_and_can_be_recollected(self):
        bank.buy_bond(self.conn, self.a, "1mo", 400)
        self.at(30)
        bank.collect_rewards(self.conn, self.a, "invest")
        txs = bank.transactions(self.conn, self.a)
        payout = next(t for t in txs if t["kind"] == "bond_payout")
        investment = next(t for t in txs if t["kind"] == "invest")
        self.assertFalse(bank.delete_transaction(self.conn, investment["id"])[0])
        self.assertEqual(bank.delete_transaction(self.conn, payout["id"]), (True, "Deleted"))
        self.assertEqual((self.state()["wallet_cents"], self.state()["invest_cents"],
                          self.state()["pending_bonds_cents"]), (100, 400, 100))
        self.assertEqual(bank.collect_rewards(self.conn, self.a, "invest"), 100)

    def test_history_corrections_are_new_snapshots_not_rewrites(self):
        bank.gift(self.conn, self.a, 100, "gift")
        tx = bank.transactions(self.conn, self.a)[0]
        self.at(1)
        bank.edit_transaction(self.conn, tx["id"], 200, " corrected ")
        self.at(2)
        bank.delete_transaction(self.conn, tx["id"])
        self.assertEqual(self.state()["history"], [
            {"ts": T0, "v": 600, "wallet": 600, "savings": 0, "invest": 0},
            {"ts": T0 + DAY, "v": 700, "wallet": 700, "savings": 0, "invest": 0},
            {"ts": T0 + 2 * DAY, "v": 500, "wallet": 500, "savings": 0, "invest": 0},
        ])
        self.assertEqual(bank.transactions(self.conn, self.a), [])

    def test_settings_are_family_global_retroactive_and_round_half_even(self):
        bank.move_to_savings(self.conn, self.a, 100)
        bank.move_to_savings(self.conn, self.b, 300)
        self.at(7)
        bank.set_rates(self.conn, savings_weekly_pct=0.5, returns={"1mo": 50, "bad": 12})
        self.assertEqual((self.state()["pending_interest_cents"], self.state(self.b)["pending_interest_cents"]), (0, 2))
        self.assertIsNone(db.get_setting(self.conn, "return_pct_bad"))
        bank.fast_forward(self.conn, 7)
        self.assertEqual(bank.now_ts(self.conn), T0 + 14 * DAY)
        self.assertEqual((self.state()["pending_interest_cents"], self.state(self.b)["pending_interest_cents"]), (1, 3))
        bank.buy_bond(self.conn, self.a, "1mo", 1)
        self.assertEqual(self.conn.execute("SELECT payout_cents FROM bonds").fetchone()[0], 2)

    def test_api_exact_json_for_collect_and_transactions(self):
        import app
        app.app.config["TESTING"] = True
        client = app.app.test_client()
        response = client.post("/api/collect", json={"kid_id": self.a, "kind": "interest"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"ok": True, "collected_cents": 0})
        response = client.get(f"/api/transactions?kid_id={self.a}")
        self.assertEqual(response.get_json(), {"server_now": T0, "transactions": []})


if __name__ == "__main__":
    unittest.main()
