"""UNSAFE synthetic scheduling experiment. Not production recovery or auth."""
import json
import math
from workers import DurableObject, WorkerEntrypoint, Response
from pyodide.ffi import create_proxy

class ClockProbe(DurableObject):
    def __init__(self, ctx, env):
        super().__init__(ctx, env)
        self.ctx, self.env = ctx, env
        ctx.storage.sql.exec('CREATE TABLE IF NOT EXISTS clock (value INTEGER NOT NULL)')
        ctx.storage.sql.exec('CREATE TABLE IF NOT EXISTS actions (value INTEGER NOT NULL)')

    async def run(self, now, hold=False, fail='', ungated=False):
        control = self.env.CONTROL.get(self.env.CONTROL.idFromName('gate'))
        async def guarded():
            try:
                await control.entered(now)
                # This is a deliberately small synthetic clock domain, not real time.
                if isinstance(now, bool) or not isinstance(now, (int, float)) or not math.isfinite(now) or int(now) != now or not 0 <= now <= 2000:
                    return 'invalid'
                now_int = int(now)
                def watermark():
                    rows = self.ctx.storage.sql.exec('SELECT value FROM clock').toArray()
                    old = rows[0].value if len(rows) else 0
                    if now_int < old: return False
                    self.ctx.storage.sql.exec('DELETE FROM clock')
                    self.ctx.storage.sql.exec('INSERT INTO clock VALUES (?)', now_int)
                    if fail == 'watermark_tx': raise RuntimeError('injected watermark rollback')
                    return True
                tx = create_proxy(watermark)
                try: valid = self.ctx.storage.transactionSync(tx)
                finally: tx.destroy()
                if not valid: return 'rollback'
                if fail == 'before_sync': return 'injected_barrier_failure'
                await self.ctx.storage.sync()
                if hold: await control.hold()
                if fail == 'after_sync': return 'injected_action_failure'
                def action():
                    if now_int >= 1010: return 'denied'
                    self.ctx.storage.sql.exec('INSERT INTO actions VALUES (?)', now_int)
                    if fail == 'action_tx': raise RuntimeError('injected action rollback')
                    return 'allowed'
                tx2 = create_proxy(action)
                try: return self.ctx.storage.transactionSync(tx2)
                finally: tx2.destroy()
            except Exception:
                return 'runtime_error'
        if ungated: return await guarded()  # Deliberately broken negative control.
        proxy = create_proxy(guarded)
        try: return await self.ctx.blockConcurrencyWhile(proxy)
        finally: proxy.destroy()

    async def snapshot(self):
        def rows(query): return [r.value for r in self.ctx.storage.sql.exec(query).toArray()]
        return json.dumps({'clock': rows('SELECT value FROM clock'), 'actions': rows('SELECT value FROM actions')})

class PythonRelay(DurableObject):
    async def run(self, family, now, hold, fail):
        target = self.env.BANK.get(self.env.BANK.idFromName(family))
        return await target.run(now, hold, fail)

class Default(WorkerEntrypoint):
    async def fetch(self, request): return Response('unsafe fixture')
