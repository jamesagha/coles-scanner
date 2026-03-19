import os
import base64
import logging
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
log = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

API_SECRET        = os.environ.get("API_SECRET", "change-me-please")
TELEGRAM_TOKEN    = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID  = os.environ.get("TELEGRAM_CHAT_ID", "")
GOOGLE_VISION_KEY = os.environ.get("GOOGLE_VISION_KEY", "")


def send_telegram(message):
    url  = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    resp = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message}, timeout=10)
    if resp.status_code == 200:
        log.info("Telegram notification sent.")
    else:
        log.error(f"Telegram failed: {resp.text}")


def lookup_product_name(barcode):
    # Source 1: Open Food Facts global
    log.info(f"Trying Open Food Facts for {barcode}...")
    try:
        resp = requests.get(
            f"https://world.openfoodfacts.org/api/v0/product/{barcode}.json", timeout=8)
        data = resp.json()
        if data.get("status") == 1:
            p = data.get("product", {})
            name = p.get("product_name_en") or p.get("product_name") or p.get("abbreviated_product_name")
            if name and name.strip():
                log.info(f"Found on OFF: {name}")
                return name.strip()
    except Exception as e:
        log.warning(f"OFF failed: {e}")

    # Source 2: Open Food Facts Australia
    log.info(f"Trying Open Food Facts AU for {barcode}...")
    try:
        resp = requests.get(
            f"https://au.openfoodfacts.org/api/v0/product/{barcode}.json", timeout=8)
        data = resp.json()
        if data.get("status") == 1:
            p = data.get("product", {})
            name = p.get("product_name_en") or p.get("product_name")
            if name and name.strip():
                log.info(f"Found on OFF AU: {name}")
                return name.strip()
    except Exception as e:
        log.warning(f"OFF AU failed: {e}")

    # Source 3: UPC Item DB
    log.info(f"Trying UPC Item DB for {barcode}...")
    try:
        resp = requests.get(
            f"https://api.upcitemdb.com/prod/trial/lookup?upc={barcode}", timeout=8)
        if resp.status_code == 200:
            items = resp.json().get("items", [])
            if items:
                name = items[0].get("title")
                if name and name.strip():
                    log.info(f"Found on UPC Item DB: {name}")
                    return name.strip()
    except Exception as e:
        log.warning(f"UPC Item DB failed: {e}")

    log.info(f"Product not found for barcode {barcode}")
    return None


def decode_barcode_from_image(image_b64):
    """Send image to Google Cloud Vision API to detect barcode."""
    try:
        url  = f"https://vision.googleapis.com/v1/images:annotate?key={GOOGLE_VISION_KEY}"
        body = {
            "requests": [{
                "image": {"content": image_b64},
                "features": [{"type": "BARCODE_DETECTION", "maxResults": 5}]
            }]
        }
        resp   = requests.post(url, json=body, timeout=15)
        result = resp.json()
        log.info(f"Vision API raw response: {result}")

        barcodes = result.get("responses", [{}])[0].get("barcodeAnnotations", [])
        if barcodes:
            value = barcodes[0].get("rawValue", "")
            if value:
                log.info(f"Google Vision decoded: {value}")
                return value

        log.info("Google Vision found no barcode")
        return None

    except Exception as e:
        log.error(f"Google Vision error: {e}")
        return None


@app.route("/decode", methods=["POST"])
def decode():
    try:
        data = request.get_json(silent=True) or {}
        if data.get("token") != API_SECRET:
            return jsonify({"success": False, "message": "Unauthorized"}), 401

        image_b64 = data.get("image", "")
        if not image_b64:
            return jsonify({"success": False, "message": "No image provided"}), 400

        log.info("Decoding barcode via Google Vision...")
        barcode = decode_barcode_from_image(image_b64)

        if not barcode:
            return jsonify({
                "success": False,
                "message": "Could not read barcode. Try: hold phone directly above, fill the frame with the barcode, good lighting."
            })

        log.info(f"Barcode: {barcode}")
        product_name = lookup_product_name(barcode)

        if product_name:
            send_telegram(f"Scanned: {product_name}\nBarcode: {barcode}")
            return jsonify({"success": True, "barcode": barcode, "product": product_name,
                            "message": f"Found: {product_name}"})
        else:
            send_telegram(f"Scanned barcode {barcode} — product not found in database.")
            return jsonify({"success": True, "barcode": barcode, "product": None,
                            "message": f"Barcode {barcode} scanned — not found in database"})

    except Exception as e:
        log.error(f"Error in /decode: {e}", exc_info=True)
        return jsonify({"success": False, "message": f"Server error: {str(e)}"}), 500


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
            send_telegram(f"Scanned: {product_name}\nBarcode: {barcode}")
            return jsonify({"success": True, "message": f"Found: {product_name}", "product_added": product_name})
        else:
            send_telegram(f"Scanned barcode {barcode} — product not in database.")
            return jsonify({"success": True, "message": f"Barcode {barcode} scanned (not found in database)",
                            "product_added": barcode})

    except Exception as e:
        log.error(f"Error in /scan: {e}", exc_info=True)
        return jsonify({"success": False, "message": f"Server error: {str(e)}"}), 500


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
