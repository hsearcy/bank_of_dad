"""
SQLite persistence for Bank of Dad.

Deliberately tiny: stdlib sqlite3, integer cents, no ORM. The schema is
designed to be easy to evolve (a transactions ledger + history snapshots),
so future reward sources can just append transactions.
"""

import os
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
        if os.environ.get('BANK_OF_DAD_MODE') == 'hosted' and path is None:
            raise RuntimeError('Hosted bank access requires an authenticated family')
        hosted_request = os.environ.get('BANK_OF_DAD_MODE') == 'hosted'
    conn = sqlite3.connect(path or DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if hosted_request:
        # Existing domain operations read then write; serialize before their first
        # read to prevent concurrent parent sessions from losing balance updates.
        conn.execute('BEGIN IMMEDIATE')
    return conn


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
    """Additive migrations for DBs created before a column existed."""
    hist_cols = {r["name"] for r in conn.execute("PRAGMA table_info(history)")}
    for col in ("wallet_cents", "savings_cents", "invest_cents"):
        if col not in hist_cols:
            conn.execute(f"ALTER TABLE history ADD COLUMN {col} INTEGER")

    tx_cols = {r["name"] for r in conn.execute("PRAGMA table_info(transactions)")}
    if "bond_id" not in tx_cols:
        conn.execute("ALTER TABLE transactions ADD COLUMN bond_id INTEGER")

    kid_cols = {r["name"] for r in conn.execute("PRAGMA table_info(kids)")}
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
