import os
import html
import emoji
import string
import threading
import asyncio
import logging
import time

from flask import Flask

from telegram import Update
from telegram.constants import ParseMode, MessageEntityType
from telegram.error import Conflict

from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    filters,
    ContextTypes,
)

from deep_translator import GoogleTranslator
from deep_translator.exceptions import TooManyRequests


# --------------------------------------------------
# CONFIGURAZIONE
# --------------------------------------------------

BOT_TOKEN = os.getenv("BOT_TOKEN")
PORT = int(os.getenv("PORT", "10000"))

REQUEST_DELAY = 2.0
GOOGLE_COOLDOWN = 120.0

# Ordine delle richieste a Google.
LANGUAGES = ("en", "ru", "de", "fr", "es", "tr", "nl")

# Ordine delle traduzioni nel messaggio finale.
LANGUAGE_FLAGS = {
    "en": "🇬🇧",
    "ru": "🇷🇺",
    "de": "🇩🇪",
    "tr": "🇹🇷",
    "nl": "🇳🇱",
    "fr": "🇫🇷",
    "es": "🇪🇸",
}

NEWLINE = chr(10)
DOUBLE_NEWLINE = NEWLINE * 2


# --------------------------------------------------
# LOG
# --------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

logger = logging.getLogger(__name__)

logging.getLogger("httpx").setLevel(logging.WARNING)


# --------------------------------------------------
# FLASK PER UPTIMEROBOT
# --------------------------------------------------

flask_app = Flask(__name__)


@flask_app.route("/", methods=["GET", "HEAD"])
def keep_alive():
    return "Bot is running", 200


def run_flask():
    flask_app.run(
        host="0.0.0.0",
        port=PORT,
        debug=False,
        use_reloader=False,
    )


# --------------------------------------------------
# CONTROLLO DELLE TRADUZIONI
# --------------------------------------------------

translation_lock = asyncio.Lock()
google_blocked_until = 0.0


async def translate_all(text):
    global google_blocked_until

    async with translation_lock:
        remaining = google_blocked_until - time.monotonic()

        if remaining > 0:
            raise TooManyRequests(
                f"Google è in pausa: restano circa "
                f"{int(remaining) + 1} secondi."
            )

        translations = {}

        for index, language in enumerate(LANGUAGES, start=1):
            logger.info(
                "Tentativo di traduzione %s/%s verso: %s",
                index,
                len(LANGUAGES),
                language,
            )

            started_at = time.monotonic()

            try:
                translator = GoogleTranslator(
                    source="auto",
                    target=language,
                )

                translated = await asyncio.to_thread(
                    translator.translate,
                    text,
                )

            except TooManyRequests:
                google_blocked_until = (
                    time.monotonic() + GOOGLE_COOLDOWN
                )

                logger.warning(
                    "Google ha bloccato la traduzione verso %s "
                    "(richiesta %s/%s; traduzioni completate: %s). "
                    "Pausa di %.0f secondi.",
                    language,
                    index,
                    len(LANGUAGES),
                    len(translations),
                    GOOGLE_COOLDOWN,
                )

                raise

            except Exception:
                logger.exception(
                    "Errore nella traduzione verso %s "
                    "(richiesta %s/%s).",
                    language,
                    index,
                    len(LANGUAGES),
                )

                raise

            if (
                not isinstance(translated, str)
                or not translated.strip()
            ):
                raise ValueError(
                    f"Traduzione vuota per la lingua '{language}'."
                )

            translations[language] = translated

            logger.info(
                "Traduzione verso %s completata in %.2f secondi.",
                language,
                time.monotonic() - started_at,
            )

            await asyncio.sleep(REQUEST_DELAY)

        return translations


# --------------------------------------------------
# FILTRI DEI MESSAGGI
# --------------------------------------------------

def is_emoji_only(text):
    if not text:
        return False

    text_without_emojis = emoji.replace_emoji(
        text,
        replace="",
    )

    cleaned = "".join(
        char
        for char in text_without_emojis
        if char not in string.whitespace
        and char not in string.punctuation
    )

    return len(cleaned) == 0


def is_link_only(message):
    if not message.text or not message.entities:
        return False

    encoded_text = message.text.encode("utf-16-le")

    link_ranges = sorted(
        (
            entity.offset * 2,
            (entity.offset + entity.length) * 2,
        )
        for entity in message.entities
        if entity.type in (
            MessageEntityType.URL,
            MessageEntityType.TEXT_LINK,
        )
    )

    if not link_ranges:
        return False

    remaining_parts = []
    cursor = 0

    for start, end in link_ranges:
        if start > cursor:
            remaining_parts.append(encoded_text[cursor:start])

        cursor = max(cursor, end)

    remaining_parts.append(encoded_text[cursor:])

    remaining_text = b"".join(remaining_parts).decode(
        "utf-16-le"
    )

    return not remaining_text.strip()


# --------------------------------------------------
# STIMA DELLA LINGUA ORIGINALE
# --------------------------------------------------

def guess_original_language(raw_text, translations):
    normalized_original = raw_text.strip().casefold()

    language_order = (
        "en",
        "ru",
        "de",
        "tr",
        "nl",
        "fr",
        "es",
    )

    for language in language_order:
        translated = translations.get(language)

        if (
            translated
            and translated.strip().casefold()
            == normalized_original
        ):
            return language

    return None


# --------------------------------------------------
# AVVISI IN CHAT
# --------------------------------------------------

async def send_notice(message, context, text):
    try:
        await context.bot.send_message(
            chat_id=message.chat.id,
            message_thread_id=message.message_thread_id,
            text=text,
        )

    except Exception:
        logger.exception(
            "Impossibile inviare l'avviso. "
            "Chat=%s Messaggio=%s",
            message.chat.id,
            message.message_id,
        )


# --------------------------------------------------
# HANDLER PRINCIPALE
# --------------------------------------------------

async def handle_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    message = update.message

    if not message:
        return

    raw_text = message.text

    if not raw_text:
        return

    if message.from_user and message.from_user.is_bot:
        return

    if is_emoji_only(raw_text) or is_link_only(message):
        return

    logger.info(
        "Messaggio ricevuto. Chat=%s Messaggio=%s Caratteri=%s",
        message.chat.id,
        message.message_id,
        len(raw_text),
    )

    try:
        translations = await translate_all(raw_text)

        original_language = guess_original_language(
            raw_text,
            translations,
        )

        text_html = message.text_html or html.escape(raw_text)

        if message.from_user:
            author_name = message.from_user.full_name
        elif message.sender_chat:
            author_name = message.sender_chat.title or "Utente"
        else:
            author_name = "Utente"

        name = html.escape(author_name)

        header = f"🗣 <b>{name}</b>:{NEWLINE}"

        translation_blocks = []

        for language, flag in LANGUAGE_FLAGS.items():
            if language == original_language:
                continue

            translated_text = html.escape(
                translations[language]
            )

            translation_blocks.append(
                f"{flag} {translated_text}"
            )

        translations_text = DOUBLE_NEWLINE.join(
            translation_blocks
        )

        final_text = (
            f"{header}"
            f"{text_html}"
            f"{DOUBLE_NEWLINE}"
            f"<blockquote expandable>"
            f"{translations_text}"
            f"</blockquote>"
        )

        # Invia prima il nuovo messaggio.
        await context.bot.send_message(
            chat_id=message.chat.id,
            message_thread_id=message.message_thread_id,
            text=final_text,
            parse_mode=ParseMode.HTML,
        )

        logger.info(
            "Messaggio tradotto inviato. Chat=%s Originale=%s",
            message.chat.id,
            message.message_id,
        )

        # Cancella l'originale solo dopo l'invio riuscito.
        try:
            await context.bot.delete_message(
                chat_id=message.chat.id,
                message_id=message.message_id,
            )

        except Exception:
            logger.exception(
                "Traduzione inviata, ma cancellazione "
                "dell'originale fallita. Chat=%s Messaggio=%s",
                message.chat.id,
                message.message_id,
            )

    except TooManyRequests as error:
        logger.warning(
            "Traduzione saltata. Chat=%s Messaggio=%s "
            "Motivo=%s. Originale conservato.",
            message.chat.id,
            message.message_id,
            error,
        )

        remaining = max(
            0,
            int(google_blocked_until - time.monotonic()) + 1,
        )

        await send_notice(
            message,
            context,
            (
                "⚠️ Google sta rifiutando le richieste "
                "di traduzione. "
                "Il messaggio originale è stato conservato. "
                f"Il bot consentirà un nuovo tentativo tra "
                f"circa {remaining} secondi. "
                "Questo messaggio non verrà ritradotto "
                "automaticamente."
            ),
        )

    except Exception:
        logger.exception(
            "Errore nella traduzione o nell'invio. "
            "Chat=%s Messaggio=%s. Originale conservato.",
            message.chat.id,
            message.message_id,
        )

        await send_notice(
            message,
            context,
            (
                "⚠️ Traduzione non riuscita. "
                "Il messaggio originale è stato conservato. "
                "I dettagli dell'errore sono nei log."
            ),
        )


# --------------------------------------------------
# ERRORI DELL'APPLICAZIONE
# --------------------------------------------------

async def handle_application_error(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
):
    error = context.error

    if isinstance(error, Conflict):
        logger.error(
            "Conflitto Telegram: un altro processo sta "
            "eseguendo il polling con lo stesso BOT_TOKEN. "
            "Controlla eventuali altre istanze del bot "
            "o la sovrapposizione durante il deploy."
        )

        return

    if error is not None:
        logger.error(
            "Errore non gestito dell'applicazione",
            exc_info=(
                type(error),
                error,
                error.__traceback__,
            ),
        )


# --------------------------------------------------
# AVVIO
# --------------------------------------------------

if __name__ == "__main__":
    if not BOT_TOKEN:
        raise RuntimeError(
            "Variabile ambiente BOT_TOKEN mancante."
        )

    threading.Thread(
        target=run_flask,
        daemon=True,
    ).start()

    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .concurrent_updates(False)
        .build()
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_message,
        )
    )

    app.add_error_handler(handle_application_error)

    logger.info(
        "Avvio versione diagnostica in polling. "
        "Flask sulla porta %s. Lingue=%s",
        PORT,
        ",".join(LANGUAGES),
    )

    app.run_polling()
