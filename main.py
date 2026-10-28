import os
import html
import emoji
import string
import threading
import asyncio
import logging
import time
import json
from urllib import request, error

from flask import Flask

from telegram import Update
from telegram.constants import ParseMode, MessageEntityType
from telegram.error import Conflict

from langdetect import detect, LangDetectException

from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    filters,
    ContextTypes,
)


# --------------------------------------------------
# CONFIGURAZIONE
# --------------------------------------------------

BOT_TOKEN = os.getenv("BOT_TOKEN")
PORT = int(os.getenv("PORT", "10000"))


# Lingue che vogliamo mostrare.
LANGUAGES = (
    "en",
    "ru",
    "de",
    "fr",
    "es",
    "tr",
    "nl",
)


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


# Timeout massimo di una singola richiesta HTTP.
TRANSLATION_TIMEOUT = 20

# Quante traduzioni possono girare contemporaneamente.
MAX_PARALLEL_TRANSLATIONS = 3


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
# FLASK PER UPTIMEROBOT / RENDER
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

translation_semaphore = asyncio.Semaphore(
    MAX_PARALLEL_TRANSLATIONS
)


# --------------------------------------------------
# ECCEZIONI TRADUZIONE
# --------------------------------------------------

class TranslationError(Exception):
    """Errore generico del servizio di traduzione."""


class TranslationRateLimitError(TranslationError):
    """Il servizio di traduzione ha rifiutato la richiesta."""


# --------------------------------------------------
# TRADUZIONE CON MYMEMORY
# --------------------------------------------------

def mymemory_request(
    text,
    source,
    target,
):
    """
    Traduce un testo con MyMemory.
    """

    params = {
        "q": text,
        "langpair": f"{source}|{target}",
    }

    query = "&".join(
        f"{key}={request.quote(str(value))}"
        for key, value in params.items()
    )

    url = (
        "https://api.mymemory.translated.net/get?"
        f"{query}"
    )

    req = request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": (
                "AnimalPlanetOfficialTranslator/1.0"
            ),
        },
        method="GET",
    )

    try:

        with request.urlopen(
            req,
            timeout=TRANSLATION_TIMEOUT,
        ) as response:

            result = json.loads(
                response.read().decode("utf-8")
            )

    except error.HTTPError as exc:

        try:
            body = exc.read().decode(
                "utf-8",
                errors="replace",
            )
        except Exception:
            body = ""

        if exc.code in (429, 403):

            raise TranslationRateLimitError(
                f"MyMemory HTTP {exc.code}. {body}"
            ) from exc

        raise TranslationError(
            f"MyMemory HTTP {exc.code}. {body}"
        ) from exc

    except error.URLError as exc:

        raise TranslationError(
            f"MyMemory non raggiungibile: {exc}"
        ) from exc

    except TimeoutError as exc:

        raise TranslationError(
            "Timeout durante la richiesta a MyMemory."
        ) from exc

    except json.JSONDecodeError as exc:

        raise TranslationError(
            "MyMemory ha restituito una risposta non valida."
        ) from exc

    translated = (
        result.get("responseData", {})
        .get("translatedText")
    )

    if (
        not isinstance(translated, str)
        or not translated.strip()
    ):

        raise TranslationError(
            f"MyMemory ha restituito una traduzione "
            f"vuota verso '{target}'."
        )

    return translated


async def translate_one(
    text,
    source,
    target,
):
    """
    Traduce un testo verso una lingua con MyMemory.
    """

    started_at = time.monotonic()

    translated = await asyncio.to_thread(
        mymemory_request,
        text,
        source,
        target,
    )

    logger.info(
        "Traduzione completata. "
        "Source=%s Target=%s Tempo=%.2f secondi.",
        source,
        target,
        time.monotonic() - started_at,
    )

    return translated


# --------------------------------------------------
# RILEVAMENTO LINGUA LOCALE
# --------------------------------------------------

def detect_original_language(text):

    try:

        language = detect(text)

        logger.info(
            "Lingua originale rilevata localmente: %s",
            language,
        )

        return language

    except LangDetectException:

        logger.warning(
            "Impossibile rilevare la lingua originale. "
            "Uso l’italiano come fallback."
        )

        return "it"


# --------------------------------------------------
# TRADUZIONE COMPLETA
# --------------------------------------------------

async def translate_all(text):

    async with translation_lock:

        logger.info(
            "Avvio traduzione verso %s lingue con MyMemory.",
            len(LANGUAGES),
        )

        started_at = time.monotonic()

        original_language = detect_original_language(
            text
        )

        translations = {}

        if original_language in LANGUAGES:

            translations[original_language] = text

        remaining_languages = [
            language
            for language in LANGUAGES
            if language not in translations
        ]

        async def translate_target(language):

            async with translation_semaphore:

                logger.info(
                    "Avvio traduzione verso: %s",
                    language,
                )

                translated = await translate_one(
                    text,
                    original_language,
                    language,
                )

                return (
                    language,
                    translated,
                )

        tasks = [
            translate_target(language)
            for language in remaining_languages
        ]

        results = await asyncio.gather(*tasks)

        for language, translated in results:

            translations[language] = translated

        missing = [
            language
            for language in LANGUAGES
            if language not in translations
        ]

        if missing:

            raise TranslationError(
                "Mancano le traduzioni per: "
                + ", ".join(missing)
            )

        elapsed = time.monotonic() - started_at

        logger.info(
            "Tutte le traduzioni completate (%s/%s) "
            "in %.2f secondi.",
            len(translations),
            len(LANGUAGES),
            elapsed,
        )

        return (
            translations,
            original_language,
        )


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

    if (
        not message.text
        or not message.entities
    ):
        return False

    encoded_text = message.text.encode(
        "utf-16-le"
    )

    link_ranges = sorted(
        (
            entity.offset * 2,
            (
                entity.offset
                + entity.length
            ) * 2,
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

            remaining_parts.append(
                encoded_text[
                    cursor:start
                ]
            )

        cursor = max(
            cursor,
            end,
        )

    remaining_parts.append(
        encoded_text[cursor:]
    )

    remaining_text = b"".join(
        remaining_parts
    ).decode("utf-16-le")

    return not remaining_text.strip()


# --------------------------------------------------
# AVVISI IN CHAT
# --------------------------------------------------

async def send_notice(
    message,
    context,
    text,
):

    try:

        await context.bot.send_message(
            chat_id=message.chat.id,
            message_thread_id=(
                message.message_thread_id
            ),
            text=text,
        )

    except Exception:

        logger.exception(
            "Impossibile inviare l’avviso. "
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

    if (
        message.from_user
        and message.from_user.is_bot
    ):
        return

    if (
        is_emoji_only(raw_text)
        or is_link_only(message)
    ):
        return

    logger.info(
        "Messaggio ricevuto. "
        "Chat=%s Messaggio=%s Caratteri=%s",
        message.chat.id,
        message.message_id,
        len(raw_text),
    )

    try:

        (
            translations,
            original_language,
        ) = await translate_all(raw_text)

        text_html = (
            message.text_html
            or html.escape(raw_text)
        )

        if message.from_user:

            author_name = (
                message.from_user.full_name
            )

        elif message.sender_chat:

            author_name = (
                message.sender_chat.title
                or "Utente"
            )

        else:

            author_name = "Utente"

        name = html.escape(author_name)

        header = (
            f"🗣 <b>{name}</b>:"
            f"{NEWLINE}"
        )

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

        await context.bot.send_message(
            chat_id=message.chat.id,
            message_thread_id=(
                message.message_thread_id
            ),
            text=final_text,
            parse_mode=ParseMode.HTML,
        )

        logger.info(
            "Messaggio tradotto inviato. "
            "Chat=%s Originale=%s",
            message.chat.id,
            message.message_id,
        )

        try:

            await context.bot.delete_message(
                chat_id=message.chat.id,
                message_id=message.message_id,
            )

        except Exception:

            logger.exception(
                "Traduzione inviata, ma cancellazione "
                "dell’originale fallita. "
                "Chat=%s Messaggio=%s",
                message.chat.id,
                message.message_id,
            )

    except TranslationRateLimitError as error:

        logger.warning(
            "Servizio di traduzione sovraccarico o bloccato. "
            "Chat=%s Messaggio=%s Motivo=%s. "
            "Originale conservato.",
            message.chat.id,
            message.message_id,
            error,
        )

        await send_notice(
            message,
            context,
            (
                "⚠️ Il servizio di traduzione "
                "ha rifiutato temporaneamente "
                "la richiesta. "
                "Il messaggio originale è stato "
                "conservato."
            ),
        )

    except TranslationError as error:

        logger.exception(
            "Errore di traduzione. "
            "Chat=%s Messaggio=%s. "
            "Originale conservato.",
            message.chat.id,
            message.message_id,
        )

        await send_notice(
            message,
            context,
            (
                "⚠️ Traduzione non riuscita. "
                "Il messaggio originale è stato "
                "conservato. "
                "Dettagli nei log."
            ),
        )

    except Exception:

        logger.exception(
            "Errore nella traduzione o nell’invio. "
            "Chat=%s Messaggio=%s. "
            "Originale conservato.",
            message.chat.id,
            message.message_id,
        )

        await send_notice(
            message,
            context,
            (
                "⚠️ Traduzione non riuscita. "
                "Il messaggio originale è stato "
                "conservato. "
                "I dettagli dell’errore sono nei log."
            ),
        )


# --------------------------------------------------
# ERRORI DELL’APPLICAZIONE
# --------------------------------------------------

async def handle_application_error(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
):

    error = context.error

    if isinstance(error, Conflict):

        logger.error(
            "CONFLITTO TELEGRAM: un altro processo "
            "sta eseguendo il polling con lo stesso "
            "BOT_TOKEN."
        )

        return

    if error is not None:

        logger.error(
            "Errore non gestito dell’applicazione",
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

    app.add_error_handler(
        handle_application_error
    )

    logger.info(
        "Avvio bot in polling. "
        "Flask sulla porta %s. "
        "Lingue=%s. "
        "Servizio=MyMemory",
        PORT,
        ",".join(LANGUAGES),
    )

    async def post_init(application):

        try:

            translated = await translate_one(
                "Ciao, questo è un test diagnostico.",
                "it",
                "en",
            )

            logger.info(
                "TEST MYMEMORY RIUSCITO. Risultato=%r",
                translated,
            )

        except Exception:

            logger.exception(
                "TEST MYMEMORY FALLITO."
            )

    app.post_init = post_init

    app.run_polling()
