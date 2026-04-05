import os, re, threading, html
from flask import Flask
from telegram import Update
from telegram.constants import ParseMode, MessageEntityType
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
    if not text:
        return False
    emoji_pattern = re.compile(r'^[U0001F300-U0001FAFFU00002700-U000027BFU0001F900-U0001F9FFU0001F600-U0001F64Fs]+$')
    return bool(emoji_pattern.fullmatch(text.strip()))

# Funzione per rilevare se ci sono solo link
def is_link_only(message) -> bool:
    if not message.text or not message.entities:
        return False
    
    link_chars = sum(
        ent.length for ent in message.entities 
        if ent.type in (MessageEntityType.URL, MessageEntityType.TEXT_LINK)
    )
    
    # Metodo sicuro: split() divide il testo ignorando tutti gli spazi e gli "a capo", 
    # join li riunisce senza interruzioni.
    text_no_spaces = "".join(message.text.split())
    
    return link_chars >= len(text_no_spaces)

# Handler principale
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message:
        return

    raw_text = message.text
    # text_html conserva automaticamente link, grassetti, ecc. scritti dall'utente
    text_html = message.text_html

    # Ignora se è vuoto, solo emoji, o solo link
    if not raw_text or is_emoji_only(raw_text) or is_link_only(message):
        return

    try:
        # Traduzioni tramite deep_translator
        en = GoogleTranslator(source='auto', target='en').translate(raw_text)
        ru = GoogleTranslator(source='auto', target='ru').translate(raw_text)
        de = GoogleTranslator(source='auto', target='de').translate(raw_text)
        tr = GoogleTranslator(source='auto', target='tr').translate(raw_text)
        nl = GoogleTranslator(source='auto', target='nl').translate(raw_text)

        # Rilevamento lingua originale
        original_lang = 'en'
        text_lower = raw_text.strip().lower()
        
        # Gestiamo il caso in cui la traduzione ritorni stringhe vuote o None
        if en and en.strip().lower() == text_lower:
            original_lang = 'en'
        elif ru and ru.strip().lower() == text_lower:
            original_lang = 'ru'
        elif de and de.strip().lower() == text_lower:
            original_lang = 'de'
        elif tr and tr.strip().lower() == text_lower:
            original_lang = 'tr'
        elif nl and nl.strip().lower() == text_lower:
            original_lang = 'nl'

        # Cancella il messaggio originale (se bot è admin)
        await context.bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)

        # Costruisci intestazione (con html.escape per sicurezza)
name = html.escape(message.from_user.full_name)

header = f"🗣 <b>{name}</b>:" + "
"

        # Dizionario delle lingue 
        all_langs = {
            'en': f"🇬🇧 {html.escape(en)}",
            'ru': f"🇷🇺 {html.escape(ru)}",
            'de': f"🇩🇪 {html.escape(de)}",
            'tr': f"🇹🇷 {html.escape(tr)}",
            'nl': f"🇳🇱 {html.escape(nl)}"
        }

        # Rimuove la lingua di partenza per non duplicarla
        if original_lang in all_langs:
            del all_langs[original_lang]

        # Crea il blocco testo per le traduzioni
        translations_text = "

".join(all_langs.values())
        
        # Assembliamo il messaggio finale col blocco espandibile HTML
        final_text = f"{header}{text_html}

<blockquote expandable>{translations_text}</blockquote>"

        # Invia un solo messaggio finale, pulito e compatto!
        await context.bot.send_message(
            chat_id=message.chat.id,
            text=final_text,
            parse_mode=ParseMode.HTML
        )

    except Exception as e:
        print(f"Error: {e}")
        # Rimane il tuo messaggio di sicurezza in caso di crash delle API
        await context.bot.send_message(chat_id=message.chat.id, text="⚠️ Translation error.")

# Avvio del bot
if __name__ == '__main__':
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    if WEBHOOK_URL and WEBHOOK_URL.startswith("https://"):
        print(f"Running in webhook mode with URL: {WEBHOOK_URL}")
        app.run_webhook(listen="0.0.0.0", port=8080, webhook_url=WEBHOOK_URL)
    else:
        print("Running in polling mode")
        app.run_polling()