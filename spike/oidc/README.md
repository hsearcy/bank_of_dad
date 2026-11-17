# Synthetic OIDC verification spike — never deploy

This isolated in-memory test harness explores an authorization-code + S256 PKCE
relying party. It is not an HTTP app, a gateway, a browser login, or a provider.
Every outbound `fetch` is intercepted by a fake issuer and never reaches the
network. The only URL literals are synthetic `http://127.0.0.1` endpoints; HTTP
is permitted solely for this local fake. No server listens on those ports. The
callback is an exact literal allowlist, not configurable for public use.

The random test `browser` value is a stand-in for a browser-bound secret: it is
not a real cookie, and the caller is trusted to hold it privately. State and PKCE
verifiers live only in one process; a successful or failed same-browser callback
consumes state synchronously before async token exchange. Cross-browser attempts do
not consume another browser's state. Restart loses pending flows. State expiry is
checked at callback; this harness has no cleanup, rate limit, or distributed atomic
store. A real implementation needs those, plus a real browser-bound secure cookie
and expiry/rotation/logout/CSRF rules; this spike issues **no session cookie**.

`openid-client` does protocol, claim, nonce and state checks and sends the PKCE
verifier; the fake issuer checks that verifier against the S256 challenge.
Explicit `enableNonRepudiationChecks` makes the client verify ID-token signatures against
the issuer JWKS. Algorithm is pinned to RS256, and issuer and single audience
are exact. The fake uses `jose` to generate ephemeral keys and sign fake tokens;
there is no hand-rolled JWT cryptography. Results expose only issuer + subject,
not email. No lookup, auto-linking or account creation exists. Errors are generic;
no application logging is present. Test runner output must not dump URLs/tokens.

Rotation is tested with a fresh verifier and retired-key mismatch. The library
caches JWKS; immediate refresh with a long-lived verifier and provider-side key
retirement behavior are not proven. There is no production configuration, trusted
discovery, TLS, session, membership or family authorization, global revocation,
child access, app integration, persistent identity mapping or reliability claim.
No provider has been chosen or provisioned. Fake issuer behavior is not an OIDC
conformance or security audit. Do not connect real users, credentials or data.

## Reproduce

Node.js 20+ (WebCrypto and Fetch globals) is required by the upstream maintained
`openid-client` v6 library; tests here ran with Node 26.8.1. ESM is used. Source:
[upstream requirements](https://github.com/panva/openid-client#supported-runtimes)
and [package](https://www.npmjs.com/package/openid-client). `openid-client` 6.9.0
and `jose` 6.2.15 are pinned directly, transitive versions and integrity in the
lockfile. Package installation needs registry access; tests do not.

```sh
cd spike/oidc
npm ci --ignore-scripts
node --test spike.test.mjs
```
