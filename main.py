from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters
from googletrans import Translator
import os

BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

translator = Translator()

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if not text:
        return

    detected = translator.detect(text).lang

    if detected != "en":
        translated = translator.translate(text, dest="en").text
        await update.message.reply_text(translated)
    else:
        ru = translator.translate(text, dest="ru").text
        ko = translator.translate(text, dest="ko").text
        await update.message.reply_text(f"🇷🇺 {ru}\n🇰🇷 {ko}")

app = ApplicationBuilder().token(BOT_TOKEN).build()
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

app.run_webhook(
    listen="0.0.0.0",
    port=8080,
    webhook_url=WEBHOOK_URL
)
