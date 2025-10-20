# Telegram Translator Bot (Google Translate)

Un bot Telegram che traduce i messaggi usando Google Translate (via `googletrans`).

## Funzionalità
- Se il messaggio è in inglese 🇬🇧 → traduce in russo 🇷🇺 e coreano 🇰🇷
- Se il messaggio è in russo 🇷🇺 → traduce in inglese 🇬🇧 e coreano 🇰🇷
- Se il messaggio è in coreano 🇰🇷 → traduce in russo 🇷🇺 e inglese 🇬🇧
- Se il messaggio è in altra lingua → traduce in inglese 🇬🇧, russo 🇷🇺 e coreano 🇰🇷
- Ignora emoji e immagini

## Setup
1. Inserisci il tuo token Telegram e l’URL del webhook nel file `.env`
2. Installa le dipendenze con `pip install -r requirements.txt`
3. Avvia `main.py`
