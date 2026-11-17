import unittest
import sqlite3
import threading
import concurrent.futures
import json
from unittest.mock import patch
from test_foundation import Storage
from hosted.family_auth import FamilyAuthorization, MAX_TTL

F = 'a' * 32
OWNER = {'issuer': 'https://issuer.test', 'subject': 'owner'}
PARENT = {'issuer': 'https://issuer.test', 'subject': 'parent'}
OTHER = {'issuer': 'https://issuer.test', 'subject': 'other'}

BANK_TABLES = ('kids', 'bonds', 'transactions', 'history', 'settings',
               'hosted_schema', 'sqlite_sequence')
OP_TABLES = ('hosted_operations', 'local_operations')

def snapshot(storage, tables=BANK_TABLES + OP_TABLES):
    return {table: [tuple(row) for row in storage.db.execute(
        'SELECT * FROM ' + table + ' ORDER BY rowid')] for table in tables}

class AuthorizationTest(unittest.TestCase):
    def setUp(self):
        self.storage = Storage(); self.addCleanup(self.storage.db.close)
        self.now = 1000
        self.a = FamilyAuthorization(self.storage, lambda x: x, lambda: self.now)
        self.a.bootstrap_privileged(F, OWNER)
        self.owner = self.a.issue_grant_privileged(F, OWNER, 100)
        self.a.set_member(F, self.owner, PARENT, 'parent', 'active')
        self.parent = self.a.issue_grant_privileged(F, PARENT, 100)

    def test_policy_revocation_expiry_and_ordering(self):
        a = self.a
        with self.assertRaises(PermissionError): a.set_member(F, self.parent, OTHER, 'parent', 'active')
        with self.assertRaises(PermissionError): a.issue_grant_privileged(F, OTHER, 10)
        with self.assertRaises(PermissionError): a.read('b'*32, self.owner, 'kids', [])
        with self.assertRaises(ValueError): a.issue_grant_privileged(F, OWNER, MAX_TTL + 1)
        a.mutate(F, self.parent, 'kid', 'add_kid', ['Synthetic'])
        a.revoke(F, self.parent)
        with self.assertRaises(PermissionError): a.mutate(F, self.parent, 'kid', 'add_kid', ['Synthetic'])
        self.assertEqual(len(a.read(F, self.owner, 'kids', [])), 1)
        p2 = a.issue_grant_privileged(F, PARENT, 2)
        a.set_member(F, self.owner, PARENT, 'parent', 'disabled')
        with self.assertRaises(PermissionError): a.mutate(F, p2, 'denied', 'gift', [1, 5])
        a.set_member(F, self.owner, PARENT, 'parent', 'active')
        with self.assertRaises(PermissionError): a.read(F, p2, 'kids', [])
        p3 = a.issue_grant_privileged(F, PARENT, 2)
        self.now += 2
        with self.assertRaises(PermissionError): a.read(F, p3, 'kids', [])

    def test_swallowed_auth_failure_poison_rolls_back(self):
        self.a.mutate(F, self.owner, 'kid', 'add_kid', ['Synthetic'])
        before = snapshot(self.storage)
        def operation(c):
            c.execute('UPDATE kids SET wallet_cents=999 WHERE id=1')
            try:
                self.a._auth(c, F, 'invalid')
            except PermissionError:
                pass
        with self.assertRaisesRegex(RuntimeError, 'poisoned'):
            self.a.bank._transaction(operation)
        self.assertEqual(snapshot(self.storage), before)

    def test_idempotency_actor_and_rollback(self):
        a = self.a
        a.mutate(F, self.owner, 'kid', 'add_kid', ['Synthetic'])
        a.mutate(F, self.owner, 'gift', 'gift', [1, 10])
        with self.assertRaises(PermissionError): a.mutate(F, self.parent, 'gift', 'gift', [1, 10])
        with self.assertRaises(ValueError): a.mutate(F, self.owner, 'gift', 'gift', [1, 11])
        before = a.read(F, self.owner, 'kids', [])
        original = self.storage.exec
        def fault(sql, *args):
            r = original(sql, *args)
            if sql.startswith('INSERT INTO local_operations'): raise RuntimeError('post-bank fault')
            return r
        with patch.object(self.storage, 'exec', side_effect=fault):
            with self.assertRaisesRegex(RuntimeError, 'post-bank fault'): a.mutate(F, self.owner, 'rollback', 'gift', [1, 12])
        self.assertEqual(before, a.read(F, self.owner, 'kids', []))
        self.assertIsNone(self.storage.db.execute("SELECT * FROM hosted_operations WHERE operation_id='rollback'").fetchone())
        self.assertIsNone(self.storage.db.execute("SELECT * FROM local_operations WHERE operation_id='rollback'").fetchone())
        self.assertTrue(a.mutate(F, self.owner, 'rollback', 'gift', [1, 12]))
        restarted = FamilyAuthorization(self.storage, lambda x: x, lambda: self.now)
        self.assertTrue(restarted.mutate(F, self.owner, 'rollback', 'gift', [1, 12]))

    def test_action_role_matrix_and_same_principal_new_grant_retry(self):
        from hosted.service import _MUTATIONS, _READS
        cases = {'add_kid': ['Extra'], 'gift': [1, 100], 'save': [1, 10],
                 'withdraw': [1, 1], 'spend': [1, 1], 'invest': [1, '1mo', 4],
                 'collect_rewards': [1, 'allowance']}
        reads = {'kids': [], 'transactions': [1]}
        self.assertEqual(set(cases), set(_MUTATIONS))
        self.assertEqual(set(reads), set(_READS))

        # Each role exercises the same independently seeded family and clock.
        expected = [
            (True, (500, 0, 0), None),
            (True, (600, 0, 0), ('gift', 100, 'From Dad')),
            ([True, 'Saved!'], (590, 10, 0), ('save', 10, 'Moved to savings')),
            ([True, 'Done!'], (591, 9, 0), ('withdraw', 1, 'Savings to wallet')),
            ([True, 'Spent!'], (590, 9, 0), ('spend', -1, 'Spent it')),
            ([True, 'Locked in!'], (586, 9, 4), ('invest', 4, 'Invested (1 Month lock)')),
            (300, (886, 9, 4), ('allowance', 300, 'Allowance')),
        ]
        self.assertEqual(len(cases), len(expected))
        with patch('bank.time.time', return_value=1700000000):
            for role in ('owner', 'parent'):
                s = Storage(); self.addCleanup(s.db.close)
                a = FamilyAuthorization(s, lambda x: x, lambda: self.now)
                a.bootstrap_privileged(F, OWNER)
                owner = a.issue_grant_privileged(F, OWNER, 100)
                a.set_member(F, owner, PARENT, 'parent', 'active')
                token = owner if role == 'owner' else a.issue_grant_privileged(F, PARENT, 100)
                self.assertIs(a.mutate(F, owner, 'seed', 'add_kid', ['Seed']), True)
                s.db.execute('UPDATE kids SET allowance_anchor_ts=? WHERE id=1', (1700000000 - 14*86400,))
                self.assertEqual(a.read(F, token, 'kids', [])[0]['pending_allowance_cents'], 300)
                ledger, history = [], []
                for (action, args), (result, balances, entry) in zip(cases.items(), expected):
                    with self.subTest(role=role, action=action):
                        self.assertEqual(a.mutate(F, token, action, action, args), result)
                        after = snapshot(s)
                        self.assertEqual(a.mutate(F, token, action, action, args), result)
                        self.assertEqual(snapshot(s), after)
                        if entry:
                            ledger.insert(0, entry)
                            w, saving, invest = balances
                            history.append((w+saving+invest, w, saving, invest))
                        kids = a.read(F, token, 'kids', [])
                        self.assertEqual([k['name'] for k in kids], ['Seed', 'Extra'])
                        kid = kids[0]
                        self.assertEqual((kid['wallet_cents'], kid['savings_cents'], kid['invest_cents']), balances)
                        self.assertEqual(kid['owned_cents'], sum(balances))
                        self.assertEqual(kid['pending_allowance_cents'], 0 if action == 'collect_rewards' else 300)
                        self.assertEqual([(h['v'], h['wallet'], h['savings'], h['invest']) for h in kid['history']], history)
                        self.assertEqual([(t['kind'], t['amount_cents'], t['note']) for t in a.read(F, token, 'transactions', [1])], ledger)
                before = (a.read(F, token, 'kids', []), a.read(F, token, 'transactions', [1]))
                self.assertEqual(a.mutate(F, token, 'collect_rewards', 'collect_rewards', [1, 'allowance']), 300)
                self.assertEqual(a.mutate(F, token, 'collect-again', 'collect_rewards', [1, 'allowance']), 0)
                self.assertEqual((a.read(F, token, 'kids', []), a.read(F, token, 'transactions', [1])), before)
        self.a.mutate(F, self.owner, 'seed', 'add_kid', ['Seed'])
        disabled = {'issuer': 'https://issuer.test', 'subject': 'disabled'}
        self.a.set_member(F, self.owner, disabled, 'parent', 'active')
        disabled_token = self.a.issue_grant_privileged(F, disabled, 100)
        self.a.set_member(F, self.owner, disabled, 'parent', 'disabled')
        for role, token, allowed in [('owner', self.owner, True), ('parent', self.parent, True),
                                     ('disabled', disabled_token, False), ('invalid', 'invalid', False)]:
            for action, args in cases.items():
                with self.subTest(role=role, action=action):
                    if allowed:
                        self.a.mutate(F, token, role + action, action, args)
                    else:
                        with self.assertRaises(PermissionError):
                            self.a.mutate(F, token, role + action, action, args)
            for action, args in reads.items():
                with self.subTest(role=role, action=action):
                    if allowed: self.a.read(F, token, action, args)
                    else:
                        with self.assertRaises(PermissionError): self.a.read(F, token, action, args)
        result = self.a.mutate(F, self.parent, 'same-principal', 'gift', [1, 7])
        replacement = self.a.issue_grant_privileged(F, PARENT, 100)
        self.a.revoke(F, self.parent)
        before = self.a.read(F, self.owner, 'kids', [])
        self.assertEqual(self.a.mutate(F, replacement, 'same-principal', 'gift', [1, 7]), result)
        self.assertEqual(self.a.read(F, self.owner, 'kids', []), before)

    def test_business_failure_and_missing_child_retry_for_each_role(self):
        for role in ('owner', 'parent'):
            with self.subTest(role=role):
                s = Storage(); self.addCleanup(s.db.close)
                a = FamilyAuthorization(s, lambda x: x, lambda: self.now)
                a.bootstrap_privileged(F, OWNER)
                owner = a.issue_grant_privileged(F, OWNER, 100)
                a.set_member(F, owner, PARENT, 'parent', 'active')
                token = owner if role == 'owner' else a.issue_grant_privileged(F, PARENT, 100)
                self.assertTrue(a.mutate(F, owner, 'seed', 'add_kid', ['Seed']))
                self.assertEqual(a.mutate(F, owner, 'empty', 'spend', [1, 500]), [True, 'Spent!'])
                before_bank = snapshot(s, BANK_TABLES)
                before_ops = snapshot(s, OP_TABLES)
                failure = [False, 'No coins to spend']
                self.assertEqual(a.mutate(F, token, 'failed', 'spend', [1, 1]), failure)
                self.assertEqual(snapshot(s, BANK_TABLES), before_bank)
                after = snapshot(s)
                self.assertEqual(len(after['hosted_operations']), len(before_ops['hosted_operations']) + 1)
                self.assertEqual(len(after['local_operations']), len(before_ops['local_operations']) + 1)
                self.assertEqual(json.loads(s.db.execute("SELECT result FROM hosted_operations WHERE operation_id='failed'").fetchone()[0]), failure)
                self.assertEqual(tuple(s.db.execute("SELECT issuer, subject FROM local_operations WHERE operation_id='failed'").fetchone()),
                                 ('https://issuer.test', role))
                self.assertEqual(a.mutate(F, token, 'failed', 'spend', [1, 1]), failure)
                self.assertEqual(snapshot(s), after)
                for _ in range(2):
                    with self.assertRaisesRegex(ValueError, '^invalid request$'):
                        a.mutate(F, token, 'missing', 'gift', [999, 1])
                    self.assertEqual(snapshot(s), after)
                self.assertTrue(a.mutate(F, owner, 'refill', 'gift', [1, 5]))
                funded = snapshot(s)
                self.assertEqual(a.mutate(F, token, 'failed', 'spend', [1, 1]), failure)
                self.assertEqual(snapshot(s), funded)
                self.assertEqual(a.mutate(F, token, 'fresh-spend', 'spend', [1, 1]), [True, 'Spent!'])

    def test_disable_invalidates_all_active_grants_across_reactivation(self):
        a = self.a
        self.assertIs(a.mutate(F, self.owner, 'seed', 'add_kid', ['Seed']), True)
        old = [self.parent, a.issue_grant_privileged(F, PARENT, 100)]
        for token in old:
            self.assertEqual(a.read(F, token, 'kids', [])[0]['wallet_cents'], 500)
        a.set_member(F, self.owner, PARENT, 'parent', 'disabled')
        for token in old:
            with self.assertRaises(PermissionError): a.read(F, token, 'kids', [])
            with self.assertRaises(PermissionError): a.mutate(F, token, 'disabled', 'gift', [1, 7])
        a.set_member(F, self.owner, PARENT, 'parent', 'active')
        for token in old:
            with self.assertRaises(PermissionError): a.read(F, token, 'kids', [])
            with self.assertRaises(PermissionError): a.mutate(F, token, 'reactivated', 'gift', [1, 7])
        new = a.issue_grant_privileged(F, PARENT, 100)
        self.assertIs(a.mutate(F, new, 'new', 'gift', [1, 7]), True)
        self.assertEqual(a.read(F, new, 'kids', [])[0]['wallet_cents'], 507)
        self.assertEqual([(t['kind'], t['amount_cents']) for t in a.read(F, new, 'transactions', [1])], [('gift', 7)])

    def test_schema_and_marker_variants_fail_closed(self):
        alterations = [
            "DROP TABLE local_operations",
            "CREATE INDEX local_extra ON local_members(role)",
            "CREATE TRIGGER local_extra AFTER INSERT ON local_members BEGIN SELECT 1; END",
            "ALTER TABLE local_members ADD COLUMN extra TEXT",
            "DROP TABLE local_grants; CREATE TABLE local_grants (digest TEXT PRIMARY KEY, issuer TEXT NOT NULL, subject TEXT NOT NULL, expires INTEGER NOT NULL, revoked INTEGER NOT NULL)",
        ]
        for sql in alterations:
            with self.subTest(sql=sql):
                s = Storage(); self.addCleanup(s.db.close)
                a = FamilyAuthorization(s, lambda x: x, lambda: self.now)
                a.bootstrap_privileged(F, OWNER); token = a.issue_grant_privileged(F, OWNER, 100)
                s.db.executescript(sql)
                with self.assertRaisesRegex(RuntimeError, 'schema'): a.read(F, token, 'kids', [])
        for sql in ['DELETE FROM local_auth_schema',
                    'INSERT INTO local_auth_schema VALUES (1, "' + F + '")',
                    'UPDATE local_auth_schema SET version=2',
                    'UPDATE local_auth_schema SET version="bad"',
                    'UPDATE local_auth_schema SET family="' + 'b'*32 + '"']:
            with self.subTest(sql=sql):
                s = Storage(); self.addCleanup(s.db.close)
                a = FamilyAuthorization(s, lambda x: x, lambda: self.now)
                a.bootstrap_privileged(F, OWNER); token = a.issue_grant_privileged(F, OWNER, 100)
                s.db.execute(sql)
                with self.assertRaises(PermissionError): a.read(F, token, 'kids', [])

    def test_controlled_transaction_ordering(self):
        # This lock models transactionSync serialization; the paused first callback
        # exposes a genuine overlapping attempt, not a sequential ordering claim.
        for first in ('operation', 'revoke'):
            with self.subTest(first=first):
                s = Storage(); s.db.close()
                s.db = sqlite3.connect(':memory:', isolation_level=None, check_same_thread=False)
                s.db.row_factory = sqlite3.Row
                self.addCleanup(s.db.close)
                lock = threading.Lock(); entered = threading.Event(); release = threading.Event(); attempted = threading.Event(); acquired = threading.Event()
                thread_state = threading.local()
                original_tx = s.transactionSync
                def serial(callback):
                    contender = getattr(thread_state, 'contender', False)
                    if contender: attempted.set()
                    with lock:
                        if contender: acquired.set()
                        return original_tx(callback)
                s.transactionSync = serial
                setup = FamilyAuthorization(s, lambda x: x, lambda: self.now)
                setup.bootstrap_privileged(F, OWNER); token = setup.issue_grant_privileged(F, OWNER, 100)
                setup.mutate(F, token, 'seed', 'add_kid', ['Seed'])
                a = FamilyAuthorization(s, lambda x: x, lambda: self.now)
                b = FamilyAuthorization(s, lambda x: x, lambda: self.now)
                original_auth = a._auth
                def pause(*args, **kwargs):
                    result = original_auth(*args, **kwargs)
                    entered.set()
                    if not release.wait(10): raise RuntimeError('schedule timed out')
                    return result
                a._auth = pause
                def operation(obj): return obj.mutate(F, token, 'race', 'gift', [1, 13])
                def revoke(obj): return obj.revoke(F, token)
                def second():
                    thread_state.contender = True
                    return (revoke if first == 'operation' else operation)(b)
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                    f1 = pool.submit(operation if first == 'operation' else revoke, a)
                    try:
                        self.assertTrue(entered.wait(10))
                        f2 = pool.submit(second); self.assertTrue(attempted.wait(10))
                        with self.assertRaises(concurrent.futures.TimeoutError): f2.result(timeout=.1)
                        self.assertFalse(acquired.is_set())
                    finally: release.set()
                    f1.result(timeout=10)
                    if first == 'operation': f2.result(timeout=10)
                    else:
                        with self.assertRaises(PermissionError): f2.result(timeout=10)
                self.assertTrue(acquired.is_set())
                count = s.db.execute("SELECT count(*) FROM hosted_operations WHERE operation_id='race'").fetchone()[0]
                self.assertEqual(count, int(first == 'operation'))
                self.assertEqual(s.db.execute("SELECT count(*) FROM local_operations WHERE operation_id='race'").fetchone()[0], count)

    def test_capacity_and_nested_guard(self):
        with patch('hosted.family_auth.MAX_GRANTS', 2):
            with self.assertRaisesRegex(ValueError, 'capacity'): self.a.issue_grant_privileged(F, OWNER, 10)
            self.a.revoke(F, self.parent)
            self.a.issue_grant_privileged(F, OWNER, 10)
        with self.assertRaisesRegex(RuntimeError, 'nested transaction'):
            self.a.bank._transaction(lambda c: self.a.read(F, self.owner, 'kids', []))

    def test_bootstrap_failure_and_nonempty_rejected(self):
        s = Storage(); self.addCleanup(s.db.close)
        a = FamilyAuthorization(s, lambda x: x, lambda: self.now)
        original = s.exec
        def fault(sql, *args):
            result = original(sql, *args)
            if sql.startswith('INSERT INTO local_members'): raise RuntimeError('bootstrap fault')
            return result
        with patch.object(s, 'exec', side_effect=fault):
            with self.assertRaisesRegex(RuntimeError, 'bootstrap fault'): a.bootstrap_privileged(F, OWNER)
        self.assertEqual(s.db.execute("SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall(), [])
        s.db.execute('CREATE VIEW prior AS SELECT 1')
        with self.assertRaisesRegex(RuntimeError, 'empty database'): a.bootstrap_privileged(F, OWNER)

    def test_fail_closed_schema_and_bootstrap(self):
        with self.assertRaises(RuntimeError): self.a.bootstrap_privileged(F, OWNER)
        self.storage.db.execute('DROP TABLE local_members')
        with self.assertRaises(RuntimeError): self.a.read(F, self.owner, 'kids', [])

if __name__ == '__main__': unittest.main()
