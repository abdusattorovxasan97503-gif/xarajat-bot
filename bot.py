import os
import sqlite3
import logging
import json
import requests
from datetime import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
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
    conn.close()

def matn_tahlil(matn):
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    data = {
        "model": "gpt-4o-mini",
        "messages": [
            {
                "role": "system",
                "content": "Foydalanuvchi xarajat haqida matn yozadi. Siz undan kategoriya va summani ajratib oling. Javobni faqat JSON formatida bering: {\"kategoriya\": \"...\", \"summa\": 12345, \"izoh\": \"...\"}\n\nKategoriyalar: oziq-ovqat, transport, kiyim, kommunal, soglik, talim, kongilochar, boshqa\n\nAgar summa topilmasa: {\"kategoriya\": null, \"summa\": null, \"izoh\": null}"
            },
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
            data={"model": "whisper-large-v3", "language": "uz"},
            files={"file": ("audio.ogg", f, "audio/ogg")}
        )
    logger.info(f"Groq Whisper: {response.status_code} - {response.text}")
    if response.status_code != 200:
        raise Exception(f"Groq xato {response.status_code}: {response.text}")
    return response.json().get("text", "")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Salom! Men xarajat hisobchi botman\n\n"
        "Ovozli yoki matnli xabar yuboring:\n"
        "Masalan: Nonga 20000 sarf qildim\n\n"
        "Hisobot uchun: /hisobot\n"
        "Tozalash uchun: /tozala"
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
        await update.message.reply_text("Bu oy hali xarajat kiritilmagan.")
        return

    jami = sum(r[1] for r in natijalar)
    xabar = f"{datetime.now().strftime('%Y-%m')} oyi hisoboti\n\n"
    for kategoriya, summa in natijalar:
        xabar += f"{kategoriya}: {summa:,.0f} som\n"
    xabar += f"\nJami: {jami:,.0f} som"
    await update.message.reply_text(xabar)

async def tozala(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = sqlite3.connect("xarajatlar.db")
    c = conn.cursor()
    c.execute("DELETE FROM xarajatlar WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()
    await update.message.reply_text("Barcha xarajatlar ochirildi.")

async def matn_xabar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    matn = update.message.text
    try:
        natija = matn_tahlil(matn)
        if natija["summa"] is None:
            await update.message.reply_text("Summa topilmadi. Masalan: Nonga 20000 sarf qildim")
            return
        xarajat_saqlash(user_id, natija["kategoriya"], natija["summa"], natija["izoh"])
        await update.message.reply_text(
            f"Saqlandi!\n"
            f"Kategoriya: {natija['kategoriya']}\n"
            f"Summa: {natija['summa']:,.0f} som\n"
            f"Izoh: {natija['izoh']}"
        )
    except Exception as e:
        logger.error(f"Xato: {e}")
        await update.message.reply_text(f"Xatolik: {e}")

async def ovoz_xabar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await update.message.reply_text("Ovoz qabul qilindi, tahlil qilinmoqda...")
    try:
        fayl = await update.message.voice.get_file()
        fayl_yoli = f"/tmp/ovoz_{user_id}.ogg"
        await fayl.download_to_drive(fayl_yoli)
        matn = ovoz_matn(fayl_yoli)
        if not matn:
            await update.message.reply_text("Ovoz tanilmadi. Aniqroq gapiring.")
            return
        await update.message.reply_text(f"Eshitildi: {matn}")
        natija = matn_tahlil(matn)
        if natija["summa"] is None:
            await update.message.reply_text("Summa topilmadi.")
            return
        xarajat_saqlash(user_id, natija["kategoriya"], natija["summa"], natija["izoh"])
        await update.message.reply_text(
            f"Saqlandi!\n"
            f"Kategoriya: {natija['kategoriya']}\n"
            f"Summa: {natija['summa']:,.0f} som\n"
            f"Izoh: {natija['izoh']}"
        )
    except Exception as e:
        logger.error(f"Ovoz xatosi: {e}")
        await update.message.reply_text(f"Xatolik: {e}")

def main():
    init_db()
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("hisobot", hisobot))
    app.add_handler(CommandHandler("tozala", tozala))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, matn_xabar))
    app.add_handler(MessageHandler(filters.VOICE, ovoz_xabar))
    logger.info("Bot ishga tushdi!")
    app.run_polling()

if __name__ == "__main__":
    main()
