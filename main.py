import os, re, threading
from flask import Flask
from telegram import Update
from telegram import MessageEntity
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
from deep_translator import GoogleTranslator

# Flask app per UptimeRobot
flask_app = Flask(__name__)

@flask_app.route('/')
def keep_alive():
    return 'Bot is running', 200

def run_flask():
    flask_app.run(host='0.0.0.0', port=10000)

# Avvia Flask in un thread separato
threading.Thread(target=run_flask, daemon=True).start()

# Variabili ambiente
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

# Funzione per rilevare solo emoji
def is_emoji_only(text):
    emoji_pattern = re.compile(r'^[\U0001F300-\U0001FAFF\U00002700-\U000027BF\U0001F900-\U0001F9FF\U0001F600-\U0001F64F]+$')
    return bool(emoji_pattern.fullmatch(text.strip()))

# Handler principale
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    text = message.text

    if not text or is_emoji_only(text):
        return

    try:
        # Traduzioni
        en = GoogleTranslator(source='auto', target='en').translate(text)
        ru = GoogleTranslator(source='auto', target='ru').translate(text)
        ko = GoogleTranslator(source='auto', target='ko').translate(text)

        # Rilevamento lingua originale
        original_lang = None
        if en.strip().lower() == text.strip().lower():
            original_lang = 'en'
        elif ru.strip().lower() == text.strip().lower():
            original_lang = 'ru'
        elif ko.strip().lower() == text.strip().lower():
            original_lang = 'ko'

        await context.bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)

        name = message.from_user.full_name
        header = f"🗣 {name}:\n"
        translations = []
        if original_lang == 'en':
            translations += [f"🇷🇺 {ru}", f"🇰🇷 {ko}"]
        elif original_lang == 'ru':
            translations += [f"🇬🇧 {en}", f"🇰🇷 {ko}"]
        elif original_lang == 'ko':
            translations += [f"🇬🇧 {en}", f"🇷🇺 {ru}"]
        else:
            translations += [f"🇬🇧 {en}", f"🇷🇺 {ru}", f"🇰🇷 {ko}"]

        final_text = header + text + "\n\n" + "\n\n".join(translations)

        # Se ci sono entità (menzioni, link, ecc.), spostane gli offset per il nuovo testo
        new_entities = []
        if message.entities:
            for ent in message.entities:
                new_ent = ent.to_dict()
                new_ent['offset'] += len(header)
                new_entities.append(MessageEntity(**new_ent))

        await context.bot.send_message(
            chat_id=message.chat.id,
            text=final_text,
            entities=new_entities or None
        )

    except Exception as e:
        print(f"Error: {e}")
        await context.bot.send_message(chat_id=message.chat.id, text="⚠️ Translation error.")

# Avvio del bot
app = ApplicationBuilder().token(BOT_TOKEN).build()
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

if WEBHOOK_URL and WEBHOOK_URL.startswith("https://"):
    print(f"Running in webhook mode with URL: {WEBHOOK_URL}")
    app.run_webhook(listen="0.0.0.0", port=8080, webhook_url=WEBHOOK_URL)
else:
    print("Running in polling mode")
    app.run_polling()