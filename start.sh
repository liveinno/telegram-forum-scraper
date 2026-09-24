#!/bin/bash

umask 077

echo "Launching Telegram Forum Scraper..."
cd "$(dirname "$0")"
source venv/bin/activate
python telegram-forum-scraper.py "$@"
