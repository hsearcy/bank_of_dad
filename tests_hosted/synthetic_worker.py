"""Synthetic loopback harness ONLY. Never deploy or bind publicly."""
import json
from urllib.parse import urlparse, parse_qs
from workers import WorkerEntrypoint, Response
from hosted.object import FamilyBank
from hosted.service import FamilyService
import bank

bank.time.time = lambda: 1700000000.0

class SyntheticBank(FamilyBank):
    def __init__(self, ctx, env):
        super().__init__(ctx, env)
        self.service = FamilyService(ctx.storage)

    async def fetch(self, request):
        try:
            q = parse_qs(urlparse(request.url).query)
            action = q['action'][0]
            args = json.loads(q.get('args', ['[]'])[0])
            if action == 'read':
                result = self.service.read(args[0], args[1])
            elif action == 'fixture_view':
                result = self.service._transaction(lambda c: c.execute('CREATE VIEW orphan AS SELECT 1').fetchall())
            elif action == 'fixture_v0':
                def zero(c):
                    c.execute('CREATE TABLE hosted_schema(version INTEGER NOT NULL)')
                    c.execute('INSERT INTO hosted_schema VALUES(0)')
                    if args and args[0]:
                        c.execute('CREATE TABLE legacy(value TEXT)')
                        c.execute("INSERT INTO legacy VALUES('keep')")
                    return True
                result = self.service._transaction(zero)
            elif action == 'fixture_rewards':
                def rewards(c):
                    c.execute('UPDATE kids SET allowance_anchor_ts=1696976000, savings_accrued_ts=1696976000 WHERE id=1')
                    c.execute('UPDATE bonds SET buy_ts=1696976000, mature_ts=1699568000 WHERE kid_id=1')
                    return True
                result = self.service._transaction(rewards)
            elif action == 'fixture_bad_bond':
                def bad(c):
                    c.execute('UPDATE bonds SET payout_cents=principal_cents-1, mature_ts=3999999999 WHERE kid_id=1')
                    return True
                result = self.service._transaction(bad)
            elif action == 'fixture_reversed_bond':
                def reversed_bond(c):
                    c.execute('UPDATE bonds SET buy_ts=1700000100, mature_ts=1699999900 WHERE kid_id=1')
                    return True
                result = self.service._transaction(reversed_bond)
            elif action == 'fixture_full_snapshot':
                def snapshot_all(c):
                    tables = ('hosted_schema', 'kids', 'bonds', 'history', 'transactions', 'settings', 'hosted_operations', 'sqlite_sequence')
                    return {table: c.execute('SELECT * FROM ' + table + ' ORDER BY rowid').fetchall() for table in tables}
                result = self.service._transaction(snapshot_all)
            elif action == 'fail_collect':
                def fail_collect(c):
                    with c._authorized_bank():
                        bank.collect_rewards(c, 1)
                    raise RuntimeError('after collection commit')
                result = self.service._transaction(fail_collect)
            elif action == 'fixture_balance':
                # Fixed synthetic-only literals; never accepts SQL or real data.
                def fixture(c):
                    self.service._initialize(c)
                    if args[0] == 'oversized':
                        c.execute('UPDATE kids SET wallet_cents=9007199254740992 WHERE id=1')
                    elif args[0] == 'near':
                        c.execute('UPDATE kids SET wallet_cents=999999999, savings_cents=0 WHERE id=1')
                    elif args[0] == 'accrual':
                        c.execute('UPDATE kids SET wallet_cents=0, savings_cents=1000000000, savings_accrued_ts=0 WHERE id=1')
                    elif args[0] == 'investment':
                        c.execute('UPDATE kids SET wallet_cents=1000000000, savings_cents=0 WHERE id=1')
                    return True
                result = self.service._transaction(fixture)
            elif action == 'fixture_snapshot':
                def snapshot(c):
                    return c.execute("SELECT CAST(wallet_cents AS TEXT) AS wallet, CAST(savings_cents AS TEXT) AS savings, (SELECT count(*) FROM transactions) AS tx, (SELECT count(*) FROM bonds) AS bonds FROM kids WHERE id=1").fetchone()
                result = self.service._transaction(snapshot)
            elif action == 'fail_after_commit':
                def fail(conn):
                    with conn._authorized_bank():
                        bank.gift(conn, 1, 900, 'rollback')
                    raise RuntimeError('after inner commit')
                result = self.service._transaction(fail)
            else:
                result = self.service.mutate(q['id'][0], action, args)
            return Response(json.dumps({'result': result}), headers={'content-type': 'application/json'})
        except Exception as e:
            return Response(json.dumps({'error': str(e)}), status=500, headers={'content-type': 'application/json'})

class Default(WorkerEntrypoint):
    async def fetch(self, request):
        q = parse_qs(urlparse(request.url).query)
        name = q.get('family', ['a'])[0]
        return await self.env.BANK.get(self.env.BANK.idFromName(name)).fetch(request.url)
