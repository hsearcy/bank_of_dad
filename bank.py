"""
Bank of Dad — core money logic.

Pure-ish functions over a sqlite connection. Interest/allowance/bonds accrue
*lazily*: nothing runs in the background. We compute what's owed from
timestamps whenever the state is read, so the app works fine on a tablet that
sleeps for days and there's no cron to babysit.
"""

import hashlib
import sqlite3
import time
from functools import wraps

import config
import db


# ---------------------------------------------------------------------------
# Time. A settable offset lets Dad "fast-forward" to demo growth instantly.
# ---------------------------------------------------------------------------
def now_ts(conn):
    offset = float(db.get_setting(conn, "time_offset", "0") or 0)
    return time.time() + offset


def fast_forward(conn, days):
    offset = float(db.get_setting(conn, "time_offset", "0") or 0)
    db.set_setting(conn, "time_offset", offset + days * 86400)
    conn.commit()


# ---------------------------------------------------------------------------
# Lazy accrual — what is waiting to be collected, without mutating anything.
# ---------------------------------------------------------------------------
def _kid_allowance(kid):
    """Per-kid allowance amount + cadence, falling back to config defaults."""
    cents = kid["allowance_cents"] if kid["allowance_cents"] is not None else config.ALLOWANCE_CENTS
    days = kid["allowance_period_days"] if kid["allowance_period_days"] is not None else config.ALLOWANCE_PERIOD_DAYS
    return cents, max(1, days)


def pending_allowance(kid, now):
    if not bool(kid["allowance_enabled"]):
        return 0, 0
    cents, days = _kid_allowance(kid)
    period = days * 86400
    elapsed_periods = int((now - kid["allowance_anchor_ts"]) // period)
    owed_periods = max(0, elapsed_periods - kid["allowance_periods_paid"])
    return owed_periods * cents, owed_periods


# ---------------------------------------------------------------------------
# Rates — Dad-configurable overrides stored in settings, else config defaults.
# ---------------------------------------------------------------------------
def savings_weekly_rate(conn):
    v = db.get_setting(conn, "savings_weekly_pct")
    return (float(v) / 100.0) if v is not None else config.SAVINGS_WEEKLY_RATE


def bond_return_pct(conn, term_key):
    v = db.get_setting(conn, "return_pct_" + term_key)
    if v is not None:
        return float(v)
    return config.BONDS.get(term_key, {}).get("return_pct", 0)


def set_rates(conn, savings_weekly_pct=None, returns=None):
    """Dad sets savings %/week and per-term investment return %. `returns` is a
    dict like {"1mo": 25, "3mo": 100}."""
    if savings_weekly_pct is not None:
        db.set_setting(conn, "savings_weekly_pct", max(0.0, float(savings_weekly_pct)))
    for key, pct in (returns or {}).items():
        if key in config.BONDS:
            db.set_setting(conn, "return_pct_" + key, max(0.0, float(pct)))
    conn.commit()
    return True


def pending_interest(kid, now, weekly_rate):
    """Savings interest earned since the last collect — a reward, but collecting
    it adds it to the SAVINGS balance (not the wallet)."""
    weeks = max(0.0, (now - kid["savings_accrued_ts"]) / (7 * 86400))
    return int(round(kid["savings_cents"] * weekly_rate * weeks))


def uncollected_principal(conn, kid_id):
    """Sum of principal locked in investments not yet collected (matured or not).
    This stays counted as the kid's money the whole time, so nothing dips."""
    row = conn.execute(
        "SELECT COALESCE(SUM(principal_cents),0) p FROM bonds "
        "WHERE kid_id=? AND collected=0", (kid_id,)
    ).fetchone()
    return row["p"]


def matured_bonds(conn, kid_id, now):
    rows = conn.execute(
        "SELECT * FROM bonds WHERE kid_id=? AND collected=0 AND mature_ts<=?",
        (kid_id, now),
    ).fetchall()
    return rows


def active_bonds(conn, kid_id, now):
    return conn.execute(
        "SELECT * FROM bonds WHERE kid_id=? AND collected=0 AND mature_ts>? "
        "ORDER BY mature_ts",
        (kid_id, now),
    ).fetchall()


# ---------------------------------------------------------------------------
# Read model — everything the dashboard needs for one kid.
# ---------------------------------------------------------------------------
def kid_state(conn, kid, now):
    allow_cents, _ = pending_allowance(kid, now)
    interest_cents = pending_interest(kid, now, savings_weekly_rate(conn))
    matured = matured_bonds(conn, kid["id"], now)
    # A reward is NEW money you earned: allowance + savings interest + the GAIN on
    # matured locks. (Locked principal was always yours, so it isn't a "reward".)
    matured_gain = sum(b["payout_cents"] - b["principal_cents"] for b in matured)
    pending = allow_cents + interest_cents + matured_gain

    invest_principal = uncollected_principal(conn, kid["id"])
    # Hero number: money that's already yours (spendable + savings + locked
    # investments). Excludes uncollected rewards, so collecting makes it pop.
    owned = kid["wallet_cents"] + kid["savings_cents"] + invest_principal
    alw_cents, alw_days = _kid_allowance(kid)
    # When the next allowance period completes (epoch seconds). A paused
    # allowance deliberately has no countdown and accrues nothing.
    allowance_enabled = bool(kid["allowance_enabled"])
    if allowance_enabled:
        period = alw_days * 86400
        elapsed_periods = int((now - kid["allowance_anchor_ts"]) // period)
        next_allowance_ts = kid["allowance_anchor_ts"] + (elapsed_periods + 1) * period
    else:
        next_allowance_ts = None

    # Panel shows every uncollected lock; matured ones are "ready to collect".
    uncollected = conn.execute(
        "SELECT * FROM bonds WHERE kid_id=? AND collected=0 ORDER BY mature_ts",
        (kid["id"],),
    ).fetchall()

    history = conn.execute(
        "SELECT ts, networth_cents, wallet_cents, savings_cents, invest_cents "
        "FROM history WHERE kid_id=? ORDER BY ts",
        (kid["id"],),
    ).fetchall()

    return {
        "id": kid["id"],
        "name": kid["name"],
        "color": kid["color"],
        "emoji": kid["emoji"],
        "wallet_cents": kid["wallet_cents"],
        "savings_cents": kid["savings_cents"],
        "invest_cents": invest_principal,
        "owned_cents": owned,
        "pending_cents": pending,
        "pending_allowance_cents": allow_cents,
        "pending_interest_cents": interest_cents,
        "pending_bonds_cents": matured_gain,
        "allowance_enabled": allowance_enabled,
        "allowance_cents": alw_cents,
        "allowance_period_days": alw_days,
        "next_allowance_ts": next_allowance_ts,
        "bonds": [
            {
                "id": b["id"],
                "term": config.BONDS.get(b["term_key"], {}).get("label", b["term_key"]),
                "emoji": config.BONDS.get(b["term_key"], {}).get("emoji", "💰"),
                "principal_cents": b["principal_cents"],
                "payout_cents": b["payout_cents"],
                "buy_ts": b["buy_ts"],
                "mature_ts": b["mature_ts"],
                "days_left": max(0, int((b["mature_ts"] - now) / 86400 + 0.999)),
                "ready": b["mature_ts"] <= now,
            }
            for b in uncollected
        ],
        "history": [
            {
                "ts": h["ts"],
                "v": h["networth_cents"],
                # fall back to "all in wallet" for rows recorded before the
                # per-pool breakdown columns existed
                "wallet": h["wallet_cents"] if h["wallet_cents"] is not None
                else h["networth_cents"],
                "savings": h["savings_cents"] or 0,
                "invest": h["invest_cents"] or 0,
            }
            for h in history
        ],
    }


def _record_history(conn, kid_id, now):
    state_row = conn.execute("SELECT * FROM kids WHERE id=?", (kid_id,)).fetchone()
    wallet = state_row["wallet_cents"]
    savings = state_row["savings_cents"]
    invest = uncollected_principal(conn, kid_id)
    networth = wallet + savings + invest   # == the hero "owned" number
    conn.execute(
        "INSERT INTO history(kid_id, ts, networth_cents, wallet_cents, "
        "savings_cents, invest_cents) VALUES(?,?,?,?,?,?)",
        (kid_id, now, networth, wallet, savings, invest),
    )


def _tx(conn, kid_id, now, kind, amount, note="", bond_id=None):
    conn.execute(
        "INSERT INTO transactions(kid_id, ts, kind, amount_cents, note, bond_id) "
        "VALUES(?,?,?,?,?,?)",
        (kid_id, now, kind, amount, note, bond_id),
    )


# ---------------------------------------------------------------------------
# Actions — these mutate and commit.
# ---------------------------------------------------------------------------
def _serialized_accrual_action(fn):
    """Serialize writers before their first read, without owning caller work.

    A caller transaction gets a savepoint and an attempted write lock. A stale
    WAL read snapshot cannot be upgraded: SQLite raises BUSY_SNAPSHOT and the
    caller must restart its outer transaction. Never commit that transaction.
    """
    @wraps(fn)
    def run(conn, *args, **kwargs):
        if not isinstance(conn, sqlite3.Connection):
            raise TypeError("bank actions require a sqlite3.Connection; adapters need an equivalent transaction implementation")
        started = not conn.in_transaction
        if started:
            conn.execute("BEGIN IMMEDIATE")
        else:
            conn.execute("SAVEPOINT bank_action")
        try:
            if not started:
                # A deferred caller transaction may have no write lock yet.
                # This acquires SQLite's database-wide writer reservation without
                # changing a row or firing row triggers.
                conn.execute("UPDATE kids SET id=id WHERE 0")
            result = fn(conn, *args, **kwargs)
            conn.execute("COMMIT" if started else "RELEASE SAVEPOINT bank_action")
            return result
        except BaseException:
            if conn.in_transaction:
                if started:
                    conn.execute("ROLLBACK")
                else:
                    conn.execute("ROLLBACK TO SAVEPOINT bank_action")
                    conn.execute("RELEASE SAVEPOINT bank_action")
            raise
    return run


@_serialized_accrual_action
def collect_rewards(conn, kid_id, kind="all"):
    """Collect rewards into the right place. `kind` is one of:
      allowance -> wallet, interest -> savings, invest -> wallet, or "all".
    Each source can be collected on its own button. Returns the amount the hero
    "all money" number grows by (allowance + interest + investment GAINS)."""
    now = now_ts(conn)
    kid = conn.execute("SELECT * FROM kids WHERE id=?", (kid_id,)).fetchone()
    if not kid:
        return 0

    new_wallet = kid["wallet_cents"]
    new_savings = kid["savings_cents"]
    periods_paid = kid["allowance_periods_paid"]
    savings_ts = kid["savings_accrued_ts"]
    hero_delta = 0
    matured = []

    if kind in ("all", "allowance"):
        allow_cents, owed = pending_allowance(kid, now)
        if allow_cents > 0:
            new_wallet += allow_cents
            periods_paid += owed
            hero_delta += allow_cents
            _tx(conn, kid_id, now, "allowance", allow_cents, "Allowance")

    if kind in ("all", "interest"):
        interest_cents = pending_interest(kid, now, savings_weekly_rate(conn))
        if interest_cents > 0:
            new_savings += interest_cents      # interest goes INTO savings
            savings_ts = now
            hero_delta += interest_cents
            _tx(conn, kid_id, now, "interest", interest_cents, "Savings interest")

    if kind in ("all", "invest"):
        matured = matured_bonds(conn, kid_id, now)
        for b in matured:
            new_wallet += b["payout_cents"]    # full payout (principal back + gain)
            hero_delta += b["payout_cents"] - b["principal_cents"]   # only gain is new
            _tx(conn, kid_id, now, "bond_payout", b["payout_cents"], "Investment matured!", b["id"])

    if hero_delta <= 0 and not matured:
        return 0

    conn.execute(
        "UPDATE kids SET wallet_cents=?, savings_cents=?, allowance_periods_paid=?, "
        "savings_accrued_ts=? WHERE id=?",
        (new_wallet, new_savings, periods_paid, savings_ts, kid_id),
    )
    for b in matured:
        conn.execute("UPDATE bonds SET collected=1 WHERE id=?", (b["id"],))

    _record_history(conn, kid_id, now)
    return hero_delta


@_serialized_accrual_action
def move_to_savings(conn, kid_id, amount_cents):
    now = now_ts(conn)
    kid = conn.execute("SELECT * FROM kids WHERE id=?", (kid_id,)).fetchone()
    amount = min(amount_cents, kid["wallet_cents"])
    if amount < config.MIN_MOVE_CENTS:
        return False, "Too little to save"
    # Bank any earned interest into the principal first, then add the deposit and
    # reset the clock so interest accrues cleanly from now.
    interest_cents = pending_interest(kid, now, savings_weekly_rate(conn))
    new_savings = kid["savings_cents"] + interest_cents + amount
    new_wallet = kid["wallet_cents"] - amount
    conn.execute(
        "UPDATE kids SET wallet_cents=?, savings_cents=?, savings_accrued_ts=? "
        "WHERE id=?",
        (new_wallet, new_savings, now, kid_id),
    )
    if interest_cents > 0:
        _tx(conn, kid_id, now, "interest", interest_cents, "Savings interest")
    _tx(conn, kid_id, now, "save", amount, "Moved to savings")
    _record_history(conn, kid_id, now)
    return True, "Saved!"


@_serialized_accrual_action
def withdraw_savings(conn, kid_id, amount_cents):
    now = now_ts(conn)
    kid = conn.execute("SELECT * FROM kids WHERE id=?", (kid_id,)).fetchone()
    interest_cents = pending_interest(kid, now, savings_weekly_rate(conn))
    savings_total = kid["savings_cents"] + interest_cents  # incl. earned interest
    amount = min(amount_cents, savings_total)
    if amount <= 0:
        return False, "Nothing to take out"
    conn.execute(
        "UPDATE kids SET wallet_cents=?, savings_cents=?, savings_accrued_ts=? "
        "WHERE id=?",
        (kid["wallet_cents"] + amount, savings_total - amount, now, kid_id),
    )
    if interest_cents > 0:
        _tx(conn, kid_id, now, "interest", interest_cents, "Savings interest")
    _tx(conn, kid_id, now, "withdraw", amount, "Savings to wallet")
    _record_history(conn, kid_id, now)
    return True, "Done!"


@_serialized_accrual_action
def spend(conn, kid_id, amount_cents, reason=""):
    """Spend coins from the wallet, recording what it was for."""
    now = now_ts(conn)
    kid = conn.execute("SELECT * FROM kids WHERE id=?", (kid_id,)).fetchone()
    amount = min(int(amount_cents), kid["wallet_cents"])
    if amount <= 0:
        return False, "No coins to spend"
    conn.execute("UPDATE kids SET wallet_cents=? WHERE id=?",
                 (kid["wallet_cents"] - amount, kid_id))
    _tx(conn, kid_id, now, "spend", -amount, (reason or "").strip() or "Spent it")
    _record_history(conn, kid_id, now)
    return True, "Spent!"


def transactions(conn, kid_id, limit=60):
    """Recent transactions, newest first, for the history view."""
    rows = conn.execute(
        "SELECT id, ts, kind, amount_cents, note FROM transactions "
        "WHERE kid_id=? ORDER BY ts DESC, id DESC LIMIT ?",
        (kid_id, limit),
    ).fetchall()
    return [{"id": r["id"], "ts": r["ts"], "kind": r["kind"],
             "amount_cents": r["amount_cents"], "note": r["note"]} for r in rows]


# Transaction kinds whose amount Dad can edit (single-pool money moves).
_AMOUNT_EDITABLE = {"gift", "allowance", "interest", "spend"}


@_serialized_accrual_action
def delete_transaction(conn, tx_id):
    """Delete a transaction AND reverse its effect on the kid's balances."""
    now = now_ts(conn)
    t = conn.execute("SELECT * FROM transactions WHERE id=?", (tx_id,)).fetchone()
    if not t:
        return False, "Not found"
    kid = conn.execute("SELECT * FROM kids WHERE id=?", (t["kid_id"],)).fetchone()
    kind, amt, bid = t["kind"], t["amount_cents"], t["bond_id"]
    w, s = kid["wallet_cents"], kid["savings_cents"]

    if kind == "invest" and bid:
        b = conn.execute("SELECT * FROM bonds WHERE id=?", (bid,)).fetchone()
        if b and b["collected"]:
            return False, "That investment already paid out — delete the payout first."

    if kind in ("gift", "allowance", "bond_payout", "spend"):
        w -= amt                      # these added `amt` to the wallet (spend's amt is negative)
    elif kind == "interest":
        s -= amt                      # added to savings
    elif kind == "save":
        w += amt; s -= amt            # was wallet->savings
    elif kind == "withdraw":
        w -= amt; s += amt            # was savings->wallet
    elif kind == "invest":
        w += amt                      # refund the locked coins

    if w < 0 or s < 0:
        return False, "Cannot delete: not enough money in the wallet or savings to reverse this transaction."

    if kind == "invest" and bid:
        conn.execute("DELETE FROM bonds WHERE id=?", (bid,))
    if kind == "bond_payout" and bid:
        conn.execute("UPDATE bonds SET collected=0 WHERE id=?", (bid,))  # re-lock it

    conn.execute("UPDATE kids SET wallet_cents=?, savings_cents=? WHERE id=?",
                 (w, s, kid["id"]))
    conn.execute("DELETE FROM transactions WHERE id=?", (tx_id,))
    _record_history(conn, kid["id"], now)
    return True, "Deleted"


@_serialized_accrual_action
def edit_transaction(conn, tx_id, new_amount_cents, new_note):
    """Edit a transaction's note (always) and amount (money kinds only),
    adjusting the balance by the difference."""
    now = now_ts(conn)
    t = conn.execute("SELECT * FROM transactions WHERE id=?", (tx_id,)).fetchone()
    if not t:
        return False, "Not found"
    note = t["note"] if new_note is None else new_note.strip()

    if new_amount_cents is None or t["kind"] not in _AMOUNT_EDITABLE:
        conn.execute("UPDATE transactions SET note=? WHERE id=?", (note, tx_id))
        return True, "Saved"

    kind, old = t["kind"], t["amount_cents"]
    new_signed = -abs(int(new_amount_cents)) if kind == "spend" else abs(int(new_amount_cents))
    delta = new_signed - old
    kid = conn.execute("SELECT * FROM kids WHERE id=?", (t["kid_id"],)).fetchone()
    w, s = kid["wallet_cents"], kid["savings_cents"]
    if kind == "interest":
        s += delta
    else:                              # gift / allowance / spend -> wallet
        w += delta
    if w < 0 or s < 0:
        return False, "Cannot edit: not enough money in the wallet or savings for this change."
    conn.execute("UPDATE kids SET wallet_cents=?, savings_cents=? WHERE id=?",
                 (w, s, kid["id"]))
    conn.execute("UPDATE transactions SET amount_cents=?, note=? WHERE id=?",
                 (new_signed, note, tx_id))
    _record_history(conn, kid["id"], now)
    return True, "Saved"


@_serialized_accrual_action
def buy_bond(conn, kid_id, term_key, amount_cents):
    if term_key not in config.BONDS:
        return False, "Unknown bond"
    now = now_ts(conn)
    kid = conn.execute("SELECT * FROM kids WHERE id=?", (kid_id,)).fetchone()
    amount = min(amount_cents, kid["wallet_cents"])
    if amount < config.MIN_MOVE_CENTS:
        return False, "Need more coins first"
    spec = config.BONDS[term_key]
    payout = int(round(amount * (1 + bond_return_pct(conn, term_key) / 100)))
    mature = now + spec["days"] * 86400
    conn.execute("UPDATE kids SET wallet_cents=? WHERE id=?",
                 (kid["wallet_cents"] - amount, kid_id))
    cur = conn.execute(
        "INSERT INTO bonds(kid_id, term_key, principal_cents, payout_cents, "
        "buy_ts, mature_ts, collected) VALUES(?,?,?,?,?,?,0)",
        (kid_id, term_key, amount, payout, now, mature),
    )
    _tx(conn, kid_id, now, "invest", amount, f"Invested ({spec['label']} lock)", cur.lastrowid)
    _record_history(conn, kid_id, now)
    return True, "Locked in!"


# ---------------------------------------------------------------------------
# Dad / admin actions.
# ---------------------------------------------------------------------------
@_serialized_accrual_action
def gift(conn, kid_id, amount_cents, note="From Dad"):
    """Add money to a kid's wallet, recording what it was for — the mirror image
    of `spend`, so a deposit carries a reason just like a purchase does."""
    now = now_ts(conn)
    kid = conn.execute("SELECT * FROM kids WHERE id=?", (kid_id,)).fetchone()
    if not kid:
        return False
    amount = abs(int(amount_cents))
    if amount <= 0:
        return False
    conn.execute("UPDATE kids SET wallet_cents=? WHERE id=?",
                 (kid["wallet_cents"] + amount, kid_id))
    _tx(conn, kid_id, now, "gift", amount, (note or "").strip() or "From Dad")
    _record_history(conn, kid_id, now)
    return True


def add_kid(conn, name, emoji="🐣", color="#4f9dde"):
    now = now_ts(conn)
    conn.execute(
        """INSERT INTO kids
           (name, color, emoji, wallet_cents, savings_cents,
            allowance_anchor_ts, allowance_periods_paid, savings_accrued_ts,
            created_ts)
           VALUES (?,?,?,?,0,?,0,?,?)""",
        (name, color, emoji, config.STARTING_WALLET_CENTS, now, now, now),
    )
    conn.commit()
    return True


def update_kid(conn, kid_id, name=None, emoji=None, color=None):
    kid = conn.execute("SELECT * FROM kids WHERE id=?", (kid_id,)).fetchone()
    if not kid:
        return False
    conn.execute(
        "UPDATE kids SET name=?, emoji=?, color=? WHERE id=?",
        (name or kid["name"], emoji or kid["emoji"], color or kid["color"], kid_id),
    )
    conn.commit()
    return True


@_serialized_accrual_action
def set_allowance(conn, kid_id, amount_cents, period_days, enabled=True):
    """Set a kid's allowance controls.

    Paused allowances accrue nothing. When one is resumed, it joins an enabled
    sibling's cadence and phase so allowance day stays a shared family event.
    If every sibling is paused, resuming starts a fresh period from now.
    """
    amount_cents = max(0, int(amount_cents))
    period_days = max(1, int(period_days))
    enabled = bool(enabled)
    kid = conn.execute("SELECT * FROM kids WHERE id=?", (kid_id,)).fetchone()
    if not kid:
        return False

    if not enabled:
        conn.execute(
            "UPDATE kids SET allowance_enabled=0, allowance_cents=?, "
            "allowance_period_days=? WHERE id=?",
            (amount_cents, period_days, kid_id),
        )
        return True

    # Only a disabled -> enabled transition needs rescheduling. Existing
    # enabled allowances retain the established behavior when Dad edits them.
    if not bool(kid["allowance_enabled"]):
        sibling = conn.execute(
            "SELECT * FROM kids WHERE id<>? AND allowance_enabled=1 ORDER BY id LIMIT 1",
            (kid_id,),
        ).fetchone()
        if sibling:
            period_days = max(1, int(sibling["allowance_period_days"]))
            anchor = sibling["allowance_anchor_ts"]
            periods_paid = sibling["allowance_periods_paid"]
        else:
            anchor = now_ts(conn)
            periods_paid = 0
        conn.execute(
            "UPDATE kids SET allowance_enabled=1, allowance_cents=?, "
            "allowance_period_days=?, allowance_anchor_ts=?, "
            "allowance_periods_paid=? WHERE id=?",
            (amount_cents, period_days, anchor, periods_paid, kid_id),
        )
        return True

    conn.execute(
        "UPDATE kids SET allowance_enabled=1, allowance_cents=?, "
        "allowance_period_days=? WHERE id=?",
        (amount_cents, period_days, kid_id),
    )
    return True


def all_kids_state(conn):
    now = now_ts(conn)
    kids = conn.execute("SELECT * FROM kids ORDER BY id").fetchall()
    return [kid_state(conn, k, now) for k in kids]


# ---------------------------------------------------------------------------
# Dad PIN. Gates the Dad controls so little hands can't gift themselves money.
# Stored hashed (SHA-256 + fixed salt). This is not strong authentication;
# plain HTTP does not protect the PIN in transit.
# ---------------------------------------------------------------------------
_PIN_SALT = "bank-of-dad::"


def _hash_pin(pin):
    return hashlib.sha256((_PIN_SALT + str(pin)).encode()).hexdigest()


def dad_pin_set(conn):
    return db.get_setting(conn, "dad_pin_hash") is not None


def check_dad_pin(conn, pin):
    """True if no PIN is set (open) or the supplied PIN matches."""
    stored = db.get_setting(conn, "dad_pin_hash")
    if not stored:
        return True
    return bool(pin) and _hash_pin(pin) == stored


def set_dad_pin(conn, new_pin, current_pin=None):
    """Set or change the PIN. If one already exists, current_pin must match.
    Pass an empty/None new_pin to remove the PIN."""
    if dad_pin_set(conn) and not check_dad_pin(conn, current_pin):
        return False, "Wrong current PIN"
    new_pin = (str(new_pin) if new_pin is not None else "").strip()
    if new_pin == "":
        conn.execute("DELETE FROM settings WHERE key='dad_pin_hash'")
        conn.commit()
        return True, "PIN removed"
    if not (new_pin.isdigit() and 4 <= len(new_pin) <= 8):
        return False, "PIN must be 4–8 digits"
    db.set_setting(conn, "dad_pin_hash", _hash_pin(new_pin))
    conn.commit()
    return True, "PIN saved"
