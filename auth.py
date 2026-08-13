"""Auth helpers for the two roles: admin (induk) and dealer."""
import functools
from flask import session, redirect, url_for, flash, g
from werkzeug.security import generate_password_hash, check_password_hash
import db


def hash_password(raw):
    return generate_password_hash(raw)


def verify_password(raw, hashed):
    return check_password_hash(hashed, raw)


def login_admin(admin):
    session.clear()
    session["role"] = "admin"
    session["user_id"] = admin["id"]


def login_dealer(dealer):
    session.clear()
    session["role"] = "dealer"
    session["user_id"] = dealer["id"]


def logout():
    session.clear()


def current_admin():
    if session.get("role") == "admin":
        return db.query_one("SELECT * FROM admins WHERE id = ?", (session["user_id"],))
    return None


def current_dealer():
    if session.get("role") == "dealer":
        return db.query_one("SELECT * FROM dealers WHERE id = ?", (session["user_id"],))
    return None


def admin_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        admin = current_admin()
        if not admin:
            flash("Sila log masuk sebagai admin.", "error")
            return redirect(url_for("admin_login"))
        g.admin = admin
        return view(*args, **kwargs)
    return wrapped


def dealer_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        dealer = current_dealer()
        if not dealer:
            flash("Sila log masuk sebagai dealer.", "error")
            return redirect(url_for("dealer_login"))
        g.dealer = dealer
        return view(*args, **kwargs)
    return wrapped
