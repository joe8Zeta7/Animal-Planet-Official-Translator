from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
from deep_translator import GoogleTranslator
from flask import Flask
import threading, os, re

flask_app = Flask(__name__)

@flask_app.route('/')
def keep_alive():
    return 'Bot is running', 200

def run_flask():
    flask_app.run(host='0.0.0.0', port=10000)

# Avvia Flask in un thread separato (daemon=True per chiusura pulita)
threading.Thread(target=run_flask, daemon=True).start()

BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

def is_emoji_only(text):
    emoji_pattern = re.compile(r'^[\U0001F300-\U0001FAFF\U00002700-\U000027BF\U0001F900-\U0001F9FF\U0001F600-\U0001F64F]+$')
    return bool(emoji_pattern.fullmatch(text.strip()))

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    text = message.text

    if not text or is_emoji_only(text):
        return

    try:
        en = GoogleTranslator(source='auto', target='en').translate(text)
        ru = GoogleTranslator(source='auto', target='ru').translate(text)
        ko = GoogleTranslator(source='auto', target='ko').translate(text)

        lang_detected = GoogleTranslator(source='auto').detect(text)

        await context.bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)

        username = message.from_user.username or message.from_user.first_name
        original = f"🗣 Messaggio di @{username}:\n{text}"

        translations = []
        if lang_detected == 'en':
            translations += [f"🇷🇺 {ru}", f"🇰🇷 {ko}"]
        elif lang_detected == 'ru':
            translations += [f"🇬🇧 {en}", f"🇰🇷 {ko}"]
        elif lang_detected == 'ko':
            translations += [f"🇬🇧 {en}", f"🇷🇺 {ru}"]
        else:
            translations += [f"🇬🇧 {en}", f"🇷🇺 {ru}", f"🇰🇷 {ko}"]

        await context.bot.send_message(chat_id=message.chat.id, text=f"{original}\n\n" + "\n".join(translations))

    except Exception as e:
        print(f"Error: {e}")
        await context.bot.send_message(chat_id=message.chat.id, text="⚠️ Errore durante la traduzione.")

app = ApplicationBuilder().token(BOT_TOKEN).build()
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

if WEBHOOK_URL and WEBHOOK_URL.startswith("https://"):
    print(f"Running in webhook mode with URL: {WEBHOOK_URL}")
    app.run_webhook(listen="0.0.0.0", port=8080, webhook_url=WEBHOOK_URL)
else:
    print("Running in polling mode")
    app.run_polling()