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

AZURE_TRANSLATOR_KEY = os.getenv("AZURE_TRANSLATOR_KEY")
AZURE_TRANSLATOR_REGION = os.getenv("AZURE_TRANSLATOR_REGION")

AZURE_TRANSLATOR_URL = (
    "https://api.cognitive.microsofttranslator.com"
    "/translate"
    "?api-version=3.0"
)

MYMEMORY_EMAIL = os.getenv("MYMEMORY_EMAIL")


# Lingue della gilda.
LANGUAGES = (
    "en",
    "ru",
    "de",
    "fr",
)


LANGUAGE_FLAGS = {
    "en": "🇬🇧",
    "ru": "🇷🇺",
    "de": "🇩🇪",
    "fr": "🇫🇷",
}


NEWLINE = chr(10)
DOUBLE_NEWLINE = NEWLINE * 2


TRANSLATION_TIMEOUT = 25
MAX_PARALLEL_TRANSLATIONS = 3

# Durata della cache in secondi.
CACHE_TTL = 60 * 30


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
# FLASK PER RENDER / UPTIMEROBOT
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
# STATO INTERNO
# --------------------------------------------------

translation_lock = asyncio.Lock()

translation_semaphore = asyncio.Semaphore(
    MAX_PARALLEL_TRANSLATIONS
)

# cache[(source, target, text)] = (traduzione, timestamp)
translation_cache = {}


# --------------------------------------------------
# ECCEZIONI
# --------------------------------------------------

class TranslationError(Exception):
    pass


class TranslationRateLimitError(TranslationError):
    pass


# --------------------------------------------------
# AZURE TRANSLATOR
# --------------------------------------------------

def azure_translate_request(
    text,
    source,
    target,
):
    """
    Traduce un testo con Azure AI Translator.
    """

    url = f"{AZURE_TRANSLATOR_URL}&to={target}"

    if source and source != "auto":
        url += f"&from={source}"

    payload = [
        {
            "text": text,
        }
    ]

    data = json.dumps(payload).encode("utf-8")

    headers = {
        "Ocp-Apim-Subscription-Key": AZURE_TRANSLATOR_KEY,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    if AZURE_TRANSLATOR_REGION:
        headers[
            "Ocp-Apim-Subscription-Region"
        ] = AZURE_TRANSLATOR_REGION

    req = request.Request(
        url,
        data=data,
        headers=headers,
        method="POST",
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
                f"Azure HTTP {exc.code}. {body}"
            ) from exc

        raise TranslationError(
            f"Azure HTTP {exc.code}. {body}"
        ) from exc

    except error.URLError as exc:

        raise TranslationError(
            f"Azure non raggiungibile: {exc}"
        ) from exc

    except TimeoutError as exc:

        raise TranslationError(
            "Timeout durante la richiesta ad Azure."
        ) from exc

    except json.JSONDecodeError as exc:

        raise TranslationError(
            "Azure ha restituito una risposta non valida."
        ) from exc

    try:

        translated = (
            result[0]["translations"][0]["text"]
        )

    except (KeyError, IndexError, TypeError) as exc:

        raise TranslationError(
            "Azure ha restituito una risposta "
            "senza traduzione valida."
        ) from exc

    if not translated.strip():

        raise TranslationError(
            f"Azure ha restituito una traduzione "
            f"vuota verso '{target}'."
        )

    return translated


# --------------------------------------------------
# MYMEMORY FALLBACK
# --------------------------------------------------

def mymemory_request(
    text,
    source,
    target,
):
    """
    Fallback gratuito con MyMemory.
    """

    params = {
        "q": text,
        "langpair": f"{source}|{target}",
    }

    if MYMEMORY_EMAIL:
        params["de"] = MYMEMORY_EMAIL

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

        if exc.code in (429, 403):

            raise TranslationRateLimitError(
                f"MyMemory HTTP {exc.code}"
            ) from exc

        raise TranslationError(
            f"MyMemory HTTP {exc.code}"
        ) from exc

    except error.URLError as exc:

        raise TranslationError(
            f"MyMemory non raggiungibile: {exc}"
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


# --------------------------------------------------
# CACHE
# --------------------------------------------------

def get_cached_translation(
    source,
    target,
    text,
):

    key = (source, target, text)

    cached = translation_cache.get(key)

    if not cached:
        return None

    translated, created_at = cached

    if time.monotonic() - created_at > CACHE_TTL:

        translation_cache.pop(key, None)

        return None

    return translated


def save_cached_translation(
    source,
    target,
    text,
    translated,
):

    key = (source, target, text)

    if len(translation_cache) > 1000:

        oldest_key = min(
            translation_cache,
            key=lambda k: translation_cache[k][1],
        )

        translation_cache.pop(oldest_key, None)

    translation_cache[key] = (
        translated,
        time.monotonic(),
    )


# --------------------------------------------------
# TRADUZIONE SINGOLA
# --------------------------------------------------

async def translate_one(
    text,
    source,
    target,
):
    """
    Traduce con Azure; usa MyMemory solo se Azure fallisce.
    """

    if source == target:
        return text

    cached = get_cached_translation(
        source,
        target,
        text,
    )

    if cached is not None:

        logger.info(
            "Traduzione dalla cache. Target=%s",
            target,
        )

        return cached

    started_at = time.monotonic()

    try:

        translated = await asyncio.to_thread(
            azure_translate_request,
            text,
            source,
            target,
        )

        service = "Azure"

    except (
        TranslationError,
        TranslationRateLimitError,
    ) as azure_error:

        logger.warning(
            "Azure fallito per %s: %s. "
            "Provo MyMemory.",
            target,
            azure_error,
        )

        try:

            translated = await asyncio.to_thread(
                mymemory_request,
                text,
                source,
                target,
            )

            service = "MyMemory"

        except (
            TranslationError,
            TranslationRateLimitError,
        ) as mymemory_error:

            raise TranslationError(
                f"Azure e MyMemory hanno fallito "
                f"per '{target}'. "
                f"Azure: {azure_error}. "
                f"MyMemory: {mymemory_error}"
            ) from mymemory_error

    save_cached_translation(
        source,
        target,
        text,
        translated,
    )

    logger.info(
        "Traduzione completata. "
        "Servizio=%s Source=%s Target=%s Tempo=%.2f",
        service,
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
            "Lingua originale rilevata: %s",
            language,
        )

        return language

    except LangDetectException:

        logger.warning(
            "Lingua non rilevata. Uso italiano."
        )

        return "it"


# --------------------------------------------------
# TRADUZIONE COMPLETA
# --------------------------------------------------

async def translate_all(text):

    async with translation_lock:

        logger.info(
            "Avvio traduzione verso %s lingue con Azure.",
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

        logger.info(
            "Tutte le traduzioni completate in %.2f secondi.",
            time.monotonic() - started_at,
        )

        return (
            translations,
            original_language,
        )


# --------------------------------------------------
# FILTRI MESSAGGI
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
# AVVISI
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
            "Impossibile inviare l’avviso."
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
                "Cancellazione originale fallita."
            )

    except TranslationRateLimitError as error:

        logger.warning(
            "Servizio di traduzione limitato. %s",
            error,
        )

        await send_notice(
            message,
            context,
            (
                "⚠️ Il servizio di traduzione "
                "ha raggiunto temporaneamente un limite. "
                "Il messaggio originale è stato conservato."
            ),
        )

    except TranslationError as error:

        logger.exception(
            "Errore di traduzione: %s",
            error,
        )

        await send_notice(
            message,
            context,
            (
                "⚠️ Traduzione non riuscita. "
                "Il messaggio originale è stato conservato."
            ),
        )

    except Exception:

        logger.exception(
            "Errore generico nella traduzione."
        )

        await send_notice(
            message,
            context,
            (
                "⚠️ Traduzione non riuscita. "
                "Il messaggio originale è stato conservato."
            ),
        )


# --------------------------------------------------
# ERRORI APPLICAZIONE
# --------------------------------------------------

async def handle_application_error(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
):

    error = context.error

    if isinstance(error, Conflict):

        logger.error(
            "CONFLITTO TELEGRAM: un altro processo "
            "usa lo stesso BOT_TOKEN."
        )

        return

    if error is not None:

        logger.error(
            "Errore non gestito.",
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
            "Variabile BOT_TOKEN mancante."
        )

    if not AZURE_TRANSLATOR_KEY:

        raise RuntimeError(
            "Variabile AZURE_TRANSLATOR_KEY mancante."
        )

    if not AZURE_TRANSLATOR_REGION:

        raise RuntimeError(
            "Variabile AZURE_TRANSLATOR_REGION mancante."
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
        "Avvio bot. "
        "Servizio=Azure Translator. "
        "Regione=%s. "
        "Lingue=%s",
        AZURE_TRANSLATOR_REGION,
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
                "TEST AZURE RIUSCITO. Risultato=%r",
                translated,
            )

        except Exception:

            logger.exception(
                "TEST AZURE FALLITO."
            )

    app.post_init = post_init

    app.run_polling()
