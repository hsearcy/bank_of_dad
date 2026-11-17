# Synthetic pre-migration schema, intentionally missing additive columns.
LEGACY = """
CREATE TABLE kids (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
INSERT INTO kids VALUES (7, 'Legacy synthetic');
CREATE TABLE history (id INTEGER PRIMARY KEY, kid_id INTEGER, ts REAL, networth_cents INTEGER);
INSERT INTO history VALUES (1, 7, 123, 321);
CREATE TABLE transactions (id INTEGER PRIMARY KEY, kid_id INTEGER, ts REAL, kind TEXT, amount_cents INTEGER, note TEXT);
INSERT INTO transactions VALUES (1, 7, 123, 'gift', 321, 'legacy');
"""
