import os
import sqlite3
import logging
import json
import requests
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def init_db():
    conn = sqlite3.connect("xarajatlar.db")
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS xarajatlar (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            kategoriya TEXT,
            summa REAL,
            izoh TEXT,
            sana TEXT
        )
    """)
    conn.commit()
    conn.close()

def xarajat_saqlash(user_id, kategoriya, summa, izoh):
    conn = sqlite3.connect("xarajatlar.db")
    c = conn.cursor()
    sana = datetime.now().strftime("%Y-%m-%d")
    c.execute(
        "INSERT INTO xarajatlar (user_id, kategoriya, summa, izoh, sana) VALUES (?, ?, ?, ?, ?)",
        (user_id, kategoriya, summa, izoh, sana)
    )
    conn.commit()
    xarajat_id = c.lastrowid
    conn.close()
    return xarajat_id

def matn_tahlil(matn):
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    data = {
        "model": "llama-3.3-70b-versatile",
        "messages": [
            {
                "role": "system",
                "content": "Foydalanuvchi xarajat haqida matn yozadi. Siz undan kategoriya va summani ajratib oling. Javobni faqat JSON formatida bering: {\"kategoriya\": \"...\", \"summa\": 12345, \"izoh\": \"...\"}\n\nKategoriyalar: oziq-ovqat, transport, kiyim, kommunal, soglik, talim, kongilochar, boshqa\n\nMuhim qoidalar:\n- 'min', 'ming', 'мин', 'миң' = 1000 koeffitsienti\n- 'yigirma' = 20, 'o'ttiz' = 30, 'qirq' = 40, 'ellik' = 50\n- 'yigirma ming' = 20000, 'o'ttiz ming' = 30000\n- Raqam va so'zni birgalikda hisobla\n\nAgar summa topilmasa: {\"kategoriya\": null, \"summa\": null, \"izoh\": null}"
            {"role": "user", "content": matn}
        ]
    }
    response = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=data)
    result = response.json()
    text = result["choices"][0]["message"]["content"].strip()
    text = text.replace("```json", "").replace("```", "").strip()
    return json.loads(text)

def ovoz_matn(fayl_yoli):
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}"}
    with open(fayl_yoli, "rb") as f:
        response = requests.post(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            headers=headers,
            data={"model": "whisper-large-v3-turbo", "language": "uz"},
            files={"file": ("audio.ogg", f, "audio/ogg")}
        )
    if response.status_code != 200:
        raise Exception(f"Groq xato {response.status_code}: {response.text}")
    return response.json().get("text", "")

def tugmalar():
    keyboard = [
        [
            InlineKeyboardButton("Hisobot", callback_data="hisobot"),
            InlineKeyboardButton("Tozalash", callback_data="tozala")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def saqlangan_tugmalar(xarajat_id):
    keyboard = [
        [InlineKeyboardButton("O'chirish", callback_data=f"del_{xarajat_id}")],
        [
            InlineKeyboardButton("Hisobot", callback_data="hisobot"),
            InlineKeyboardButton("Tozalash", callback_data="tozala")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

async def oylik_hisobot_yuborish(app):
    conn = sqlite3.connect("xarajatlar.db")
    c = conn.cursor()
    c.execute("SELECT DISTINCT user_id FROM xarajatlar")
    foydalanuvchilar = c.fetchall()
    conn.close()

    oy = datetime.now().strftime("%Y-%m")
    for (user_id,) in foydalanuvchilar:
        conn = sqlite3.connect("xarajatlar.db")
        c = conn.cursor()
        c.execute("""
            SELECT kategoriya, SUM(summa)
            FROM xarajatlar
            WHERE user_id=? AND sana LIKE ?
            GROUP BY kategoriya
            ORDER BY SUM(summa) DESC
        """, (user_id, f"{oy}%"))
        natijalar = c.fetchall()
        conn.close()

        if not natijalar:
            continue

        jami = sum(r[1] for r in natijalar)
        xabar = f"{oy} oyi yakuniy hisoboti\n\n"
        for kategoriya, summa in natijalar:
            xabar += f"{kategoriya}: {summa:,.0f} som\n"
        xabar += f"\nJami: {jami:,.0f} som"

        try:
            await app.bot.send_message(chat_id=user_id, text=xabar)
        except Exception as e:
            logger.error(f"Hisobot yuborishda xato {user_id}: {e}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Salom! Men Xarajat Hisobchi botman\n\n"
        "Mening vazifam:\n"
        "- Ovozli yoki matnli xarajatlaringizni qabul qilaman\n"
        "- Kategoriyalarga ajratib saqlayman\n"
        "- Har oy yakunida hisobot yuboraman\n\n"
        "Ishlatish juda oson:\n"
        "Shunchaki ovozli xabar yuboring:\n"
        "Masalan: 'Nonga 20000 sarf qildim'\n"
        "Yoki matn yozing: 'Taksi 15000'\n\n"
        "Har oyning oxirida barcha xarajatlaringiz\n"
        "hisoboti avtomatik yuboriladi!\n\n"
        "Boshlaylik!",
        reply_markup=tugmalar()
    )

async def hisobot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = sqlite3.connect("xarajatlar.db")
    c = conn.cursor()
    oy = datetime.now().strftime("%Y-%m")
    c.execute("""
        SELECT kategoriya, SUM(summa)
        FROM xarajatlar
        WHERE user_id=? AND sana LIKE ?
        GROUP BY kategoriya
        ORDER BY SUM(summa) DESC
    """, (user_id, f"{oy}%"))
    natijalar = c.fetchall()
    conn.close()

    if not natijalar:
        await update.message.reply_text("Bu oy hali xarajat kiritilmagan.", reply_markup=tugmalar())
        return

    jami = sum(r[1] for r in natijalar)
    xabar = f"{oy} oyi hisoboti\n\n"
    for kategoriya, summa in natijalar:
        xabar += f"{kategoriya}: {summa:,.0f} som\n"
    xabar += f"\nJami: {jami:,.0f} som"
    await update.message.reply_text(xabar, reply_markup=tugmalar())

async def tozala(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = sqlite3.connect("xarajatlar.db")
    c = conn.cursor()
    c.execute("DELETE FROM xarajatlar WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()
    await update.message.reply_text("Barcha xarajatlar ochirildi.", reply_markup=tugmalar())

async def tugma_bosildi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if query.data == "hisobot":
        conn = sqlite3.connect("xarajatlar.db")
        c = conn.cursor()
        oy = datetime.now().strftime("%Y-%m")
        c.execute("""
            SELECT kategoriya, SUM(summa)
            FROM xarajatlar
            WHERE user_id=? AND sana LIKE ?
            GROUP BY kategoriya
            ORDER BY SUM(summa) DESC
        """, (user_id, f"{oy}%"))
        natijalar = c.fetchall()
        conn.close()

        if not natijalar:
            await query.message.reply_text("Bu oy hali xarajat kiritilmagan.", reply_markup=tugmalar())
            return

        jami = sum(r[1] for r in natijalar)
        xabar = f"{oy} oyi hisoboti\n\n"
        for kategoriya, summa in natijalar:
            xabar += f"{kategoriya}: {summa:,.0f} som\n"
        xabar += f"\nJami: {jami:,.0f} som"
        await query.message.reply_text(xabar, reply_markup=tugmalar())

    elif query.data == "tozala":
        conn = sqlite3.connect("xarajatlar.db")
        c = conn.cursor()
        c.execute("DELETE FROM xarajatlar WHERE user_id=?", (user_id,))
        conn.commit()
        conn.close()
        await query.message.reply_text("Barcha xarajatlar ochirildi.", reply_markup=tugmalar())

    elif query.data.startswith("del_"):
        xid = int(query.data.split("_")[1])
        conn = sqlite3.connect("xarajatlar.db")
        c = conn.cursor()
        c.execute("DELETE FROM xarajatlar WHERE id=? AND user_id=?", (xid, user_id))
        conn.commit()
        conn.close()
        await query.message.reply_text("Xarajat o'chirildi.", reply_markup=tugmalar())

async def matn_xabar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    matn = update.message.text
    try:
        natija = matn_tahlil(matn)
        if natija["summa"] is None:
            await update.message.reply_text("Summa topilmadi. Masalan: Nonga 20000 sarf qildim", reply_markup=tugmalar())
            return
        xarajat_id = xarajat_saqlash(user_id, natija["kategoriya"], natija["summa"], natija["izoh"])
        await update.message.reply_text(
            f"Saqlandi!\n"
            f"Kategoriya: {natija['kategoriya']}\n"
            f"Summa: {natija['summa']:,.0f} som\n"
            f"Izoh: {natija['izoh']}",
            reply_markup=saqlangan_tugmalar(xarajat_id)
        )
    except Exception as e:
        logger.error(f"Xato: {e}")
        await update.message.reply_text(f"Xatolik: {e}", reply_markup=tugmalar())

async def ovoz_xabar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await update.message.reply_text("Ovoz qabul qilindi, tahlil qilinmoqda...")
    try:
        fayl = await update.message.voice.get_file()
        fayl_yoli = f"/tmp/ovoz_{user_id}.ogg"
        await fayl.download_to_drive(fayl_yoli)
        matn = ovoz_matn(fayl_yoli)
        if not matn:
            await update.message.reply_text("Ovoz tanilmadi.", reply_markup=tugmalar())
            return
        await update.message.reply_text(f"Eshitildi: {matn}")
        natija = matn_tahlil(matn)
        if natija["summa"] is None:
            await update.message.reply_text("Summa topilmadi.", reply_markup=tugmalar())
            return
        xarajat_id = xarajat_saqlash(user_id, natija["kategoriya"], natija["summa"], natija["izoh"])
        await update.message.reply_text(
            f"Saqlandi!\n"
            f"Kategoriya: {natija['kategoriya']}\n"
            f"Summa: {natija['summa']:,.0f} som\n"
            f"Izoh: {natija['izoh']}",
            reply_markup=saqlangan_tugmalar(xarajat_id)
        )
    except Exception as e:
        logger.error(f"Ovoz xatosi: {e}")
        await update.message.reply_text(f"Xatolik: {e}", reply_markup=tugmalar())

def main():
    init_db()
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    async def post_init(application):
        scheduler = AsyncIOScheduler()
        scheduler.add_job(
            oylik_hisobot_yuborish,
            trigger="cron",
            day="last",
            hour=20,
            minute=0,
            args=[application]
        )
        scheduler.start()

    app.post_init = post_init

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("hisobot", hisobot))
    app.add_handler(CommandHandler("tozala", tozala))
    app.add_handler(CallbackQueryHandler(tugma_bosildi))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, matn_xabar))
    app.add_handler(MessageHandler(filters.VOICE, ovoz_xabar))
    logger.info("Bot ishga tushdi!")
    app.run_polling()

if __name__ == "__main__":
    main()
