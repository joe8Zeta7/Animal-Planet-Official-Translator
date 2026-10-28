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

# Istanze LibreTranslate pubbliche.
# La prima viene usata per prima; le altre sono fallback.
LIBRETRANSLATE_URLS = [
    url.rstrip("/")
    for url in os.getenv(
        "LIBRETRANSLATE_URLS",
        "https://translate.mentality.rip,"
        "https://translate.cutie.dating,"
        "https://translate.fedilab.app",
    ).split(",")
    if url.strip()
]


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

# Tentativi totali per ogni traduzione:
# uno per ogni istanza disponibile.
MAX_TRANSLATION_ATTEMPTS = len(LIBRETRANSLATE_URLS)

# Pausa tra un tentativo e l’altro, in secondi.
RETRY_DELAY = 1

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
# CHIAMATA A LIBRETRANSLATE
# --------------------------------------------------

def libretranslate_request(
    base_url,
    text,
    source,
    target,
):
    """
    Esegue una singola richiesta POST a LibreTranslate.
    """

    url = f"{base_url}/translate"

    payload = {
        "q": text,
        "source": source,
        "target": target,
        "format": "text",
    }

    data = json.dumps(payload).encode("utf-8")

    req = request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": (
                "AnimalPlanetOfficialTranslator/1.0"
            ),
        },
        method="POST",
    )

    try:

        with request.urlopen(
            req,
            timeout=TRANSLATION_TIMEOUT,
        ) as response:

            response_data = response.read()

            result = json.loads(
                response_data.decode("utf-8")
            )

            translated = result.get("translatedText")

            if (
                not isinstance(translated, str)
                or not translated.strip()
            ):

                raise TranslationError(
                    "Risposta senza testo tradotto."
                )

            return result

    except error.HTTPError as exc:

        try:
            body = exc.read().decode(
                "utf-8",
                errors="replace",
            )
        except Exception:
            body = ""

        if exc.code in (429, 403, 503):

            raise TranslationRateLimitError(
                f"LibreTranslate ha rifiutato la richiesta "
                f"HTTP {exc.code}. "
                f"Risposta: {body}"
            ) from exc

        raise TranslationError(
            f"LibreTranslate HTTP {exc.code}. "
            f"Risposta: {body}"
        ) from exc

    except error.URLError as exc:

        raise TranslationError(
            f"Impossibile raggiungere LibreTranslate: {exc}"
        ) from exc

    except TimeoutError as exc:

        raise TranslationError(
            "Timeout durante la richiesta a LibreTranslate."
        ) from exc

    except json.JSONDecodeError as exc:

        raise TranslationError(
            "LibreTranslate ha restituito una risposta non valida."
        ) from exc


# --------------------------------------------------
# SINGOLA TRADUZIONE CON FALLBACK
# --------------------------------------------------

async def translate_one(
    text,
    source,
    target,
):
    """
    Traduce un testo provando tutte le istanze disponibili.
    """

    started_at = time.monotonic()

    last_error = None

    for attempt, base_url in enumerate(
        LIBRETRANSLATE_URLS,
        start=1,
    ):

        try:

            logger.info(
                "Traduzione verso %s. "
                "Tentativo %s/%s su %s.",
                target,
                attempt,
                MAX_TRANSLATION_ATTEMPTS,
                base_url,
            )

            result = await asyncio.to_thread(
                libretranslate_request,
                base_url,
                text,
                source,
                target,
            )

            translated = result.get("translatedText")

            if (
                not isinstance(translated, str)
                or not translated.strip()
            ):

                raise TranslationError(
                    f"Traduzione vuota verso '{target}'."
                )

            elapsed = time.monotonic() - started_at

            logger.info(
                "Traduzione completata. "
                "Target=%s Istanza=%s Tempo=%.2f secondi.",
                target,
                base_url,
                elapsed,
            )

            return translated

        except (
            TranslationError,
            TranslationRateLimitError,
        ) as exc:

            last_error = exc

            logger.warning(
                "Istanza %s fallita per target %s. "
                "Errore: %s",
                base_url,
                target,
                exc,
            )

            if attempt < MAX_TRANSLATION_ATTEMPTS:

                await asyncio.sleep(RETRY_DELAY)

    raise TranslationError(
        f"Tutte le istanze LibreTranslate hanno fallito "
        f"per la lingua '{target}'. "
        f"Ultimo errore: {last_error}"
    )


# --------------------------------------------------
# DIAGNOSTICA ALL’AVVIO
# --------------------------------------------------

async def libretranslate_diagnostic_test():

    logger.info(
        "Avvio test diagnostico LibreTranslate..."
    )

    started_at = time.monotonic()

    try:

        translated = await translate_one(
            "Ciao, questo è un test diagnostico.",
            "auto",
            "en",
        )

        elapsed = time.monotonic() - started_at

        logger.info(
            "TEST LIBRETRANSLATE RIUSCITO. "
            "Risultato=%r Tempo=%.2f secondi.",
            translated,
            elapsed,
        )

    except Exception:

        elapsed = time.monotonic() - started_at

        logger.exception(
            "TEST LIBRETRANSLATE FALLITO. "
            "Istanze=%s Tempo=%.2f secondi.",
            ", ".join(LIBRETRANSLATE_URLS),
            elapsed,
        )


# --------------------------------------------------
# TRADUZIONE COMPLETA
# --------------------------------------------------

async def translate_all(text):

    async with translation_lock:

        logger.info(
            "Avvio traduzione verso %s lingue.",
            len(LANGUAGES),
        )

        started_at = time.monotonic()

        # --------------------------------------------------
        # PRIMA TRADUZIONE: rileva anche la lingua originale.
        # --------------------------------------------------

        first_result = None

        last_error = None

        for attempt, base_url in enumerate(
            LIBRETRANSLATE_URLS,
            start=1,
        ):

            try:

                logger.info(
                    "Rilevamento lingua e traduzione inglese. "
                    "Tentativo %s/%s su %s.",
                    attempt,
                    MAX_TRANSLATION_ATTEMPTS,
                    base_url,
                )

                first_result = await asyncio.to_thread(
                    libretranslate_request,
                    base_url,
                    text,
                    "auto",
                    "en",
                )

                break

            except (
                TranslationError,
                TranslationRateLimitError,
            ) as exc:

                last_error = exc

                logger.warning(
                    "Istanza %s fallita nella prima traduzione. "
                    "Errore: %s",
                    base_url,
                    exc,
                )

                if attempt < MAX_TRANSLATION_ATTEMPTS:

                    await asyncio.sleep(RETRY_DELAY)

        if first_result is None:

            raise TranslationError(
                "Tutte le istanze hanno fallito "
                "la prima traduzione. "
                f"Ultimo errore: {last_error}"
            )

        english_translation = first_result.get(
            "translatedText"
        )

        detected_info = first_result.get(
            "detectedLanguage"
        )

        if (
            not isinstance(english_translation, str)
            or not english_translation.strip()
        ):

            raise TranslationError(
                "LibreTranslate ha restituito "
                "una traduzione inglese vuota."
            )

        # --------------------------------------------------
        # LINGUA ORIGINALE
        # --------------------------------------------------

        original_language = None

        if isinstance(detected_info, dict):

            detected_language = detected_info.get(
                "language"
            )

            if detected_language:

                original_language = detected_language

        logger.info(
            "Lingua originale rilevata: %s.",
            original_language or "sconosciuta",
        )

        translations = {}

        if original_language == "en":

            translations["en"] = text

        else:

            translations["en"] = english_translation

        remaining_languages = [
            language
            for language in LANGUAGES
            if language != "en"
        ]

        if (
            original_language
            in remaining_languages
        ):

            translations[original_language] = text

            remaining_languages.remove(
                original_language
            )

        # --------------------------------------------------
        # TRADUZIONI CONTROLLATE, MAX 3 PER VOLTA
        # --------------------------------------------------

        async def translate_target(language):

            async with translation_semaphore:

                logger.info(
                    "Avvio traduzione verso: %s",
                    language,
                )

                translated = await translate_one(
                    text,
                    original_language or "auto",
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
        "Istanze LibreTranslate=%s",
        PORT,
        ",".join(LANGUAGES),
        ", ".join(LIBRETRANSLATE_URLS),
    )

    async def post_init(application):

        await libretranslate_diagnostic_test()

    app.post_init = post_init

    app.run_polling()
