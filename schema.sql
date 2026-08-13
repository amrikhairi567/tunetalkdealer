-- Tunetalk Dealer Referral Platform — SQLite schema

CREATE TABLE IF NOT EXISTS admins (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS dealers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    email TEXT UNIQUE NOT NULL,
    phone TEXT NOT NULL,
    whatsapp TEXT,
    password_hash TEXT NOT NULL,
    bio TEXT,
    state TEXT,
    latitude REAL,                                  -- set at registration (browser geolocation)
    longitude REAL,
    status TEXT NOT NULL DEFAULT 'active',        -- active | pending | suspended
    referred_by_id INTEGER REFERENCES dealers(id),
    commission_rate REAL,                          -- override %, NULL = use site default
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS packages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'prepaid',       -- prepaid | postpaid | data | promo
    price REAL NOT NULL,
    data_quota TEXT,
    validity TEXT,
    description TEXT,
    is_promo INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1,
    sort_order INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS commissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dealer_id INTEGER NOT NULL REFERENCES dealers(id),        -- earner
    source_dealer_id INTEGER NOT NULL REFERENCES dealers(id), -- the referred dealer
    reason TEXT NOT NULL DEFAULT 'pendaftaran_dealer',
    base_amount REAL NOT NULL,
    rate REAL NOT NULL,
    amount REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',        -- pending | approved | paid | rejected
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS payout_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dealer_id INTEGER NOT NULL REFERENCES dealers(id),
    amount REAL NOT NULL,
    method TEXT NOT NULL DEFAULT 'bank_transfer',
    bank_name TEXT,
    bank_account TEXT,
    status TEXT NOT NULL DEFAULT 'pending',        -- pending | processing | paid | failed
    provider_ref TEXT,
    note TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    processed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_dealers_referred_by ON dealers(referred_by_id);
CREATE INDEX IF NOT EXISTS idx_commissions_dealer ON commissions(dealer_id);
CREATE INDEX IF NOT EXISTS idx_payouts_dealer ON payout_requests(dealer_id);
