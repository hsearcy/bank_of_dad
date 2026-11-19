# Family polish integration

This public snapshot combines:

- About and Privacy dialogs plus compact screen wake/tour controls.
- Dad controls to rename or permanently remove one confirmed child.
- Configurable savings and investment periods, preserving earned interest and
  existing investment contracts.
- Additive schema upgrades for retained hosted sessions and rewind-safe savings
  carry consumption.

Production configuration remains a portable example. Account-specific deployment
settings, credentials, databases and internal development history are not included.

## Reproducible local checks

Use synthetic, disposable state only. Set HOME, XDG_CONFIG_HOME and XDG_CACHE_HOME
in a scratch directory; set PLAYWRIGHT_BROWSERS_PATH explicitly if reusing installed
Chromium with an isolated HOME. Run Worker tests serially in each checkout: changing
or rebuilding watched sources during acceptance tears down active sockets.

After installing the test dependencies and Playwright Chromium, run from the root:

```sh
BANK_OF_DAD_BROWSER_TESTS=1 python -m pytest -q tests
```

For the optional Flask-hosted target, install `requirements-hosted.txt` and pytest
in a separate test environment:

```sh
python -m pytest -q tests_hosted
```

For the Cloudflare target, from `cloudflare/`, after `uv sync --frozen` and `npm ci`:

```sh
uv run pytest -q \
  test_footer.py test_kid_management.py test_rate_periods_runtime.py \
  test_runtime.py test_public.py test_owner.py test_owner_selection.py \
  test_family_polish.py test_harness.py
```

`test_family_polish.py` combines guest child rename/add/removal, accrued-interest
preservation when periods change, unchanged stored investment contracts, account
claiming with original trial dates, invalidation of the old guest capability,
owner grant, exact full-database restart comparison, returning-device sign-in,
cross-family unchanged snapshots, and 390px/1280px Chromium UI acceptance.
Identity uses a signed loopback fixture, not real Google. Public HTTPS tests use
local TLS with an explicit public Host. These checks neither deploy nor establish
real Google/Stripe acceptance or physical display wake behavior.

See [footer and screen checks](footer-screen-verification.md),
[kid management](kid-management.md), and [rate periods](rate-periods.md) for
feature-specific behavior and coverage.
