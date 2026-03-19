import os
import logging
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
log = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

API_SECRET       = os.environ.get("API_SECRET", "change-me-please")
TELEGRAM_TOKEN   = "8756679947:AAGG1k89Cdoxj1vOW69GHR0iwx6W-VbiGzY"
TELEGRAM_CHAT_ID = "8655784613"


def send_telegram(message):
    url  = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    resp = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message}, timeout=10)
    if resp.status_code == 200:
        log.info("Telegram notification sent.")
    else:
        log.error(f"Telegram failed: {resp.text}")


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


@app.route("/scan", methods=["POST"])
def scan():
    try:
        data = request.get_json(silent=True) or {}
        if data.get("token") != API_SECRET:
            return jsonify({"success": False, "message": "Unauthorized"}), 401

        barcode = str(data.get("barcode", "")).strip()
        if not barcode:
            return jsonify({"success": False, "message": "No barcode provided"}), 400

        log.info(f"=== Scan request: barcode={barcode} ===")

        product_name = lookup_product_name(barcode)

        if product_name:
            message = f"Scanned: {product_name}\nBarcode: {barcode}"
            send_telegram(message)
            return jsonify({"success": True, "message": f"Found: {product_name}", "product_added": product_name})
        else:
            message = f"Scanned barcode {barcode} — product not found in database."
            send_telegram(message)
            return jsonify({"success": True, "message": f"Barcode {barcode} scanned (product not found in database)", "product_added": barcode})

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
    }), 200


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
