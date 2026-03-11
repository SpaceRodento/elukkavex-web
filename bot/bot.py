"""
Elukkavex Telegram-botti
Siltataa MQTT <-> Telegram: push-ilmoitukset tilamuutoksista + ohjauspainikkeet.

Ympäristömuuttujat:
  TELEGRAM_TOKEN  - BotFather-token (pakollinen)
  CHAT_IDS        - pilkulla erotetut sallitut chat-ID:t (pakollinen)
  MQTT_BROKER     - oletuksena broker.hivemq.com
  MQTT_PORT       - oletuksena 1883
"""

import asyncio
import json
import logging
import os
import random
from datetime import datetime

import paho.mqtt.client as mqtt
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── Konfiguraatio ─────────────────────────────────────────────────────────────

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_IDS = set(int(x) for x in os.environ.get("CHAT_IDS", "").split(",") if x.strip())

MQTT_BROKER = os.environ.get("MQTT_BROKER", "broker.hivemq.com")
MQTT_PORT   = int(os.environ.get("MQTT_PORT", "1883"))

TOPIC_STATUS = "elukkavex/status"
TOPIC_ACK    = "elukkavex/ack"
TOPIC_CMD    = "elukkavex/cmd"
TOPIC_ERROR  = "elukkavex/error"

STATE_LABELS = {
    "CLOSED":    "🔵 Suljettu",
    "OPENING":   "🟡 Avataan\u2026",
    "LOCKING":   "🟡 Lukitaan\u2026",
    "RELEASING": "🟡 Vapautetaan\u2026",
    "LOCKED":    "🟢 Auki + lukittu",
    "CLOSING":   "🟡 Suljetaan\u2026",
    "ERROR":     "🔴 Virhe",
}

# Lähetetään ilmoitus vain näistä tilamuutoksista
NOTIFY_STATES = {"LOCKED", "CLOSED", "ERROR"}

# ── Tilanhallinta ─────────────────────────────────────────────────────────────

_last_state: str | None = None
_last_status: dict = {}
_app: Application | None = None
_loop: asyncio.AbstractEventLoop | None = None

# ── Näppäimistö ───────────────────────────────────────────────────────────────

def main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🟢 Avaa",  callback_data="open"),
            InlineKeyboardButton("🔵 Sulje", callback_data="close"),
        ],
        [InlineKeyboardButton("🔴 Hätäpysäytys", callback_data="stop")],
        [InlineKeyboardButton("ℹ️ Status",        callback_data="status")],
    ])

# ── Apufunktiot ───────────────────────────────────────────────────────────────

def fmt_uptime(ms: int) -> str:
    s = ms // 1000
    h, rem = divmod(s, 3600)
    m, s2  = divmod(rem, 60)
    if h:
        return f"{h}h {m}min"
    return f"{m}min {s2}s"

def build_status_text(d: dict) -> str:
    state  = d.get("state", "?").upper()
    label  = STATE_LABELS.get(state, state)
    sol    = d.get("solenoid", "—")
    mag    = "Kiinni" if d.get("magnet") else ("Auki" if d.get("magnet") is not None else "—")
    sig    = f"{d['signal']} dBm" if d.get("signal") is not None else "—"
    uptime = fmt_uptime(d["uptime"]) if d.get("uptime") is not None else "—"
    ip     = d.get("ip", "—")
    ts     = datetime.now().strftime("%H:%M:%S")
    return (
        f"*{label}*\n\n"
        f"Solenoidi: `{sol}`\n"
        f"Magneetti: `{mag}`\n"
        f"Signaali:  `{sig}`\n"
        f"Käyntiaika: `{uptime}`\n"
        f"IP: `{ip}`\n\n"
        f"⏱ {ts}"
    )

def auth(update: Update) -> bool:
    cid = update.effective_chat.id
    if CHAT_IDS and cid not in CHAT_IDS:
        log.warning("Luvaton käyttäjä: %s", cid)
        return False
    return True

async def broadcast(text: str, **kwargs) -> None:
    """Lähetä viesti kaikille sallituille chat-ID:lle."""
    if _app is None:
        return
    for cid in CHAT_IDS:
        try:
            await _app.bot.send_message(cid, text, parse_mode="Markdown", **kwargs)
        except Exception as e:
            log.error("broadcast %s: %s", cid, e)

def mqtt_publish(cmd: str) -> None:
    _mqttc.publish(TOPIC_CMD, cmd, qos=1)
    log.info("MQTT publish cmd: %s", cmd)

# ── Telegram-komennot ─────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not auth(update): return
    await update.message.reply_text(
        "🐾 *Elukkavex* — luukun ohjaus\n\nValitse toiminto:",
        parse_mode="Markdown",
        reply_markup=main_keyboard(),
    )

async def cmd_avaa(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not auth(update): return
    mqtt_publish("open")
    await update.message.reply_text("📤 Avataan luukku\u2026", reply_markup=main_keyboard())

async def cmd_sulje(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not auth(update): return
    mqtt_publish("close")
    await update.message.reply_text("📤 Suljetaan luukku\u2026", reply_markup=main_keyboard())

async def cmd_stop(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not auth(update): return
    mqtt_publish("stop")
    await update.message.reply_text("🛑 Hätäpysäytys lähetetty", reply_markup=main_keyboard())

async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not auth(update): return
    if _last_status:
        await update.message.reply_text(
            build_status_text(_last_status),
            parse_mode="Markdown",
            reply_markup=main_keyboard(),
        )
    else:
        mqtt_publish("status")
        await update.message.reply_text("⏳ Haetaan status\u2026", reply_markup=main_keyboard())

# ── Inline-nappi-käsittelijä ──────────────────────────────────────────────────

async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    if not auth(update): return

    data = q.data
    if data == "open":
        mqtt_publish("open")
        await q.edit_message_text("📤 Avataan luukku\u2026", reply_markup=main_keyboard())
    elif data == "close":
        mqtt_publish("close")
        await q.edit_message_text("📤 Suljetaan luukku\u2026", reply_markup=main_keyboard())
    elif data == "stop":
        mqtt_publish("stop")
        await q.edit_message_text("🛑 Hätäpysäytys lähetetty", reply_markup=main_keyboard())
    elif data == "status":
        if _last_status:
            await q.edit_message_text(
                build_status_text(_last_status),
                parse_mode="Markdown",
                reply_markup=main_keyboard(),
            )
        else:
            mqtt_publish("status")
            await q.edit_message_text("⏳ Haetaan status\u2026", reply_markup=main_keyboard())

# ── MQTT-käsittelijät ─────────────────────────────────────────────────────────

def on_mqtt_connect(client, userdata, flags, rc):
    if rc == 0:
        log.info("MQTT yhdistetty")
        client.subscribe([(TOPIC_STATUS, 0), (TOPIC_ACK, 0), (TOPIC_ERROR, 0)])
    else:
        log.error("MQTT yhdistysvirhe rc=%s", rc)

def on_mqtt_message(client, userdata, msg):
    """Paho-callback — ajetaan erillisessä säikeessä, joten käytä run_coroutine_threadsafe."""
    topic   = msg.topic
    payload = msg.payload.decode(errors="replace")
    asyncio.run_coroutine_threadsafe(_handle_mqtt(topic, payload), _loop)

async def _handle_mqtt(topic: str, payload: str) -> None:
    global _last_state, _last_status

    if topic == TOPIC_STATUS:
        try:
            d = json.loads(payload)
        except json.JSONDecodeError:
            return
        _last_status = d
        state = d.get("state", "").upper()

        # Push-ilmoitus vain tilamuutoksesta merkittäviin tiloihin
        if state != _last_state and state in NOTIFY_STATES:
            label = STATE_LABELS.get(state, state)
            await broadcast(
                build_status_text(d),
                reply_markup=main_keyboard(),
            )
        _last_state = state

    elif topic == TOPIC_ACK:
        await broadcast(f"✅ {payload}")

    elif topic == TOPIC_ERROR:
        await broadcast(f"⚠️ *Virhe:* {payload}", reply_markup=main_keyboard())

# ── Käynnistys ────────────────────────────────────────────────────────────────

_mqttc = mqtt.Client(client_id=f"elukkavex-bot-{random.randint(1000, 9999)}")
_mqttc.on_connect = on_mqtt_connect
_mqttc.on_message = on_mqtt_message

async def main() -> None:
    global _app, _loop
    _loop = asyncio.get_running_loop()

    _mqttc.connect_async(MQTT_BROKER, MQTT_PORT)
    _mqttc.loop_start()
    log.info("MQTT loop käynnistetty (%s:%s)", MQTT_BROKER, MQTT_PORT)

    _app = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .build()
    )
    _app.add_handler(CommandHandler("start",  cmd_start))
    _app.add_handler(CommandHandler("avaa",   cmd_avaa))
    _app.add_handler(CommandHandler("sulje",  cmd_sulje))
    _app.add_handler(CommandHandler("stop",   cmd_stop))
    _app.add_handler(CommandHandler("status", cmd_status))
    _app.add_handler(CallbackQueryHandler(handle_callback))

    log.info("Telegram-botti käynnistyy (polling)\u2026")
    await _app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    asyncio.run(main())
