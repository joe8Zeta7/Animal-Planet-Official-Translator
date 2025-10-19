from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters
from deep_translator import GoogleTranslator
import os

BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if not text:
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
