"""Actual bank + authorization on disposable SQLite; no DO or UI integration."""
import json
import unittest
from unittest.mock import patch

from test_foundation import Storage
from test_family_auth import F, OWNER, PARENT
from test_recovery_matrix import full_snapshot
from hosted.family_auth import FamilyAuthorization
from hosted.recovery import RecoveryAuthorization
from hosted.recovery_revision import RecoveryRevisionAuthorization, _MAX_REVISION
from hosted.service import FamilyService, _MUTATIONS


class CombinedTest(unittest.TestCase):
    def setUp(self):
        self.s = Storage(); self.addCleanup(self.s.db.close)
        self.a = RecoveryRevisionAuthorization(self.s, lambda x:x, lambda:1000)
        self.a.bootstrap_privileged(F, OWNER)
        self.token = self.a.issue_grant_privileged(F, OWNER, 100)
        self.a.set_member(F, self.token, PARENT, 'parent', 'active')
        self.parent = self.a.issue_grant_privileged(F, PARENT, 100)
        self.clock = patch('bank.time.time', return_value=1700000000); self.clock.start(); self.addCleanup(self.clock.stop)

    def rev(self):
        return self.a.snapshot(F, self.token)['revision']

    def op(self, opid, action, args, expected, revision, token=None):
        token = token or self.token
        result = self.a.mutate(F, token, opid, action, args)
        self.assertEqual(json.dumps(result), json.dumps(expected))
        self.assertEqual(self.rev(), revision)
        committed = full_snapshot(self.s)
        self.assertEqual(json.dumps(self.a.mutate(F, token, opid, action, args)), json.dumps(expected))
        self.assertEqual(full_snapshot(self.s), committed)
        pending = self.a.pending(F, token)
        self.assertEqual(pending['operation_id'], opid)
        self.assertEqual(json.dumps(pending['result']), json.dumps(expected))
        self.assertTrue(self.a.acknowledge(F, token, opid, expected))
        self.assertEqual(self.rev(), revision)
        return result

    def test_all_actions_replays_failures_and_zero_gain_change(self):
        cases = [('kid','add_kid',['Seed'],True), ('gift','gift',[1,100],True),
                 ('save','save',[1,10],[True,'Saved!']), ('withdraw','withdraw',[1,1],[True,'Done!']),
                 ('spend','spend',[1,1],[True,'Spent!']), ('invest','invest',[1,'1mo',4],[True,'Locked in!'])]
        self.assertEqual({c[1] for c in cases} | {'collect_rewards'}, set(_MUTATIONS))
        for n,(opid,action,args,result) in enumerate(cases,1): self.op(opid,action,args,result,n)
        # Synthetic setup: zero-gain matured bond still returns principal and writes state.
        self.s.db.execute('UPDATE bonds SET payout_cents=principal_cents, mature_ts=buy_ts WHERE id=1')
        self.op('zero-change','collect_rewards',[1,'invest'],0,7)
        self.op('zero-noop','collect_rewards',[1,'invest'],0,7)
        self.op('false','withdraw',[1,1000],[True,'Done!'],8)
        self.op('false2','withdraw',[1,1],[False,'Nothing to take out'],8)

    def test_pending_principals_conflicts_and_auth_before_replay(self):
        self.a.mutate(F,self.token,'kid','add_kid',['Seed'])
        self.assertEqual(self.rev(),1)
        self.assertIsNone(self.a.pending(F,self.parent))
        self.a.mutate(F,self.parent,'parent','gift',[1,2])
        self.assertEqual(self.rev(),2)
        before=full_snapshot(self.s)
        for call in (lambda:self.a.mutate(F,self.token,'other','gift',[1,1]),
                     lambda:self.a.mutate(F,self.token,'kid','add_kid',['Other']),
                     lambda:self.a.acknowledge(F,self.token,'kid',1),
                     lambda:self.a.acknowledge(F,self.parent,'kid',True),
                     lambda:self.a.mutate(F,self.parent,'kid','add_kid',['Seed']),
                     lambda:self.a.mutate(F,'bad','kid','add_kid',['Seed'])):
            with self.assertRaises((ValueError,PermissionError)): call()
            self.assertEqual(full_snapshot(self.s),before)
        self.assertEqual(self.a.pending(F,self.token)['request'],['add_kid',['Seed','🐣','#4f9dde']])
        self.assertIsNone(self.a.outcome(F,self.parent,'kid'))
        self.a.acknowledge(F,self.token,'kid',True)
        self.assertTrue(self.a.acknowledge(F,self.token,'kid',True))
        self.assertEqual(self.a.outcome(F,self.token,'kid')['acknowledged'],True)
        self.a.revoke(F,self.token)
        with self.assertRaises(PermissionError): self.a.mutate(F,self.token,'kid','add_kid',['Seed'])
        self.assertEqual(self.a.snapshot(F,self.parent)['revision'],2)

    def test_faults_restore_full_database_and_retry(self):
        self.op('kid','add_kid',['Seed'],True,1)
        before=full_snapshot(self.s)
        for prefix in ('UPDATE kids SET wallet_cents','INSERT INTO hosted_operations','INSERT INTO local_operations',
                       'INSERT INTO local_recovery','UPDATE fixture_revision SET revision=revision+1'):
            original=self.s.exec; hit=[]
            def fail(sql,*args):
                result=original(sql,*args)
                if sql.startswith(prefix): hit.append(sql); raise RuntimeError('injected')
                return result
            with self.subTest(prefix=prefix), patch.object(self.s,'exec',side_effect=fail):
                with self.assertRaisesRegex(RuntimeError,'injected'): self.a.mutate(F,self.token,'fault','gift',[1,3])
            self.assertTrue(hit); self.assertEqual(full_snapshot(self.s),before)
        self.assertTrue(self.a.mutate(F,self.token,'fault','gift',[1,3]))
        self.assertEqual(self.rev(),2)
        committed=full_snapshot(self.s)
        original=self.s.exec; hit=[]
        def fail_ack(sql,*args):
            result=original(sql,*args)
            if sql.startswith('UPDATE local_recovery SET acknowledged=1'):
                hit.append(sql); raise RuntimeError('ack injected')
            return result
        with patch.object(self.s,'exec',side_effect=fail_ack):
            with self.assertRaisesRegex(RuntimeError,'ack injected'):
                self.a.acknowledge(F,self.token,'fault',True)
        self.assertTrue(hit); self.assertEqual(full_snapshot(self.s),committed)
        self.assertTrue(self.a.acknowledge(F,self.token,'fault',True))
        self.assertEqual(self.rev(),2)

    def test_reject_raw_v1_and_schema_damage(self):
        before=full_snapshot(self.s)
        raw=FamilyService(self.s,lambda x:x)
        with self.assertRaisesRegex(RuntimeError,'coordinator'): raw.mutate('raw','add_kid',['bad'])
        with self.assertRaisesRegex(RuntimeError,'coordinator'):
            self.a.bank._transaction(lambda c: FamilyAuthorization._mutate_in_transaction(self.a,c,F,self.token,'bad','add_kid',['bad']))
        self.assertEqual(full_snapshot(self.s),before)
        for cls in (FamilyAuthorization, RecoveryAuthorization):
            other=cls(self.s,lambda x:x,lambda:1000)
            with self.assertRaises((PermissionError,RuntimeError)): other.read(F,self.token,'kids',[])
        self.s.db.execute('CREATE TABLE extra (x INTEGER)')
        before=full_snapshot(self.s)
        for call in (lambda:self.a.snapshot(F,self.token), lambda:self.a.pending(F,self.token),
                     lambda:self.a.mutate(F,self.token,'bad','add_kid',['bad'])):
            with self.assertRaises(RuntimeError): call()
            self.assertEqual(full_snapshot(self.s),before)

    def test_overflow_rolls_back(self):
        self.s.db.execute('UPDATE fixture_revision SET revision=?',(_MAX_REVISION,))
        before=full_snapshot(self.s)
        with self.assertRaisesRegex(RuntimeError,'capacity'): self.a.mutate(F,self.token,'kid','add_kid',['Seed'])
        self.assertEqual(full_snapshot(self.s),before)

    def test_bootstrap_fault_and_marker_corruption_fail_closed(self):
        s=Storage(); self.addCleanup(s.db.close)
        a=RecoveryRevisionAuthorization(s,lambda x:x,lambda:1000)
        before=full_snapshot(s); original=s.exec; hit=[]
        def fail(sql,*args):
            result=original(sql,*args)
            if sql.startswith('INSERT INTO local_members'):
                hit.append(sql); raise RuntimeError('bootstrap injected')
            return result
        with patch.object(s,'exec',side_effect=fail):
            with self.assertRaisesRegex(RuntimeError,'bootstrap injected'): a.bootstrap_privileged(F,OWNER)
        self.assertTrue(hit); self.assertEqual(full_snapshot(s),before)
        a.bootstrap_privileged(F,OWNER)
        token=a.issue_grant_privileged(F,OWNER,100)
        for sql in ('UPDATE fixture_revision SET version=2', 'DELETE FROM fixture_revision',
                    'INSERT INTO fixture_revision VALUES (1,0)', 'UPDATE local_auth_schema SET version=2'):
            with self.subTest(sql=sql):
                s.db.execute('DELETE FROM fixture_revision'); s.db.execute('INSERT INTO fixture_revision VALUES (1,0)')
                s.db.execute('UPDATE local_auth_schema SET version=3')
                s.db.execute(sql); broken=full_snapshot(s)
                with self.assertRaises((RuntimeError,PermissionError)): a.snapshot(F,token)
                with self.assertRaises((RuntimeError,PermissionError)): a.mutate(F,token,'x','add_kid',['x'])
                self.assertEqual(full_snapshot(s),broken)

    def test_reject_existing_v1_v2(self):
        for cls in (FamilyAuthorization,RecoveryAuthorization):
            s=Storage(); self.addCleanup(s.db.close)
            old=cls(s,lambda x:x,lambda:1000); old.bootstrap_privileged(F,OWNER)
            token=old.issue_grant_privileged(F,OWNER,100)
            a=RecoveryRevisionAuthorization(s,lambda x:x,lambda:1000); before=full_snapshot(s)
            with self.assertRaises(RuntimeError): a.bootstrap_privileged(F,OWNER)
            with self.assertRaises(RuntimeError): a.snapshot(F,token)
            self.assertEqual(full_snapshot(s),before)

if __name__ == '__main__': unittest.main()
