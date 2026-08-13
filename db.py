"""SQLite helper layer for the Tunetalk Dealer Referral Platform."""
import sqlite3
import os
from contextlib import contextmanager

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("DATABASE_PATH", os.path.join(BASE_DIR, "tunetalk.db"))
SCHEMA_PATH = os.path.join(BASE_DIR, "schema.sql")


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_conn()
    with open(SCHEMA_PATH, "r") as f:
        conn.executescript(f.read())
    conn.commit()
    _migrate(conn)
    conn.close()


def _migrate(conn):
    """Lightweight, idempotent migrations for databases created by an older schema.sql."""
    cols = [r[1] for r in conn.execute("PRAGMA table_info(dealers)").fetchall()]
    if "latitude" not in cols:
        conn.execute("ALTER TABLE dealers ADD COLUMN latitude REAL")
    if "longitude" not in cols:
        conn.execute("ALTER TABLE dealers ADD COLUMN longitude REAL")
    conn.commit()


@contextmanager
def db_cursor(commit=False):
    conn = get_conn()
    try:
        cur = conn.cursor()
        yield cur
        if commit:
            conn.commit()
    finally:
        conn.close()


def query(sql, params=()):
    with db_cursor() as cur:
        cur.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]


def query_one(sql, params=()):
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql, params=()):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Settings helpers
# ---------------------------------------------------------------------------

DEFAULT_SETTINGS = {
    "company_name": "Tunetalk Dealer Network",
    "company_tagline": "Pusat Sehenti Maklumat & Rangkaian Dealer Tunetalk",
    "company_phone": "011-1234 5678",
    "company_whatsapp": "60111234567",
    "company_email": "induk@tunetalk-dealer.test",
    "company_about": (
        "Kami adalah pengedar rasmi produk Tunetalk. Laman ini dikemaskini terus oleh "
        "pasukan induk supaya semua dealer sentiasa ada maklumat pakej dan promosi terkini."
    ),
    "default_commission_rate": "10",       # percent
    "referral_bonus_base": "50",           # RM value used as commission base per new dealer sign-up
    "min_payout_amount": "30",             # RM
}


def get_setting(key, default=None):
    row = query_one("SELECT value FROM settings WHERE key = ?", (key,))
    if row:
        return row["value"]
    return DEFAULT_SETTINGS.get(key, default)


def get_all_settings():
    rows = query("SELECT key, value FROM settings")
    settings = dict(DEFAULT_SETTINGS)
    for r in rows:
        settings[r["key"]] = r["value"]
    return settings


def set_setting(key, value):
    execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
