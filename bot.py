import os
import sqlite3
import logging
from datetime import datetime
from fastapi import FastAPI, Request
import google.generativeai as genai
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ─────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_API_KEY  = os.environ.get("GEMINI_API_KEY")
ADMIN_IDS       = os.environ.get("ADMIN_IDS", "")
RENDER_URL      = os.environ.get("RENDER_URL", "")   # ለምሳሌ https://roha-bot.onrender.com
DB_PATH         = os.environ.get("DB_PATH", "roha.db")
PORT            = int(os.environ.get("PORT", 8000))

if not TELEGRAM_TOKEN or not GEMINI_API_KEY:
    raise RuntimeError("TELEGRAM_TOKEN and GEMINI_API_KEY must be set")

ADMIN_LIST = [int(x.strip()) for x in ADMIN_IDS.split(",") if x.strip()]

# Gemini setup
genai.configure(api_key=GEMINI_API_KEY)
gemini_model = genai.GenerativeModel("gemini-2.0-flash")

# ─────────────────────────────────────────────
# Database
# ─────────────────────────────────────────────
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS shops (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_name     TEXT NOT NULL,
            category      TEXT,
            working_hours TEXT,
            delivery_info TEXT,
            payment       TEXT,
            location      TEXT,
            price_list    TEXT,
            extra_info    TEXT,
            created_at    TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id    INTEGER NOT NULL,
            shop_id    INTEGER NOT NULL DEFAULT 1,
            role       TEXT NOT NULL,
            content    TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id       INTEGER NOT NULL,
            shop_id       INTEGER NOT NULL DEFAULT 1,
            product       TEXT,
            status        TEXT DEFAULT 'pending',
            created_at    TEXT NOT NULL
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            chat_id    INTEGER PRIMARY KEY,
            username   TEXT,
            full_name  TEXT,
            shop_id    INTEGER DEFAULT 1,
            joined_at  TEXT
        )
    """)

    c.execute("SELECT COUNT(*) FROM shops")
    if c.fetchone()[0] == 0:
        c.execute("""
            INSERT INTO shops
              (shop_name, category, working_hours, delivery_info, payment, location, price_list, extra_info, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "ለምለም ቡቲክ",
            "የሴቶች ልብስ እና accessories",
            "ሰኞ-ቅዳሜ ጠዋት 2:30 - ምሽት 12:00",
            "በአዲስ አበባ ውስጥ 24 ሰዓት ማድረሻ | ዋጋ ከ200 ብር ይጀምራል",
            "ቴሌብር | CBE Birr | ባንክ ትራንስፈር",
            "ቦሌ፣ አዲስ አበባ",
            "ቀሚስ: 1200-2500 ብር | ጃኬት: 1800-3000 ብር | ጫማ: 1500-2200 ብር | ቦርሳ: 800-1500 ብር",
            "ሁሉም ምርቶቻችን ከፍተኛ ጥራት ያላቸው ናቸው። ምርቱ ካልተወደደ 3 ቀን ውስጥ መልስ ይቀበላል።",
            datetime.utcnow().isoformat(),
        ))

    conn.commit()
    conn.close()


def get_shop(shop_id: int = 1) -> dict:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT * FROM shops WHERE id = ?", (shop_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        return {}
    keys = ["id","shop_name","category","working_hours","delivery_info",
            "payment","location","price_list","extra_info","created_at"]
    return dict(zip(keys, row))


def save_message(chat_id: int, role: str, content: str, shop_id: int = 1):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "INSERT INTO messages (chat_id, shop_id, role, content, created_at) VALUES (?,?,?,?,?)",
        (chat_id, shop_id, role, content, datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()


def get_history(chat_id: int, limit: int = 8) -> list:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "SELECT role, content FROM messages WHERE chat_id=? ORDER BY id DESC LIMIT ?",
        (chat_id, limit),
    )
    rows = c.fetchall()
    conn.close()
    rows.reverse()
    return [{"role": r, "content": txt} for r, txt in rows]


def save_user(chat_id: int, username: str, full_name: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        INSERT OR IGNORE INTO users (chat_id, username, full_name, joined_at)
        VALUES (?, ?, ?, ?)
    """, (chat_id, username, full_name, datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()


def save_order(chat_id: int, details: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "INSERT INTO orders (chat_id, product, status, created_at) VALUES (?,?,?,?)",
        (chat_id, details, "pending", datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()


def get_stats() -> dict:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT COUNT(DISTINCT chat_id) FROM messages")
    total_users = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM orders")
    total_orders = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM orders WHERE status='pending'")
    pending_orders = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM messages")
    total_messages = c.fetchone()[0]
    conn.close()
    return {
        "total_users": total_users,
        "total_orders": total_orders,
        "pending_orders": pending_orders,
        "total_messages": total_messages,
    }


# ─────────────────────────────────────────────
# System Prompt
# ─────────────────────────────────────────────
def build_system_prompt(shop: dict) -> str:
    return f"""አንተ Roha Bot ነህ - የ{shop['shop_name']} ኦፊሴላዊ AI ረዳት።

የሱቁ መረጃ፡
- ስም: {shop['shop_name']}
- ምርት: {shop['category']}
- የስራ ሰዓት: {shop['working_hours']}
- ማድረሻ: {shop['delivery_info']}
- ክፍያ: {shop['payment']}
- አድራሻ: {shop['location']}
- ዋጋ: {shop['price_list']}
- ተጨማሪ: {shop.get('extra_info', '')}

ህጎች፡
1. ደንበኛ በተናገረበት ቋንቋ (አማርኛ ወይም እንግሊዝኛ) መልስ ስጥ
2. መልስ አጭር፣ ግልጽ እና ጨዋ ይሁን (ከ4 አረፍተ ነገር አይብለጥ)
3. የማታውቀውን ነገር "ለባለቤቱ አጣራለሁ" በል
4. ትዕዛዝ ሲደረግ ስም፣ ምርት፣ ብዛት፣ አድራሻ ጠይቅ
5. ትንሽ emoji ጨምር (🙏 ✅ 📦 💛)
"""


# ─────────────────────────────────────────────
# Gemini AI
# ─────────────────────────────────────────────
def ask_gemini(chat_id: int, user_message: str, shop: dict) -> str:
    try:
        history = get_history(chat_id, limit=8)
        system_prompt = build_system_prompt(shop)
        chat_history = []
        for msg in history:
            role = "user" if msg["role"] == "user" else "model"
            chat_history.append({"role": role, "parts": [msg["content"]]})
        chat = gemini_model.start_chat(history=chat_history)
        full_message = f"{system_prompt}\n\nደንበኛ: {user_message}"
        response = chat.send_message(full_message)
        return response.text.strip()
    except Exception as e:
        logger.error(f"Gemini error: {e}")
        return "ይቅርታ፣ ትንሽ ችግር ገጥሞኛል። እባክዎ ትንሽ ቆይተው ይሞክሩ 🙏"


# ─────────────────────────────────────────────
# Keyboards
# ─────────────────────────────────────────────
def main_keyboard():
    keyboard = [
        [
            InlineKeyboardButton("📦 ምርቶች", callback_data="products"),
            InlineKeyboardButton("💰 ዋጋ", callback_data="prices"),
        ],
        [
            InlineKeyboardButton("🚚 ማድረሻ", callback_data="delivery"),
            InlineKeyboardButton("💳 ክፍያ", callback_data="payment"),
        ],
        [
            InlineKeyboardButton("🛒 ትዕዛዝ", callback_data="order"),
            InlineKeyboardButton("📍 አድራሻ", callback_data="location"),
        ],
        [
            InlineKeyboardButton("⏰ ሰዓት", callback_data="hours"),
            InlineKeyboardButton("📞 ያግኙን", callback_data="contact"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def admin_keyboard():
    keyboard = [
        [
            InlineKeyboardButton("📊 Statistics", callback_data="admin_stats"),
            InlineKeyboardButton("📋 Orders", callback_data="admin_orders"),
        ],
        [
            InlineKeyboardButton("✏️ Shop Info አስተካክል", callback_data="admin_edit"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


# ─────────────────────────────────────────────
# Handlers
# ─────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id
    shop = get_shop(1)
    save_user(chat_id, user.username or "", user.full_name or "")
    welcome = (
        f"ሰላም {user.first_name}! 👋\n\n"
        f"እንኳን ወደ *{shop['shop_name']}* በደህና መጡ 🎉\n\n"
        f"እኔ Roha Bot ነኝ - ስለ ምርቶቻችን፣ ዋጋ፣ ትዕዛዝ ልርዳዎ ነኝ 🙏\n\n"
        f"ከታች ካሉት አማራጮች ምረጡ ወይም ቀጥታ ጥያቄዎን ፃፉ 👇"
    )
    await update.message.reply_text(
        welcome,
        parse_mode="Markdown",
        reply_markup=main_keyboard(),
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_text = update.message.text
    shop = get_shop(1)

    order_keywords = ["ልዝዝ", "order", "ትዕዛዝ", "እዝዛለሁ", "ልግዛ"]
    is_order = any(kw in user_text.lower() for kw in order_keywords)

    save_message(chat_id, "user", user_text)
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    reply = ask_gemini(chat_id, user_text, shop)
    save_message(chat_id, "assistant", reply)

    if is_order:
        save_order(chat_id, user_text)

    await update.message.reply_text(reply, reply_markup=main_keyboard())


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    shop = get_shop(1)
    data = query.data

    responses = {
        "products": f"📦 *ምርቶቻችን*\n\n{shop['category']}\n\nምርት ስም ጽፈው ዋጋ ይጠይቁ 👇",
        "prices":   f"💰 *ዋጋ ዝርዝር*\n\n{shop['price_list']}",
        "delivery": f"🚚 *ማድረሻ*\n\n{shop['delivery_info']}",
        "payment":  f"💳 *ክፍያ*\n\n{shop['payment']}",
        "location": f"📍 *አድራሻ*\n\n{shop['location']}",
        "hours":    f"⏰ *የስራ ሰዓት*\n\n{shop['working_hours']}",
        "contact":  f"📞 *ያግኙን*\n\nይህ bot ጋር ይፃፉ ወይም admin ያነጋግሩ 🙏",
        "order": (
            "🛒 *ትዕዛዝ ለማድረግ*\n\n"
            "እባክዎ ይህን ፎርም ሙሉ 👇\n\n"
            "1️⃣ ስምዎ\n"
            "2️⃣ ምርት\n"
            "3️⃣ ብዛት\n"
            "4️⃣ አድራሻ\n\n"
            "ምሳሌ: አቤ | ቀሚስ ቀይ | 1 | ቦሌ"
        ),
    }

    if data == "admin_stats":
        stats = get_stats()
        text = (
            f"📊 *Statistics*\n\n"
            f"👥 ደንበኞች: {stats['total_users']}\n"
            f"📦 ትዕዛዞች: {stats['total_orders']}\n"
            f"⏳ Pending: {stats['pending_orders']}\n"
            f"💬 Messages: {stats['total_messages']}"
        )
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=admin_keyboard())
        return

    if data == "admin_orders":
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT chat_id, product, status, created_at FROM orders ORDER BY id DESC LIMIT 10")
        rows = c.fetchall()
        conn.close()
        if not rows:
            text = "📋 ምንም ትዕዛዝ እስካሁን የለም"
        else:
            lines = ["📋 *ትዕዛዞች*\n"]
            for r in rows:
                lines.append(f"• {r[0]} | {str(r[1])[:25]} | {r[2]} | {str(r[3])[:10]}")
            text = "\n".join(lines)
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=admin_keyboard())
        return

    if data == "admin_edit":
        text = (
            "✏️ *Shop Info ለማስተካከል*\n\n"
            "/setname [ስም]\n"
            "/setprice [ዋጋ]\n"
            "/setdelivery [ማድረሻ]\n"
            "/setpayment [ክፍያ]\n"
            "/setlocation [አድራሻ]\n"
            "/sethours [ሰዓት]"
        )
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=admin_keyboard())
        return

    if data in responses:
        await query.edit_message_text(
            responses[data],
            parse_mode="Markdown",
            reply_markup=main_keyboard(),
        )


# ─────────────────────────────────────────────
# Admin Commands
# ─────────────────────────────────────────────
def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_LIST


async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Admin ብቻ ነው።")
        return
    stats = get_stats()
    shop = get_shop(1)
    text = (
        f"👨‍💼 *Admin Panel — {shop['shop_name']}*\n\n"
        f"👥 ደንበኞች: {stats['total_users']}\n"
        f"📦 ትዕዛዞች: {stats['total_orders']}\n"
        f"⏳ Pending: {stats['pending_orders']}\n"
        f"💬 Messages: {stats['total_messages']}"
    )
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=admin_keyboard())


async def set_shop_field(update: Update, context: ContextTypes.DEFAULT_TYPE, field: str):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Admin ብቻ ነው።")
        return
    if not context.args:
        await update.message.reply_text(f"ምሳሌ: /{field} [እዚህ ጻፍ]")
        return
    value = " ".join(context.args)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(f"UPDATE shops SET {field}=? WHERE id=1", (value,))
    conn.commit()
    conn.close()
    await update.message.reply_text(f"✅ ተዘምኗል!")


async def cmd_setname(u, c):     await set_shop_field(u, c, "shop_name")
async def cmd_setprice(u, c):    await set_shop_field(u, c, "price_list")
async def cmd_setdelivery(u, c): await set_shop_field(u, c, "delivery_info")
async def cmd_setpayment(u, c):  await set_shop_field(u, c, "payment")
async def cmd_setlocation(u, c): await set_shop_field(u, c, "location")
async def cmd_sethours(u, c):    await set_shop_field(u, c, "working_hours")


async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Admin ብቻ ነው።")
        return
    if not context.args:
        await update.message.reply_text("ምሳሌ: /broadcast [መልዕክት]")
        return
    message = " ".join(context.args)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT DISTINCT chat_id FROM users")
    users = c.fetchall()
    conn.close()
    sent, failed = 0, 0
    for (chat_id,) in users:
        try:
            await context.bot.send_message(chat_id=chat_id, text=f"📢 {message}")
            sent += 1
        except Exception:
            failed += 1
    await update.message.reply_text(f"✅ ተልኳል: {sent} | ❌ ያልተሳካ: {failed}")


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🤖 *Roha Bot — Help*\n\n"
        "/start — ቦቱን ጀምር\n"
        "/admin — Admin panel\n"
        "/setname — ሱቅ ስም\n"
        "/setprice — ዋጋ\n"
        "/setdelivery — ማድረሻ\n"
        "/setpayment — ክፍያ\n"
        "/setlocation — አድራሻ\n"
        "/sethours — ሰዓት\n"
        "/broadcast — ለሁሉም ላክ\n"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


# ─────────────────────────────────────────────
# FastAPI + Webhook Setup
# ─────────────────────────────────────────────
init_db()

app_tg = Application.builder().token(TELEGRAM_TOKEN).build()

app_tg.add_handler(CommandHandler("start", start))
app_tg.add_handler(CommandHandler("help", help_cmd))
app_tg.add_handler(CommandHandler("admin", admin_panel))
app_tg.add_handler(CommandHandler("setname", cmd_setname))
app_tg.add_handler(CommandHandler("setprice", cmd_setprice))
app_tg.add_handler(CommandHandler("setdelivery", cmd_setdelivery))
app_tg.add_handler(CommandHandler("setpayment", cmd_setpayment))
app_tg.add_handler(CommandHandler("setlocation", cmd_setlocation))
app_tg.add_handler(CommandHandler("sethours", cmd_sethours))
app_tg.add_handler(CommandHandler("broadcast", broadcast))
app_tg.add_handler(CallbackQueryHandler(handle_callback))
app_tg.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

# FastAPI app
web = FastAPI()


@web.on_event("startup")
async def startup():
    await app_tg.initialize()
    await app_tg.bot.set_webhook(
        url=f"{RENDER_URL}/webhook",
        allowed_updates=["message", "callback_query"],
    )
    logger.info(f"✅ Webhook set: {RENDER_URL}/webhook")


@web.on_event("shutdown")
async def shutdown():
    await app_tg.shutdown()


@web.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, app_tg.bot)
    await app_tg.process_update(update)
    return {"ok": True}


@web.get("/")
async def root():
    return {"status": "Roha Bot is running 🚀"}
