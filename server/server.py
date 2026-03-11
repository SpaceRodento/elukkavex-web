"""
Elukkavex — Raspberry Pi -kuvapalvelin

Vastaanottaa JPEG-kuvia ESP32:lta (HTTP POST), tallentaa levylle
ja lähettää automaattisesti Telegram-chattiin.

Endpointit:
  POST /upload            — ESP32 lähettää kuvan (Content-Type: image/jpeg)
  GET  /image/latest      — viimeisin kuva (web-dashboard käyttää)
  GET  /image/<tiedosto>  — yksittäinen kuva tiedostonimellä
  GET  /images            — lista 50 viimeisimmästä kuvasta (JSON)
  GET  /health            — palvelimen tila

Ympäristömuuttujat:
  TELEGRAM_TOKEN   — BotFather-token (pakollinen)
  CHAT_IDS         — pilkulla erotetut sallitut chat-ID:t (pakollinen)
  UPLOAD_TOKEN     — ESP32:n autentikointitunnus X-Token-headerissa (suositeltava)
  IMAGE_DIR        — kuvakansiopolku (oletus: ~/elukkavex/images)
  PORT             — HTTP-portti (oletus: 8080)
"""

import datetime
import logging
import os
import threading

import requests
from flask import Flask, jsonify, request, send_file

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── Konfiguraatio ─────────────────────────────────────────────────────────────

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_IDS       = [int(x) for x in os.environ.get("CHAT_IDS", "").split(",") if x.strip()]
UPLOAD_TOKEN   = os.environ.get("UPLOAD_TOKEN", "")
IMAGE_DIR      = os.path.expanduser(os.environ.get("IMAGE_DIR", "~/elukkavex/images"))
PORT           = int(os.environ.get("PORT", "8080"))

os.makedirs(IMAGE_DIR, exist_ok=True)

app = Flask(__name__)

# ── Apufunktiot ───────────────────────────────────────────────────────────────

def latest_path() -> str:
    return os.path.join(IMAGE_DIR, "latest.jpg")

def _send_telegram_photo(data: bytes, caption: str) -> None:
    """Lähetä kuva Telegram-chattiin suoraan Bot API:n kautta."""
    if not TELEGRAM_TOKEN or not CHAT_IDS:
        log.warning("Telegram ei konfiguroitu — kuva tallennettu vain levylle")
        return
    for cid in CHAT_IDS:
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto",
                files={"photo": ("kuva.jpg", data, "image/jpeg")},
                data={"chat_id": cid, "caption": f"📸 {caption}"},
                timeout=20,
            )
            if r.ok:
                log.info("Telegram kuva lähetetty → chat %s", cid)
            else:
                log.warning("Telegram virhe %s: %s", cid, r.text[:120])
        except Exception as e:
            log.error("Telegram lähetys epäonnistui (%s): %s", cid, e)

def _notify_async(data: bytes, caption: str) -> None:
    """Käynnistä Telegram-lähetys taustaketjussa, jotta HTTP-vastaus ei viivästy."""
    threading.Thread(target=_send_telegram_photo, args=(data, caption), daemon=True).start()

# ── Endpointit ────────────────────────────────────────────────────────────────

@app.route("/upload", methods=["POST"])
def upload():
    # Autentikointi
    if UPLOAD_TOKEN:
        token = request.headers.get("X-Token", "")
        if token != UPLOAD_TOKEN:
            log.warning("Luvaton upload-yritys (väärä token)")
            return jsonify({"error": "unauthorized"}), 401

    data = request.get_data()
    if len(data) < 100:
        return jsonify({"error": "liian lyhyt payload"}), 400

    # Tarkista JPEG-magic (FF D8 FF)
    if not (data[0] == 0xFF and data[1] == 0xD8 and data[2] == 0xFF):
        return jsonify({"error": "ei JPEG-data"}), 400

    ts       = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{ts}.jpg"
    filepath = os.path.join(IMAGE_DIR, filename)

    with open(filepath, "wb") as f:
        f.write(data)
    log.info("Kuva tallennettu: %s (%d B)", filename, len(data))

    # Päivitä latest-symlink
    lp = latest_path()
    if os.path.lexists(lp):
        os.remove(lp)
    os.symlink(filepath, lp)

    # Lähetä Telegramiin taustaketjussa
    caption = f"Elukkavex — {ts[:4]}-{ts[4:6]}-{ts[6:8]} {ts[9:11]}:{ts[11:13]}:{ts[13:15]}"
    _notify_async(data, caption)

    return jsonify({"ok": True, "filename": filename, "size": len(data)})


@app.route("/image/latest")
def image_latest():
    lp = latest_path()
    if not os.path.exists(lp):
        return jsonify({"error": "ei kuvaa"}), 404
    return send_file(lp, mimetype="image/jpeg")


@app.route("/image/<filename>")
def image_get(filename):
    # Estä path traversal
    safe = os.path.basename(filename)
    path = os.path.join(IMAGE_DIR, safe)
    if not os.path.exists(path) or not safe.endswith(".jpg"):
        return jsonify({"error": "ei löydy"}), 404
    return send_file(path, mimetype="image/jpeg")


@app.route("/images")
def images_list():
    files = sorted(
        [f for f in os.listdir(IMAGE_DIR) if f.endswith(".jpg") and f != "latest.jpg"],
        reverse=True,
    )
    return jsonify({"images": files[:50], "total": len(files)})


@app.route("/health")
def health():
    files = [f for f in os.listdir(IMAGE_DIR) if f.endswith(".jpg") and f != "latest.jpg"]
    latest = os.path.exists(latest_path())
    return jsonify({
        "ok":        True,
        "image_count": len(files),
        "latest":    latest,
        "image_dir": IMAGE_DIR,
    })


# ── Käynnistys ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    log.info("Elukkavex kuvapalvelin käynnistyy portissa %d", PORT)
    log.info("Kuvakansio: %s", IMAGE_DIR)
    log.info("Telegram: %s", "konfiguroitu" if TELEGRAM_TOKEN else "EI KONFIGUROITU")
    log.info("Upload-token: %s", "asetettu" if UPLOAD_TOKEN else "ei käytössä")
    app.run(host="0.0.0.0", port=PORT)
