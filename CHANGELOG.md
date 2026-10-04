# Changelog

Все заметные изменения проекта фиксируются здесь.
Формат основан на [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/),
версионирование — [Semantic Versioning](https://semver.org/lang/ru/).

## [1.1.0] - 2026-10-04

### Fixed
- `GetForumTopicsRequest` перенесён в `telethon.tl.functions.messages` и принимает `peer=` вместо `channel=` — совместимость с Telethon ≥ 1.45, где старый вызов падал с `TypeError`
- `remove_diacritics()` в `src/utils.py` больше не возвращает `None`: дописано тело (NFKD-нормализация + фильтрация combining marks)

### Added
- `message_text_with_links()`: URL из `MessageEntityTextUrl` вшиваются обратно в текст сообщения — `«по ссылке»` сохраняется как `«по ссылке (URL)»`; голые `MessageEntityUrl` пропускаются (уже в тексте)
- `__version__` и флаг `--version`
- `CHANGELOG.md`

### Changed
- `start.sh`: `umask 077` и проброс аргументов `"$@"` в скрипт
- `.gitignore`: `*.db.bak`, `logs_scrape_*.log`, `*.log`, `.env*`, WAL/SHM-сайдкары, `*.bak`, патч-остатки, `channels.txt`; экспорты `<канал>/<канал>.csv|json` и `channels.txt` защищены от случайного коммита

## [1.0.0] - 2026-09-24

### Added
- Initial commit: Advanced Telegram scraper with forum support
