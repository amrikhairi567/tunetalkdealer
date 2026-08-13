# Tune Talk Dealer Referral Platform (Prototype)

Flask + SQLite web app. No build step needed — pure Python + server-rendered HTML.

## Run locally

```bash
pip install -r requirements.txt
python seed.py        # creates DB + demo admin/dealers/packages (safe to re-run)
python app.py         # dev server on http://localhost:5050
```

Admin login: `admin@tunetalk-dealer.test` / `admin123` — **change this immediately.**
Demo dealer login: `wan@tunetalk-dealer.test` / `dealer123`

## Structure

- `app.py` — all routes
- `db.py` — SQLite helpers + site settings
- `auth.py` — password hashing + session-based login for admin/dealer roles
- `payments.py` — payout gateway adapter (test-mode by default; see file docstring to go live)
- `schema.sql` — database tables
- `seed.py` — demo data, adapted from simkadtunetalk.com content
- `templates/`, `static/` — Jinja2 views + CSS (no external CDN dependency)

## Production

Set real environment variables before deploying: `SECRET_KEY`, `DATABASE_PATH` (persistent disk),
and optionally `PAYOUT_PROVIDER` + `STRIPE_SECRET_KEY` / `TOYYIBPAY_SECRET_KEY` for real payouts.
Run with `gunicorn app:app` (see `Procfile`). Full deployment walkthrough is in the separate guide
delivered alongside this code.
