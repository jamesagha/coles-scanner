"""
Coles Barcode Scanner - Cloud Server
=====================================
Receives a barcode from your phone/scanner app,
looks up the product name, then adds it to your Coles cart.

Deploy this to Railway.app or Render.com (both free tiers available).
import os
import logging
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
import json
# ── Logging setup ──────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s"
)
log = logging.getLogger(__name__)
app = Flask(__name__)
CORS(app)
# ── Config (loaded from environment variables — set these in Railway/Render) ───
COLES_EMAIL    = os.environ.get("COLES_EMAIL", "")
COLES_PASSWORD = os.environ.get("COLES_PASSWORD", "")
API_SECRET     = os.environ.get("API_SECRET", "change-me-please")   # simple auth token
# ══════════════════════════════════════════════════════════════════════════════
# STEP 1 — Look up product name from barcode
def lookup_product_name(barcode: str) -> str | None:
    """
    Try Open Food Facts first (free, no key needed).
    If not found, fall back to searching Coles directly by barcode.
    Returns a product name string, or None if nothing found.
    # --- Attempt 1: Open Food Facts ---
    log.info(f"Looking up barcode {barcode} on Open Food Facts...")
    try:
        url = f"https://world.openfoodfacts.org/api/v0/product/{barcode}.json"
        resp = requests.get(url, timeout=8)
        data = resp.json()
        if data.get("status") == 1:
            product = data.get("product", {})
            name = (
                product.get("product_name_en")
                or product.get("product_name")
                or product.get("abbreviated_product_name")
            )
            if name:
                log.info(f"Found on Open Food Facts: {name}")
                return name.strip()
    except Exception as e:
        log.warning(f"Open Food Facts lookup failed: {e}")
    # --- Attempt 2: Search Coles by barcode number directly ---
    log.info(f"Barcode not found on Open Food Facts, will search Coles by barcode...")
    return None   # signals to the automation to search by raw barcode
# STEP 2 — Automate Coles website to add item to cart
def add_to_coles_cart(search_term: str) -> dict:
    Opens a headless browser, logs into coles.com.au, searches for the
    product, and clicks Add to Cart on the first result.
    Returns a dict with keys: success (bool), message (str), product_added (str|None)
    result = {"success": False, "message": "", "product_added": None}
    with sync_playwright() as pw:
        # Launch headless Chromium (no visible window needed on the server)
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()
        try:
            # ── 2a. Navigate to Coles ─────────────────────────────────────
            log.info("Navigating to coles.com.au...")
            page.goto("https://www.coles.com.au", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2000)
            # ── 2b. Log in ────────────────────────────────────────────────
            log.info("Logging in...")
            # Click Sign In button
            page.click("a[href*='sign-in'], button:has-text('Sign in')", timeout=10000)
            page.wait_for_timeout(1500)
            # Fill email
            page.fill("input[type='email'], input[name='email'], #username", COLES_EMAIL)
            page.wait_for_timeout(500)
            # Fill password
            page.fill("input[type='password'], input[name='password'], #password", COLES_PASSWORD)
            # Submit
            page.click("button[type='submit'], button:has-text('Sign in'), button:has-text('Log in')")
            page.wait_for_timeout(3000)
            # Check we're actually logged in
            if "sign-in" in page.url.lower() or "login" in page.url.lower():
                result["message"] = "Login failed — check your COLES_EMAIL and COLES_PASSWORD settings."
                return result
            log.info("Logged in successfully.")
            # ── 2c. Search for the product ────────────────────────────────
            log.info(f"Searching for: {search_term}")
            search_box = page.wait_for_selector(
                "input[placeholder*='Search'], input[aria-label*='Search'], input[name='q']",
                timeout=10000
            search_box.click()
            search_box.fill(search_term)
            search_box.press("Enter")
            # ── 2d. Find the first product and add to cart ─────────────────
            # Coles product tiles typically have an "Add" button
            add_button = page.wait_for_selector(
                "button:has-text('Add'), [data-testid='add-to-cart-button'], "
                ".product-tile button[aria-label*='Add']",
            # Grab the product name for confirmation
            try:
                product_name_el = page.query_selector(
                    "h2.product-title, .product-name, [data-testid='product-name']"
                )
                product_name = product_name_el.inner_text() if product_name_el else search_term
            except Exception:
                product_name = search_term
            add_button.click()
            log.info(f"Successfully added to cart: {product_name}")
            result["success"] = True
            result["message"] = f"Added to cart: {product_name}"
            result["product_added"] = product_name
        except PlaywrightTimeout as e:
            result["message"] = f"Timed out on Coles website — the page may have changed. Details: {e}"
            log.error(result["message"])
        except Exception as e:
            result["message"] = f"Unexpected error: {e}"
        finally:
            browser.close()
    return result
# API ENDPOINT — POST /scan
@app.route("/scan", methods=["POST"])
def scan():
    Expects JSON body:  { "barcode": "9310015513452", "token": "your-api-secret" }
    Returns JSON:       { "success": true, "message": "Added to cart: ...", "product_added": "..." }
    # ── Auth check ─────────────────────────────────────────────────────────────
    data = request.get_json(silent=True) or {}
    if data.get("token") != API_SECRET:
        return jsonify({"success": False, "message": "Unauthorized"}), 401
    barcode = str(data.get("barcode", "")).strip()
    if not barcode:
        return jsonify({"success": False, "message": "No barcode provided"}), 400
    if not COLES_EMAIL or not COLES_PASSWORD:
        return jsonify({"success": False, "message": "Coles credentials not configured on server"}), 500
    log.info(f"=== Scan request received: barcode={barcode} ===")
    # ── Product lookup ─────────────────────────────────────────────────────────
    product_name = lookup_product_name(barcode)
    # If Open Food Facts didn't find it, search Coles by the raw barcode number
    search_term = product_name if product_name else barcode
    # ── Add to cart ────────────────────────────────────────────────────────────
    cart_result = add_to_coles_cart(search_term)
    status_code = 200 if cart_result["success"] else 500
    return jsonify(cart_result), status_code
# ── Health check endpoint (useful for Railway/Render uptime monitoring) ────────
@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200
# ── Local dev entry point ──────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
