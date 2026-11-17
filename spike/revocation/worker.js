// Synthetic loopback harness only. No production bindings, authentication, or real data.
import { DurableObject } from 'cloudflare:workers';
const id = x => { if (typeof x !== 'string' || !/^[a-zA-Z0-9_-]{1,48}$/.test(x)) throw Error('invalid id'); return x; };
const num = x => { if (!Number.isSafeInteger(x) || x < 0) throw Error('invalid number'); return x; };
const one = (sql, q, ...args) => [...sql.exec(q, ...args)][0];
const response = obj => Response.json(obj);
const send = async (stub, body) => { const r = await stub.fetch('http://internal/', {method:'POST', body:JSON.stringify(body)}); if (!r.ok) throw Error('remote failed'); return await r.json(); };

export class Family extends DurableObject {
  constructor(ctx, env) { super(ctx, env); this.sql = ctx.storage.sql; this.sql.exec('CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, fence INTEGER NOT NULL, grant_gen INTEGER, expiry INTEGER)'); }
  async fetch(req) {
    try {
      const b = await req.json(); id(b.session);
      if (b.action === 'fence') {
        num(b.gen); if (b.gen < 1) throw Error('bad fence');
        this.ctx.storage.transactionSync(() => this.sql.exec('INSERT INTO sessions(id,fence) VALUES (?,?) ON CONFLICT(id) DO UPDATE SET fence=MAX(fence,excluded.fence)', b.session,b.gen));
        return response({ack:b.gen});
      }
      if (b.action === 'grant') {
        num(b.gen); num(b.expiry); if (b.gen !== 0) throw Error('bad grant');
        const ok = this.ctx.storage.transactionSync(() => {
          const row = one(this.sql,'SELECT * FROM sessions WHERE id=?',b.session);
          if (row && row.fence >= b.gen + 1) return false;
          if (row && row.expiry !== b.expiry) throw Error('conflicting grant');
          this.sql.exec('INSERT INTO sessions(id,fence,grant_gen,expiry) VALUES (?,0,?,?) ON CONFLICT(id) DO UPDATE SET grant_gen=excluded.grant_gen,expiry=excluded.expiry',b.session,b.gen,b.expiry); return true;
        }); return response({ok});
      }
      if (b.action === 'access') {
        num(b.now); const r = one(this.sql,'SELECT * FROM sessions WHERE id=?',b.session);
        return response({ok:!!r && r.grant_gen === 0 && r.fence === 0 && b.now < r.expiry});
      }
      throw Error('unknown action');
    } catch (_) { return Response.json({error:'invalid'}, {status:400}); }
  }
}

export class Coordinator extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env); this.sql=ctx.storage.sql;
    this.sql.exec('CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, expiry INTEGER NOT NULL, state TEXT NOT NULL, generation INTEGER NOT NULL)');
    this.sql.exec('CREATE TABLE IF NOT EXISTS targets (session TEXT NOT NULL, family TEXT NOT NULL, ack INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(session,family))');
  }
  family(name) { return this.env.FAMILY.get(this.env.FAMILY.idFromName(name)); }
  async fetch(req) {
    try {
      const b=await req.json(); id(b.session);
      const tx = fn => this.ctx.storage.transactionSync(fn);
      if (b.action === 'issue') {
        num(b.expiry); num(b.now); if (b.expiry <= b.now) throw Error('expired');
        tx(() => this.sql.exec('INSERT INTO sessions VALUES (?, ?, ?, 0)',b.session,b.expiry,'active'));
        return response({ok:true});
      }
      if (b.action === 'reserve') {
        id(b.family); num(b.now);
        const grant = tx(() => {
          const s=one(this.sql,'SELECT * FROM sessions WHERE id=?',b.session);
          if (!s || s.state !== 'active' || b.now >= s.expiry) throw Error('denied');
          this.sql.exec('INSERT OR IGNORE INTO targets(session,family) VALUES (?,?)',b.session,b.family);
          return {action:'grant', session:b.session,gen:s.generation,expiry:s.expiry};
        }); return response({grant});
      }
      if (b.action === 'deliver') {
        id(b.family); if (!['none','lost'].includes(b.fault)) throw Error('bad fault');
        const grant=tx(() => {
          const s=one(this.sql,'SELECT * FROM sessions WHERE id=?',b.session);
          const t=one(this.sql,'SELECT * FROM targets WHERE session=? AND family=?',b.session,b.family);
          if (!s || !t) throw Error('unregistered');
          // Represents a message captured while active, even if delivery occurs after revoke.
          return {action:'grant',session:b.session,gen:0,expiry:s.expiry};
        });
        const result=await send(this.family(b.family),grant);
        return response(b.fault === 'lost' ? {unknown:true} : result);
      }
      if (b.action === 'begin') {
        tx(() => {
          const s=one(this.sql,'SELECT * FROM sessions WHERE id=?',b.session); if (!s) throw Error('unknown');
          if (s.state === 'active') this.sql.exec("UPDATE sessions SET state='revoking',generation=generation+1 WHERE id=?",b.session);
          this.sql.exec("UPDATE sessions SET state='complete' WHERE id=? AND state='revoking' AND NOT EXISTS (SELECT 1 FROM targets WHERE session=? AND ack=0)",b.session,b.session);
        }); return response({ok:true});
      }
      if (b.action === 'retry') {
        // Faults affect transport only; there is never an await within transactionSync.
        id(b.family); if (!['none','unreachable','lost'].includes(b.fault)) throw Error('bad fault');
        const snapshot=tx(() => {
          const s=one(this.sql,'SELECT * FROM sessions WHERE id=?',b.session);
          const t=one(this.sql,'SELECT * FROM targets WHERE session=? AND family=?',b.session,b.family);
          if (!s || !t || s.state === 'active') throw Error('denied');
          return {gen:s.generation, ack:t.ack};
        });
        if (!snapshot.ack) {
          if (b.fault === 'unreachable') return response({pending:true});
          const result=await send(this.family(b.family),{action:'fence',session:b.session,gen:snapshot.gen});
          if (result.ack !== snapshot.gen) throw Error('bad ack');
          if (b.fault === 'lost') return response({pending:true});
          tx(() => {
            this.sql.exec('UPDATE targets SET ack=1 WHERE session=? AND family=?',b.session,b.family);
            this.sql.exec("UPDATE sessions SET state='complete' WHERE id=? AND state='revoking' AND NOT EXISTS (SELECT 1 FROM targets WHERE session=? AND ack=0)",b.session,b.session);
          });
        }
        return response({pending:false});
      }
      if (b.action === 'status') {
        const s=one(this.sql,'SELECT * FROM sessions WHERE id=?',b.session); if (!s) throw Error('unknown');
        const targets=[...this.sql.exec('SELECT family,ack FROM targets WHERE session=? ORDER BY family',b.session)]; return response({state:s.state,generation:s.generation,targets});
      }
      throw Error('unknown');
    } catch (_) { return Response.json({error:'invalid'}, {status:400}); }
  }
}

export default { async fetch(req, env) {
  try {
    const b=await req.json(); if (b.to === 'coordinator') return await env.COORD.get(env.COORD.idFromName('singleton')).fetch('http://internal/', {method:'POST',body:JSON.stringify(b.message)});
    if (b.to === 'family') { id(b.family); return await env.FAMILY.get(env.FAMILY.idFromName(b.family)).fetch('http://internal/', {method:'POST',body:JSON.stringify(b.message)}); }
    throw Error('invalid');
  } catch (_) { return Response.json({error:'invalid'}, {status:400}); }
}};
