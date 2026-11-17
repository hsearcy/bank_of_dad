# Synthetic OIDC verification spike — never deploy

This isolated in-memory test harness explores an authorization-code + S256 PKCE
relying party. It is not an HTTP app, a gateway, a browser login, or a provider.
Every outbound `fetch` is intercepted by a fake issuer and never reaches the
network. The only URL literals are synthetic `http://127.0.0.1` endpoints; HTTP
is permitted solely for this local fake. No server listens on those ports. The
callback is an exact literal allowlist, not configurable for public use.

The random test `browser` value is a stand-in for a browser-bound secret: it is
not a real cookie, and the caller is trusted to hold it privately. State and PKCE
verifiers live only in one process. Callback URL and parameter-shape checks happen
first: malformed URLs, disallowed callback locations, duplicate/missing state or
code, and `error` parameters do not consume state. A missing/unknown state or
wrong-browser attempt also does not consume it. Once a matching browser and state
are found, state is consumed synchronously, before expiry checking and async token
exchange, even if a later step fails. Restart loses pending flows. State expiry is
checked at callback; this harness has no cleanup, rate limit, or distributed atomic
store. A real implementation needs those, plus a real browser-bound secure cookie
and expiry/rotation/logout/CSRF rules; this spike issues **no session cookie**.

`openid-client` does protocol, claim, nonce and state checks and sends the PKCE
verifier; the fake issuer checks that verifier against the S256 challenge.
Explicit `enableNonRepudiationChecks` makes the client verify ID-token signatures against
the issuer JWKS. Algorithm is pinned to RS256, and issuer and single audience
are exact (an audience array is not accepted); when `azp` is present it must
equal the client ID. After library validation, the synthetic issuance-time policy
accepts a finite numeric `iat` from 150 seconds before through 30 seconds after
the current whole Unix second, inclusive: a 120-second freshness window plus 30
seconds of assumed clock skew in either direction. This is a conservative test
choice, not a production policy decision. Transaction expiry and this `iat` policy
share the verifier's `now` clock, which defaults to `Date.now`. An injected clock
is test-only and does not alter the library's clock: the pinned library validates
`exp` using global `Date.now` with its default 30-second tolerance. The fake issuer
also uses global time. Skewed-clock tests explicitly exercise this split; do not
interpret injection as controlling all token or JWKS validation. The fake uses `jose` to generate ephemeral keys and sign fake tokens;
there is no hand-rolled JWT cryptography. Results expose only issuer + subject,
not email. No lookup, auto-linking or account creation exists. Errors are generic;
no application logging is present. Test runner output must not dump URLs/tokens.

Extra callback parameters are not generally rejected: in the tested code flow a
front-channel `access_token` is ignored and identity comes from the token endpoint;
a front-channel `id_token` is rejected by the library after state is consumed.
This is not a general front-channel parameter security policy.

In deterministic fake-clock tests with the pinned library, a long-lived verifier
caches JWKS. A new `kid` fails without refresh while the cached set is less than
60 seconds old; at age 60 a new `kid` triggers refresh. A retired key already in
the cache can still validate a correctly signed token until a refresh removes it:
without an earlier refresh, the test accepts it at age 299 seconds and rejects it
after refresh at age 300 seconds. The library compares whole Unix seconds for the
fetch and current time, not exact elapsed duration. Depending on the fractional
second of the fetch, a threshold can occur almost one second earlier than the
nominal elapsed duration; the tests start on exact second boundaries. Ages are
since JWKS fetch, not since retirement. An unknown key after the cooldown can trigger an earlier refresh.
This demonstrates a stale-key acceptance and new-key availability window, not
instant revocation, real-provider rotation, or a production mitigation.

There is no production configuration, trusted
discovery, TLS, session, membership or family authorization, global revocation,
child access, app integration, persistent identity mapping or reliability claim.
No provider has been chosen or provisioned. Fake issuer behavior is not an OIDC
conformance or security audit. Do not connect real users, credentials or data.

## Reproduce

Node.js 20+ (WebCrypto and Fetch globals) is required by the upstream maintained
`openid-client` v6 library; the tests use Node's mock Date timers and have been
verified here with Node 26.8.1. ESM is used. Source:
[upstream requirements](https://github.com/panva/openid-client#supported-runtimes)
and [package](https://www.npmjs.com/package/openid-client). `openid-client` 6.9.0
and `jose` 6.2.15 are pinned directly, transitive versions and integrity in the
lockfile. Package installation needs registry access; tests do not.

```sh
cd spike/oidc
npm ci --ignore-scripts
node --test spike.test.mjs
```
