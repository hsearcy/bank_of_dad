import { test } from 'node:test';
import assert from 'node:assert/strict';
import { generateKeyPair } from 'jose';
import { FakeIssuer, LocalVerifier, CALLBACK, ISSUER, CLIENT } from './spike.mjs';
import * as oidc from 'openid-client';
const A = 'a'.repeat(40), B = 'b'.repeat(40);
async function setup(options) { const issuer = new FakeIssuer(); await issuer.rotate(); return { issuer, rp: new LocalVerifier(issuer, options) }; }
async function flow(rp, issuer, browser = A) { return issuer.authorize(await rp.begin(browser)); }
const fail = { ok: false, error: 'authentication_failed' };

test('positive identity is only exact issuer and subject; JSON round trip and no email link', async () => {
  const { issuer, rp } = await setup(); issuer.overrides.claims = { email: 'fake@example.invalid', email_verified: true };
  const result = await rp.callback(A, await flow(rp, issuer));
  assert.deepEqual(JSON.parse(JSON.stringify(result)), { ok: true, identity: { issuer: ISSUER, subject: 'fake-parent-1' } });
  issuer.overrides.claims.sub = 'another-fake-subject';
  const other = await rp.callback(A, await flow(rp, issuer));
  assert.deepEqual(other, { ok: true, identity: { issuer: ISSUER, subject: 'another-fake-subject' } });
});

test('wrong issuer, audience, nonce, subject, expiry and algorithm reject generically', async () => {
  for (const [claims, verified] of [[{ iss: 'http://127.0.0.1:1' }, false], [{ aud: 'wrong' }, false], [{ aud: [CLIENT, 'other'], azp: CLIENT }, true], [{ nonce: 'wrong' }, false], [{ sub: '' }, true], [{ sub: 5 }, false], [{ exp: 1 }, false]]) {
    const events = []; const { issuer, rp } = await setup({ observe: event => events.push(event) }); issuer.overrides.claims = claims;
    assert.deepEqual(await rp.callback(A, await flow(rp, issuer)), fail, JSON.stringify(claims));
    assert.deepEqual(issuer.calls, { token: 1, jwks: verified ? 1 : 0 });
    assert.deepEqual(events, verified ? ['verified_token'] : []);
  }
  const { issuer, rp } = await setup();
  const other = await generateKeyPair('RS256'); issuer.overrides.signingKey = other.privateKey;
  assert.deepEqual(await rp.callback(A, await flow(rp, issuer)), fail, 'wrong signature');
  assert.deepEqual(issuer.calls, { token: 1, jwks: 1 });
  const x = await setup(); x.issuer.overrides.header = { alg: 'PS256' };
  x.issuer.overrides.signingKey = (await generateKeyPair('PS256')).privateKey;
  assert.deepEqual(await x.rp.callback(A, await flow(x.rp, x.issuer)), fail, 'wrong algorithm');
  assert.deepEqual(x.issuer.calls, { token: 1, jwks: 0 });
});

test('malformed and unknown JWKS rejected', async () => {
  for (const jwks of [[{ kty: 'RSA', kid: 'wrong', n: '!', e: 'AQAB' }], [{ kty: 'RSA', kid: 'ignored', n: '', e: '' }], []]) {
    const { issuer, rp } = await setup(); issuer.overrides.jwks = jwks;
    assert.deepEqual(await rp.callback(A, await flow(rp, issuer)), fail);
    assert.deepEqual(issuer.calls, { token: 1, jwks: 1 });
  }
  const { issuer, rp } = await setup();
  issuer.overrides.jwks = [{ ...issuer.keys[0].jwk, n: '!' }];
  assert.deepEqual(await rp.callback(A, await flow(rp, issuer)), fail);
  assert.deepEqual(issuer.calls, { token: 1, jwks: 1 });
});

test('state isolation, expiry, replay and concurrent callback consumption', async () => {
  let now = Date.now(); const { issuer, rp } = await setup({ now: () => now, ttl: 20 });
  const url = await flow(rp, issuer);
  assert.deepEqual(await rp.callback(B, url), fail);
  const results = await Promise.all([rp.callback(A, url), rp.callback(A, url)]);
  assert.equal(results.filter(x => x.ok).length, 1);
  assert.deepEqual(await rp.callback(A, url), fail);
  const expired = await flow(rp, issuer); now += 20;
  assert.deepEqual(await rp.callback(A, expired), fail);
  now -= 20; // a restored expired transaction must not become usable after clock rollback
  assert.deepEqual(await rp.callback(A, expired), fail);
  const good = await flow(rp, issuer), bad = new URL(good); bad.searchParams.set('state', 'unknown');
  assert.deepEqual(await rp.callback(A, bad), fail);
  assert.equal((await rp.callback(A, good)).ok, true);
});

test('code substitution and PKCE mismatch fail, independent browsers still work', async () => {
  const { issuer, rp } = await setup();
  const a = await flow(rp, issuer, A), b = await flow(rp, issuer, B);
  const swapped = new URL(a); swapped.searchParams.set('code', b.searchParams.get('code'));
  assert.deepEqual(await rp.callback(A, swapped), fail);
  assert.deepEqual(await rp.callback(B, b), fail); // substituted code was burned
  const c = await flow(rp, issuer, A), state = c.searchParams.get('state');
  rp.pending.get(state).verifier = oidc.randomPKCECodeVerifier();
  assert.deepEqual(await rp.callback(A, c), fail);
  const d = await flow(rp, issuer, B); assert.equal((await rp.callback(B, d)).ok, true);
});

test('literal callback allowlist, ambiguous callback parameters, generic errors', async () => {
  const { issuer, rp } = await setup();
  for (const callback of [CALLBACK + '/', CALLBACK + '#x', CALLBACK.replace('127.0.0.1', 'localhost'), CALLBACK.replace('http:', 'https:'), 'http://127.0.0.1:49153@evil.invalid/callback']) assert.throws(() => new LocalVerifier(issuer, { callback }));
  const u = await flow(rp, issuer); const bad = new URL(u); bad.searchParams.append('state', 'duplicate');
  assert.deepEqual(await rp.callback(A, bad), fail);
  const other = new URL(u); other.hostname = 'localhost'; assert.deepEqual(await rp.callback(A, other), fail);
  assert.equal((await rp.callback(A, u)).ok, true);
});

test('malformed callbacks reject without consuming legitimate state', async () => {
  for (const mutate of [
    url => url.searchParams.append('code', 'duplicate'),
    url => url.searchParams.set('error', 'synthetic-sensitive-error'),
    url => { url.hash = '#synthetic-sensitive-fragment'; },
    url => url.searchParams.append('state', 'duplicate'),
  ]) {
    const { issuer, rp } = await setup();
    const valid = await flow(rp, issuer), bad = new URL(valid);
    mutate(bad);
    assert.deepEqual(await rp.callback(A, bad), fail);
    assert.deepEqual(issuer.calls, { token: 0, jwks: 0 });
    assert.deepEqual(await rp.callback(A, valid), { ok: true, identity: { issuer: ISSUER, subject: 'fake-parent-1' } });
    assert.deepEqual(await rp.callback(A, valid), fail);
  }
});

test('injected policy clock governs transaction expiry and iat, library expiry uses system clock', async t => {
  t.mock.timers.enable({ apis: ['Date'], now: 1700000000000 });
  let clock = Date.now() + 60000;
  const events = []; const { issuer, rp } = await setup({ now: () => clock, ttl: 20000, observe: e => events.push(e) });
  const globalSeconds = Date.now() / 1000;
  // Library accepts these synthetic tokens; our policy rejects the future iat relative to its injected clock.
  issuer.overrides.claims = { iat: globalSeconds + 91 };
  assert.deepEqual(await rp.callback(A, await flow(rp, issuer)), fail);
  assert.deepEqual(events.splice(0), ['verified_token', 'reject_iat']);
  issuer.overrides.claims = { iat: globalSeconds + 90 };
  assert.equal((await rp.callback(A, await flow(rp, issuer))).ok, true);
  assert.deepEqual(events.splice(0), ['verified_token']);
  issuer.overrides.claims = { iat: globalSeconds - 91 };
  assert.deepEqual(await rp.callback(A, await flow(rp, issuer)), fail);
  assert.deepEqual(events.splice(0), ['verified_token', 'reject_iat']);
  issuer.overrides.claims = { iat: globalSeconds - 90 };
  assert.equal((await rp.callback(A, await flow(rp, issuer))).ok, true);
  assert.deepEqual(events.splice(0), ['verified_token']);
  const expired = await flow(rp, issuer); clock += 20000;
  assert.deepEqual(await rp.callback(A, expired), fail);
  clock -= 20000;
  assert.deepEqual(await rp.callback(A, expired), fail);
  // The pinned library has a 30-second exp tolerance and uses Date.now, not the policy clock.
  clock -= 120000;
  issuer.overrides.claims = { iat: globalSeconds - 60, exp: globalSeconds - 31 };
  assert.deepEqual(await rp.callback(A, await flow(rp, issuer)), fail);
  assert.deepEqual(events, []);
});

test('extra front-channel tokens are not used as identity', async () => {
  const { issuer, rp } = await setup();
  const access = await flow(rp, issuer); access.searchParams.set('access_token', 'untrusted');
  assert.deepEqual(await rp.callback(A, access), { ok: true, identity: { issuer: ISSUER, subject: 'fake-parent-1' } });
  assert.deepEqual(issuer.calls, { token: 1, jwks: 1 });
  const id = await flow(rp, issuer); const plain = new URL(id); id.searchParams.set('id_token', 'untrusted');
  assert.deepEqual(await rp.callback(A, id), fail);
  assert.deepEqual(await rp.callback(A, plain), fail);
  assert.deepEqual(issuer.calls, { token: 1, jwks: 1 });
});

test('key rotation observed by fresh verifier, retired key rejected', async () => {
  const { issuer, rp } = await setup(); const old = issuer.keys[0];
  assert.equal((await rp.callback(A, await flow(rp, issuer))).ok, true);
  await issuer.rotate();
  const fresh = new LocalVerifier(issuer);
  assert.equal((await fresh.callback(A, await flow(fresh, issuer))).ok, true);
  const newer = new LocalVerifier(issuer); issuer.overrides.signingKey = old.privateKey;
  assert.deepEqual(await newer.callback(A, await flow(newer, issuer)), fail);
});


test('azp and issuance time policy after verified token exchange', async t => {
  t.mock.timers.enable({ apis: ['Date'], now: 1700000000000 });
  const time = Date.now() / 1000;
  const events = []; const { issuer, rp } = await setup({ observe: event => events.push(event) });
  for (const [claims, accepted] of [
    [{ azp: 'wrong' }, false], [{ azp: null }, false], [{ azp: CLIENT }, true],
    [{ iat: time - 151 }, false], [{ iat: time - 150 }, true],
    [{ iat: time + 30 }, true], [{ iat: time + 31 }, false],
    [{ iat: 1 }, false], [{ iat: 'not-a-number' }, false],
  ]) {
    issuer.overrides.claims = claims;
    const before = { ...issuer.calls }, eventCount = events.length;
    const result = await rp.callback(A, await flow(rp, issuer));
    assert.equal(result.ok, accepted, JSON.stringify(claims));
    assert.equal(issuer.calls.token, before.token + 1);
    if (typeof claims.iat !== 'string') {
      const expected = accepted ? ['verified_token'] : ['verified_token', 'azp' in claims ? 'reject_azp' : 'reject_iat'];
      assert.deepEqual(events.slice(eventCount), expected, 'reached intended policy after library validation');
    } else assert.deepEqual(events.slice(eventCount), [], 'library rejects invalid iat type');
    if (!accepted) assert.deepEqual(result, fail);
  }
});

test('long-lived JWKS cache refresh and retirement windows', async t => {
  t.mock.timers.enable({ apis: ['Date'], now: 1700000000000 });
  const { issuer, rp } = await setup();
  const old = issuer.keys[0];
  assert.equal((await rp.callback(A, await flow(rp, issuer))).ok, true);
  assert.deepEqual(issuer.calls, { token: 1, jwks: 1 });
  await issuer.rotate(); const current = issuer.keys[0];
  // A valid retired-key token is still accepted while its public key is cached.
  issuer.overrides.signingKey = old.privateKey; issuer.overrides.header = { kid: old.kid };
  assert.equal((await rp.callback(A, await flow(rp, issuer))).ok, true);
  assert.equal(issuer.calls.jwks, 1);
  // A new kid does not cause refresh during the initial cooldown.
  delete issuer.overrides.signingKey; delete issuer.overrides.header;
  t.mock.timers.tick(59000);
  assert.deepEqual(await rp.callback(A, await flow(rp, issuer)), fail);
  assert.equal(issuer.calls.jwks, 1);
  t.mock.timers.tick(1000);
  assert.equal((await rp.callback(A, await flow(rp, issuer))).ok, true);
  assert.equal(issuer.calls.jwks, 2);
  issuer.overrides.signingKey = old.privateKey; issuer.overrides.header = { kid: old.kid };
  assert.deepEqual(await rp.callback(A, await flow(rp, issuer)), fail);
  assert.equal(issuer.calls.jwks, 2);
  assert.notEqual(current.kid, old.kid);
});

test('cached retired key survives until cache age limit without a new-kid refresh', async t => {
  t.mock.timers.enable({ apis: ['Date'], now: 1700000000000 });
  const { issuer, rp } = await setup(); const old = issuer.keys[0];
  assert.equal((await rp.callback(A, await flow(rp, issuer))).ok, true);
  await issuer.rotate();
  issuer.overrides.signingKey = old.privateKey; issuer.overrides.header = { kid: old.kid };
  t.mock.timers.tick(299000);
  assert.equal((await rp.callback(A, await flow(rp, issuer))).ok, true);
  assert.equal(issuer.calls.jwks, 1);
  t.mock.timers.tick(1000);
  assert.deepEqual(await rp.callback(A, await flow(rp, issuer)), fail);
  assert.equal(issuer.calls.jwks, 2);
});
