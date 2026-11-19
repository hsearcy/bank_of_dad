"""
Bank of Dad — core money logic.

Pure-ish functions over a sqlite connection. Interest/allowance/bonds accrue
*lazily*: nothing runs in the background. We compute what's owed from
timestamps whenever the state is read, so the app works fine on a tablet that
sleeps for days and there's no cron to babysit.
"""

import hashlib
import math
import sqlite3
import time

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
def savings_period_days(conn):
    return int(db.get_setting(conn, "savings_period_days", 7))


def savings_period_pct(conn):
    # Retain the legacy storage/API key: this is the percentage per chosen period.
    return float(db.get_setting(conn, "savings_weekly_pct", config.SAVINGS_WEEKLY_RATE * 100))


def savings_weekly_rate(conn):
    """Equivalent weekly simple rate for existing accrual callers."""
    return savings_period_pct(conn) / 100 * (7 / savings_period_days(conn))


def bond_days(conn, term_key):
    return int(db.get_setting(conn, "days_" + term_key, config.BONDS[term_key]["days"]))


def duration_label(days):
    # Months are the existing fixed 30-day units, not calendar months.
    if days % 30 == 0:
        n = days // 30
        return f"{n} Month" + ("s" if n != 1 else "")
    return f"{days} Day" + ("s" if days != 1 else "")


def bond_return_pct(conn, term_key):
    v = db.get_setting(conn, "return_pct_" + term_key)
    if v is not None:
        return float(v)
    return config.BONDS.get(term_key, {}).get("return_pct", 0)


def _rate_number(value, *, days=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("Enter a finite number")
    if days:
        if value != int(value) or not 1 <= value <= 3650:
            raise ValueError("Periods must be whole days from 1 to 3650")
        return int(value)
    if value < 0:
        raise ValueError("Percentages cannot be negative")
    return float(value)


def set_rates(conn, savings_weekly_pct=None, returns=None, savings_days=None, bond_period_days=None):
    """Change future terms only; preserve accrued savings (including fractions).

    No automatic collection/compounding or balance/ledger changes. An unchanged
    save is a true no-op. Native SQLite owns a writer lock before the first read;
    Workers already own the enclosing family transactionSync.
    """
    changes = {}
    if savings_weekly_pct is not None:
        changes["savings_weekly_pct"] = _rate_number(savings_weekly_pct)
    if savings_days is not None:
        changes["savings_period_days"] = _rate_number(savings_days, days=True)
    for values, prefix, days in ((returns, "return_pct_", False), (bond_period_days, "days_", True)):
        if values is None:
            continue
        if not isinstance(values, dict) or any(key not in config.BONDS for key in values):
            raise ValueError("Unknown investment term")
        for key, value in values.items():
            changes[prefix + key] = _rate_number(value, days=days)

    native = isinstance(conn, sqlite3.Connection)
    owned = native and not conn.in_transaction
    if owned:
        conn.execute("BEGIN IMMEDIATE")
    if native:
        conn.execute("SAVEPOINT rates_update")
    savepoint_open = native
    try:
        current = {"savings_weekly_pct": savings_period_pct(conn),
                   "savings_period_days": savings_period_days(conn)}
        for key in config.BONDS:
            current["return_pct_" + key] = bond_return_pct(conn, key)
            current["days_" + key] = bond_days(conn, key)
        changes = {key: value for key, value in changes.items() if value != current[key]}
        if any(key in changes for key in ("savings_weekly_pct", "savings_period_days")):
            now = now_ts(conn)
            old_rate = savings_weekly_rate(conn)
            for kid in conn.execute("SELECT * FROM kids").fetchall():
                earned = _interest_exact(kid, now, old_rate)
                conn.execute("UPDATE kids SET savings_interest_carry=?, savings_accrued_ts=? WHERE id=?",
                             (earned, max(now, kid["savings_accrued_ts"]), kid["id"]))
        for key, value in changes.items():
            db.set_setting(conn, key, value)
        if native:
            conn.execute("RELEASE rates_update")
            savepoint_open = False
        if owned:
            conn.commit()
    except Exception:
        if native and conn.in_transaction:
            if savepoint_open:
                conn.execute("ROLLBACK TO rates_update")
                conn.execute("RELEASE rates_update")
            if owned:
                conn.rollback()
        raise
    return True


def _interest_exact(kid, now, weekly_rate):
    weeks = max(0.0, (now - kid["savings_accrued_ts"]) / (7 * 86400))
    return kid["savings_interest_carry"] + kid["savings_cents"] * weekly_rate * weeks


def pending_interest(kid, now, weekly_rate):
    """Simple continuous interest, rounded only for the existing collection rule."""
    return int(round(_interest_exact(kid, now, weekly_rate)))


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
                "term": duration_label(max(1, round((b["mature_ts"] - b["buy_ts"]) / 86400))),
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
    savings_carry = kid["savings_interest_carry"]
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
            # Carry can be collected after a demo/system clock rewind. Never
            # reopen time already represented by that earned carry.
            savings_ts = max(now, savings_ts)
            savings_carry = 0
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
        "savings_accrued_ts=?, savings_interest_carry=? WHERE id=?",
        (new_wallet, new_savings, periods_paid, savings_ts, savings_carry, kid_id),
    )
    for b in matured:
        conn.execute("UPDATE bonds SET collected=1 WHERE id=?", (b["id"],))

    _record_history(conn, kid_id, now)
    conn.commit()
    return hero_delta


def move_to_savings(conn, kid_id, amount_cents):
    now = now_ts(conn)
    kid = conn.execute("SELECT * FROM kids WHERE id=?", (kid_id,)).fetchone()
    amount = min(amount_cents, kid["wallet_cents"])
    if amount < config.MIN_MOVE_CENTS:
        return False, "Too little to save"
    # Bank any earned interest into the principal first, then add the deposit and
    # reset the clock, but never reopen an already-earned interval on a rewind.
    new_savings = kid["savings_cents"] + pending_interest(kid, now, savings_weekly_rate(conn)) + amount
    new_wallet = kid["wallet_cents"] - amount
    conn.execute(
        "UPDATE kids SET wallet_cents=?, savings_cents=?, savings_accrued_ts=?, savings_interest_carry=0 "
        "WHERE id=?",
        (new_wallet, new_savings, max(now, kid["savings_accrued_ts"]), kid_id),
    )
    _tx(conn, kid_id, now, "save", amount, "Moved to savings")
    _record_history(conn, kid_id, now)
    conn.commit()
    return True, "Saved!"


def withdraw_savings(conn, kid_id, amount_cents):
    now = now_ts(conn)
    kid = conn.execute("SELECT * FROM kids WHERE id=?", (kid_id,)).fetchone()
    savings_total = kid["savings_cents"] + pending_interest(kid, now, savings_weekly_rate(conn))  # incl. earned interest
    amount = min(amount_cents, savings_total)
    if amount <= 0:
        return False, "Nothing to take out"
    conn.execute(
        "UPDATE kids SET wallet_cents=?, savings_cents=?, savings_accrued_ts=?, savings_interest_carry=0 "
        "WHERE id=?",
        (kid["wallet_cents"] + amount, savings_total - amount,
         max(now, kid["savings_accrued_ts"]), kid_id),
    )
    _tx(conn, kid_id, now, "withdraw", amount, "Savings to wallet")
    _record_history(conn, kid_id, now)
    conn.commit()
    return True, "Done!"


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
    conn.commit()
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
    conn.commit()
    return True, "Deleted"


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
        conn.commit()
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
    conn.commit()
    return True, "Saved"


def buy_bond(conn, kid_id, term_key, amount_cents):
    if term_key not in config.BONDS:
        return False, "Unknown bond"
    now = now_ts(conn)
    kid = conn.execute("SELECT * FROM kids WHERE id=?", (kid_id,)).fetchone()
    amount = min(amount_cents, kid["wallet_cents"])
    if amount < config.MIN_MOVE_CENTS:
        return False, "Need more coins first"
    payout = int(round(amount * (1 + bond_return_pct(conn, term_key) / 100)))
    days = bond_days(conn, term_key)
    mature = now + days * 86400
    conn.execute("UPDATE kids SET wallet_cents=? WHERE id=?",
                 (kid["wallet_cents"] - amount, kid_id))
    cur = conn.execute(
        "INSERT INTO bonds(kid_id, term_key, principal_cents, payout_cents, "
        "buy_ts, mature_ts, collected) VALUES(?,?,?,?,?,?,0)",
        (kid_id, term_key, amount, payout, now, mature),
    )
    _tx(conn, kid_id, now, "invest", amount, f"Invested ({duration_label(days)} lock)", cur.lastrowid)
    _record_history(conn, kid_id, now)
    conn.commit()
    return True, "Locked in!"


# ---------------------------------------------------------------------------
# Dad / admin actions.
# ---------------------------------------------------------------------------
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
    conn.commit()
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
    if name is not None:
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 80:
            raise ValueError("Use a name between 1 and 80 characters.")
        name = name.strip()
    kid = conn.execute("SELECT * FROM kids WHERE id=?", (kid_id,)).fetchone()
    if not kid:
        return False
    conn.execute(
        "UPDATE kids SET name=?, emoji=?, color=? WHERE id=?",
        (name or kid["name"], emoji or kid["emoji"], color or kid["color"], kid_id),
    )
    conn.commit()
    return True


def remove_kid(conn, kid_id, confirm_name):
    """Permanently remove exactly the confirmed child and their dependent rows.

    Both adapters enforce the schema's ON DELETE CASCADE foreign keys. One SQL
    statement makes balances, bonds, ledger and chart history disappear atomically
    (including on a trigger failure), without reversing money into another child.
    Matching the displayed name also rejects a stale confirmation after a rename.
    There is no existing archive/soft-delete model; do not reseed an empty bank.
    """
    if not isinstance(confirm_name, str):
        return False
    conn.execute("DELETE FROM kids WHERE id=? AND name=?", (kid_id, confirm_name))
    removed = conn.execute("SELECT changes() AS removed").fetchone()["removed"] == 1
    conn.commit()
    return removed


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
        conn.commit()
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
        conn.commit()
        return True

    conn.execute(
        "UPDATE kids SET allowance_enabled=1, allowance_cents=?, "
        "allowance_period_days=? WHERE id=?",
        (amount_cents, period_days, kid_id),
    )
    conn.commit()
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
