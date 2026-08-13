"""
Seed demo data — run once with `python seed.py`.

Package info below is adapted from the public Tune Talk reseller site
(simkadtunetalk.com) as a starting point. Prices/promos change often —
log in to /admin/tetapan and /admin/pakej to edit or replace them with
whatever is current; every dealer salespage updates automatically.
"""
import db
import auth

db.init_db()

# --- Site settings (shown on the master site + every dealer salespage) ----
settings = {
    "company_name": "Tune Talk Dealer Network",
    "company_tagline": "Unlimited Data, Call Dan Income — Sertai Rangkaian Dealer Tune Talk",
    "company_about": (
        "Nikmati internet tanpa risau kehabisan data — sesuai untuk streaming, kerja, dan "
        "kegunaan harian dengan panggilan tanpa had ke semua rangkaian. Kami mengedarkan pakej "
        "prabayar Tune Talk rasmi dan membuka peluang menjadi dealer dengan komisen rujukan."
    ),
    "company_phone": "013-650 4939",
    "company_whatsapp": "60136504939",
    "company_email": "induk@tunetalk-dealer.test",
    "default_commission_rate": "10",
    "referral_bonus_base": "50",
    "min_payout_amount": "30",
}
for k, v in settings.items():
    db.set_setting(k, v)

# --- Admin account ----------------------------------------------------------
if not db.query_one("SELECT id FROM admins WHERE email = ?", ("admin@tunetalk-dealer.test",)):
    db.execute(
        "INSERT INTO admins (email, password_hash, name) VALUES (?, ?, ?)",
        ("admin@tunetalk-dealer.test", auth.hash_password("admin123"), "Admin Induk"),
    )
    print("Admin created: admin@tunetalk-dealer.test / admin123  (TUKAR password ini sebelum go-live)")

# --- Packages (adapted from simkadtunetalk.com — edit freely in /admin/pakej) ----
packages = [
    dict(title="Epik 50", category="prepaid", price=50.00, data_quota="350GB Data + 350GB Hotspot",
         validity="30 hari", description="Pelan paling laris — data besar, panggilan tanpa had ke semua rangkaian.",
         is_promo=1, sort_order=1),
    dict(title="Epik 35", category="prepaid", price=35.00, data_quota="150GB Data + 150GB Hotspot",
         validity="30 hari", description="Pelan value-for-money dengan data mencukupi untuk kegunaan harian.",
         is_promo=1, sort_order=2),
    dict(title="Pelan Permulaan", category="prepaid", price=5.00, data_quota="Tempahan SIM starter pack",
         validity="-", description="SIM permulaan Tune Talk — top-up dan pilih pelan mengikut keperluan.",
         is_promo=0, sort_order=3),
]
for p in packages:
    if not db.query_one("SELECT id FROM packages WHERE title = ?", (p["title"],)):
        db.execute(
            "INSERT INTO packages (title, category, price, data_quota, validity, description, is_promo, is_active, sort_order) "
            "VALUES (:title, :category, :price, :data_quota, :validity, :description, :is_promo, 1, :sort_order)",
            p,
        )

# --- Demo dealers (2-level referral chain to show commissions working) -----
def ensure_dealer(name, email, phone, whatsapp, state, bio, referred_by_slug=None, lat=None, lng=None):
    existing = db.query_one("SELECT * FROM dealers WHERE email = ?", (email,))
    if existing:
        return existing
    import re, unicodedata
    slug = re.sub(r"[\s_-]+", "-", unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().strip().lower())
    referred_by = db.query_one("SELECT * FROM dealers WHERE slug = ?", (referred_by_slug,)) if referred_by_slug else None
    dealer_id = db.execute(
        "INSERT INTO dealers (slug, name, email, phone, whatsapp, password_hash, bio, state, latitude, longitude, status, referred_by_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?)",
        (slug, name, email, phone, whatsapp, auth.hash_password("dealer123"), bio, state, lat, lng,
         referred_by["id"] if referred_by else None),
    )
    dealer = db.query_one("SELECT * FROM dealers WHERE id = ?", (dealer_id,))
    if dealer.get("referred_by_id"):
        ref = db.query_one("SELECT * FROM dealers WHERE id = ?", (dealer["referred_by_id"],))
        rate = ref["commission_rate"] if ref["commission_rate"] is not None else float(db.get_setting("default_commission_rate"))
        base = float(db.get_setting("referral_bonus_base"))
        amount = round(base * rate / 100, 2)
        db.execute(
            "INSERT INTO commissions (dealer_id, source_dealer_id, reason, base_amount, rate, amount, status) "
            "VALUES (?, ?, 'pendaftaran_dealer', ?, ?, ?, 'approved')",
            (ref["id"], dealer["id"], base, rate, amount),
        )
    return dealer

wan = ensure_dealer("Wan Terengganu", "wan@tunetalk-dealer.test", "013-650 4939", "60136504939",
                     "Terengganu", "Dealer rasmi Tune Talk kawasan Terengganu. Sedia bantu 24/7.",
                     lat=5.3302, lng=103.1408)  # Kuala Terengganu
siti = ensure_dealer("Siti Aminah", "siti@tunetalk-dealer.test", "012-345 6789", "60123456789",
                      "Selangor", "Dealer aktif kawasan Klang Valley — respon pantas!", referred_by_slug=wan["slug"],
                      lat=3.0733, lng=101.5185)  # Shah Alam
ensure_dealer("Ahmad Faiz", "ahmad@tunetalk-dealer.test", "019-876 5432", "60198765432",
              "Johor", "Dealer baru — dijemput oleh Siti Aminah.", referred_by_slug=siti["slug"],
              lat=1.4927, lng=103.7414)  # Johor Bahru

print("Seed selesai.")
print(f"Contoh salespage dealer: /d/{wan['slug']}  dan  /d/{siti['slug']}")
print("Log masuk dealer demo: wan@tunetalk-dealer.test / dealer123")
