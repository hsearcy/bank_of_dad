import json
import unittest
from unittest.mock import patch
from test_foundation import Storage
from test_family_auth import F, OWNER, PARENT, BANK_TABLES, snapshot
from hosted.recovery import RecoveryAuthorization
from hosted.service import _MUTATIONS

def full_snapshot(s):
    schema = list(map(tuple, s.db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name")))
    tables = {}
    for typ, name, _, _ in schema:
        if typ == 'table':
            quoted = '"' + name.replace('"', '""') + '"'
            tables[name] = sorted(map(tuple, s.db.execute('SELECT * FROM ' + quoted)), key=repr)
    return schema, tables


class Matrix(unittest.TestCase):
    def assertJsonExact(self, actual, expected):
        # JSON encoding distinguishes false/0 and true/1 at every nesting level.
        self.assertEqual(json.dumps(actual, sort_keys=True, ensure_ascii=False),
                         json.dumps(expected, sort_keys=True, ensure_ascii=False))

    def test_action_roles_results_and_defaults(self):
        cases = [
            ('add_kid', ['Extra'], ['Extra', '🐣', '#4f9dde'], True, (500, 0, 0), None),
            ('gift', [1, 100], [1, 100, 'From Dad'], True, (600, 0, 0), ('gift', 100, 'From Dad')),
            ('save', [1, 10], [1, 10], [True, 'Saved!'], (590, 10, 0), ('save', 10, 'Moved to savings')),
            ('withdraw', [1, 1], [1, 1], [True, 'Done!'], (591, 9, 0), ('withdraw', 1, 'Savings to wallet')),
            ('spend', [1, 1], [1, 1, ''], [True, 'Spent!'], (590, 9, 0), ('spend', -1, 'Spent it')),
            ('invest', [1, '1mo', 4], [1, '1mo', 4], [True, 'Locked in!'], (586, 9, 4), ('invest', 4, 'Invested (1 Month lock)')),
            ('collect_rewards', [1, 'allowance'], [1, 'allowance'], 300, (886, 9, 4), ('allowance', 300, 'Allowance')),
        ]
        self.assertEqual({c[0] for c in cases}, set(_MUTATIONS))
        for role in ('owner', 'parent'):
            s = Storage(); self.addCleanup(s.db.close)
            a = RecoveryAuthorization(s, lambda x:x, lambda:1000)
            a.bootstrap_privileged(F, OWNER); owner = a.issue_grant_privileged(F, OWNER, 100)
            a.set_member(F, owner, PARENT, 'parent', 'active')
            token = owner if role == 'owner' else a.issue_grant_privileged(F, PARENT, 100)
            with patch('bank.time.time', return_value=1700000000):
                self.assertIs(a.mutate(F, owner, 'seed', 'add_kid', ['Seed']), True)
                a.acknowledge(F, owner, 'seed', True)
                s.db.execute('UPDATE kids SET allowance_anchor_ts=? WHERE id=1', (1700000000-14*86400,))
                ledger=[]; history=[]
                for action, args, explicit, result, balances, entry in cases:
                    with self.subTest(role=role, action=action):
                        self.assertJsonExact(a.mutate(F, token, action, action, args), result)
                        state = snapshot(s, BANK_TABLES + ('hosted_operations','local_operations','local_recovery'))
                        self.assertJsonExact(a.mutate(F, token, action, action, explicit), result)
                        self.assertEqual(snapshot(s, tuple(state)), state)
                        out = a.pending(F, token)
                        self.assertEqual(out['request'], [action, explicit]); self.assertJsonExact(out['result'], result)
                        with self.assertRaises(ValueError): a.acknowledge(F, token, action, None)
                        mismatch = 1 if result is True else (True if type(result) is int else [False, result[1]])
                        with self.assertRaises(ValueError): a.acknowledge(F, token, action, mismatch)
                        with self.assertRaises(ValueError): a.mutate(F, token, 'blocked', 'gift', [1,1])
                        with self.assertRaises(ValueError): a.mutate(F, token, action, 'gift', [1,99])
                        self.assertTrue(a.acknowledge(F, token, action, result))
                        self.assertTrue(a.acknowledge(F, token, action, result))
                        self.assertJsonExact(a.mutate(F, token, action, action, explicit), result)
                        if entry:
                            ledger.insert(0, entry); w, sv, inv = balances
                            history.append((w+sv+inv,w,sv,inv))
                        kid = a.read(F, token, 'kids', [])[0]
                        self.assertEqual((kid['wallet_cents'],kid['savings_cents'],kid['invest_cents']), balances)
                        self.assertEqual([(h['v'],h['wallet'],h['savings'],h['invest']) for h in kid['history']],history)
                        self.assertEqual([(t['kind'],t['amount_cents'],t['note']) for t in a.read(F, token,'transactions',[1])],ledger)
                before=snapshot(s,BANK_TABLES)
                self.assertJsonExact(a.mutate(F,token,'zero','collect_rewards',[1,'allowance']),0)
                self.assertEqual(snapshot(s,BANK_TABLES),before)
                with self.assertRaises(ValueError): a.acknowledge(F,token,'zero',False)
                self.assertTrue(a.acknowledge(F,token,'zero',0))
                self.assertTrue(a.acknowledge(F,token,'zero',0))
                self.assertJsonExact(a.mutate(F,token,'zero','collect_rewards',[1,'allowance']),0)
                self.assertJsonExact(a.mutate(F,token,'default-collect','collect_rewards',[1]),0)
                self.assertEqual(a.pending(F,token)['request'],['collect_rewards',[1,'all']])
                self.assertJsonExact(a.mutate(F,token,'default-collect','collect_rewards',[1,'all']),0)
                self.assertTrue(a.acknowledge(F,token,'default-collect',0))

    def test_exact_text_conflict_and_full_fault_snapshots(self):
        s=Storage(); self.addCleanup(s.db.close)
        a=RecoveryAuthorization(s,lambda x:x,lambda:1000); a.bootstrap_privileged(F,OWNER)
        token=a.issue_grant_privileged(F,OWNER,100)
        a.mutate(F,token,'seed','add_kid',['Seed']); a.acknowledge(F,token,'seed',True)
        def full(): return full_snapshot(s)
        before=full()
        for prefix in ('UPDATE kids SET wallet_cents', 'INSERT INTO hosted_operations','INSERT INTO local_operations','INSERT INTO local_recovery'):
            original=s.exec; hit=[]
            def fail(sql,*args):
                result=original(sql,*args)
                if sql.startswith(prefix): hit.append(True); raise RuntimeError('injected')
                return result
            with self.subTest(prefix=prefix), patch.object(s,'exec',side_effect=fail):
                with self.assertRaisesRegex(RuntimeError,'injected'): a.mutate(F,token,'fault','gift',[1,3])
            self.assertTrue(hit); self.assertEqual(full(),before)
        self.assertTrue(a.mutate(F,token,'gift','gift',[1,3,' hi ']))
        before=full()
        with self.assertRaises(ValueError): a.mutate(F,token,'gift','gift',[1,3,'hi'])
        self.assertEqual(full(),before)
        original=s.exec; hit=[]
        def ack_fault(sql,*args):
            result=original(sql,*args)
            if sql.startswith('UPDATE local_recovery SET acknowledged=1'):
                hit.append(True); raise RuntimeError('ack injected')
            return result
        with patch.object(s,'exec',side_effect=ack_fault):
            with self.assertRaisesRegex(RuntimeError,'ack injected'): a.acknowledge(F,token,'gift',True)
        self.assertTrue(hit); self.assertEqual(full(),before)
        self.assertTrue(a.acknowledge(F,token,'gift',True))
        self.assertTrue(a.acknowledge(F,token,'gift',True))

    def test_business_failures_for_each_role(self):
        for role in ('owner', 'parent'):
            s = Storage(); self.addCleanup(s.db.close)
            a = RecoveryAuthorization(s, lambda x:x, lambda:1000)
            a.bootstrap_privileged(F, OWNER); owner = a.issue_grant_privileged(F, OWNER, 100)
            a.set_member(F, owner, PARENT, 'parent', 'active')
            token = owner if role == 'owner' else a.issue_grant_privileged(F, PARENT, 100)
            a.mutate(F, owner, 'seed', 'add_kid', ['Seed']); a.acknowledge(F, owner, 'seed', True)
            spent = a.mutate(F, owner, 'drain', 'spend', [1, 500]); a.acknowledge(F, owner, 'drain', spent)
            for action, args, expected in [
                ('save', [1, 1], [False, 'Too little to save']),
                ('withdraw', [1, 1], [False, 'Nothing to take out']),
                ('spend', [1, 1], [False, 'No coins to spend']),
                ('invest', [1, '1mo', 1], [False, 'Need more coins first']),
                ('collect_rewards', [1, 'allowance'], 0),
            ]:
                with self.subTest(role=role, action=action):
                    bank_before = snapshot(s, BANK_TABLES)
                    self.assertJsonExact(a.mutate(F, token, action, action, args), expected)
                    self.assertEqual(snapshot(s, BANK_TABLES), bank_before)
                    committed = full_snapshot(s)
                    self.assertJsonExact(a.pending(F, token)['result'], expected)
                    self.assertJsonExact(a.outcome(F, token, action)['result'], expected)
                    self.assertJsonExact(a.mutate(F, token, action, action, args), expected)
                    self.assertEqual(full_snapshot(s), committed)
                    self.assertTrue(a.acknowledge(F, token, action, expected))
                    self.assertJsonExact(a.mutate(F, token, action, action, args), expected)
                    self.assertEqual(snapshot(s, BANK_TABLES), bank_before)

    def test_expiry_can_reverse_on_clock_rollback(self):
        s = Storage(); self.addCleanup(s.db.close); clock = [1000]
        a = RecoveryAuthorization(s, lambda x:x, lambda:clock[0])
        a.bootstrap_privileged(F, OWNER); token = a.issue_grant_privileged(F, OWNER, 10)
        clock[0] = 1009; self.assertIsNone(a.pending(F, token))
        clock[0] = 1010
        with self.assertRaises(PermissionError): a.pending(F, token)
        # Known unresolved risk: denial rolled back and no observed-time state exists.
        clock[0] = 1009
        restarted = RecoveryAuthorization(s, lambda x:x, lambda:clock[0])
        self.assertIsNone(restarted.pending(F, token))

    def test_denied_matrix(self):
        s=Storage(); self.addCleanup(s.db.close); clock=[1000]
        a=RecoveryAuthorization(s,lambda x:x,lambda:clock[0]); a.bootstrap_privileged(F,OWNER)
        owner=a.issue_grant_privileged(F,OWNER,100); a.set_member(F,owner,PARENT,'parent','active')
        parent=a.issue_grant_privileged(F,PARENT,100)
        other={'issuer':'https://other.test','subject':'parent'}
        a.set_member(F,owner,other,'parent','active'); wrong_issuer=a.issue_grant_privileged(F,other,100)
        wrong_subject={'issuer':OWNER['issuer'],'subject':'other'}
        a.set_member(F,owner,wrong_subject,'parent','active'); wrong_principal=a.issue_grant_privileged(F,wrong_subject,100)
        expired=a.issue_grant_privileged(F,PARENT,1); revoked=a.issue_grant_privileged(F,PARENT,100); a.revoke(F,revoked)
        a.mutate(F,parent,'op','add_kid',['Test']); clock[0]=1001
        # Valid different principals may read their own scope, but cannot see or ack another's outcome.
        for token in (wrong_issuer,wrong_principal):
            self.assertIsNone(a.pending(F,token)); self.assertIsNone(a.outcome(F,token,'op'))
            with self.assertRaises(ValueError): a.acknowledge(F,token,'op',True)
            with self.assertRaises(PermissionError): a.mutate(F,token,'op','add_kid',['Test'])
        disabled={'issuer':OWNER['issuer'],'subject':'disabled'}
        a.set_member(F,owner,disabled,'parent','active'); dt=a.issue_grant_privileged(F,disabled,100)
        a.set_member(F,owner,disabled,'parent','disabled')
        for family,token in [(F,expired),(F,revoked),(F,dt),('b'*32,parent),(F,'invalid')]:
            for call in (lambda:a.read(family,token,'kids',[]), lambda:a.pending(family,token), lambda:a.outcome(family,token,'op'), lambda:a.acknowledge(family,token,'op',True), lambda:a.mutate(family,token,'op','add_kid',['Test'])):
                with self.subTest(family=family,token=token), self.assertRaises(PermissionError): call()
