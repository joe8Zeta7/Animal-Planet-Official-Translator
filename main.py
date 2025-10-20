import os, re
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
from deep_translator import GoogleTranslator

BOT_TOKEN = os.getenv("BOT_TOKEN")

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

        original_lang = None
        if en.strip().lower() == text.strip().lower():
            original_lang = 'en'
        elif ru.strip().lower() == text.strip().lower():
            original_lang = 'ru'
        elif ko.strip().lower() == text.strip().lower():
            original_lang = 'ko'

        await context.bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)

        name = message.from_user.full_name
        original = f"🗣 {name}:\n{text}"

        translations = []
        if original_lang == 'en':
            translations += [f"🇷🇺 {ru}", f"🇰🇷 {ko}"]
        elif original_lang == 'ru':
            translations += [f"🇬🇧 {en}", f"🇰🇷 {ko}"]
        elif original_lang == 'ko':
            translations += [f"🇬🇧 {en}", f"🇷🇺 {ru}"]
        else:
            translations += [f"🇬🇧 {en}", f"🇷🇺 {ru}", f"🇰🇷 {ko}"]

        await context.bot.send_message(chat_id=message.chat.id, text=f"{original}\n\n" + "\n\n".join(translations))

    except Exception as e:
        print(f"Error: {e}")
        await context.bot.send_message(chat_id=message.chat.id, text="⚠️ Translation error.")

app = ApplicationBuilder().token(BOT_TOKEN).build()
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))