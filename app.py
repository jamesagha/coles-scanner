import os
import base64
import logging
import requests
from io import BytesIO
from PIL import Image, ImageEnhance, ImageOps, ExifTags
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
    # --- Source 1: Open Food Facts (global) ---
    log.info(f"Trying Open Food Facts for {barcode}...")
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
            if name and name.strip():
                log.info(f"Found on Open Food Facts: {name}")
                return name.strip()
    except Exception as e:
        log.warning(f"Open Food Facts failed: {e}")

    # --- Source 2: Open Food Facts Australia specifically ---
    log.info(f"Trying Open Food Facts AU for {barcode}...")
    try:
        url  = f"https://au.openfoodfacts.org/api/v0/product/{barcode}.json"
        resp = requests.get(url, timeout=8)
        data = resp.json()
        if data.get("status") == 1:
            product = data.get("product", {})
            name = (
                product.get("product_name_en")
                or product.get("product_name")
            )
            if name and name.strip():
                log.info(f"Found on Open Food Facts AU: {name}")
                return name.strip()
    except Exception as e:
        log.warning(f"Open Food Facts AU failed: {e}")

    # --- Source 3: Coles website search by barcode ---
    log.info(f"Trying Coles search for {barcode}...")
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
        }
        url  = f"https://www.coles.com.au/api/2.0/page/search/products?q={barcode}&pageSize=5"
        resp = requests.get(url, headers=headers, timeout=8)
        if resp.status_code == 200:
            data = resp.json()
            results = (
                data.get("searchResults", {}).get("results", [])
                or data.get("results", [])
                or data.get("products", [])
            )
            if results:
                name = (
                    results[0].get("name")
                    or results[0].get("displayName")
                    or results[0].get("productName")
                )
                if name and name.strip():
                    log.info(f"Found on Coles: {name}")
                    return name.strip()
    except Exception as e:
        log.warning(f"Coles search failed: {e}")

    # --- Source 4: UPC Item DB (free, no key needed) ---
    log.info(f"Trying UPC Item DB for {barcode}...")
    try:
        url  = f"https://api.upcitemdb.com/prod/trial/lookup?upc={barcode}"
        resp = requests.get(url, timeout=8)
        if resp.status_code == 200:
            data = resp.json()
            items = data.get("items", [])
            if items:
                name = items[0].get("title")
                if name and name.strip():
                    log.info(f"Found on UPC Item DB: {name}")
                    return name.strip()
    except Exception as e:
        log.warning(f"UPC Item DB failed: {e}")

    log.info(f"Product not found in any database for barcode {barcode}")
    return None


def fix_orientation(image):
    """Fix EXIF rotation so iOS photos aren't upside down or sideways."""
    try:
        exif = image._getexif()
        if exif:
            for tag, value in exif.items():
                if ExifTags.TAGS.get(tag) == 'Orientation':
                    if value == 3:
                        image = image.rotate(180, expand=True)
                    elif value == 6:
                        image = image.rotate(270, expand=True)
                    elif value == 8:
                        image = image.rotate(90, expand=True)
    except Exception:
        pass
    return image


def try_decode(image):
    """Try pyzbar on an image, return barcode string or None."""
    results = zbar_decode(image)
    if results:
        return results[0].data.decode("utf-8")
    return None


def preprocess_variants(image):
    """Return a list of image variants to try decoding, from most to least likely."""
    gray = ImageOps.grayscale(image)
    w, h = image.size
    variants = []

    # Original full-res grayscale
    variants.append(("full grayscale", gray))

    # Contrast boosts
    for contrast in [2.0, 3.0, 1.5]:
        variants.append((f"contrast {contrast}", ImageEnhance.Contrast(gray).enhance(contrast)))

    # Sharpened
    variants.append(("sharpened", ImageEnhance.Sharpness(gray).enhance(3.0)))

    # Scaled up 2x (helps with small barcodes)
    variants.append(("2x upscale", gray.resize((w * 2, h * 2), Image.LANCZOS)))

    # Scaled down (helps with very large photos)
    if w > 1200 or h > 1200:
        scale = 1200 / max(w, h)
        small = gray.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        variants.append(("scaled down", small))
        variants.append(("scaled down contrast", ImageEnhance.Contrast(small).enhance(2.5)))

    # Centre horizontal strip crops (barcode usually in middle of frame)
    for top_pct, bot_pct in [(0.25, 0.75), (0.1, 0.9), (0.35, 0.65)]:
        crop = gray.crop((0, int(h * top_pct), w, int(h * bot_pct)))
        variants.append((f"crop {top_pct}-{bot_pct}", crop))
        variants.append((f"crop {top_pct}-{bot_pct} contrast", ImageEnhance.Contrast(crop).enhance(2.5)))

    # Centre vertical strip (for portrait-oriented barcodes)
    for left_pct, right_pct in [(0.1, 0.9), (0.2, 0.8)]:
        crop = gray.crop((int(w * left_pct), 0, int(w * right_pct), h))
        variants.append((f"vcrop {left_pct}-{right_pct}", crop))

    # Rotations (barcode held sideways or upside down)
    for angle in [90, 270, 180]:
        rotated = gray.rotate(angle, expand=True)
        variants.append((f"rotated {angle}", rotated))
        variants.append((f"rotated {angle} contrast", ImageEnhance.Contrast(rotated).enhance(2.0)))

    # Binarize (pure black/white threshold)
    binarized = gray.point(lambda p: 255 if p > 128 else 0, '1').convert('L')
    variants.append(("binarized", binarized))

    return variants


def decode_barcode_from_image(image_b64):
    try:
        image_data = base64.b64decode(image_b64)
        image = Image.open(BytesIO(image_data))
        image = fix_orientation(image)
        image = image.convert("RGB")

        w, h = image.size
        log.info(f"Image size: {w}x{h}")

        # Try original colour image first
        result = try_decode(image)
        if result:
            log.info(f"Decoded from colour image: {result}")
            return result

        # Try all preprocessed variants
        for name, variant in preprocess_variants(image):
            result = try_decode(variant)
            if result:
                log.info(f"Decoded with variant '{name}': {result}")
                return result

        log.info("All decode attempts failed")
        return None

    except Exception as e:
        log.error(f"Image decode error: {e}")
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

        log.info("Decoding barcode from image...")
        barcode = decode_barcode_from_image(image_b64)

        if not barcode:
            return jsonify({
                "success": False,
                "message": "Could not read the barcode. Try: lay the item flat, fill the frame with just the barcode, ensure good lighting."
            })

        log.info(f"Barcode: {barcode}")
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
            return jsonify({"success": True, "message": f"Barcode {barcode} scanned (not found in database)", "product_added": barcode})

    except Exception as e:
        log.error(f"Error in /scan: {e}", exc_info=True)
        return jsonify({"success": False, "message": f"Server error: {str(e)}"}), 500


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
