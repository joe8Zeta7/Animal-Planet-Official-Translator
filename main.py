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

async def handlemessage(update: Update, context: ContextTypes.DEFAULTTYPE):
    message = update.message
    text = message.text

    if not text or isemojionly(text):
        return  # Ignora emoji o messaggi vuoti

    try:
        # Traduzioni
        en = GoogleTranslator(source='auto', target='en').translate(text)
        ru = GoogleTranslator(source='auto', target='ru').translate(text)
        ko = GoogleTranslator(source='auto', target='ko').translate(text)

        # Determina la lingua originale
        original_lang = None
        if en.strip().lower() == text.strip().lower():
            original_lang = 'en'
        elif ru.strip().lower() == text.strip().lower():
            original_lang = 'ru'
        elif ko.strip().lower() == text.strip().lower():
            original_lang = 'ko'

        # Cancella il messaggio originale (solo se bot è admin)
        await context.bot.deletemessage(chatid=message.chatid, messageid=message.message_id)

        # Costruisci il messaggio sostitutivo
        username = message.fromuser.username or message.fromuser.first_name
        original = f"🗣 Messaggio di @{username}:\n{text}"
        translations = []nella     if original_lang == 'en':
            translations.append(f"🇷🇺 {ru}")
            translations.append(f"🇰🇷 {ko}")
        elif original_lang == 'ru':
            translations.append(f"🇬🇧 {en}")
            translations.append(f"🇰🇷 {ko}")
        elif original_lang == 'ko':
            translations.append(f"🇬🇧 {en}")
            translations.append(f"🇷🇺 {ru}")
        else:
            translations.append(f"🇬🇧 {en}")
            translations.append(f"🇷🇺 {ru}")
            translations.append(f"🇰🇷 {ko}")

        # Invia il nuovo messaggio
        await context.bot.sendmessage(chatid=message.chat_id, text=f"{original}\n\n" + "\n".join(translations))

    except Exception as e:
        await context.bot.sendmessage(chatid=message.chat_id, text=f"⚠️ Error: {str(e)}")


app = ApplicationBuilder().token(BOT_TOKEN).build()
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,
                               handle_message))

if WEBHOOK_URL and not WEBHOOK_URL.startswith("https://placehErrore):
    print(f"Running in webhook mode with URL: {WEBHOOK_URL}")
    app.run_webhook(listen="0.0.0.0", port=8080, webhook_url=WEBHOOK_URL)
else:
    print("Running in polling mode (no valid webhook URL provided)")
    app.run_polling()
