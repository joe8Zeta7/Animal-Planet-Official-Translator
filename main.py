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

# Endpoint pubblico gratuito LibreTranslate.
# Può essere sostituito da Render tramite variabile
# d'ambiente LIBRETRANSLATE_URL.
LIBRETRANSLATE_URL = os.getenv(
    "LIBRETRANSLATE_URL",
    "https://libretranslate.de",
).rstrip("/")


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
TRANSLATION_TIMEOUT = 30


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
    text,
    source,
    target,
):
    """
    Esegue una singola richiesta POST a LibreTranslate.

    Restituisce:
        {
            "translatedText": "...",
            ...
        }
    """

    url = (
        f"{LIBRETRANSLATE_URL}/translate"
    )

    payload = {
        "q": text,
        "source": source,
        "target": target,
        "format": "text",
    }

    data = json.dumps(
        payload
    ).encode("utf-8")

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

            return result

    except error.HTTPError as exc:

        try:
            body = exc.read().decode(
                "utf-8",
                errors="replace",
            )
        except Exception:
            body = ""

        if exc.code in (
            429,
            403,
        ):

            raise TranslationRateLimitError(
                f"LibreTranslate ha rifiutato "
                f"la richiesta HTTP {exc.code}. "
                f"Risposta: {body}"
            ) from exc

        raise TranslationError(
            f"LibreTranslate HTTP {exc.code}. "
            f"Risposta: {body}"
        ) from exc

    except error.URLError as exc:

        raise TranslationError(
            f"Impossibile raggiungere "
            f"LibreTranslate: {exc}"
        ) from exc

    except TimeoutError as exc:

        raise TranslationError(
            "Timeout durante la richiesta "
            "a LibreTranslate."
        ) from exc

    except json.JSONDecodeError as exc:

        raise TranslationError(
            "LibreTranslate ha restituito "
            "una risposta non valida."
        ) from exc


# --------------------------------------------------
# DIAGNOSTICA LIBRETRANSLATE
# --------------------------------------------------

async def libretranslate_diagnostic_test():
    """
    Esegue una singola richiesta diagnostica
    all'avvio del bot.
    """

    logger.info(
        "Avvio test diagnostico LibreTranslate..."
    )

    started_at = time.monotonic()

    try:

        result = await asyncio.to_thread(
            libretranslate_request,
            "Ciao, questo è un test diagnostico.",
            "auto",
            "en",
        )

        elapsed = (
            time.monotonic()
            - started_at
        )

        translated = result.get(
            "translatedText"
        )

        detected = result.get(
            "detectedLanguage"
        )

        if (
            not isinstance(
                translated,
                str,
            )
            or not translated.strip()
        ):

            raise TranslationError(
                "Risposta vuota da LibreTranslate."
            )

        logger.info(
            "TEST LIBRETRANSLATE RIUSCITO. "
            "Risultato=%r Lingua rilevata=%r "
            "Tempo=%.2f secondi.",
            translated,
            detected,
            elapsed,
        )

    except Exception:

        elapsed = (
            time.monotonic()
            - started_at
        )

        logger.exception(
            "TEST LIBRETRANSLATE FALLITO. "
            "Endpoint=%s Tempo=%.2f secondi.",
            LIBRETRANSLATE_URL,
            elapsed,
        )


# --------------------------------------------------
# SINGOLA TRADUZIONE
# --------------------------------------------------

async def translate_one(
    text,
    source,
    target,
):
    """
    Traduce un testo verso una lingua.
    """

    started_at = time.monotonic()

    result = await asyncio.to_thread(
        libretranslate_request,
        text,
        source,
        target,
    )

    translated = result.get(
        "translatedText"
    )

    if (
        not isinstance(
            translated,
            str,
        )
        or not translated.strip()
    ):

        raise TranslationError(
            f"Traduzione vuota verso '{target}'."
        )

    elapsed = (
        time.monotonic()
        - started_at
    )

    logger.info(
        "Traduzione completata. "
        "Target=%s Tempo=%.2f secondi.",
        target,
        elapsed,
    )

    return translated


# --------------------------------------------------
# TRADUZIONE COMPLETA
# --------------------------------------------------

async def translate_all(text):

    async with translation_lock:

        logger.info(
            "Avvio traduzione verso %s lingue "
            "tramite LibreTranslate.",
            len(LANGUAGES),
        )

        # --------------------------------------------------
        # PRIMA RICHIESTA:
        # usiamo source="auto" per individuare
        # automaticamente la lingua originale.
        # --------------------------------------------------

        started_at = time.monotonic()

        first_result = await asyncio.to_thread(
            libretranslate_request,
            text,
            "auto",
            "en",
        )

        first_elapsed = (
            time.monotonic()
            - started_at
        )

        english_translation = (
            first_result.get(
                "translatedText"
            )
        )

        detected_info = (
            first_result.get(
                "detectedLanguage"
            )
        )

        if (
            not isinstance(
                english_translation,
                str,
            )
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

        if isinstance(
            detected_info,
            dict,
        ):

            detected_language = (
                detected_info.get(
                    "language"
                )
            )

            if detected_language:
                original_language = (
                    detected_language
                )

        logger.info(
            "Lingua originale rilevata: %s. "
            "Prima traduzione (en) completata "
            "in %.2f secondi.",
            original_language or "sconosciuta",
            first_elapsed,
        )

        translations = {}

        # Se il testo originale è inglese,
        # non dobbiamo tradurlo.
        if original_language == "en":

            translations["en"] = text

        else:

            translations["en"] = (
                english_translation
            )

        # --------------------------------------------------
        # RESTANTI LINGUE
        # --------------------------------------------------

        remaining_languages = [
            language
            for language in LANGUAGES
            if language != "en"
        ]

        # Se la lingua originale è una delle
        # nostre lingue, possiamo conservare
        # direttamente il testo originale.
        if (
            original_language
            in remaining_languages
        ):

            translations[
                original_language
            ] = text

            remaining_languages.remove(
                original_language
            )

        # --------------------------------------------------
        # TRADUZIONI PARALLELE
        # --------------------------------------------------

        async def translate_target(
            language
        ):

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
            for language
            in remaining_languages
        ]

        if tasks:

            results = await asyncio.gather(
                *tasks
            )

            for (
                language,
                translated,
            ) in results:

                translations[
                    language
                ] = translated

        # --------------------------------------------------
        # CONTROLLO FINALE
        # --------------------------------------------------

        missing = [
            language
            for language in LANGUAGES
            if language
            not in translations
        ]

        if missing:

            raise TranslationError(
                "Mancano le traduzioni per: "
                + ", ".join(missing)
            )

        logger.info(
            "Tutte le traduzioni completate "
            "correttamente (%s/%s).",
            len(translations),
            len(LANGUAGES),
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

        # ------------------------------------------
        # TRADUZIONE
        # ------------------------------------------

        (
            translations,
            original_language,
        ) = await translate_all(
            raw_text
        )

        # ------------------------------------------
        # TESTO ORIGINALE
        # ------------------------------------------

        text_html = (
            message.text_html
            or html.escape(raw_text)
        )

        # ------------------------------------------
        # AUTORE
        # ------------------------------------------

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

        name = html.escape(
            author_name
        )

        header = (
            f"🗣 <b>{name}</b>:"
            f"{NEWLINE}"
        )

        # ------------------------------------------
        # TRADUZIONI FINALI
        # ------------------------------------------

        translation_blocks = []

        for (
            language,
            flag,
        ) in LANGUAGE_FLAGS.items():

            if language == original_language:
                continue

            translated_text = html.escape(
                translations[language]
            )

            translation_blocks.append(
                f"{flag} {translated_text}"
            )

        translations_text = (
            DOUBLE_NEWLINE.join(
                translation_blocks
            )
        )

        # ------------------------------------------
        # MESSAGGIO FINALE
        # ------------------------------------------

        final_text = (
            f"{header}"
            f"{text_html}"
            f"{DOUBLE_NEWLINE}"
            f"<blockquote expandable>"
            f"{translations_text}"
            f"</blockquote>"
        )

        # ------------------------------------------
        # INVIA PRIMA IL NUOVO MESSAGGIO
        # ------------------------------------------

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

        # ------------------------------------------
        # CANCELLA ORIGINALE
        # SOLO DOPO L'INVIO RIUSCITO
        # ------------------------------------------

        try:

            await context.bot.delete_message(
                chat_id=message.chat.id,
                message_id=message.message_id,
            )

        except Exception:

            logger.exception(
                "Traduzione inviata, ma "
                "cancellazione dell'originale fallita. "
                "Chat=%s Messaggio=%s",
                message.chat.id,
                message.message_id,
            )

    # ----------------------------------------------
    # ERRORE SERVIZIO DI TRADUZIONE
    # ----------------------------------------------

    except TranslationRateLimitError as error:

        logger.warning(
            "LibreTranslate ha rifiutato la richiesta. "
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

    # ----------------------------------------------
    # ALTRI ERRORI DI TRADUZIONE
    # ----------------------------------------------

    except TranslationError as error:

        logger.exception(
            "Errore LibreTranslate. "
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

    # ----------------------------------------------
    # ERRORE GENERICO
    # ----------------------------------------------

    except Exception:

        logger.exception(
            "Errore nella traduzione o nell'invio. "
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
            "CONFLITTO TELEGRAM: un altro processo "
            "sta eseguendo il polling con lo stesso "
            "BOT_TOKEN. "
            "Controllare eventuali altre istanze "
            "del bot, processi locali o sovrapposizioni "
            "durante il deploy."
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

    # ----------------------------------------------
    # FLASK
    # ----------------------------------------------

    threading.Thread(
        target=run_flask,
        daemon=True,
    ).start()

    # ----------------------------------------------
    # TELEGRAM
    # ----------------------------------------------

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
        "LibreTranslate=%s",
        PORT,
        ",".join(LANGUAGES),
        LIBRETRANSLATE_URL,
    )

    # ----------------------------------------------
    # TEST LIBRETRANSLATE
    # ----------------------------------------------

    async def post_init(application):

        await libretranslate_diagnostic_test()

    app.post_init = post_init

    # ----------------------------------------------
    # AVVIO POLLING
    # ----------------------------------------------

    app.run_polling()
