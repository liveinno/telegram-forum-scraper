      _____                    _____                    _____          
     /\    \                  /\    \                  /\    \         
    /::\    \                /::\    \                /::\    \        
    \:::\    \              /::::\    \              /::::\    \       
     \:::\    \            /::::::\    \            /::::::\    \      
      \:::\    \          /:::/\:::\    \          /:::/\:::\    \     
       \:::\    \        /:::/__\:::\    \        /:::/__\:::\    \    
       /::::\    \      /::::\   \:::\    \       \:::\   \:::\    \   
      /::::::\    \    /::::::\   \:::\    \    ___\:::\   \:::\    \  
     /:::/\:::\    \  /:::/\:::\   \:::\    \  /\   \:::\   \:::\    \ 
    /:::/  \:::\____\/:::/  \:::\   \:::\____\/::\   \:::\   \:::\____\
   /:::/    \::/    /\::/    \:::\   \::/    /\:::\   \:::\   \::/    /
  /:::/    / \/____/  \/____/ \:::\   \/____/  \:::\   \:::\   \/____/ 
 /:::/    /                    \:::\    \       \:::\   \:::\    \     
/:::/    /                      \:::\____\       \:::\   \:::\____\    
\::/    /                        \::/    /        \:::\  /:::/    /    
 \/____/                          \/____/          \:::\/:::/    /     
                                                    \::::::/    /      
                                                     \::::/    /       
                                                      \::/    /        
                                                       \/____/         
                                                                    

Мощный асинхронный скрейпер Telegram с поддержкой форумов, топиков, фоновой загрузки медиа и продвинутым CLI.

## ✨ Ключевые особенности

- **📋 Поддержка форумов и топиков** — автоматическое определение форум-каналов и парсинг по отдельным темам (General + топики)
- **⚡ Rate Limiting** — встроенный ограничитель 29 запросов/сек для избежания FloodWaitError
- **🔄 Фоновые воркеры** — асинхронная очередь загрузки медиа (3+ параллельных потока)
- **💾 Автовосстановление** — восстановление состояния из SQLite даже при удалении `state.json`
- **🎯 Binary Search** — точная оценка последнего сообщения для реалистичного прогресса
- **🛡️ Dry-run режим** — тестирование без записи в БД и скачивания файлов
- **📊 Экспорт** — CSV и JSON с сохранением структуры топиков
- **🚦 Graceful Shutdown** — корректное завершение по Ctrl+C с сохранением прогресса

## 🚀 Быстрый старт

### Требования
- Python 3.8+
- Telegram API ID и Hash ([my.telegram.org](https://my.telegram.org))

### Установка

```bash
git clone https://github.com/liveinno/telegram-forum-scraper.git
cd telegram-forum-scraper
pip install -r requirements.txt
```

### Первый запуск

```bash
python telegram-forum-scraper.py --api-id YOUR_API_ID --api-hash YOUR_API_HASH --phone +79991234567
```

## 🛠️ Использование

### Базовый парсинг каналов

```bash
# Парсинг конкретных каналов (одноразово)
python telegram-forum-scraper.py --scrape --channels "@channel1,@channel2,-1001234567890"

# Из файла со списком каналов
python telegram-forum-scraper.py --scrape --channels-file channels.txt

# С ограничением по количеству сообщений
python telegram-forum-scraper.py --scrape --channels "@channel1" --limit 1000

# С ограничением по времени (секунды)
python telegram-forum-scraper.py --scrape --channels "@channel1" --time-limit 3600
```

### Работа с медиа

```bash
# Без скачивания медиа (только текст)
python telegram-forum-scraper.py --scrape --channels "@channel1" --no-media

# Перезагрузка недостающих медиафайлов
python telegram-forum-scraper.py --rescrape-media --channels "@channel1"

# Изменить количество параллельных загрузок (по умолчанию 3)
python telegram-forum-scraper.py --scrape --channels "@channel1" --workers 5
```

### Тестирование и отладка

```bash
# Dry-run: посмотреть что будет спаршено без записи в БД
python telegram-forum-scraper.py --scrape --channels "@channel1" --dry-run

# Просмотр сохраненных каналов
python telegram-forum-scraper.py --view

# Список диалогов аккаунта (для поиска ID)
python telegram-forum-scraper.py --list

# Инспекция БД: вывести случайные записи
python telegram-forum-scraper.py --scrape --channels "@channel1" --inspect-db --sample 5
```

### Экспорт данных

```bash
# Экспорт всех каналов в CSV и JSON
python telegram-forum-scraper.py --export
```

## 📁 Структура проекта

```
telegram-forum-scraper/
├── telegram-forum-scraper.py  # Основной скрипт
├── state.json              # Состояние парсинга (авто-создание)
├── requirements.txt        # Зависимости
├── @channel_name/          # Директория для каждого канала
│   ├── @channel_name.db    # SQLite база
│   ├── @channel_name.csv   # Экспорт CSV
│   ├── @channel_name.json  # Экспорт JSON
│   └── media/              # Загруженные файлы
│       ├── 12345.jpg
│       └── 12346 (1).pdf
└── README.md
```

## 🗄️ Структура базы данных

Таблица `messages`:

- `message_id` — ID сообщения в Telegram
- `date` — дата/время
- `sender_id`, `first_name`, `last_name`, `username` — данные отправителя
- `message` — текст сообщения (очищенный от HTML)
- `media_type` — тип медиа (MessageMediaPhoto, MessageMediaDocument)
- `media_path` — путь к скачанному файлу
- `reply_to` — ID сообщения, на которое ответ
- `topic_id`, `topic_title` — ID и название топика (для форумов)

## ⚙️ Как это работает

### Форумы и топики

Если канал является форумом (имеет обсуждения), скрипт:

- Получает список всех топиков через `GetForumTopicsRequest`
- Парсит каждый топик отдельно с сохранением `topic_id` и `topic_title`
- Сохраняет оффсет для каждого топика отдельно (возможность докачки)

### Rate Limiting

- Глобальный семафор: минимальный интервал между запросами 1/29 секунды
- Защита от `FloodWaitError` с автоматической паузой

### Восстановление после сбоев

- Каждые 10 сообщений сохраняется `state.json`
- При запуске проверяет существующие SQLite файлы и восстанавливает оффсеты из `MAX(message_id)`
- Поддержка докачки по таймаутам

## 📝 Лицензия

Этот проект базируется на коде telegram-scraper от unnohwn (MIT License).
Модификации и улучшения: Copyright (c) 2026 Azat (liveinno)
Распространяется под лицензией Creative Commons Attribution-NonCommercial 4.0 International (CC BY-NC 4.0).

- ✅ Разрешено: использование, модификация, распространение в некоммерческих целях
- ❌ Запрещено: использование в коммерческих продуктах, SaaS-решениях, платных сборках
- ⚠️ Условие: обязательное указание авторства

## 🤝 Благодарности

- unnohwn — оригинальный telegram-scraper (MIT)
- Telethon — библиотека для работы с Telegram API

## ⚠️ Дисклеймер

Используйте только для каналов, где у вас есть право на сбор данных. Соблюдайте Terms of Service Telegram и политики конкретных каналов. Автор не несет ответственности за неправомерное использование инструмента.
