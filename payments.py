"""
Payout gateway integration layer.

This module pays OUT commission to dealers (a payout/disbursement, not a checkout
that collects money from a customer). Real money movement needs a real provider
account — something Claude cannot create on your behalf. Two adapters are wired
up below so you can pick whichever fits your business:

  1. StripeConnectGateway   — good if your dealers are comfortable completing a
     Stripe Express onboarding (works internationally, needs STRIPE_SECRET_KEY).
  2. ToyyibPayGateway       — a Malaysian gateway; note ToyyibPay is primarily a
     bill/checkout collector, so for real dealer bank payouts in Malaysia you would
     more typically use your own bank's batch/API disbursement (Maybank, CIMB,
     DuitNow) or a payroll-style provider. The adapter here shows the request
     shape so you (or your developer) can swap in the real endpoint + credentials.

If no API key is configured, both adapters fall back to TEST MODE: the payout is
marked as "paid (test mode)" in the database with a fake provider reference, so
you can demo and test the full flow end-to-end without a real merchant account.
Nothing is ever actually transferred in test mode.

To go live:
  - Create a real account with your chosen provider and obtain API credentials.
  - Set the matching environment variable(s) before starting the app.
  - Set PAYOUT_PROVIDER to "stripe" or "toyibpay" (or leave unset for test mode).
"""
import os
import uuid
import requests

PAYOUT_PROVIDER = os.environ.get("PAYOUT_PROVIDER", "").lower()
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
TOYYIBPAY_SECRET_KEY = os.environ.get("TOYYIBPAY_SECRET_KEY", "")


class PayoutResult:
    def __init__(self, success, provider_ref, message, test_mode=False):
        self.success = success
        self.provider_ref = provider_ref
        self.message = message
        self.test_mode = test_mode


class PayoutGateway:
    """Common interface every adapter implements."""

    def send_payout(self, dealer, amount_myr, bank_name=None, bank_account=None):
        raise NotImplementedError


class StripeConnectGateway(PayoutGateway):
    """
    Real integration sketch for Stripe Connect transfers, using plain HTTP
    (no `stripe` SDK dependency required). Requires the dealer to already have
    a connected Stripe account id stored somewhere (not modelled in this schema
    yet — add a `stripe_account_id` column to `dealers` before going live).
    """

    API_URL = "https://api.stripe.com/v1/transfers"

    def send_payout(self, dealer, amount_myr, bank_name=None, bank_account=None):
        if not STRIPE_SECRET_KEY:
            return _test_mode_result(amount_myr)
        try:
            resp = requests.post(
                self.API_URL,
                auth=(STRIPE_SECRET_KEY, ""),
                data={
                    "amount": int(round(amount_myr * 100)),  # sen
                    "currency": "myr",
                    "destination": dealer.get("stripe_account_id", ""),
                    "description": f"Komisen rujukan - {dealer['name']} ({dealer['slug']})",
                },
                timeout=15,
            )
            data = resp.json()
            if resp.status_code == 200:
                return PayoutResult(True, data.get("id"), "Pemindahan Stripe berjaya.")
            return PayoutResult(False, None, data.get("error", {}).get("message", "Ralat Stripe."))
        except requests.RequestException as exc:
            return PayoutResult(False, None, f"Ralat sambungan Stripe: {exc}")


class ToyyibPayGateway(PayoutGateway):
    """
    Placeholder adapter. ToyyibPay's public API is designed for collecting
    payments (bills), not disbursing them, so `send_payout` here only shows the
    request shape — replace API_URL / payload with your actual disbursement
    provider (e.g. your bank's corporate API) before relying on this in
    production.
    """

    API_URL = "https://toyyibpay.com/index.php/api/disburse"  # placeholder — confirm real endpoint

    def send_payout(self, dealer, amount_myr, bank_name=None, bank_account=None):
        if not TOYYIBPAY_SECRET_KEY:
            return _test_mode_result(amount_myr)
        try:
            resp = requests.post(
                self.API_URL,
                data={
                    "userSecretKey": TOYYIBPAY_SECRET_KEY,
                    "amount": amount_myr,
                    "bankName": bank_name,
                    "bankAccount": bank_account,
                    "description": f"Komisen rujukan - {dealer['name']}",
                },
                timeout=15,
            )
            if resp.status_code == 200:
                return PayoutResult(True, str(uuid.uuid4()), "Diserahkan kepada ToyyibPay.")
            return PayoutResult(False, None, f"Ralat ToyyibPay: HTTP {resp.status_code}")
        except requests.RequestException as exc:
            return PayoutResult(False, None, f"Ralat sambungan ToyyibPay: {exc}")


def _test_mode_result(amount_myr):
    return PayoutResult(
        success=True,
        provider_ref=f"TEST-{uuid.uuid4().hex[:10].upper()}",
        message=(
            "Mod ujian: tiada gateway pembayaran sebenar dikonfigurasikan lagi "
            "(tetapkan STRIPE_SECRET_KEY atau TOYYIBPAY_SECRET_KEY). Bayaran ini "
            "ditanda 'dibayar' untuk demo sahaja — TIADA wang sebenar dipindahkan."
        ),
        test_mode=True,
    )


def get_gateway():
    if PAYOUT_PROVIDER == "stripe":
        return StripeConnectGateway()
    if PAYOUT_PROVIDER == "toyibpay":
        return ToyyibPayGateway()
    # Default: test-mode gateway (no provider configured)
    return _TestGateway()


class _TestGateway(PayoutGateway):
    def send_payout(self, dealer, amount_myr, bank_name=None, bank_account=None):
        return _test_mode_result(amount_myr)
