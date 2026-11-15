"""
Bank of Dad — tunable rules.

Everything kid-facing about how money grows lives here so Dad can tweak it
without touching the app logic. All money is stored as integer CENTS everywhere
in the codebase; only the UI formats it as dollars.
"""

# ---------------------------------------------------------------------------
# Allowance: a few dollars, every other week. Accrues until a kid taps
# "Get rewards!" to collect the coins into their wallet.
# ---------------------------------------------------------------------------
ALLOWANCE_CENTS = 300          # $3.00 per period
ALLOWANCE_PERIOD_DAYS = 14     # every other week

# ---------------------------------------------------------------------------
# Savings account: deliberately high, kid-visible interest. Interest accrues
# continuously on the savings balance and is collected with "Get rewards!".
# Expressed as a weekly rate so growth is visible on a kid's timescale.
#
# Keep this BELOW what investing returns over the same period — the intended
# lesson is wallet (no growth) < savings (steady) < invest (biggest). At 5%/wk a
# 3-month savings balance grows ~1.9x, vs the 3-month lock's 3x below.
# ---------------------------------------------------------------------------
SAVINGS_WEEKLY_RATE = 0.04     # grows 4% every week

# ---------------------------------------------------------------------------
# "Invest in the Bank of Dad": lock coins for a fixed term for a big payout —
# the BEST vehicle, and locking longer pays off most (patience reward). Kids
# don't need the word "bond"; the UI calls this Investing. Amounts are tiny and
# this is educational, so the returns are deliberately exciting.
#
# Hierarchy check over each term (so longer locks always win, and both beat
# savings at 4%/wk):
#   1 month  +25%  (vs ~1 month savings 1.04^4.3 = 1.18x) -> 1.25x
#   3 months +100% (vs 3x one-month 1.25^3 = 1.95x, and savings 1.04^13 = 1.67x) -> 2.00x
# (internal keys/table are still named "bond" to avoid a data migration.)
# ---------------------------------------------------------------------------
BONDS = {
    "1mo": {"label": "1 Month",  "days": 30, "return_pct": 25,  "emoji": "🌱"},
    "3mo": {"label": "3 Months", "days": 90, "return_pct": 100, "emoji": "🚀"},
}

# Smallest amount (cents) a kid can put into savings or a bond at once.
MIN_MOVE_CENTS = 1             # $0.01 — any positive amount can move

# Starting pocket money for a freshly created kid (cents).
STARTING_WALLET_CENTS = 500    # $5.00 to get the loop going
