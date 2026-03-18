import os
import subprocess
import logging
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
log = logging.getLogger(__name__)


def ensure_browser_deps():
    log.info("Installing system dependencies for Chromium...")
    apt = subprocess.run(
        ["apt-get", "install", "-y", "--no-install-recommends",
         "libglib2.0-0", "libnss3", "libnspr4", "libdbus-1-3",
         "libatk1.0-0", "libatk-bridge2.0-0", "libgio-2.0-dev",
         "libexpat1", "libatspi2.0-0", "libx11-6", "libxcomposite1",
         "libxdamage1", "libxext6", "libxfixes3", "libxrandr2",
         "libgbm1", "libdrm2", "libxcb1", "libxkbcommon0", "libasound2"],
        capture_output=True, text=True
    )
    if apt.returncode != 0:
        log.error(f"apt-get failed: {apt.stderr[-500:]}")
    else:
        log.info("System deps installed.")

    log.info("Installing Chromium via playwright...")
    pw = subprocess.run(
        ["playwright", "install", "chromium"],
        capture_output=True, text=True
    )
    if pw.returncode != 0:
        log.error(f"playwright install failed: {pw.stderr[-500:]}")
    else:
        log.info("Chromium installed.")


ensure_browser_deps()

app = Flask(__name__)
CORS(app)

COLES_EMAIL    = os.environ.get("COLES_EMAIL", "")
COLES_PASSWORD = os.environ.get("COLES_PASSWORD", "")
API_SECRET     = os.environ.get("API_SECRET", "change-me-please")


def lookup_product_name(barcode):
    log.info(f"Looking up barcode {barcode} on Open Food Facts...")
    try:
        url  = f"https://world.openfoodfacts.org/api/v0/product/{barcode}.json"
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
                log.info(f"Found: {name}")
                return name.strip()
    except Exception as e:
        log.warning(f"Open Food Facts lookup failed: {e}")
    return None


def add_to_coles_cart(search_term):
    result = {"success": False, "message": "", "product_added": None}

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--no-zygote",
                "--single-process",
            ]
        )
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        )
        page = context.new_page()

        try:
            log.info("Navigating to coles.com.au...")
            page.goto("https://www.coles.com.au", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2000)

            log.info("Logging in...")
            page.click("a[href*='sign-in'], button:has-text('Sign in')", timeout=10000)
            page.wait_for_timeout(1500)

            page.fill("input[type='email'], input[name='email'], #username", COLES_EMAIL)
            page.wait_for_timeout(500)
            page.fill("input[type='password'], input[name='password'], #password", COLES_PASSWORD)
            page.wait_for_timeout(500)
            page.click("button[type='submit'], button:has-text('Sign in'), button:has-text('Log in')")
            page.wait_for_timeout(3000)

            if "sign-in" in page.url.lower() or "login" in page.url.lower():
                result["message"] = "Login failed — check COLES_EMAIL and COLES_PASSWORD in Railway Variables."
                return result

            log.info(f"Searching for: {search_term}")
            search_box = page.wait_for_selector(
                "input[placeholder*='Search'], input[aria-label*='Search'], input[name='q']",
                timeout=10000
            )
            search_box.click()
            search_box.fill(search_term)
            search_box.press("Enter")
            page.wait_for_timeout(3000)

            add_button = page.wait_for_selector(
                "button:has-text('Add'), [data-testid='add-to-cart-button'], "
                ".product-tile button[aria-label*='Add']",
                timeout=10000
            )

            try:
                el = page.query_selector("h2.product-title, .product-name, [data-testid='product-name']")
                product_name = el.inner_text() if el else search_term
            except Exception:
                product_name = search_term

            add_button.click()
            page.wait_for_timeout(2000)

            log.info(f"Added to cart: {product_name}")
            result["success"]       = True
            result["message"]       = f"Added to cart: {product_name}"
            result["product_added"] = product_name

        except PlaywrightTimeout as e:
            result["message"] = f"Timed out on Coles website — it may have changed. Details: {e}"
            log.error(result["message"])
        except Exception as e:
            result["message"] = f"Unexpected error: {e}"
            log.error(result["message"])
        finally:
            browser.close()

    return result


@app.route("/scan", methods=["POST"])
def scan():
    try:
        data = request.get_json(silent=True) or {}
        if data.get("token") != API_SECRET:
            return jsonify({"success": False, "message": "Unauthorized"}), 401

        barcode = str(data.get("barcode", "")).strip()
        if not barcode:
            return jsonify({"success": False, "message": "No barcode provided"}), 400

        if not COLES_EMAIL or not COLES_PASSWORD:
            return jsonify({"success": False, "message": "Coles credentials not set in Railway Variables"}), 500

        log.info(f"=== Scan request: barcode={barcode} ===")
        product_name = lookup_product_name(barcode)
        search_term  = product_name if product_name else barcode
        cart_result  = add_to_coles_cart(search_term)

        return jsonify(cart_result), (200 if cart_result["success"] else 500)
    except Exception as e:
        log.error(f"Unhandled exception in /scan: {e}", exc_info=True)
        return jsonify({"success": False, "message": f"Server error: {str(e)}"}), 500


@app.route("/test", methods=["POST"])
def test():
    data = request.get_json(silent=True) or {}
    return jsonify({
        "request_received": True,
        "token_valid": data.get("token") == API_SECRET,
        "barcode_received": data.get("barcode", "none"),
        "coles_email_set": bool(COLES_EMAIL),
        "coles_password_set": bool(COLES_PASSWORD),
    }), 200


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
