"""
Salon booking server (faqat Python standart kutubxonasi — hech narsa o'rnatish shart emas).

Vazifalari:
  GET  /                -> index.html (Mini App)
  GET  /api/busy?date=  -> shu kundagi band vaqtlar
  POST /api/book        -> bronni bazaga yozadi va salon egasiga Telegramda xabar yuboradi

Muhit o'zgaruvchilari:
  BOT_TOKEN   — @BotFather bergan token
  ADMIN_IDS   — xabar oluvchilar Telegram ID lari, vergul bilan: 123456789,987654321
  WEBAPP_URL  — ilova havolasi (/start javobidagi tugma uchun)
  PORT        — (ixtiyoriy) standart 8000
  DB_PATH     — (ixtiyoriy) standart bookings.db
  ALLOW_NO_TELEGRAM=1 — faqat sinash uchun: Telegramsiz (oddiy brauzerdan) bron qilishga ruxsat
"""
import hashlib
import hmac
import json
import os
import sqlite3
import threading
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE = Path(__file__).parent
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ADMIN_IDS = [x.strip() for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()]
PORT = int(os.environ.get("PORT", "8000"))
DB_PATH = os.environ.get("DB_PATH", str(BASE / "bookings.db"))
ALLOW_NO_TELEGRAM = os.environ.get("ALLOW_NO_TELEGRAM") == "1"
TG_API = os.environ.get("TG_API", "https://api.telegram.org")
WEBAPP_URL = os.environ.get("WEBAPP_URL", "")  # masalan https://salon-app-asmm.onrender.com/

lock = threading.Lock()


def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    with db() as con:
        con.execute(
            """CREATE TABLE IF NOT EXISTS bookings(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                master TEXT NOT NULL,
                master_name TEXT,
                date TEXT NOT NULL,
                start INTEGER NOT NULL,
                dur INTEGER NOT NULL,
                services TEXT,
                total INTEGER,
                client_name TEXT,
                client_phone TEXT,
                tg_user_id TEXT,
                created TEXT DEFAULT CURRENT_TIMESTAMP
            )"""
        )


def verify_init_data(init_data: str):
    """Telegram Mini App initData imzosini tekshiradi. To'g'ri bo'lsa user dict qaytaradi."""
    if not init_data or not BOT_TOKEN:
        return None
    try:
        pairs = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
        got_hash = pairs.pop("hash", "")
        check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
        secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        calc = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calc, got_hash):
            return None
        return json.loads(pairs.get("user", "{}"))
    except Exception:
        return None


def tg_send(chat_id, text):
    if not BOT_TOKEN:
        print(f"[TG o'tkazib yuborildi: BOT_TOKEN yo'q] -> {chat_id}:\n{text}\n")
        return False
    data = json.dumps({"chat_id": chat_id, "text": text, "parse_mode": "HTML"}).encode()
    req = urllib.request.Request(
        f"{TG_API}/bot{BOT_TOKEN}/sendMessage", data=data, headers={"Content-Type": "application/json"}
    )
    try:
        urllib.request.urlopen(req, timeout=10).read()
        return True
    except Exception as e:
        print("Telegramga yuborib bo'lmadi:", chat_id, e)
        return False


def tg_call(method, payload, timeout=40):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{TG_API}/bot{BOT_TOKEN}/{method}", data=data, headers={"Content-Type": "application/json"}
    )
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


def poll_updates():
    """Botga yozilgan xabarlarni o'qiydi: /start bosilganda salom va ilova tugmasini yuboradi."""
    import time

    offset = 0
    while True:
        try:
            res = tg_call("getUpdates", {"offset": offset, "timeout": 25}, timeout=35)
            for u in res.get("result", []):
                offset = u["update_id"] + 1
                msg = u.get("message") or {}
                chat = (msg.get("chat") or {}).get("id")
                if not chat or not str(msg.get("text", "")).startswith("/start"):
                    continue
                payload = {
                    "chat_id": chat,
                    "text": "Assalomu alaykum! 💐\nNavbatga yozilish uchun pastdagi tugmani bosing.",
                }
                if WEBAPP_URL:
                    payload["reply_markup"] = {
                        "inline_keyboard": [[{"text": "Navbatga yozilish", "web_app": {"url": WEBAPP_URL}}]]
                    }
                tg_call("sendMessage", payload)
        except Exception as e:
            print("getUpdates xatosi:", e)
            time.sleep(5)


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def hhmm(m):
    return f"{m // 60:02d}:{m % 60:02d}"


def money(n):
    return f"{int(n):,}".replace(",", " ") + " so'm"


class H(BaseHTTPRequestHandler):
    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        if url.path in ("/", "/index.html"):
            body = (BASE / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif url.path == "/api/busy":
            date = urllib.parse.parse_qs(url.query).get("date", [""])[0]
            with db() as con:
                rows = con.execute("SELECT master, start, dur FROM bookings WHERE date=?", (date,)).fetchall()
            self._json(200, {"bookings": [{"m": r["master"], "start": r["start"], "dur": r["dur"]} for r in rows]})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/api/book":
            return self._json(404, {"error": "not found"})
        try:
            n = int(self.headers.get("Content-Length", "0"))
            p = json.loads(self.rfile.read(n))
            master = str(p["master"]["id"])
            master_name = str(p["master"]["name"])
            date = str(p["date"])
            h, m = str(p["time"]).split(":")
            start = int(h) * 60 + int(m)
            dur = int(p["duration_min"])
            name = str(p["client"]["name"]).strip()[:80]
            phone = str(p["client"]["phone"]).strip()[:20]
            services = ", ".join(str(s["name"]) for s in p["services"])[:300]
            total = int(p.get("total", 0))
            if not name or len(phone) < 9 or dur <= 0:
                raise ValueError("bad data")
        except Exception:
            return self._json(400, {"error": "bad_request"})

        user = verify_init_data(p.get("initData", ""))
        if user is None and not ALLOW_NO_TELEGRAM:
            return self._json(403, {"error": "telegram_only"})

        with lock:  # bir vaqtda ikki bron kelsa ham to'qnashuv bo'lmaydi
            with db() as con:
                clash = con.execute(
                    "SELECT 1 FROM bookings WHERE master=? AND date=? AND ? < start+dur AND ? > start",
                    (master, date, start, start + dur),
                ).fetchone()
                if clash:
                    return self._json(409, {"error": "busy"})
                cur = con.execute(
                    """INSERT INTO bookings(master,master_name,date,start,dur,services,total,client_name,client_phone,tg_user_id)
                       VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (master, master_name, date, start, dur, services, total, name, phone,
                     str(user["id"]) if user and "id" in user else None),
                )
                bid = cur.lastrowid

        admin_text = (
            f"🆕 <b>Yangi navbat #{bid}</b>\n\n"
            f"👤 {esc(name)}\n📞 {esc(phone)}\n"
            f"💇 {esc(services)}\n🧑‍🎨 Usta: {esc(master_name)}\n"
            f"📅 {esc(date)}, {hhmm(start)}–{hhmm(start + dur)}\n"
            f"💰 {money(total)}"
        )
        for aid in ADMIN_IDS:
            tg_send(aid, admin_text)

        if user and user.get("id"):
            tg_send(
                user["id"],
                f"✅ Navbatingiz qabul qilindi!\n\n📅 {esc(date)}, {hhmm(start)}\n"
                f"🧑‍🎨 Usta: {esc(master_name)}\n💇 {esc(services)}",
            )
        self._json(200, {"ok": True, "id": bid})

    def log_message(self, fmt, *args):
        print("%s - %s" % (self.address_string(), fmt % args))


if __name__ == "__main__":
    init_db()
    if not BOT_TOKEN:
        print("DIQQAT: BOT_TOKEN berilmagan — xabarlar Telegramga yuborilmaydi, faqat konsolga chiqadi.")
    if not ADMIN_IDS:
        print("DIQQAT: ADMIN_IDS berilmagan — salon egasiga xabar ketmaydi.")
    if BOT_TOKEN:
        threading.Thread(target=poll_updates, daemon=True).start()
    print(f"Server: http://0.0.0.0:{PORT}")
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
