import os
import re
import math
import unicodedata
from datetime import datetime

from flask import Flask, render_template, request, redirect, url_for, flash, session, g, abort, jsonify

import db
import auth
import payments

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me-before-deploying")

# init_db() is idempotent (CREATE TABLE IF NOT EXISTS + column migrations),
# so it's safe to always run at startup — this also picks up schema changes
# (like the dealer latitude/longitude columns) on a database created by an
# older version of schema.sql.
db.init_db()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def slugify(text):
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-zA-Z0-9\s-]", "", text).strip().lower()
    text = re.sub(r"[\s_-]+", "-", text)
    return text or "dealer"


def unique_slug(name):
    base = slugify(name)
    slug = base
    i = 1
    while db.query_one("SELECT id FROM dealers WHERE slug = ?", (slug,)):
        i += 1
        slug = f"{base}-{i}"
    return slug


def get_active_packages():
    return db.query(
        "SELECT * FROM packages WHERE is_active = 1 ORDER BY sort_order ASC, id ASC"
    )


def haversine_km(lat1, lon1, lat2, lon2):
    """Great-circle distance in km between two lat/lng points."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def nearest_dealers(lat, lng, limit=3):
    candidates = db.query(
        "SELECT id, name, slug, state, whatsapp, latitude, longitude FROM dealers "
        "WHERE status = 'active' AND latitude IS NOT NULL AND longitude IS NOT NULL"
    )
    for d in candidates:
        d["distance_km"] = round(haversine_km(lat, lng, d["latitude"], d["longitude"]), 1)
    candidates.sort(key=lambda d: d["distance_km"])
    return candidates[:limit]


def dealer_stats(dealer_id):
    referred = db.query(
        "SELECT id, name, slug, status, created_at FROM dealers WHERE referred_by_id = ? ORDER BY created_at DESC",
        (dealer_id,),
    )
    commissions = db.query(
        "SELECT c.*, d.name AS source_name FROM commissions c "
        "JOIN dealers d ON d.id = c.source_dealer_id "
        "WHERE c.dealer_id = ? ORDER BY c.created_at DESC",
        (dealer_id,),
    )
    total_pending = sum(c["amount"] for c in commissions if c["status"] == "pending")
    total_approved = sum(c["amount"] for c in commissions if c["status"] == "approved")
    total_paid = sum(c["amount"] for c in commissions if c["status"] == "paid")
    payout_requested = sum(
        p["amount"]
        for p in db.query(
            "SELECT amount FROM payout_requests WHERE dealer_id = ? AND status IN ('pending','processing')",
            (dealer_id,),
        )
    )
    available_balance = max(total_approved - payout_requested, 0)
    return {
        "referred": referred,
        "commissions": commissions,
        "total_pending": total_pending,
        "total_approved": total_approved,
        "total_paid": total_paid,
        "available_balance": available_balance,
        "referred_count": len(referred),
    }


def award_referral_commission(new_dealer):
    """Called right after a dealer signs up. If they were referred, credit the referrer."""
    if not new_dealer.get("referred_by_id"):
        return
    referrer = db.query_one("SELECT * FROM dealers WHERE id = ?", (new_dealer["referred_by_id"],))
    if not referrer or referrer["status"] != "active":
        return
    settings = db.get_all_settings()
    rate = referrer["commission_rate"] if referrer["commission_rate"] is not None else float(settings["default_commission_rate"])
    base_amount = float(settings["referral_bonus_base"])
    amount = round(base_amount * rate / 100, 2)
    db.execute(
        "INSERT INTO commissions (dealer_id, source_dealer_id, reason, base_amount, rate, amount, status) "
        "VALUES (?, ?, 'pendaftaran_dealer', ?, ?, ?, 'pending')",
        (referrer["id"], new_dealer["id"], base_amount, rate, amount),
    )


@app.context_processor
def inject_globals():
    return {
        "settings": db.get_all_settings(),
        "current_dealer": auth.current_dealer(),
        "current_admin": auth.current_admin(),
        "now": datetime.utcnow(),
    }


# ---------------------------------------------------------------------------
# Public master site
# ---------------------------------------------------------------------------

@app.route("/")
def home():
    packages = get_active_packages()
    promos = [p for p in packages if p["is_promo"]]
    top_dealers = db.query(
        "SELECT id, name, slug, state FROM dealers WHERE status = 'active' ORDER BY created_at DESC LIMIT 6"
    )
    return render_template("home.html", packages=packages, promos=promos, top_dealers=top_dealers)


@app.route("/pakej")
def packages_page():
    packages = get_active_packages()
    return render_template("packages.html", packages=packages)


@app.route("/dealer-list")
def dealer_list():
    dealers = db.query("SELECT * FROM dealers WHERE status = 'active' ORDER BY name ASC")
    return render_template("dealer_list.html", dealers=dealers)


@app.route("/api/dealer-terdekat")
def api_nearest_dealers():
    """Used by the homepage: given the visitor's browser geolocation, return the
    3 closest active dealers (only dealers who set a location at registration)."""
    try:
        lat = float(request.args.get("lat"))
        lng = float(request.args.get("lng"))
    except (TypeError, ValueError):
        return jsonify({"dealers": [], "error": "lat/lng tidak sah"}), 400
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return jsonify({"dealers": [], "error": "lat/lng di luar julat"}), 400
    dealers = nearest_dealers(lat, lng, limit=3)
    return jsonify({
        "dealers": [
            {
                "name": d["name"], "slug": d["slug"], "state": d["state"],
                "distance_km": d["distance_km"],
            }
            for d in dealers
        ]
    })


# ---------------------------------------------------------------------------
# Dealer auth
# ---------------------------------------------------------------------------

@app.route("/dealer/daftar", methods=["GET", "POST"])
def dealer_register():
    ref_slug = request.args.get("ref", "").strip()
    referrer = db.query_one("SELECT * FROM dealers WHERE slug = ? AND status = 'active'", (ref_slug,)) if ref_slug else None

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        phone = request.form.get("phone", "").strip()
        whatsapp = request.form.get("whatsapp", "").strip() or phone
        state = request.form.get("state", "").strip()
        bio = request.form.get("bio", "").strip()
        password = request.form.get("password", "")
        ref_slug_form = request.form.get("ref_slug", "").strip()
        latitude = request.form.get("latitude", "").strip()
        longitude = request.form.get("longitude", "").strip()

        errors = []
        if not name:
            errors.append("Nama penuh diperlukan.")
        if not email or "@" not in email:
            errors.append("E-mel tidak sah.")
        if not phone:
            errors.append("Nombor telefon diperlukan.")
        if len(password) < 6:
            errors.append("Kata laluan mestilah sekurang-kurangnya 6 aksara.")
        if db.query_one("SELECT id FROM dealers WHERE email = ?", (email,)):
            errors.append("E-mel ini sudah didaftarkan.")
        if not latitude or not longitude:
            errors.append("Sila tetapkan lokasi semasa anda (klik butang 'Guna Lokasi Semasa') supaya bakal dealer/pelanggan berdekatan boleh jumpa salespage anda.")

        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("dealer_register.html", referrer=referrer, form=request.form)

        referred_by = db.query_one("SELECT * FROM dealers WHERE slug = ? AND status = 'active'", (ref_slug_form,)) if ref_slug_form else None

        slug = unique_slug(name)
        dealer_id = db.execute(
            "INSERT INTO dealers (slug, name, email, phone, whatsapp, password_hash, bio, state, latitude, longitude, status, referred_by_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?)",
            (slug, name, email, phone, whatsapp, auth.hash_password(password), bio, state,
             float(latitude), float(longitude), referred_by["id"] if referred_by else None),
        )
        new_dealer = db.query_one("SELECT * FROM dealers WHERE id = ?", (dealer_id,))
        award_referral_commission(new_dealer)
        auth.login_dealer(new_dealer)
        flash("Pendaftaran berjaya! Salespage anda sudah aktif.", "success")
        return redirect(url_for("dealer_dashboard"))

    return render_template("dealer_register.html", referrer=referrer, form={})


@app.route("/dealer/log-masuk", methods=["GET", "POST"])
def dealer_login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        dealer = db.query_one("SELECT * FROM dealers WHERE email = ?", (email,))
        if dealer and auth.verify_password(password, dealer["password_hash"]):
            if dealer["status"] == "suspended":
                flash("Akaun anda digantung. Sila hubungi admin.", "error")
                return render_template("dealer_login.html")
            auth.login_dealer(dealer)
            return redirect(url_for("dealer_dashboard"))
        flash("E-mel atau kata laluan salah.", "error")
    return render_template("dealer_login.html")


@app.route("/dealer/log-keluar")
def dealer_logout():
    auth.logout()
    return redirect(url_for("home"))


@app.route("/dealer/papan-pemuka")
@auth.dealer_required
def dealer_dashboard():
    dealer = g.dealer
    stats = dealer_stats(dealer["id"])
    salespage_url = url_for("dealer_salespage", slug=dealer["slug"], _external=True)
    referral_url = f"{url_for('dealer_register', _external=True)}?ref={dealer['slug']}"
    payouts = db.query(
        "SELECT * FROM payout_requests WHERE dealer_id = ? ORDER BY created_at DESC", (dealer["id"],)
    )
    return render_template(
        "dealer_dashboard.html", dealer=dealer, stats=stats,
        salespage_url=salespage_url, referral_url=referral_url, payouts=payouts,
    )


@app.route("/dealer/papan-pemuka/kemaskini", methods=["POST"])
@auth.dealer_required
def dealer_update_profile():
    dealer = g.dealer
    whatsapp = request.form.get("whatsapp", "").strip()
    bio = request.form.get("bio", "").strip()
    state = request.form.get("state", "").strip()
    phone = request.form.get("phone", "").strip()
    latitude = request.form.get("latitude", "").strip()
    longitude = request.form.get("longitude", "").strip()

    if latitude and longitude:
        db.execute(
            "UPDATE dealers SET whatsapp = ?, bio = ?, state = ?, phone = ?, latitude = ?, longitude = ? WHERE id = ?",
            (whatsapp, bio, state, phone, float(latitude), float(longitude), dealer["id"]),
        )
    else:
        db.execute(
            "UPDATE dealers SET whatsapp = ?, bio = ?, state = ?, phone = ? WHERE id = ?",
            (whatsapp, bio, state, phone, dealer["id"]),
        )
    flash("Profil salespage anda dikemaskini.", "success")
    return redirect(url_for("dealer_dashboard"))


@app.route("/dealer/mohon-bayaran", methods=["POST"])
@auth.dealer_required
def dealer_request_payout():
    dealer = g.dealer
    stats = dealer_stats(dealer["id"])
    settings = db.get_all_settings()
    min_payout = float(settings["min_payout_amount"])
    amount = stats["available_balance"]
    bank_name = request.form.get("bank_name", "").strip()
    bank_account = request.form.get("bank_account", "").strip()

    if amount < min_payout:
        flash(f"Baki minimum untuk mohon bayaran ialah RM{min_payout:.2f}.", "error")
    elif not bank_name or not bank_account:
        flash("Sila isi nama bank dan nombor akaun.", "error")
    else:
        db.execute(
            "INSERT INTO payout_requests (dealer_id, amount, bank_name, bank_account, status) "
            "VALUES (?, ?, ?, ?, 'pending')",
            (dealer["id"], amount, bank_name, bank_account),
        )
        flash("Permohonan bayaran komisen dihantar. Admin akan proses tidak lama lagi.", "success")
    return redirect(url_for("dealer_dashboard"))


# ---------------------------------------------------------------------------
# Dealer public salespage — auto-updates because it reads live DB data
# ---------------------------------------------------------------------------

@app.route("/d/<slug>")
def dealer_salespage(slug):
    dealer = db.query_one("SELECT * FROM dealers WHERE slug = ? AND status = 'active'", (slug,))
    if not dealer:
        abort(404)
    packages = get_active_packages()
    promos = [p for p in packages if p["is_promo"]]
    referral_count = db.query_one(
        "SELECT COUNT(*) AS c FROM dealers WHERE referred_by_id = ?", (dealer["id"],)
    )["c"]
    return render_template(
        "dealer_salespage.html", dealer=dealer, packages=packages, promos=promos,
        referral_count=referral_count,
    )


# ---------------------------------------------------------------------------
# Admin auth
# ---------------------------------------------------------------------------

@app.route("/admin/log-masuk", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        admin = db.query_one("SELECT * FROM admins WHERE email = ?", (email,))
        if admin and auth.verify_password(password, admin["password_hash"]):
            auth.login_admin(admin)
            return redirect(url_for("admin_dashboard"))
        flash("E-mel atau kata laluan salah.", "error")
    return render_template("admin_login.html")


@app.route("/admin/log-keluar")
def admin_logout():
    auth.logout()
    return redirect(url_for("home"))


@app.route("/admin")
@auth.admin_required
def admin_dashboard():
    counts = {
        "dealers": db.query_one("SELECT COUNT(*) c FROM dealers")["c"],
        "active_dealers": db.query_one("SELECT COUNT(*) c FROM dealers WHERE status='active'")["c"],
        "packages": db.query_one("SELECT COUNT(*) c FROM packages WHERE is_active=1")["c"],
        "pending_commission": db.query_one(
            "SELECT COALESCE(SUM(amount),0) s FROM commissions WHERE status='pending'"
        )["s"],
        "approved_commission": db.query_one(
            "SELECT COALESCE(SUM(amount),0) s FROM commissions WHERE status='approved'"
        )["s"],
        "paid_commission": db.query_one(
            "SELECT COALESCE(SUM(amount),0) s FROM commissions WHERE status='paid'"
        )["s"],
        "pending_payouts": db.query_one(
            "SELECT COUNT(*) c FROM payout_requests WHERE status='pending'"
        )["c"],
    }
    recent_dealers = db.query("SELECT * FROM dealers ORDER BY created_at DESC LIMIT 8")
    top_referrers = db.query(
        "SELECT d.name, d.slug, COUNT(r.id) AS total FROM dealers d "
        "JOIN dealers r ON r.referred_by_id = d.id "
        "GROUP BY d.id ORDER BY total DESC LIMIT 5"
    )
    return render_template(
        "admin_dashboard.html", counts=counts, recent_dealers=recent_dealers, top_referrers=top_referrers
    )


# ---------------------------------------------------------------------------
# Admin — packages (this is what auto-propagates to every dealer salespage)
# ---------------------------------------------------------------------------

@app.route("/admin/pakej")
@auth.admin_required
def admin_packages():
    packages = db.query("SELECT * FROM packages ORDER BY sort_order ASC, id ASC")
    return render_template("admin_packages.html", packages=packages)


@app.route("/admin/pakej/baru", methods=["GET", "POST"])
@auth.admin_required
def admin_package_new():
    if request.method == "POST":
        _save_package(None)
        return redirect(url_for("admin_packages"))
    return render_template("admin_package_form.html", package=None)


@app.route("/admin/pakej/<int:package_id>/edit", methods=["GET", "POST"])
@auth.admin_required
def admin_package_edit(package_id):
    package = db.query_one("SELECT * FROM packages WHERE id = ?", (package_id,))
    if not package:
        abort(404)
    if request.method == "POST":
        _save_package(package_id)
        return redirect(url_for("admin_packages"))
    return render_template("admin_package_form.html", package=package)


def _save_package(package_id):
    title = request.form.get("title", "").strip()
    category = request.form.get("category", "prepaid")
    price = float(request.form.get("price") or 0)
    data_quota = request.form.get("data_quota", "").strip()
    validity = request.form.get("validity", "").strip()
    description = request.form.get("description", "").strip()
    is_promo = 1 if request.form.get("is_promo") == "on" else 0
    is_active = 1 if request.form.get("is_active") == "on" else 0
    sort_order = int(request.form.get("sort_order") or 0)

    if package_id:
        db.execute(
            "UPDATE packages SET title=?, category=?, price=?, data_quota=?, validity=?, "
            "description=?, is_promo=?, is_active=?, sort_order=?, updated_at=datetime('now') WHERE id=?",
            (title, category, price, data_quota, validity, description, is_promo, is_active, sort_order, package_id),
        )
        flash("Pakej dikemaskini — semua salespage dealer auto-update serta-merta.", "success")
    else:
        db.execute(
            "INSERT INTO packages (title, category, price, data_quota, validity, description, is_promo, is_active, sort_order) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (title, category, price, data_quota, validity, description, is_promo, is_active, sort_order),
        )
        flash("Pakej baru ditambah — terus muncul di semua salespage dealer.", "success")


@app.route("/admin/pakej/<int:package_id>/padam", methods=["POST"])
@auth.admin_required
def admin_package_delete(package_id):
    db.execute("DELETE FROM packages WHERE id = ?", (package_id,))
    flash("Pakej dipadam.", "success")
    return redirect(url_for("admin_packages"))


# ---------------------------------------------------------------------------
# Admin — dealers
# ---------------------------------------------------------------------------

@app.route("/admin/dealer")
@auth.admin_required
def admin_dealers():
    dealers = db.query(
        "SELECT d.*, r.name AS referred_by_name, "
        "(SELECT COUNT(*) FROM dealers x WHERE x.referred_by_id = d.id) AS referral_count "
        "FROM dealers d LEFT JOIN dealers r ON r.id = d.referred_by_id "
        "ORDER BY d.created_at DESC"
    )
    return render_template("admin_dealers.html", dealers=dealers)


@app.route("/admin/dealer/<int:dealer_id>/status", methods=["POST"])
@auth.admin_required
def admin_dealer_status(dealer_id):
    new_status = request.form.get("status")
    if new_status not in ("active", "pending", "suspended"):
        abort(400)
    db.execute("UPDATE dealers SET status = ? WHERE id = ?", (new_status, dealer_id))
    flash("Status dealer dikemaskini.", "success")
    return redirect(url_for("admin_dealers"))


@app.route("/admin/dealer/<int:dealer_id>/kadar", methods=["POST"])
@auth.admin_required
def admin_dealer_rate(dealer_id):
    rate = request.form.get("commission_rate", "").strip()
    db.execute(
        "UPDATE dealers SET commission_rate = ? WHERE id = ?",
        (float(rate) if rate else None, dealer_id),
    )
    flash("Kadar komisen dealer dikemaskini.", "success")
    return redirect(url_for("admin_dealers"))


# ---------------------------------------------------------------------------
# Admin — commissions & payouts
# ---------------------------------------------------------------------------

@app.route("/admin/komisen")
@auth.admin_required
def admin_commissions():
    commissions = db.query(
        "SELECT c.*, d.name AS dealer_name, d.slug AS dealer_slug, s.name AS source_name "
        "FROM commissions c "
        "JOIN dealers d ON d.id = c.dealer_id "
        "JOIN dealers s ON s.id = c.source_dealer_id "
        "ORDER BY c.created_at DESC"
    )
    return render_template("admin_commissions.html", commissions=commissions)


@app.route("/admin/komisen/<int:commission_id>/status", methods=["POST"])
@auth.admin_required
def admin_commission_status(commission_id):
    new_status = request.form.get("status")
    if new_status not in ("pending", "approved", "paid", "rejected"):
        abort(400)
    db.execute("UPDATE commissions SET status = ? WHERE id = ?", (new_status, commission_id))
    flash("Status komisen dikemaskini.", "success")
    return redirect(url_for("admin_commissions"))


@app.route("/admin/bayaran")
@auth.admin_required
def admin_payouts():
    payouts = db.query(
        "SELECT p.*, d.name AS dealer_name, d.slug AS dealer_slug FROM payout_requests p "
        "JOIN dealers d ON d.id = p.dealer_id ORDER BY p.created_at DESC"
    )
    return render_template("admin_payouts.html", payouts=payouts)


@app.route("/admin/bayaran/<int:payout_id>/proses", methods=["POST"])
@auth.admin_required
def admin_payout_process(payout_id):
    payout = db.query_one("SELECT * FROM payout_requests WHERE id = ?", (payout_id,))
    if not payout:
        abort(404)
    dealer = db.query_one("SELECT * FROM dealers WHERE id = ?", (payout["dealer_id"],))
    gateway = payments.get_gateway()
    result = gateway.send_payout(dealer, payout["amount"], payout["bank_name"], payout["bank_account"])

    if result.success:
        db.execute(
            "UPDATE payout_requests SET status='paid', provider_ref=?, note=?, processed_at=datetime('now') WHERE id=?",
            (result.provider_ref, result.message, payout_id),
        )
        # mark that dealer's approved commissions as paid, oldest first, up to the payout amount
        remaining = payout["amount"]
        approved = db.query(
            "SELECT * FROM commissions WHERE dealer_id = ? AND status='approved' ORDER BY created_at ASC",
            (dealer["id"],),
        )
        for c in approved:
            if remaining <= 0:
                break
            db.execute("UPDATE commissions SET status='paid' WHERE id=?", (c["id"],))
            remaining -= c["amount"]
        flash(f"Bayaran diproses. {result.message}", "success" if not result.test_mode else "warning")
    else:
        db.execute(
            "UPDATE payout_requests SET status='failed', note=? WHERE id=?",
            (result.message, payout_id),
        )
        flash(f"Bayaran gagal: {result.message}", "error")
    return redirect(url_for("admin_payouts"))


@app.route("/admin/bayaran/<int:payout_id>/lulus", methods=["POST"])
@auth.admin_required
def admin_payout_approve_commissions(payout_id):
    """Move this dealer's pending commissions to approved (making them eligible for payout)."""
    payout = db.query_one("SELECT * FROM payout_requests WHERE id = ?", (payout_id,))
    if not payout:
        abort(404)
    db.execute(
        "UPDATE commissions SET status='approved' WHERE dealer_id = ? AND status='pending'",
        (payout["dealer_id"],),
    )
    flash("Komisen dealer diluluskan.", "success")
    return redirect(url_for("admin_payouts"))


# ---------------------------------------------------------------------------
# Admin — settings (site-wide info that every page + salespage reads live)
# ---------------------------------------------------------------------------

@app.route("/admin/tetapan", methods=["GET", "POST"])
@auth.admin_required
def admin_settings():
    if request.method == "POST":
        for key in db.DEFAULT_SETTINGS.keys():
            if key in request.form:
                db.set_setting(key, request.form.get(key, "").strip())
        flash("Tetapan laman induk dikemaskini.", "success")
        return redirect(url_for("admin_settings"))
    return render_template("admin_settings.html", settings=db.get_all_settings())


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

@app.errorhandler(404)
def not_found(e):
    return render_template("404.html"), 404


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5050)), debug=True)
