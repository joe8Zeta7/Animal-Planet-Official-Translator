from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from deep_translator import GoogleTranslator
from flask import Flask
import threading
import os
import re

# Flask app per il ping
flask_app = Flask(__name__)

@flask_app.route('/')
def keep_alive():
    return 'Bot is running', 200

def run_flask():
    flask_app.run(host='0.0.0.0', port=10000)

# Avvia Flask in un thread separato
threading.Thread(target=run_flask).start()
    
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

def isemojionly(text):
    emoji_pattern = re.compile(r'^[\U0001F300-\U0001FAFF\U00002700-\U000027BF\U0001F900-\U0001F9FF\U0001F600-\U0001F64F]+$')
    return bool(emoji_pattern.fullmatch(text.strip()))

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if not text or isemojionly(text):
        return

    try:
        # Traduci in inglese
        en = GoogleTranslator(source='auto', target='en').translate(text)
        ru = GoogleTranslator(source='auto', target='ru').translate(text)
        ko = GoogleTranslator(source='auto', target='ko').translate(text)

        # Se il testo originale è già in inglese
        if en.strip().lower() == text.strip().lower():
            ru = GoogleTranslator(source='en', target='ru').translate(text)
            ko = GoogleTranslator(source='en', target='ko').translate(text)
            await update.message.reply_text(f"🇷🇺 {ru}\n🇰🇷 {ko}")
        else:
            # Se il testo originale è già in russo
            if ru.strip().lower() == text.strip().lower():
                en = GoogleTranslator(source='ru', target='en').translate(text)
                ko = GoogleTranslator(source='ru', target='ko').translate(text)
                await update.message.reply_text(f"🇬🇧 {en}\n🇰🇷 {ko}")
            else:
                # Se il testo originale è già in coreano
                if ko.strip().lower() == text.strip().lower():
                    en = GoogleTranslator(source='ko', target='en').translate(text)
                    ru = GoogleTranslator(source='ko', target='ru').translate(text)
                    await update.message.reply_text(f"🇬🇧 {en}\n🇷🇺 {ru}")
                else:
                    # Se il testo originale è in una lingua diversa
                    await update.message.reply_text(f"🇬🇧 {en}\n🇷🇺 {ru}\n🇰🇷 {ko}")

    except Exception as e:
        await update.message.reply_text(f"Translation error: {str(e)}")


app = ApplicationBuilder().token(BOT_TOKEN).build()
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,
                               handle_message))

if WEBHOOK_URL and not WEBHOOK_URL.startswith("https://placeholder"):
    print(f"Running in webhook mode with URL: {WEBHOOK_URL}")
    app.run_webhook(listen="0.0.0.0", port=8080, webhook_url=WEBHOOK_URL)
else:
    print("Running in polling mode (no valid webhook URL provided)")
    app.run_polling()
