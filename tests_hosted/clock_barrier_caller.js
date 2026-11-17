// UNSAFE loopback-only synthetic fixture. Never deploy.
import { DurableObject } from 'cloudflare:workers';
export class Control extends DurableObject {
  constructor(ctx, env) { super(ctx, env); this.entries = []; this.starts = []; this.waiter = null; this.held = false; }
  async entered(n) { this.entries.push(n); }
  async started(n) { this.starts.push(n); }
  async hold() { this.held = true; await new Promise(resolve => { this.waiter = resolve; }); }
  async state() { return {entries: this.entries, starts: this.starts, held: this.held}; }
  async release() { if (!this.waiter) throw Error('not held'); this.waiter(); this.waiter = null; this.held = false; }
}
export default {
 async fetch(request, env) {
   if (request.method !== 'POST') return new Response('fixture', {status: 404});
   const b = await request.json();
   if (b.special === 'nan') b.now = NaN;
   if (b.special === 'infinity') b.now = Infinity;
   const control = env.CONTROL.get(env.CONTROL.idFromName('gate'));
   const target = env.BANK.get(env.BANK.idFromName(b.family || 'a'));
   try {
     let result;
     if (b.method === 'run' || b.method === 'python') {
       const promise = b.method === 'run' ? target.run(b.now, !!b.hold, b.fail || '', !!b.ungated)
         : env.RELAY.get(env.RELAY.idFromName(b.relay || 'relay')).run(b.family || 'a', b.now, !!b.hold, b.fail || '');
       await control.started(b.now);
       result = await promise;
     } else if (b.method === 'snapshot') result = JSON.parse(await target.snapshot());
     else if (b.method === 'state') result = await control.state();
     else if (b.method === 'release') { await control.release(); result = null; }
     return Response.json({result});
   } catch (error) { return Response.json({error: String(error)}, {status: 500}); }
 }
};
