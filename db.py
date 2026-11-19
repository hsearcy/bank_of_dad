"""
SQLite persistence for Bank of Dad.

Deliberately tiny: stdlib sqlite3, integer cents, no ORM. The schema is
designed to be easy to evolve (a transactions ledger + history snapshots),
so future reward sources can just append transactions.
"""

import os
from pathlib import Path
import sqlite3

import config

# Overridable so tests (and throwaway demos) never touch the real family DB.
DB_PATH = os.environ.get(
    "BANK_OF_DAD_DB",
    os.path.join(os.path.dirname(__file__), "data", "bank.db"),
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS kids (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    color           TEXT NOT NULL DEFAULT '#4f9dde',
    emoji           TEXT NOT NULL DEFAULT '🐣',
    wallet_cents    INTEGER NOT NULL DEFAULT 0,
    savings_cents   INTEGER NOT NULL DEFAULT 0,
    -- per-kid allowance (Dad-configurable): on/off + amount + cadence in days
    allowance_enabled      INTEGER NOT NULL DEFAULT 1,
    allowance_cents        INTEGER NOT NULL DEFAULT 300,
    allowance_period_days  INTEGER NOT NULL DEFAULT 14,
    -- accrual bookkeeping (epoch seconds)
    allowance_anchor_ts     REAL NOT NULL,
    allowance_periods_paid  INTEGER NOT NULL DEFAULT 0,
    savings_accrued_ts      REAL NOT NULL,
    savings_interest_carry  REAL NOT NULL DEFAULT 0,
    savings_interest_exact  TEXT,
    auto_interest_due_ts    REAL,
    created_ts              REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS bonds (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    kid_id        INTEGER NOT NULL REFERENCES kids(id) ON DELETE CASCADE,
    term_key      TEXT NOT NULL,
    principal_cents INTEGER NOT NULL,
    payout_cents  INTEGER NOT NULL,
    buy_ts        REAL NOT NULL,
    mature_ts     REAL NOT NULL,
    collected     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS transactions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kid_id      INTEGER NOT NULL REFERENCES kids(id) ON DELETE CASCADE,
    ts          REAL NOT NULL,
    kind        TEXT NOT NULL,   -- allowance|interest|invest|bond_payout|save|withdraw|gift|spend
    amount_cents INTEGER NOT NULL, -- signed, relative to net worth
    note        TEXT,
    bond_id     INTEGER          -- links invest / bond_payout rows to their bond
);

CREATE TABLE IF NOT EXISTS history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kid_id      INTEGER NOT NULL REFERENCES kids(id) ON DELETE CASCADE,
    ts          REAL NOT NULL,
    networth_cents INTEGER NOT NULL,
    -- per-pool breakdown so the chart can draw separate lines (added later;
    -- old rows have NULL and fall back to "all in wallet")
    wallet_cents   INTEGER,
    savings_cents  INTEGER,
    invest_cents   INTEGER
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def get_conn(path=None):
    request_transaction = False
    hosted_request = False
    if path is None:
        from flask import g, has_request_context, request
        if os.environ.get('BANK_OF_DAD_MODE') == 'cloudflare':
            # Only the authenticated, transaction-scoped Durable Object request
            # can supply this connection. Never fall back to ephemeral disk.
            conn = request.environ.get('bod.connection') if has_request_context() else None
            if conn is None or not conn.in_transaction:
                raise RuntimeError('Cloudflare bank access requires a family transaction')
            return conn
        path = getattr(g, 'bank_db_path', None) if has_request_context() else None
        hosted_request = os.environ.get('BANK_OF_DAD_MODE') == 'hosted'
        if hosted_request and path is None:
            raise RuntimeError('Hosted bank access requires an authenticated family')
        request_transaction = has_request_context()
    # Hosted files are created only by the explicit onboarding/login paths. A
    # retained session must never turn a missing family file into an empty bank.
    if hosted_request:
        assert path is not None  # fail-closed check above, never the default DB
        conn = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=rw', uri=True)
    else:
        conn = sqlite3.connect(path or DB_PATH)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        if request_transaction:
            # Both SQLite HTTP targets must serialize before the first read. In
            # particular a collector cannot combine a pre-change accrual anchor with
            # a post-change savings rate. DO requests already own transactionSync.
            conn.execute('BEGIN IMMEDIATE')
            if hosted_request and _migrate(conn):
                # Only additive upgrades, never init/seed or startup-wide writes.
                # Commit the upgrade even for a read whose request transaction is
                # later closed without committing. Reacquire before domain reads.
                conn.commit()
                conn.execute('BEGIN IMMEDIATE')
        return conn
    except Exception:
        conn.close()  # Roll back partial DDL too; do not retain a failed writer.
        raise


def get_setting(conn, key, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn, key, value):
    conn.execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )


def init_db(seed=True, path=None):
    """Initialize an empty database; seed is retained for API compatibility."""
    os.makedirs(os.path.dirname(os.path.abspath(path or DB_PATH)), exist_ok=True)
    conn = get_conn(path)
    try:
        conn.executescript(SCHEMA)
        _migrate(conn)
        conn.commit()
    finally:
        conn.close()


def _migrate(conn):
    """Add missing columns; return whether this transaction upgraded the schema."""
    hist_cols = {r["name"] for r in conn.execute("PRAGMA table_info(history)")}
    for col in ("wallet_cents", "savings_cents", "invest_cents"):
        if col not in hist_cols:
            conn.execute(f"ALTER TABLE history ADD COLUMN {col} INTEGER")

    tx_cols = {r["name"] for r in conn.execute("PRAGMA table_info(transactions)")}
    if "bond_id" not in tx_cols:
        conn.execute("ALTER TABLE transactions ADD COLUMN bond_id INTEGER")

    kid_cols = {r["name"] for r in conn.execute("PRAGMA table_info(kids)")}
    if "savings_interest_exact" not in kid_cols:
        conn.execute("ALTER TABLE kids ADD COLUMN savings_interest_exact TEXT")
    if "auto_interest_due_ts" not in kid_cols:
        conn.execute("ALTER TABLE kids ADD COLUMN auto_interest_due_ts REAL")
    if "savings_interest_carry" not in kid_cols:
        conn.execute("ALTER TABLE kids ADD COLUMN savings_interest_carry REAL NOT NULL DEFAULT 0")
    if "allowance_enabled" not in kid_cols:
        conn.execute("ALTER TABLE kids ADD COLUMN allowance_enabled INTEGER NOT NULL DEFAULT 1")
    if "allowance_cents" not in kid_cols:
        conn.execute("ALTER TABLE kids ADD COLUMN allowance_cents INTEGER")
        conn.execute("UPDATE kids SET allowance_cents=? WHERE allowance_cents IS NULL",
                     (config.ALLOWANCE_CENTS,))
    if "allowance_period_days" not in kid_cols:
        conn.execute("ALTER TABLE kids ADD COLUMN allowance_period_days INTEGER")
        conn.execute("UPDATE kids SET allowance_period_days=? WHERE allowance_period_days IS NULL",
                     (config.ALLOWANCE_PERIOD_DAYS,))
    return (not {"wallet_cents", "savings_cents", "invest_cents"} <= hist_cols
            or "bond_id" not in tx_cols
            or not {"savings_interest_exact", "auto_interest_due_ts", "savings_interest_carry", "allowance_enabled", "allowance_cents",
                    "allowance_period_days"} <= kid_cols)
