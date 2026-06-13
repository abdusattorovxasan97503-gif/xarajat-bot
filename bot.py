import os
import sqlite3
import logging
from datetime import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from openai import OpenAI

# Sozlamalar
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

client = OpenAI(api_key=OPENAI_API_KEY)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Ma'lumotlar bazasini yaratish
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

# Xarajatni saqlash
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

# Matnni tahlil qilish (GPT orqali)
def matn_tahlil(matn):
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": """Foydalanuvchi xarajat haqida matn yozadi. 
Siz undan kategoriya va summani ajratib oling.
Javobni faqat JSON formatida bering:
{"kategoriya": "...", "summa": 12345, "izoh": "..."}

Kategoriyalar: oziq-ovqat, transport, kiyim, kommunal, sog'liq, ta'lim, ko'ngilochar, boshqa

Misol:
"bugun nonga 20000 sarf qildim" -> {"kategoriya": "oziq-ovqat", "summa": 20000, "izoh": "non"}
"taksi 15000" -> {"kategoriya": "transport", "summa": 15000, "izoh": "taksi"}

Agar summa topilmasa: {"kategoriya": null, "summa": null, "izoh": null}"""
            },
            {"role": "user", "content": matn}
        ]
    )
    import json
    text = response.choices[0].message.content.strip()
    text = text.replace("```json", "").replace("```", "").strip()
    return json.loads(text)

# /start buyrug'i
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Salom! Men xarajat hisobchi botman 💰\n\n"
        "Ovozli yoki matnli xabar yuboring:\n"
        "Masalan: 'Nonga 20000 sarf qildim'\n\n"
        "📊 Hisobot uchun: /hisobot\n"
        "🗑 Tozalash uchun: /tozala"
    )

# /hisobot buyrug'i
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
    
    xabar = f"📊 *{datetime.now().strftime('%Y-%B')} oyi hisoboti*\n\n"
    for kategoriya, summa in natijalar:
        emoji = {
            "oziq-ovqat": "🍞",
            "transport": "🚗",
            "kiyim": "👕",
            "kommunal": "💡",
            "sog'liq": "💊",
            "ta'lim": "📚",
            "ko'ngilochar": "🎮",
            "boshqa": "📦"
        }.get(kategoriya, "📦")
        xabar += f"{emoji} {kategoriya.capitalize()}: *{summa:,.0f}* so'm\n"
    
    xabar += f"\n💰 *Jami: {jami:,.0f} so'm*"
    
    await update.message.reply_text(xabar, parse_mode="Markdown")

# /tozala buyrug'i
async def tozala(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = sqlite3.connect("xarajatlar.db")
    c = conn.cursor()
    c.execute("DELETE FROM xarajatlar WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()
    await update.message.reply_text("✅ Barcha xarajatlar o'chirildi.")

# Matnli xabar
async def matn_xabar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    matn = update.message.text
    
    try:
        natija = matn_tahlil(matn)
        
        if natija["summa"] is None:
            await update.message.reply_text(
                "❌ Summa topilmadi. Masalan:\n'Nonga 20000 sarf qildim'"
            )
            return
        
        xarajat_saqlash(user_id, natija["kategoriya"], natija["summa"], natija["izoh"])
        
        await update.message.reply_text(
            f"✅ Saqlandi!\n"
            f"📂 Kategoriya: {natija['kategoriya']}\n"
            f"💵 Summa: {natija['summa']:,.0f} so'm\n"
            f"📝 Izoh: {natija['izoh']}"
        )
    except Exception as e:
        logger.error(f"Xato: {e}")
        await update.message.reply_text("❌ Xatolik yuz berdi. Qayta urinib ko'ring.")

# Ovozli xabar
async def ovoz_xabar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    await update.message.reply_text("🎤 Ovoz qabul qilindi, tahlil qilinmoqda...")
    
    try:
        fayl = await update.message.voice.get_file()
        fayl_yoli = f"/tmp/ovoz_{user_id}.ogg"
        await fayl.download_to_drive(fayl_yoli)
        
        with open(fayl_yoli, "rb") as audio:
            transkript = client.audio.transcriptions.create(
                model="whisper-1",
                file=audio,
                language="uz"
            )
        
        matn = transkript.text
        await update.message.reply_text(f"🗣 Eshitildi: {matn}")
        
        natija = matn_tahlil(matn)
        
        if natija["summa"] is None:
            await update.message.reply_text(
                "❌ Summa topilmadi. Masalan:\n'Nonga 20000 sarf qildim'"
            )
            return
        
        xarajat_saqlash(user_id, natija["kategoriya"], natija["summa"], natija["izoh"])
        
        await update.message.reply_text(
            f"✅ Saqlandi!\n"
            f"📂 Kategoriya: {natija['kategoriya']}\n"
            f"💵 Summa: {natija['summa']:,.0f} so'm\n"
            f"📝 Izoh: {natija['izoh']}"
        )
    except Exception as e:
        logger.error(f"Ovoz xatosi: {e}")
        await update.message.reply_text("❌ Ovozni qayta ishlashda xatolik.")

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
