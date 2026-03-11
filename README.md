# Elukkavex — Web-dashboard ja Telegram-botti

ESP32-luukun etäohjaus. Kaksi komponenttia:

| Komponentti | Sijainti | Kuvaus |
|-------------|----------|--------|
| **Web-dashboard** | `index.html` | GitHub Pages, MQTT via WebSocket |
| **Telegram-botti** | `bot/` | Push-ilmoitukset + ohjauspainikkeet |

**Live-dashboard:** https://spacerodento.github.io/elukkavex-web/

---

## Web-dashboard

Avataan selaimessa — ei asennuksia. Toimii myös PWA-sovelluksena (lisää kotinäytölle).

Yhdistyy `broker.hivemq.com:8884` (WSS) ja tilaa `elukkavex/status`.

---

## Telegram-botti

### 1. Luo botti BotFatherilla

1. Avaa [@BotFather](https://t.me/BotFather) Telegramissa
2. `/newbot` → anna nimi ja käyttäjänimi
3. Kopioi token

### 2. Hae oma chat-ID

Lähetä botille viesti, avaa selaimessa:
```
https://api.telegram.org/bot<TOKEN>/getUpdates
```
Etsi `"chat":{"id": 123456789}` — tämä on CHAT_IDS-arvo.

### 3. Aja paikallisesti

```bash
cd bot
pip install -r requirements.txt
cp .env.example .env
# Muokkaa .env: lisää TELEGRAM_TOKEN ja CHAT_IDS
export $(cat .env | xargs) && python bot.py
```

### 4. Deploy Render.com (ilmainen)

1. Luo tili [render.com](https://render.com)
2. New → Blueprint → yhdistä tämä repo
3. Render löytää `render.yaml` automaattisesti
4. Aseta ympäristömuuttujat (`TELEGRAM_TOKEN`, `CHAT_IDS`) Renderin dashboardissa
5. Deploy

Render Worker-palvelu pyörii jatkuvasti — ei sammu kuten web-palvelut.

### Komennot

| Komento | Toiminto |
|---------|----------|
| `/start` | Näytä ohjausnapit |
| `/avaa` | Avaa luukku |
| `/sulje` | Sulje luukku |
| `/stop` | Hätäpysäytys |
| `/status` | Näytä viimeisin tila |

Push-ilmoitukset lähetetään automaattisesti kun luukku siirtyy tilaan **Auki+lukittu**, **Suljettu** tai **Virhe**.

---

## MQTT-topicit

| Topic | Suunta | Kuvaus |
|-------|--------|--------|
| `elukkavex/status` | ESP32 → | JSON-tilapäivitys |
| `elukkavex/cmd` | → ESP32 | Komento: `open`, `close`, `stop`, `status` |
| `elukkavex/ack` | ESP32 → | Kuittaus komennosta |
| `elukkavex/error` | ESP32 → | Virheviesti |
