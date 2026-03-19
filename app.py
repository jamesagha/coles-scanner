import os
import base64
import logging
import requests
from io import BytesIO
from PIL import Image
from pyzbar.pyzbar import decode as zbar_decode
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


def decode_barcode_from_image(image_b64):
    """Decode a barcode from a base64-encoded image using pyzbar."""
    try:
        image_data = base64.b64decode(image_b64)
        image = Image.open(BytesIO(image_data)).convert("RGB")

        # Try full image first
        barcodes = zbar_decode(image)
        if barcodes:
            return barcodes[0].data.decode("utf-8")

        # Try resizing to help with large phone photos
        w, h = image.size
        if w > 1200 or h > 1200:
            scale = 1200 / max(w, h)
            image = image.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
            barcodes = zbar_decode(image)
            if barcodes:
                return barcodes[0].data.decode("utf-8")

        # Try converting to grayscale and boosting contrast
        from PIL import ImageEnhance, ImageOps
        gray = ImageOps.grayscale(image)
        enhanced = ImageEnhance.Contrast(gray).enhance(2.0)
        barcodes = zbar_decode(enhanced)
        if barcodes:
            return barcodes[0].data.decode("utf-8")

        return None
    except Exception as e:
        log.error(f"Image decode error: {e}")
        return None


@app.route("/decode", methods=["POST"])
def decode():
    """Receive a photo, decode the barcode, look up product, send Telegram."""
    try:
        data = request.get_json(silent=True) or {}
        if data.get("token") != API_SECRET:
            return jsonify({"success": False, "message": "Unauthorized"}), 401

        image_b64 = data.get("image", "")
        if not image_b64:
            return jsonify({"success": False, "message": "No image provided"}), 400

        log.info("Decoding barcode from image...")
        barcode = decode_barcode_from_image(image_b64)

        if not barcode:
            return jsonify({"success": False, "message": "Could not find a barcode in that photo. Try better lighting or hold the barcode flatter."}), 200

        log.info(f"Barcode decoded: {barcode}")
        product_name = lookup_product_name(barcode)

        if product_name:
            send_telegram(f"Scanned: {product_name}\nBarcode: {barcode}")
            return jsonify({"success": True, "barcode": barcode, "product": product_name, "message": f"Found: {product_name}"})
        else:
            send_telegram(f"Scanned barcode {barcode} — product not found in database.")
            return jsonify({"success": True, "barcode": barcode, "product": None, "message": f"Barcode {barcode} scanned — not found in database"})

    except Exception as e:
        log.error(f"Error in /decode: {e}", exc_info=True)
        return jsonify({"success": False, "message": f"Server error: {str(e)}"}), 500


@app.route("/scan", methods=["POST"])
def scan():
    """Receive a barcode string directly (from Bluetooth scanner)."""
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
            send_telegram(f"Scanned barcode {barcode} — product not found in database.")
            return jsonify({"success": True, "message": f"Barcode {barcode} scanned (not found in database)", "product_added": barcode})

    except Exception as e:
        log.error(f"Unhandled exception in /scan: {e}", exc_info=True)
        return jsonify({"success": False, "message": f"Server error: {str(e)}"}), 500


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
