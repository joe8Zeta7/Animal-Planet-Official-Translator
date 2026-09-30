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

# Intervallo tra le richieste a Google.
REQUEST_DELAY = 3.0

# Cooldown iniziale dopo un rifiuto di Google.
GOOGLE_COOLDOWN = 120.0

# Cooldown massimo.
MAX_GOOGLE_COOLDOWN = 1800.0

# ATTENZIONE:
# Lasciare True durante questa fase diagnostica.
# Dopo aver verificato i log, si può mettere False.
RUN_GOOGLE_DIAGNOSTIC = True

# Ordine delle richieste a Google.
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
google_failures = 0


# --------------------------------------------------
# TRADUTTORI GOOGLE
# --------------------------------------------------

# Creiamo una sola istanza per ogni lingua e la riutilizziamo.
# In questo modo non viene ricreato il translator ad ogni messaggio.

google_translators = {
    language: GoogleTranslator(
        source="auto",
        target=language,
    )
    for language in LANGUAGES
}


# --------------------------------------------------
# DIAGNOSTICA GOOGLE
# --------------------------------------------------

async def google_diagnostic_test():
    """
    Esegue UNA sola richiesta di prova a Google all'avvio.
    Serve esclusivamente per capire se Google sta rifiutando
    anche una singola richiesta indipendente dal normale
    ciclo di traduzione.
    """

    if not RUN_GOOGLE_DIAGNOSTIC:
        logger.info(
            "Test diagnostico Google disattivato."
        )
        return

    logger.info(
        "Avvio test diagnostico Google..."
    )

    started_at = time.monotonic()

    try:
        translator = google_translators["en"]

        result = await asyncio.to_thread(
            translator.translate,
            "Ciao, questo è un test diagnostico.",
        )

        elapsed = time.monotonic() - started_at

        if (
            not isinstance(result, str)
            or not result.strip()
        ):
            logger.warning(
                "TEST GOOGLE: richiesta riuscita "
                "ma risposta vuota."
            )
            return

        logger.info(
            "TEST GOOGLE RIUSCITO. "
            "Risultato=%r Tempo=%.2f secondi.",
            result,
            elapsed,
        )

    except TooManyRequests as error:
        elapsed = time.monotonic() - started_at

        logger.error(
            "TEST GOOGLE FALLITO: Google ha rifiutato "
            "anche una singola richiesta. "
            "Tempo=%.2f secondi. Errore=%s",
            elapsed,
            error,
        )

    except Exception:
        elapsed = time.monotonic() - started_at

        logger.exception(
            "TEST GOOGLE FALLITO con errore inatteso. "
            "Tempo=%.2f secondi.",
            elapsed,
        )


# --------------------------------------------------
# TRADUZIONE
# --------------------------------------------------

async def translate_all(text):
    global google_blocked_until
    global google_failures

    async with translation_lock:

        # ------------------------------------------
        # CONTROLLO COOLDOWN GOOGLE
        # ------------------------------------------

        remaining = (
            google_blocked_until
            - time.monotonic()
        )

        if remaining > 0:
            raise TooManyRequests(
                f"Google è in pausa: restano circa "
                f"{int(remaining) + 1} secondi."
            )

        translations = {}

        # ------------------------------------------
        # TRADUZIONE NELLE VARIE LINGUE
        # ------------------------------------------

        for index, language in enumerate(
            LANGUAGES,
            start=1,
        ):

            logger.info(
                "Tentativo di traduzione %s/%s verso: %s",
                index,
                len(LANGUAGES),
                language,
            )

            started_at = time.monotonic()

            try:
                translator = google_translators[
                    language
                ]

                translated = await asyncio.to_thread(
                    translator.translate,
                    text,
                )

                elapsed = (
                    time.monotonic()
                    - started_at
                )

                logger.info(
                    "Richiesta Google completata. "
                    "Target=%s Tempo=%.2f secondi.",
                    language,
                    elapsed,
                )

            except TooManyRequests as error:

                # ----------------------------------
                # BACKOFF PROGRESSIVO
                # ----------------------------------

                google_failures += 1

                cooldown = min(
                    GOOGLE_COOLDOWN
                    * (
                        2
                        ** (
                            google_failures - 1
                        )
                    ),
                    MAX_GOOGLE_COOLDOWN,
                )

                google_blocked_until = (
                    time.monotonic()
                    + cooldown
                )

                logger.warning(
                    "Google ha bloccato la traduzione "
                    "verso %s "
                    "(richiesta %s/%s; "
                    "traduzioni completate: %s). "
                    "Fallimento consecutivo=%s. "
                    "Pausa di %.0f secondi.",
                    language,
                    index,
                    len(LANGUAGES),
                    len(translations),
                    google_failures,
                    cooldown,
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

            # --------------------------------------
            # CONTROLLO RISPOSTA
            # --------------------------------------

            if (
                not isinstance(translated, str)
                or not translated.strip()
            ):
                raise ValueError(
                    f"Traduzione vuota per la lingua "
                    f"'{language}'."
                )

            translations[language] = translated

            logger.info(
                "Traduzione verso %s completata in %.2f secondi.",
                language,
                time.monotonic() - started_at,
            )

            # --------------------------------------
            # PAUSA TRA LE RICHIESTE
            # --------------------------------------

            if index < len(LANGUAGES):
                await asyncio.sleep(
                    REQUEST_DELAY
                )

        # ------------------------------------------
        # TUTTE LE TRADUZIONI RIUSCITE
        # ------------------------------------------

        if google_failures > 0:
            logger.info(
                "Google nuovamente disponibile. "
                "Reset del contatore dei fallimenti "
                "consecutivi."
            )

        google_failures = 0
        google_blocked_until = 0.0

        logger.info(
            "Tutte le traduzioni completate "
            "correttamente (%s/%s).",
            len(translations),
            len(LANGUAGES),
        )

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

    encoded_text = message.text.encode(
        "utf-16-le"
    )

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
            remaining_parts.append(
                encoded_text[cursor:start]
            )

        cursor = max(cursor, end)

    remaining_parts.append(
        encoded_text[cursor:]
    )

    remaining_text = b"".join(
        remaining_parts
    ).decode("utf-16-le")

    return not remaining_text.strip()


# --------------------------------------------------
# STIMA DELLA LINGUA ORIGINALE
# --------------------------------------------------

def guess_original_language(
    raw_text,
    translations,
):
    normalized_original = (
        raw_text.strip().casefold()
    )

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

        translated = translations.get(
            language
        )

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

async def send_notice(
    message,
    context,
    text,
):
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

        translations = await translate_all(
            raw_text
        )

        # ------------------------------------------
        # LINGUA ORIGINALE
        # ------------------------------------------

        original_language = (
            guess_original_language(
                raw_text,
                translations,
            )
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
            message_thread_id=message.message_thread_id,
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
    # GOOGLE RIFIUTA LA RICHIESTA
    # ----------------------------------------------

    except TooManyRequests as error:

        logger.warning(
            "Traduzione saltata. "
            "Chat=%s Messaggio=%s "
            "Motivo=%s. Originale conservato.",
            message.chat.id,
            message.message_id,
            error,
        )

        remaining = max(
            0,
            int(
                google_blocked_until
                - time.monotonic()
            )
            + 1,
        )

        await send_notice(
            message,
            context,
            (
                "⚠️ Google sta rifiutando le "
                "richieste di traduzione. "
                "Il messaggio originale è stato "
                "conservato. "
                f"Il bot consentirà un nuovo "
                f"tentativo tra circa "
                f"{remaining} secondi. "
                "Questo messaggio non verrà "
                "ritradotto automaticamente."
            ),
        )

    # ----------------------------------------------
    # ALTRI ERRORI
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
        "Avvio versione diagnostica in polling. "
        "Flask sulla porta %s. Lingue=%s",
        PORT,
        ",".join(LANGUAGES),
    )

    # ----------------------------------------------
    # TEST GOOGLE
    # ----------------------------------------------

    async def post_init(application):

        await google_diagnostic_test()

    app.post_init = post_init

    # ----------------------------------------------
    # AVVIO POLLING
    # ----------------------------------------------

    app.run_polling()
