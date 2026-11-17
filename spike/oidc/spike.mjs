// LOCAL TEST HARNESS ONLY: no server, real provider, app account, or session.
import * as oidc from 'openid-client';
import { generateKeyPair, exportJWK, SignJWT } from 'jose';

const ISSUER = 'http://127.0.0.1:49152';
const CALLBACK = 'http://127.0.0.1:49153/callback';
const CLIENT = 'synthetic-client';
const json = (body, status = 200) => Response.json(body, { status, headers: { 'cache-control': 'no-store' } });

export class FakeIssuer {
  constructor() { this.codes = new Map(); this.keys = []; this.overrides = {}; this.calls = { token: 0, jwks: 0 }; }
  async rotate() {
    const pair = await generateKeyPair('RS256', { extractable: true });
    const kid = oidc.randomState();
    const jwk = { ...await exportJWK(pair.publicKey), kid, alg: 'RS256', use: 'sig' };
    this.keys = [{ ...pair, jwk, kid }];
  }
  async authorize(url) {
    const p = new URL(url).searchParams;
    if (p.get('client_id') !== CLIENT || p.get('redirect_uri') !== CALLBACK || p.get('code_challenge_method') !== 'S256') throw Error('invalid synthetic request');
    const code = oidc.randomState();
    this.codes.set(code, { challenge: p.get('code_challenge'), nonce: p.get('nonce'), redirect: p.get('redirect_uri') });
    const result = new URL(CALLBACK);
    result.searchParams.set('code', code); result.searchParams.set('state', p.get('state'));
    return result;
  }
  // This fetch never calls the network, even for unknown URLs.
  fetch = async (input, options = {}) => {
    const url = String(input);
    if (url === `${ISSUER}/jwks` && options.method === 'GET') { this.calls.jwks++; return json({ keys: this.overrides.jwks ?? this.keys.map(k => k.jwk) }); }
    if (url !== `${ISSUER}/token` || options.method !== 'POST') return json({ error: 'invalid_request' }, 400);
    this.calls.token++;
    const p = new URLSearchParams(options.body);
    const code = this.codes.get(p.get('code'));
    this.codes.delete(p.get('code')); // burn even on failed verification
    if (!code || p.get('client_id') !== CLIENT || p.get('redirect_uri') !== code.redirect ||
        await oidc.calculatePKCECodeChallenge(p.get('code_verifier') || '') !== code.challenge) return json({ error: 'invalid_grant' }, 400);
    const key = this.keys[0];
    const now = Math.floor(Date.now() / 1000);
    const claims = { iss: ISSUER, aud: CLIENT, sub: 'fake-parent-1', iat: now, exp: now + 120, nonce: code.nonce, ...this.overrides.claims };
    const header = { alg: 'RS256', kid: key.kid, ...this.overrides.header };
    const id_token = await new SignJWT(claims).setProtectedHeader(header).sign(this.overrides.signingKey ?? key.privateKey);
    return json({ access_token: 'synthetic-access-token', token_type: 'Bearer', id_token });
  };
}

export class LocalVerifier {
  constructor(issuer, { callback = CALLBACK, now = () => Date.now(), ttl = 120000, observe = () => {} } = {}) {
    // Exact literal comparison rules out userinfo, alternate hosts, ports, fragments and path tricks.
    if (callback !== CALLBACK) throw Error('callback not allowlisted');
    this.issuer = issuer; this.now = now; this.ttl = ttl; this.observe = observe; this.pending = new Map();
    this.config = new oidc.Configuration({ issuer: ISSUER, authorization_endpoint: `${ISSUER}/authorize`, token_endpoint: `${ISSUER}/token`, jwks_uri: `${ISSUER}/jwks`, response_types_supported: ['code'], id_token_signing_alg_values_supported: ['RS256'] }, CLIENT, { id_token_signed_response_alg: 'RS256' }, oidc.None());
    oidc.allowInsecureRequests(this.config); // synthetic loopback URLs only, fetch is fully in-memory
    this.config[oidc.customFetch] = issuer.fetch;
    oidc.enableNonRepudiationChecks(this.config); // mandatory: code-flow TLS alone does not verify ID token signatures
  }
  async begin(browser) {
    if (typeof browser !== 'string' || browser.length < 32) throw Error('invalid test browser');
    const state = oidc.randomState(), nonce = oidc.randomNonce(), verifier = oidc.randomPKCECodeVerifier();
    const challenge = await oidc.calculatePKCECodeChallenge(verifier);
    const url = oidc.buildAuthorizationUrl(this.config, { redirect_uri: CALLBACK, scope: 'openid', response_type: 'code', state, nonce, code_challenge: challenge, code_challenge_method: 'S256' });
    this.pending.set(state, { browser, nonce, verifier, expires: this.now() + this.ttl });
    return url;
  }
  async callback(browser, url) {
    try {
      const parsed = new URL(url);
      if (`${parsed.origin}${parsed.pathname}` !== CALLBACK || parsed.hash || parsed.username || parsed.password) throw Error();
      if (parsed.searchParams.getAll('state').length !== 1 || parsed.searchParams.getAll('code').length !== 1 || parsed.searchParams.has('error')) throw Error();
      const state = parsed.searchParams.get('state');
      const tx = this.pending.get(state);
      if (!tx || tx.browser !== browser) throw Error();
      this.pending.delete(state); // synchronously consume before any async work, including failures
      if (this.now() >= tx.expires) throw Error();
      const tokens = await oidc.authorizationCodeGrant(this.config, parsed, { expectedState: state, expectedNonce: tx.nonce, pkceCodeVerifier: tx.verifier, idTokenExpected: true });
      const claims = tokens.claims();
      this.observe('verified_token'); // test-only instrumentation, never returned to the browser
      // Strict single audience, issuer and subject; email is deliberately never considered.
      if (claims.iss !== ISSUER || claims.aud !== CLIENT || typeof claims.sub !== 'string' || !claims.sub) throw Error();
      if (Object.hasOwn(claims, 'azp') && claims.azp !== CLIENT) { this.observe('reject_azp'); throw Error(); }
      // Synthetic policy: at most 120 seconds old plus 30 seconds skew, or 30 seconds ahead.
      const now = Math.floor(Date.now() / 1000);
      if (typeof claims.iat !== 'number' || !Number.isFinite(claims.iat) || claims.iat < now - 150 || claims.iat > now + 30) {
        this.observe('reject_iat'); throw Error();
      }
      return { ok: true, identity: { issuer: claims.iss, subject: claims.sub } };
    } catch { return { ok: false, error: 'authentication_failed' }; }
  }
}
export { ISSUER, CALLBACK, CLIENT };
