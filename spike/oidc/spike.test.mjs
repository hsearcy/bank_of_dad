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
  for (const claims of [{ iss: 'http://127.0.0.1:1' }, { aud: 'wrong' }, { aud: [CLIENT, 'other'], azp: CLIENT }, { nonce: 'wrong' }, { sub: '' }, { sub: 5 }, { exp: 1 }]) {
    const { issuer, rp } = await setup(); issuer.overrides.claims = claims;
    assert.deepEqual(await rp.callback(A, await flow(rp, issuer)), fail, JSON.stringify(claims));
  }
  const { issuer, rp } = await setup();
  const other = await generateKeyPair('RS256'); issuer.overrides.signingKey = other.privateKey;
  assert.deepEqual(await rp.callback(A, await flow(rp, issuer)), fail, 'wrong signature');
  const x = await setup(); x.issuer.overrides.header = { alg: 'PS256' };
  x.issuer.overrides.signingKey = (await generateKeyPair('PS256')).privateKey;
  assert.deepEqual(await x.rp.callback(A, await flow(x.rp, x.issuer)), fail, 'wrong algorithm');
});

test('malformed and unknown JWKS rejected', async () => {
  for (const jwks of [[{ kty: 'RSA', kid: 'wrong', n: '!', e: 'AQAB' }], [{ kty: 'RSA', kid: 'ignored', n: '', e: '' }], []]) {
    const { issuer, rp } = await setup(); issuer.overrides.jwks = jwks;
    assert.deepEqual(await rp.callback(A, await flow(rp, issuer)), fail);
  }
  const { issuer, rp } = await setup();
  issuer.overrides.jwks = [{ ...issuer.keys[0].jwk, n: '!' }];
  assert.deepEqual(await rp.callback(A, await flow(rp, issuer)), fail);
});

test('state isolation, expiry, replay and concurrent callback consumption', async () => {
  let now = 1000; const { issuer, rp } = await setup({ now: () => now, ttl: 20 });
  const url = await flow(rp, issuer);
  assert.deepEqual(await rp.callback(B, url), fail);
  const results = await Promise.all([rp.callback(A, url), rp.callback(A, url)]);
  assert.equal(results.filter(x => x.ok).length, 1);
  assert.deepEqual(await rp.callback(A, url), fail);
  const expired = await flow(rp, issuer); now += 20;
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

test('key rotation observed by fresh verifier, retired key rejected', async () => {
  const { issuer, rp } = await setup(); const old = issuer.keys[0];
  assert.equal((await rp.callback(A, await flow(rp, issuer))).ok, true);
  await issuer.rotate();
  const fresh = new LocalVerifier(issuer);
  assert.equal((await fresh.callback(A, await flow(fresh, issuer))).ok, true);
  const newer = new LocalVerifier(issuer); issuer.overrides.signingKey = old.privateKey;
  assert.deepEqual(await newer.callback(A, await flow(newer, issuer)), fail);
});
