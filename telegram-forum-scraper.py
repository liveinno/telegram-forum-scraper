import os
import sqlite3
import json
import csv
import asyncio
from telethon import TelegramClient
from telethon.tl.types import MessageMediaPhoto, MessageMediaDocument, User, PeerChannel, Channel, Chat, InputPeerChannel, MessageEntityTextUrl, MessageEntityUrl
from telethon.tl.functions.messages import GetForumTopicsRequest
from telethon.errors import FloodWaitError, RPCError
import aiohttp
import sys
import time
import re
from datetime import datetime
import argparse
import signal

def display_ascii_art():
    WHITE = "\033[97m"
    RESET = "\033[0m"
    
    art = r"""
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
    """
    
    print(WHITE + art + RESET)

__version__ = "1.1.0"

# Получаем директорию, где находится скрипт
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(SCRIPT_DIR, 'state.json')
MAX_MEDIA_SIZE_MB = 15  # Maximum media size in MB

# Асинхронная очередь для фоновой загрузки медиа
media_download_queue = asyncio.Queue()
MEDIA_DOWNLOAD_WORKERS = 3  # Максимум 3 одновременные загрузки
media_download_semaphore = asyncio.Semaphore(MEDIA_DOWNLOAD_WORKERS)
media_download_tasks = []  # Список активных задач загрузки

# CLI arguments
parser = argparse.ArgumentParser(description="Telegram scraper non-interactive runner")
parser.add_argument('--version', action='version', version=f'%(prog)s {__version__}')
parser.add_argument('--scrape', action='store_true', help='Run one-shot scraping for provided channels or saved state channels')
parser.add_argument('--channels', type=str, help='Comma-separated list of channels to scrape, e.g. @ens1enp1,@hikvision_chat,-100123')
parser.add_argument('--channels-file', type=str, help='Path to a file with channels, one per line')
parser.add_argument('--no-media', action='store_true', help='Disable media downloading')
parser.add_argument('--limit', type=int, default=0, help='Max messages per channel for this run')
parser.add_argument('--time-limit', type=int, default=0, help='Max seconds to run per channel for this run')
parser.add_argument('--dry-run', action='store_true', help='Do not write to DB or download media')
parser.add_argument('--workers', type=int, default=MEDIA_DOWNLOAD_WORKERS, help='Number of concurrent media download workers')
parser.add_argument('--api-id', type=str, help='Telegram API ID')
parser.add_argument('--api-hash', type=str, help='Telegram API Hash')
parser.add_argument('--phone', type=str, help='Phone number with country code')
parser.add_argument('--rescrape-media', action='store_true', help='Re-download missing media for matching messages')
parser.add_argument('--export', action='store_true', help='Export databases to CSV and JSON')
parser.add_argument('--list', action='store_true', dest='list_dialogs', help='List account dialogs (channels/chats)')
parser.add_argument('--view', action='store_true', help='View saved channels from state')
parser.add_argument('--no-progress', action='store_true', help='Simplify output and skip heavy progress/ETA estimation')
parser.add_argument('--inspect-db', action='store_true', help='Print random sample rows from DB after scraping')
parser.add_argument('--sample', type=int, default=10, help='Sample size for --inspect-db')
args = parser.parse_args()

# adjust workers/semaphore
try:
    if args.workers and args.workers > 0:
        MEDIA_DOWNLOAD_WORKERS = args.workers
        media_download_semaphore = asyncio.Semaphore(MEDIA_DOWNLOAD_WORKERS)
except Exception:
    pass

# default to no-progress when running one-shot scrape unless explicitly allowed
if args.scrape and not args.no_progress:
    args.no_progress = True

# ASCII art only in interactive (menu) mode
if not (args.scrape or args.rescrape_media or args.export or args.list_dialogs or args.view):
    display_ascii_art()

def recover_state_from_databases():
    """Автоматическое восстановление состояния из существующих баз данных"""
    recovered_state = {
        'api_id': None,
        'api_hash': None,
        'phone': None,
        'channels': {},
        'scrape_media': True,
        'topic_offsets': {}
    }
    
    # Проходим по всем подкаталогам
    for item in os.listdir(SCRIPT_DIR):
        item_path = os.path.join(SCRIPT_DIR, item)
        
        # Проверяем, является ли элемент каталогом и содержит ли он .db файл
        if os.path.isdir(item_path):
            db_file = os.path.join(item_path, f"{item}.db")
            
            if os.path.exists(db_file):
                # Восстанавливаем информацию о канале
                try:
                    conn = sqlite3.connect(db_file)
                    c = conn.cursor()
                    
                    # Получаем максимальный message_id для канала
                    c.execute("SELECT MAX(message_id) FROM messages")
                    max_message_id = c.fetchone()[0] or 0
                    
                    # Преобразуем имя каталога обратно в имя канала (@username или -ID)
                    # Для простоты предполагаем, что это @username
                    channel_name = f"@{item}"
                    recovered_state['channels'][channel_name] = max_message_id
                    
                    # Проверяем наличие топиков
                    c.execute("SELECT DISTINCT topic_id, topic_title FROM messages WHERE topic_id IS NOT NULL")
                    topics = c.fetchall()
                    
                    # Если есть топики, сохраняем информацию о них
                    if topics:
                        if 'topic_offsets' not in recovered_state:
                            recovered_state['topic_offsets'] = {}
                        
                        recovered_state['topic_offsets'][channel_name] = {}
                        for topic_id, topic_title in topics:
                            # Для каждого топика получаем максимальный message_id
                            c.execute("SELECT MAX(message_id) FROM messages WHERE topic_id = ?", (topic_id,))
                            max_topic_message_id = c.fetchone()[0] or 0
                            recovered_state['topic_offsets'][channel_name][str(topic_id)] = max_topic_message_id
                    
                    conn.close()
                    print(f"Recovered state for channel: {channel_name} (max message ID: {max_message_id})")
                    
                except Exception as e:
                    print(f"Error recovering state from {db_file}: {e}")
    
    return recovered_state

def sanitize_filename(name):
    """Sanitize channel name to create valid directory names"""
    # Remove @ symbol and trailing spaces
    name = name.replace('@', '').strip()
    # Replace invalid characters with underscores
    name = re.sub(r'[<>:"/\\|?*\x00-\x1F]', '_', name)
    # Limit length to 100 characters
    name = name[:100]
    # Remove leading/trailing dots and spaces
    name = name.strip('. ')
    # If name is empty, use a default name
    if not name:
        name = 'unnamed_channel'
    return name

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r') as f:
            state = json.load(f)
            # Обработка null значений в channels
            for channel, offset in state['channels'].items():
                if offset is None:
                    state['channels'][channel] = 0
            # Обработка null значений в topic_offsets
            if 'topic_offsets' in state:
                for channel, topics in state['topic_offsets'].items():
                    for topic_id, offset in topics.items():
                        if offset is None:
                            state['topic_offsets'][channel][topic_id] = 0
                # Ensure per-channel offset is at least the max of its topic offsets (avoid regressions)
                try:
                    for ch, topics in state['topic_offsets'].items():
                        max_topic = max((v or 0) for v in topics.values()) if topics else 0
                        state['channels'][ch] = max(state['channels'].get(ch, 0) or 0, max_topic)
                except Exception:
                    pass
            return state
    else:
        # Если файл state.json не существует, пытаемся восстановить состояние из БД
        print("State file not found. Attempting to recover from existing databases...")
        recovered_state = recover_state_from_databases()
        if recovered_state['channels']:
            print("Successfully recovered state from databases.")
            # Сохраняем восстановленное состояние
            save_state(recovered_state)
            return recovered_state
        else:
            print("No existing databases found. Creating new state file.")
    
    # Возвращаем пустое состояние по умолчанию
    return {
        'api_id': None,
        'api_hash': None,
        'phone': None,
        'channels': {},
        'scrape_media': True,
    }

def save_state(state):
    tmp_file = STATE_FILE + ".tmp"
    try:
        with open(tmp_file, 'w') as f:
            json.dump(state, f)
        os.replace(tmp_file, STATE_FILE)
    except Exception as e:
        print(f"Error saving state to file: {e}")
        try:
            if os.path.exists(tmp_file):
                os.remove(tmp_file)
        except Exception:
            pass

state = load_state()

# Apply CLI overrides
try:
    if 'args' in globals() and args is not None:
        if args.no_media:
            state['scrape_media'] = False
        if args.api_id:
            state['api_id'] = int(args.api_id)
        if args.api_hash:
            state['api_hash'] = args.api_hash
        if args.phone:
            state['phone'] = args.phone
        # Merge channels provided via CLI into state without dropping existing ones
        input_channels = []
        if args.channels:
            input_channels.extend([c.strip() for c in args.channels.split(',') if c.strip()])
        if args.channels_file:
            try:
                with open(args.channels_file, 'r', encoding='utf-8') as f:
                    for line in f:
                        ch = line.strip()
                        if ch:
                            input_channels.append(ch)
            except Exception as e:
                print(f"Could not read channels file: {e}")
        for ch in input_channels:
            if ch not in state['channels']:
                state['channels'][ch] = 0
        if input_channels:
            save_state(state)
except Exception as _e:
    print(f"CLI overrides error: {_e}")

if not state['api_id'] or not state['api_hash'] or not state['phone']:
    if 'args' in globals() and args and args.api_id and args.api_hash and args.phone:
        # Already set from CLI above
        pass
    else:
        state['api_id'] = int(input("Enter your API ID: "))
        state['api_hash'] = input("Enter your API Hash: ")
        state['phone'] = input("Enter your phone number: ")
        save_state(state)

client = TelegramClient(os.path.join(SCRIPT_DIR, 'session'), state['api_id'], state['api_hash'])

# Запуск воркеров для фоновой загрузки медиа
async def start_media_download_workers():
    """Запуск воркеров для фоновой загрузки медиа"""
    for i in range(MEDIA_DOWNLOAD_WORKERS):
        task = asyncio.create_task(media_downloader_worker())
        media_download_tasks.append(task)
    print(f"Started {MEDIA_DOWNLOAD_WORKERS} media download workers")
# Глобальные переменные для отслеживания последней загрузки
last_download_info = ""
last_download_lock = asyncio.Lock()

def format_time(seconds):
    """Форматирует время в часы, минуты и секунды"""
    if seconds < 0:
        return "Calculating..."
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    if hours > 0:
        return f"{hours}h {minutes}m {secs}s"
    elif minutes > 0:
        return f"{minutes}m {secs}s"
    else:
        return f"{secs}s"

def update_display():
    """Обновляет отображение информации в 4 строках"""
    if 'args' in globals() and args and args.no_progress:
        return
    # Очищаем 4 строки перед обновлением
    for i in range(4):
        sys.stdout.write(f"\033[{i+1};1H\033[K")
    
    # Строка 1: Прогресс парсинга
    if estimated_total_messages > 0:
        progress = (processed_messages_count / estimated_total_messages) * 100
        if progress > 100:
            progress = 100
        sys.stdout.write(f"\033[1;1HProgress: {progress:.2f}% (Processed: {processed_messages_count}/{estimated_total_messages})")
    else:
        sys.stdout.write(f"\033[1;1HProcessed: {processed_messages_count} messages")
    
    # Строка 2: Информация о последней загрузке
    sys.stdout.write(f"\033[2;1H{last_download_info}")
    
    # Строка 3: Оставшееся время
    if processed_messages_count > 0 and estimated_total_messages > 0:
        elapsed_time = time.time() - start_time
        rate = processed_messages_count / elapsed_time if elapsed_time > 0 else 0
        remaining_messages = estimated_total_messages - processed_messages_count
        if rate > 0:
            eta_seconds = remaining_messages / rate
            eta_formatted = format_time(eta_seconds)
            sys.stdout.write(f"\033[3;1HEstimated time remaining: {eta_formatted}")
        else:
            sys.stdout.write(f"\033[3;1HEstimated time remaining: Calculating...")
    else:
        sys.stdout.write(f"\033[3;1HEstimated time remaining: Calculating...")
    
    # Строка 4: Информация об ошибках
    sys.stdout.write(f"\033[4;1H{last_error_info}")
    
    sys.stdout.flush()
# Глобальные переменные для расчета оставшегося времени
start_time = time.time()
processed_messages_count = 0
estimated_total_messages = 0
# Глобальная переменная для отслеживания ошибок
last_error_info = ""
last_error_lock = asyncio.Lock()

def compute_rate(start_ts, processed):
    elapsed = time.time() - start_ts
    return processed / elapsed if elapsed > 0 else 0.0

def format_eta(seconds):
    return format_time(seconds) if seconds is not None else "N/A"

def print_status(channel, processed, start_ts, last_msg_id, topic_title='General', topic_index=1, topics_total=1):
    rate = compute_rate(start_ts, processed)
    eta = None
    remain = None
    if 'args' in globals() and args:
        if args.limit:
            remain = max(args.limit - processed, 0)
            if rate > 0:
                eta = remain / rate
        elif args.time_limit:
            eta = max(args.time_limit - (time.time() - start_ts), 0)
    lm = last_download_info if 'last_download_info' in globals() and last_download_info else "-"
    remain_str = f"{remain}" if remain is not None else "-"
    print(f"[{channel}] Topic: {topic_title} ({topic_index}/{topics_total}) | last_id={last_msg_id} | processed={processed} | remaining={remain_str} | rate={rate:.2f}/s | ETA={format_eta(eta)} | last_media={lm}")

def inspect_db(channel, sample=10):
    try:
        sanitized_channel = sanitize_filename(channel)
        db_file = os.path.join(SCRIPT_DIR, sanitized_channel, f'{sanitized_channel}.db')
        if not os.path.exists(db_file):
            print(f"DB not found for {channel}: {db_file}")
            return
        conn = sqlite3.connect(db_file)
        c = conn.cursor()
        try:
            c.execute("SELECT topic_id, COUNT(*) FROM messages GROUP BY topic_id ORDER BY COUNT(*) DESC")
            counts = c.fetchall()
            print("DB topic distribution for {}: {}".format(channel, ", ".join([f"topic {tid if tid is not None else 'NULL'}: {cnt}" for tid, cnt in counts])))
        except Exception as e:
            print(f"Count query failed: {e}")
        try:
            c.execute("SELECT message_id, date, username, substr(message,1,80), topic_id FROM messages ORDER BY RANDOM() LIMIT ?", (sample,))
            rows = c.fetchall()
            print(f"Sample {len(rows)} rows from {channel}:")
            for r in rows:
                mid, dt, uname, preview, tpid = r
                print(f" - id={mid} t={dt} by={uname} topic={tpid} text={preview}")
        finally:
            conn.close()
    except Exception as e:
        print(f"inspect_db error for {channel}: {e}")

async def media_downloader_worker():
    """Фоновый воркер для скачивания медиа"""
    while True:
        try:
            # Получаем задачу из очереди
            task = await media_download_queue.get()
            channel, message = task
            
            # Скачиваем медиа с ограничением по количеству одновременных загрузок
            async with media_download_semaphore:
                media_path = await download_media(channel, message)
                
                # Обновляем базу данных
                if media_path:
                    sanitized_channel = sanitize_filename(channel)
                    project_dir = os.path.dirname(os.path.abspath(__file__))
                    channel_dir = os.path.join(project_dir, sanitized_channel)
                    conn = sqlite3.connect(os.path.join(channel_dir, f'{sanitized_channel}.db'))
                    c = conn.cursor()
                    c.execute('''UPDATE messages SET media_path = ? WHERE message_id = ?''', (media_path, message.id))
                    conn.commit()
                    conn.close()
                    
                    # Обновляем информацию о последней загрузке
                    global last_download_info
                    async with last_download_lock:
                        last_download_info = f"Last download: {media_path}"
                    print(f"Updated media path for message {message.id} in database")
            
            # Отмечаем задачу как выполненную
            media_download_queue.task_done()
        except asyncio.CancelledError:
            print("Media downloader worker cancelled")
            break
        except Exception as e:
            # Обновляем информацию об ошибке
            global last_error_info
            async with last_error_lock:
                last_error_info = f"Error in media downloader worker: {e}"
            print(f"Error in media downloader worker: {e}")
            media_download_queue.task_done()

# Функция для добавления задачи загрузки в очередь
# Helper: compute existing media file path if present
def compute_existing_media_path(channel, message):
    if not message.media:
        return None
    sanitized_channel = sanitize_filename(channel)
    project_dir = os.path.dirname(os.path.abspath(__file__))
    channel_dir = os.path.join(project_dir, sanitized_channel)
    media_folder = os.path.join(channel_dir, 'media')
    os.makedirs(media_folder, exist_ok=True)
    media_file_name = None
    if isinstance(message.media, MessageMediaPhoto):
        media_file_name = message.file.name if getattr(message.file, 'name', None) else f"{message.id}.jpg"
    elif isinstance(message.media, MessageMediaDocument):
        file_ext = message.file.ext if getattr(message.file, 'ext', None) else '.bin'
        media_file_name = message.file.name if getattr(message.file, 'name', None) else f"{message.id}{file_ext}"
    if not media_file_name:
        if isinstance(message.media, MessageMediaPhoto):
            media_file_name = f"{message.id}.jpg"
        else:
            media_file_name = f"{message.id}.bin"
    media_path = os.path.join(media_folder, media_file_name)
    if os.path.exists(media_path):
        return media_path
    base_name, ext = os.path.splitext(media_file_name)
    for counter in range(1, 101):
        numbered_path = os.path.join(media_folder, f"{base_name} ({counter}){ext}")
        if os.path.exists(numbered_path):
            return numbered_path
    return None

async def queue_media_download(channel, message):
    """Добавление задачи загрузки медиа в очередь"""
    if not state['scrape_media'] or not message.media:
        return
    if 'args' in globals() and args and args.dry_run:
        return
    # if media already exists, update db and skip enqueuing
    existing = compute_existing_media_path(channel, message)
    if existing:
        try:
            sanitized_channel = sanitize_filename(channel)
            project_dir = os.path.dirname(os.path.abspath(__file__))
            channel_dir = os.path.join(project_dir, sanitized_channel)
            conn = sqlite3.connect(os.path.join(channel_dir, f'{sanitized_channel}.db'))
            c = conn.cursor()
            c.execute('''UPDATE messages SET media_path = ? WHERE message_id = ?''', (existing, message.id))
            conn.commit()
            conn.close()
            print(f"Media already exists, skipping download: {existing}")
        except Exception as e:
            global last_error_info
            async with last_error_lock:
                last_error_info = f"Error updating existing media path: {e}"
        return
    await media_download_queue.put((channel, message))
    print(f"Queued media download for message {message.id} (queue size: {media_download_queue.qsize()})")

# Rate limiting variables
last_request_time = 0
min_request_interval = 1/29  # 29 requests per second

async def rate_limited_request():
    global last_request_time
    current_time = time.time()
    time_since_last_request = current_time - last_request_time
    
    if time_since_last_request < min_request_interval:
        sleep_time = min_request_interval - time_since_last_request
        await asyncio.sleep(sleep_time)
    
    last_request_time = time.time()

def message_text_with_links(message):
    """Текст сообщения с вшитыми URL из entities.

    Telethon отдаёт в message.message голый текст: ссылки, спрятанные
    за словом («по ссылке»), живут в message.entities как
    MessageEntityTextUrl и в текст не попадают. Вшиваем их обратно
    в позицию entity: «по ссылке» -> «по ссылке (URL)». Голые
    MessageEntityUrl уже есть в тексте — пропускаем.
    """
    text = message.message or ""
    entities = getattr(message, "entities", None) or []
    inserts = []  # (offset_end, url)
    for e in entities:
        url = getattr(e, "url", None)
        if isinstance(e, MessageEntityTextUrl) and url:
            inserts.append((e.offset + e.length, url))
    if not inserts:
        return text
    # вставляем с конца, чтобы не сбить offset'ы
    for pos, url in sorted(inserts, reverse=True):
        text = text[:pos] + f" ({url})" + text[pos:]
    return text


def save_message_to_db(channel, message, sender, topic_id=None, topic_title=None):
    if 'args' in globals() and args and args.dry_run:
        return
    # Sanitize channel name for directory creation
    sanitized_channel = sanitize_filename(channel)
    # Create channel directory within the project directory
    project_dir = os.path.dirname(os.path.abspath(__file__))
    channel_dir = os.path.join(project_dir, sanitized_channel)
    os.makedirs(channel_dir, exist_ok=True)

    db_file = os.path.join(channel_dir, f'{sanitized_channel}.db')
    conn = sqlite3.connect(db_file)
    c = conn.cursor()
    
    # Check if the table exists and has the topic_id column
    c.execute("PRAGMA table_info(messages)")
    columns = [info[1] for info in c.fetchall()]
    
    if 'topic_id' not in columns:
        # Add topic_id column if it doesn't exist
        try:
            c.execute("ALTER TABLE messages ADD COLUMN topic_id INTEGER")
        except sqlite3.OperationalError:
            # Table doesn't exist, create it with all columns
            c.execute(f'''CREATE TABLE IF NOT EXISTS messages
                          (id INTEGER PRIMARY KEY, message_id INTEGER, date TEXT, sender_id INTEGER, first_name TEXT, last_name TEXT, username TEXT, message TEXT, media_type TEXT, media_path TEXT, reply_to INTEGER, topic_id INTEGER)''')
    else:
        # Table exists and has topic_id column, ensure it's created properly
        c.execute(f'''CREATE TABLE IF NOT EXISTS messages
                      (id INTEGER PRIMARY KEY, message_id INTEGER, date TEXT, sender_id INTEGER, first_name TEXT, last_name TEXT, username TEXT, message TEXT, media_type TEXT, media_path TEXT, reply_to INTEGER, topic_id INTEGER)''')
    
    # Check if the table exists and has the topic_title column
    c.execute("PRAGMA table_info(messages)")
    columns = [info[1] for info in c.fetchall()]
    
    if 'topic_title' not in columns:
        # Add topic_title column if it doesn't exist
        try:
            c.execute("ALTER TABLE messages ADD COLUMN topic_title TEXT")
        except sqlite3.OperationalError:
            # Table doesn't exist, create it with all columns
            c.execute(f'''CREATE TABLE IF NOT EXISTS messages
                          (id INTEGER PRIMARY KEY, message_id INTEGER, date TEXT, sender_id INTEGER, first_name TEXT, last_name TEXT, username TEXT, message TEXT, media_type TEXT, media_path TEXT, reply_to INTEGER, topic_id INTEGER, topic_title TEXT)''')
    else:
        # Table exists and has topic_title column, ensure it's created properly
        c.execute(f'''CREATE TABLE IF NOT EXISTS messages
                      (id INTEGER PRIMARY KEY, message_id INTEGER, date TEXT, sender_id INTEGER, first_name TEXT, last_name TEXT, username TEXT, message TEXT, media_type TEXT, media_path TEXT, reply_to INTEGER, topic_id INTEGER, topic_title TEXT)''')
    
    c.execute('''INSERT OR IGNORE INTO messages (message_id, date, sender_id, first_name, last_name, username, message, media_type, media_path, reply_to, topic_id, topic_title)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
              (message.id,
               message.date.strftime('%Y-%m-%d %H:%M:%S'),
               message.sender_id,
               getattr(sender, 'first_name', None) if isinstance(sender, User) else None,
               getattr(sender, 'last_name', None) if isinstance(sender, User) else None,
               getattr(sender, 'username', None) if isinstance(sender, User) else None,
               message_text_with_links(message),
               message.media.__class__.__name__ if message.media else None,
               None,
               message.reply_to_msg_id if message.reply_to else None,
               topic_id,
               topic_title if 'topic_title' in locals() else None))
    conn.commit()
    conn.close()

MAX_RETRIES = 5

async def download_media(channel, message):
    if not message.media or not state['scrape_media']:
        return None

    # Check media size
    if hasattr(message, 'file') and hasattr(message.file, 'size'):
        media_size_mb = message.file.size / (1024 * 1024)  # Convert bytes to MB
        if media_size_mb > MAX_MEDIA_SIZE_MB:
            print(f"Skipping media for message {message.id} - File too large ({media_size_mb:.2f} MB > {MAX_MEDIA_SIZE_MB} MB)")
            return None

    # Sanitize channel name for directory creation
    sanitized_channel = sanitize_filename(channel)
    # Create channel directory within the project directory
    project_dir = os.path.dirname(os.path.abspath(__file__))
    channel_dir = os.path.join(project_dir, sanitized_channel)
    media_folder = os.path.join(channel_dir, 'media')
    os.makedirs(media_folder, exist_ok=True)
    media_file_name = None
    if isinstance(message.media, MessageMediaPhoto):
        # Для фото используем ID сообщения как имя файла по умолчанию
        media_file_name = message.file.name if message.file.name else f"{message.id}.jpg"
    elif isinstance(message.media, MessageMediaDocument):
        # Для документов пытаемся получить имя файла, если не удалось - используем ID сообщения
        file_ext = message.file.ext if message.file.ext else '.bin'
        media_file_name = message.file.name if message.file.name else f"{message.id}{file_ext}"
    
    # Дополнительная проверка на случай, если все методы определения имени файла не сработали
    if not media_file_name:
        # Генерируем имя файла по умолчанию на основе типа медиа и ID сообщения
        if isinstance(message.media, MessageMediaPhoto):
            media_file_name = f"{message.id}.jpg"
        else:
            media_file_name = f"{message.id}.bin"
        print(f"Using default file name for message {message.id}: {media_file_name}")
    
    media_path = os.path.join(media_folder, media_file_name)
    
    # Check if file already exists (including numbered versions)
    if os.path.exists(media_path):
        print(f"Media file already exists: {media_path}")
        return media_path
    
    # Check for numbered versions of the file
    base_name, ext = os.path.splitext(media_file_name)
    counter = 1
    while True:
        numbered_name = f"{base_name} ({counter}){ext}"
        numbered_path = os.path.join(media_folder, numbered_name)
        if os.path.exists(numbered_path):
            print(f"Media file already exists: {numbered_path}")
            return numbered_path
        if counter > 100:  # Prevent infinite loop
            break
        counter += 1

    retries = 0
    while retries < MAX_RETRIES:
        try:
            if isinstance(message.media, MessageMediaPhoto):
                await rate_limited_request()
                media_path = await message.download_media(file=media_folder)
            elif isinstance(message.media, MessageMediaDocument):
                await rate_limited_request()
                media_path = await message.download_media(file=media_folder)
            if media_path:
                print(f"Successfully downloaded media to: {media_path}")
            break
        except (TimeoutError, aiohttp.ClientError, RPCError) as e:
            retries += 1
            # Обновляем информацию об ошибке
            global last_error_info
            async with last_error_lock:
                last_error_info = f"Error downloading media for message {message.id}: {e}"
            print(f"Retrying download for message {message.id}. Attempt {retries}...")
            await asyncio.sleep(2 ** retries)
    return media_path

async def rescrape_media(channel):
    # Sanitize channel name
    sanitized_channel = sanitize_filename(channel)
    # Create channel directory within the project directory
    project_dir = os.path.dirname(os.path.abspath(__file__))
    channel_dir = os.path.join(project_dir, sanitized_channel)
    db_file = os.path.join(channel_dir, f'{sanitized_channel}.db')
    conn = sqlite3.connect(db_file)
    c = conn.cursor()
    c.execute('SELECT message_id FROM messages WHERE media_type IS NOT NULL AND media_path IS NULL')
    rows = c.fetchall()
    conn.close()

    total_messages = len(rows)
    if total_messages == 0:
        print(f"No media files to reprocess for channel {channel}.")
        return

    for index, (message_id,) in enumerate(rows):
        try:
            await rate_limited_request()
            entity = await client.get_entity(PeerChannel(int(channel)))
            await rate_limited_request()
            message = await client.get_messages(entity, ids=message_id)
            
            # Check media size before downloading
            if hasattr(message, 'file') and hasattr(message.file, 'size'):
                media_size_mb = message.file.size / (1024 * 1024)
                if media_size_mb > MAX_MEDIA_SIZE_MB:
                    print(f"Skipping media reprocessing for message {message_id} - File too large ({media_size_mb:.2f} MB > {MAX_MEDIA_SIZE_MB} MB)")
                    continue
            
            media_path = await download_media(channel, message)
            if media_path:
                conn = sqlite3.connect(db_file)
                c = conn.cursor()
                c.execute('''UPDATE messages SET media_path = ? WHERE message_id = ?''', (media_path, message_id))
                conn.commit()
                conn.close()
            
            progress = (index + 1) / total_messages * 100
            sys.stdout.write(f"\rReprocessing media for channel {channel}: {progress:.2f}% complete")
            sys.stdout.flush()
        except Exception as e:
            # Обновляем информацию об ошибке
            global last_error_info
            async with last_error_lock:
                last_error_info = f"Error reprocessing message {message_id}: {e}"
            print(f"Error reprocessing message {message_id}: {e}")
    print()

async def binary_search_last_message(entity, low, high):
    """Binary search to find the exact last message ID"""
    print(f"Performing binary search between {low} and {high}...")
    
    last_valid_id = low
    while low <= high:
        mid = (low + high) // 2
        try:
            print(f"Binary search checking ID: {mid}")
            start_time = time.time()
            
            await rate_limited_request()
            messages = await client.get_messages(entity, min_id=mid, limit=1)
            
            end_time = time.time()
            request_duration = end_time - start_time
            
            if messages:
                last_valid_id = mid
                low = mid + 1
                print(f"Found message at ID {mid} during binary search (request took {request_duration:.2f}s)")
            else:
                high = mid - 1
                print(f"No messages found at ID {mid} during binary search (request took {request_duration:.2f}s)")
                
            # Troubleshooting info for slow requests
            if request_duration > 3.0:
                print(f"WARNING: Slow binary search request detected ({request_duration:.2f}s).")
                
            await asyncio.sleep(0.05)
        except FloodWaitError as e:
            print(f"Flood wait error during binary search: Need to wait {e.seconds} seconds")
            await asyncio.sleep(e.seconds)
        except asyncio.TimeoutError:
            print(f"Timeout error during binary search at ID {mid}.")
            high = mid - 1  # Assume no message found
            await asyncio.sleep(0.5)
        except Exception as e:
            # Обновляем информацию об ошибке
            global last_error_info
            async with last_error_lock:
                last_error_info = f"Error during binary search at ID {mid}: {type(e).__name__}: {e}"
            print(f"Error during binary search at ID {mid}: {type(e).__name__}: {e}")
            high = mid - 1  # Assume no message found
            await asyncio.sleep(0.5)
    
    print(f"Binary search complete. Last message ID found: {last_valid_id}")
    return last_valid_id

async def estimate_total_messages(channel, entity):
    """Estimate total messages using adaptive step approach with binary search refinement"""
    print("Estimating total messages...")
    
    # Start with a larger step for big channels
    step = 10000
    last_valid_id = 0
    consecutive_empty = 0
    max_empty_attempts = 3
    
    # First, try to find a rough upper bound with large steps
    current_id = step
    while consecutive_empty < max_empty_attempts:
        try:
            print(f"Checking message ID: {current_id}")
            start_time = time.time()
            
            await rate_limited_request()
            messages = await client.get_messages(entity, min_id=current_id, limit=1)
            
            end_time = time.time()
            request_duration = end_time - start_time
            
            if messages:
                last_valid_id = current_id
                consecutive_empty = 0
                print(f"Found message at ID ~{current_id} (request took {request_duration:.2f}s)")
            else:
                consecutive_empty += 1
                print(f"No messages found at ID ~{current_id} (request took {request_duration:.2f}s, consecutive empty: {consecutive_empty})")
                
            # Troubleshooting info for slow requests
            if request_duration > 3.0:
                print(f"WARNING: Slow request detected ({request_duration:.2f}s). This might indicate network issues or Telegram rate limiting.")
                
            current_id += step
            
            # Add a small delay to prevent overwhelming Telegram
            await asyncio.sleep(0.1)
        except FloodWaitError as e:
            print(f"Flood wait error: Need to wait {e.seconds} seconds")
            await asyncio.sleep(e.seconds)
            # Don't increment consecutive_empty counter on flood wait
        except asyncio.TimeoutError:
            print(f"Timeout error checking ID {current_id}. This might indicate network issues.")
            consecutive_empty += 1
            await asyncio.sleep(1)
        except Exception as e:
            consecutive_empty += 1
            # Обновляем информацию об ошибке
            global last_error_info
            async with last_error_lock:
                last_error_info = f"Error checking ID {current_id}: {type(e).__name__}: {e}"
            print(f"Error checking ID {current_id}: {type(e).__name__}: {e}")
            # Add a delay after errors
            await asyncio.sleep(1)
    
    # If we found a large ID, use binary search to find the exact last message
    if last_valid_id > 0:
        print(f"Found messages up to ID {last_valid_id}. Using binary search to find exact last message...")
        
        # Define search range: from last found ID to a reasonable upper bound
        search_low = last_valid_id
        search_high = last_valid_id + (step * 2)  # Search 2 steps ahead
        
        # Perform binary search to find exact last message
        exact_last_id = await binary_search_last_message(entity, search_low, search_high)
        last_valid_id = exact_last_id
    
    # Now we have a rough upper bound, return it as estimate
    print(f"Estimated total messages: {last_valid_id}")
    return last_valid_id

async def get_forum_topics(entity):
    """Get all topics from a forum using the correct method"""
    print("Getting forum topics...")
    topics = []
    try:
        # Get forum topics using the correct method
        # We need to get the input channel for the request
        if hasattr(entity, 'id') and hasattr(entity, 'access_hash'):
            input_channel = InputPeerChannel(entity.id, entity.access_hash)
            
            # Get topics with pagination
            offset_topic = 0
            offset_id = 0
            offset_date = 0
            limit = 100
            
            while True:
                await rate_limited_request()
                result = await client(GetForumTopicsRequest(
                    peer=input_channel,
                    offset_date=offset_date,
                    offset_id=offset_id,
                    offset_topic=offset_topic,
                    limit=limit
                ))
                
                # Process topics
                for topic in result.topics:
                    topics.append({
                        'id': topic.id,
                        'title': topic.title if hasattr(topic, 'title') else f'Topic {topic.id}',
                        'date': topic.date if hasattr(topic, 'date') else None
                    })
                    print(f"Found topic: {topic.title if hasattr(topic, 'title') else 'Unknown'} (ID: {topic.id})")
                
                # Check if we have more topics
                if len(result.topics) < limit:
                    break
                    
                # Update offset for next iteration
                offset_topic = result.topics[-1].id
                offset_id = result.topics[-1].top_message
                if hasattr(result.topics[-1], 'date'):
                    offset_date = int(result.topics[-1].date.timestamp())
                
                # Small delay to prevent overwhelming Telegram
                await asyncio.sleep(0.1)
                
        print(f"Total topics found: {len(topics)}")
        return topics
    except Exception as e:
        print(f"Could not get forum topics using GetForumTopicsRequest: {e}")
        # Fallback to getting message threads
        try:
            print("Trying to get topics from message threads...")
            # Get some recent messages to identify topics
            async for message in client.iter_messages(entity, limit=100):
                if hasattr(message, 'reply_to') and message.reply_to:
                    if hasattr(message.reply_to, 'reply_to_top_id') and message.reply_to.reply_to_top_id:
                        topic_id = message.reply_to.reply_to_top_id
                        if not any(t['id'] == topic_id for t in topics):
                            topics.append({
                                'id': topic_id,
                                'title': f'Topic {topic_id}',
                                'date': message.date
                            })
                            print(f"Found topic from message thread: Topic {topic_id}")
        except Exception as e2:
            # Обновляем информацию об ошибке
            global last_error_info
            async with last_error_lock:
                last_error_info = f"Could not get topics from message threads: {e2}"
            print(f"Could not get topics from message threads: {e2}")
    
    if not topics:
        # Add a default topic for regular channels
        topics.append({
            'id': 1,
            'title': 'General',
            'date': None
        })
        print("No topics found, using default General topic")
    
    return topics

async def scrape_forum_channel(channel, offset_id):
    """Scrape a forum channel with topic support"""
    print(f"Starting to scrape forum channel: {channel}")
    try:
        await rate_limited_request()
        if channel.startswith('-'):
            entity = await client.get_entity(PeerChannel(int(channel)))
        else:
            entity = await client.get_entity(channel)

        print(f"Accessing forum channel: {channel}")
        
        # Check if this is a forum
        is_forum = hasattr(entity, 'forum') and entity.forum
        print(f"Is forum: {is_forum}")
        
        # Get topics if this is a forum
        topics = []
        if is_forum:
            topics = await get_forum_topics(entity)
        else:
            # For regular channels, use a default topic
            topics.append({
                'id': 1,
                'title': 'General',
                'date': None
            })
            print("No topics found, using default General topic")
        
        print(f"Found {len(topics)} topics")
        
        # Estimate total messages to show progress
        estimated_total = 0
        if not ('args' in globals() and args and (args.no_progress or args.limit or args.time_limit)):
            estimated_total = await estimate_total_messages(channel, entity)
        processed_messages = 0
        last_message_id = None
        # Track max message ID across all topics to avoid decreasing channel offset
        channel_max_id = state['channels'].get(channel, 0) or 0

        print("Starting to process messages...")
        run_started = time.time()
        last_status_ts = 0
        
        # Load topic-specific offsets
        topic_offsets = state.get('topic_offsets', {}).get(channel, {})
        
        # Process each topic
        for idx, topic in enumerate(topics, start=1):
            topic_id = topic['id']
            topic_title = topic['title']
            print(f"\nProcessing topic: {topic_title} (ID: {topic_id})")
            
            # Get offset for this topic
            # Использовать 0 вместо None
            topic_offset = topic_offsets.get(str(topic_id), 0) or 0
            print(f"Starting from message ID: {topic_offset} for topic {topic_title}")
            
            # Initialize message_count for this topic
            message_count = 0
            
            # Process messages for this topic
            await rate_limited_request()
            # For forums, we need to iterate through all messages in the topic
            # We'll use a different approach to ensure we get all messages
            topic_message_count = 0
            topic_last_message_id = None
            # Use min_id for this topic if it exists and iterate oldest->newest
            async for message in client.iter_messages(
                    entity,
                    reply_to=topic_id,
                    min_id=topic_offset or 0,
                    reverse=True
                ):
                try:
                    if not ('args' in globals() and args and args.no_progress):
                        print(f"Processing message ID: {message.id} in topic {topic_title}")
                    await rate_limited_request()
                    sender = await message.get_sender()
                    save_message_to_db(channel, message, sender, topic_id, topic_title)

                    if state['scrape_media'] and message.media:
                        print(f"Queueing media download for message ID: {message.id}")
                        await queue_media_download(channel, message)
                    
                    last_message_id = message.id
                    topic_last_message_id = message.id
                    # Monotonic channel max id across topics
                    channel_max_id = max(channel_max_id, message.id)
                    processed_messages += 1
                    topic_message_count += 1
                    message_count += 1

                    # Periodic status output
                    if time.time() - last_status_ts >= 2 or processed_messages % 50 == 0:
                        print_status(channel, processed_messages, run_started, message.id, topic_title, idx, len(topics))
                        last_status_ts = time.time()

                    # Limits/time budget enforcement
                    if 'args' in globals() and args:
                        if (args.limit and processed_messages >= args.limit) or (args.time_limit and (time.time() - run_started) >= args.time_limit):
                            # Save checkpoint and return to stop further topics in this channel
                            if last_message_id is not None:
                                state['channels'][channel] = max(state['channels'].get(channel, 0) or 0, (channel_max_id if 'channel_max_id' in locals() else (last_message_id or 0)))
                            # Prepare topic_offsets structure
                            if 'topic_offsets' not in state:
                                state['topic_offsets'] = {}
                            if channel not in state['topic_offsets']:
                                state['topic_offsets'][channel] = {}
                            # Use last processed message for topic if available; otherwise keep existing offset (or 0)
                            if topic_last_message_id is not None:
                                prev = state['topic_offsets'][channel].get(str(topic_id), topic_offset or 0)
                                state['topic_offsets'][channel][str(topic_id)] = max(prev, topic_last_message_id)
                            else:
                                # Ensure key exists without writing nulls
                                prev = state['topic_offsets'][channel].get(str(topic_id), topic_offset or 0)
                                state['topic_offsets'][channel][str(topic_id)] = prev
                            save_state(state)
                            return

                    
                    # Show progress based on estimate (clean output in non-interactive mode)
                    if 'args' in globals() and args and args.no_progress:
                        if processed_messages % 50 == 0:
                            print(f"Scraping {channel}: processed {processed_messages}")
                    else:
                        if estimated_total > 0:
                            progress = (processed_messages / estimated_total) * 100
                            if progress > 100:
                                progress = 100
                            # Обновляем глобальные переменные для расчета оставшегося времени
                            global processed_messages_count, estimated_total_messages, start_time
                            processed_messages_count = processed_messages
                            estimated_total_messages = estimated_total
                            # Выводим информацию в 4 строки
                            sys.stdout.write(f"\033[4;1HScraping channel: {channel} - Progress: {progress:.2f}% (Processed: {processed_messages})")
                        else:
                            sys.stdout.write(f"\033[4;1HScraping channel: {channel} - Processed: {processed_messages} messages")
                        sys.stdout.flush()
                    # Update state with last processed message ID more frequently
                    if processed_messages % 10 == 0:  # Save state every 10 messages
                        state['channels'][channel] = max(state['channels'].get(channel, 0) or 0, (channel_max_id if 'channel_max_id' in locals() else (last_message_id or 0)))
                        save_state(state)
                    
                    # Обновляем отображение каждые 5 сообщений
                    if processed_messages % 5 == 0:
                        update_display()
                        
                except FloodWaitError as e:
                    print(f"\nFlood wait error: Need to wait {e.seconds} seconds")
                    await asyncio.sleep(e.seconds)
                except asyncio.TimeoutError:
                    # Обновляем информацию об ошибке
                    global last_error_info
                    async with last_error_lock:
                        last_error_info = f"Timeout error processing message {message.id}. Retrying..."
                    print(f"\nTimeout error processing message {message.id}. Retrying...")
                    await asyncio.sleep(1)
                except Exception as e:
                    # Обновляем информацию об ошибке
                    async with last_error_lock:
                        last_error_info = f"Error processing message {message.id}: {type(e).__name__}: {e}"
                    print(f"\nError processing message {message.id}: {type(e).__name__}: {e}")
            
            print(f"Finished processing topic {topic_title}. Messages processed: {topic_message_count}")
            
            # Save state after finishing each topic
            state['channels'][channel] = max(state['channels'].get(channel, 0) or 0, (channel_max_id if 'channel_max_id' in locals() else (last_message_id or 0)))
            
            # Save topic-specific offset
            if 'topic_offsets' not in state:
                state['topic_offsets'] = {}
            if channel not in state['topic_offsets']:
                state['topic_offsets'][channel] = {}
            if topic_last_message_id is not None:
                prev = state['topic_offsets'][channel].get(str(topic_id), topic_offset or 0)
                state['topic_offsets'][channel][str(topic_id)] = max(prev, topic_last_message_id)
            else:
                # Do not write nulls; keep previous offset or default to topic_offset/0
                state['topic_offsets'][channel].setdefault(str(topic_id), topic_offset or 0)
            
            save_state(state)
        
        print(f"\nFinished scraping forum channel: {channel}. Processed {processed_messages} messages.")
    except ValueError as e:
        print(f"Error with channel {channel}: {e}")

async def scrape_regular_channel(channel, offset_id):
    """Scrape a regular channel (backward compatibility)"""
    print(f"Starting to scrape regular channel: {channel}")
    try:
        await rate_limited_request()
        if channel.startswith('-'):
            entity = await client.get_entity(PeerChannel(int(channel)))
        else:
            entity = await client.get_entity(channel)

        print(f"Accessing channel: {channel}")
        
        # Estimate total messages to show progress
        estimated_total = 0
        if not ('args' in globals() and args and (args.no_progress or args.limit or args.time_limit)):
            estimated_total = await estimate_total_messages(channel, entity)
        processed_messages = 0

        # Initialize message_count for this channel
        message_count = 0
        last_message_id = None

        print("Starting to process messages...")
        run_started = time.time()
        last_status_ts = 0
        await rate_limited_request()
        async for message in client.iter_messages(entity, min_id=offset_id or 0, reverse=True):
            try:
                if not ('args' in globals() and args and args.no_progress):
                    print(f"Processing message ID: {message.id}")
                await rate_limited_request()
                sender = await message.get_sender()
                save_message_to_db(channel, message, sender, topic_id=1, topic_title='General')  # Default topic for regular channels

                if state['scrape_media'] and message.media:
                    print(f"Queueing media download for message ID: {message.id}")
                    await queue_media_download(channel, message)
                
                last_message_id = message.id
                processed_messages += 1
                message_count += 1

                # Periodic status output
                if time.time() - last_status_ts >= 2 or processed_messages % 50 == 0:
                    print_status(channel, processed_messages, run_started, message.id, 'General', 1, 1)
                    last_status_ts = time.time()

                # Limits/time budget enforcement
                if 'args' in globals() and args:
                    if (args.limit and processed_messages >= args.limit) or (args.time_limit and (time.time() - run_started) >= args.time_limit):
                        if last_message_id is not None:
                            state['channels'][channel] = max(state['channels'].get(channel, 0) or 0, last_message_id)
                        save_state(state)
                        return

                # Show progress based on estimate (clean output in non-interactive mode)
                if 'args' in globals() and args and args.no_progress:
                    if processed_messages % 50 == 0:
                        print(f"Scraping {channel}: processed {processed_messages}")
                else:
                    if estimated_total > 0:
                        progress = (processed_messages / estimated_total) * 100
                        if progress > 100:
                            progress = 100
                        # Обновляем глобальные переменные для расчета оставшегося времени
                        global processed_messages_count, estimated_total_messages, start_time
                        processed_messages_count = processed_messages
                        estimated_total_messages = estimated_total
                        # Выводим информацию в 4 строки
                        sys.stdout.write(f"\033[4;1HScraping channel: {channel} - Progress: {progress:.2f}% (Processed: {processed_messages})")
                    else:
                        sys.stdout.write(f"\033[4;1HScraping channel: {channel} - Processed: {processed_messages} messages")
                    sys.stdout.flush()
                
                # Update state with last processed message ID more frequently
                if processed_messages % 10 == 0:  # Save state every 10 messages
                    state['channels'][channel] = max(state['channels'].get(channel, 0) or 0, last_message_id or 0)
                    save_state(state)
                
                # Обновляем отображение каждые 5 сообщений
                if processed_messages % 5 == 0:
                    update_display()
                    
            except FloodWaitError as e:
                print(f"\nFlood wait error: Need to wait {e.seconds} seconds")
                await asyncio.sleep(e.seconds)
            except asyncio.TimeoutError:
                # Обновляем информацию об ошибке
                global last_error_info
                async with last_error_lock:
                    last_error_info = f"Timeout error processing message {message.id}. Retrying..."
                print(f"\nTimeout error processing message {message.id}. Retrying...")
                await asyncio.sleep(1)
            except Exception as e:
                # Обновляем информацию об ошибке
                async with last_error_lock:
                    last_error_info = f"Error processing message {message.id}: {type(e).__name__}: {e}"
                print(f"\nError processing message {message.id}: {type(e).__name__}: {e}")
                
        print(f"\nFinished scraping channel: {channel}. Processed {processed_messages} messages.")
        
        # Save final state
        if last_message_id is not None:
            state['channels'][channel] = max(state['channels'].get(channel, 0) or 0, last_message_id)
        save_state(state)
    except ValueError as e:
        print(f"Error with channel {channel}: {e}")

async def scrape_channel(channel, offset_id):
    """Main scraping function that detects channel type"""
    try:
        await rate_limited_request()
        if channel.startswith('-'):
            entity = await client.get_entity(PeerChannel(int(channel)))
        else:
            entity = await client.get_entity(channel)
        
        # Check if this is a forum
        is_forum = hasattr(entity, 'forum') and entity.forum
        
        if is_forum:
            await scrape_forum_channel(channel, offset_id)
        else:
            await scrape_regular_channel(channel, offset_id)
    except Exception as e:
        print(f"Error determining channel type for {channel}: {e}")
        # Fallback to regular channel scraping
        await scrape_regular_channel(channel, offset_id)

async def continuous_scraping():
    global continuous_scraping_active
    continuous_scraping_active = True

    try:
        while continuous_scraping_active:
            for channel in state['channels']:
                print(f"\nChecking for new messages in channel: {channel}")
                await scrape_channel(channel, state['channels'][channel])
                print(f"New messages or media scraped from channel: {channel}")
            await asyncio.sleep(60)
    except asyncio.CancelledError:
        print("Continuous scraping stopped.")
        continuous_scraping_active = False

async def export_data():
    for channel in state['channels']:
        # Sanitize channel name for export
        sanitized_channel = sanitize_filename(channel)
        export_to_csv(sanitized_channel)
        export_to_json(sanitized_channel)

def export_to_csv(channel):
    # Connect to database within the project directory
    project_dir = os.path.dirname(os.path.abspath(__file__))
    channel_dir = os.path.join(project_dir, channel)
    db_file = os.path.join(channel_dir, f'{channel}.db')
    csv_file = os.path.join(channel_dir, f'{channel}.csv')
    conn = sqlite3.connect(db_file)
    c = conn.cursor()
    c.execute('SELECT * FROM messages')
    rows = c.fetchall()
    conn.close()

    with open(csv_file, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow([description[0] for description in c.description])
        writer.writerows(rows)

def export_to_json(channel):
    # Connect to database within the project directory
    project_dir = os.path.dirname(os.path.abspath(__file__))
    channel_dir = os.path.join(project_dir, channel)
    db_file = os.path.join(channel_dir, f'{channel}.db')
    json_file = os.path.join(channel_dir, f'{channel}.json')
    conn = sqlite3.connect(db_file)
    c = conn.cursor()
    c.execute('SELECT * FROM messages')
    rows = c.fetchall()
    conn.close()

    data = [dict(zip([description[0] for description in c.description], row)) for row in rows]
    with open(json_file, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

async def view_channels():
    if not state['channels']:
        print("No channels to view.")
        return
    
    print("\nCurrent channels:")
    for channel, last_id in state['channels'].items():
        sanitized_channel = sanitize_filename(channel)
        print(f"Channel ID: {channel}, Last Message ID: {last_id}, Folder: {sanitized_channel}")

async def list_Channels():
    try:
        print("\nList of channels joined by account: ")
        await rate_limited_request()
        async for dialog in client.iter_dialogs():
            if (dialog.id != 777000):
                # Check if it's a forum
                is_forum = hasattr(dialog.entity, 'forum') and dialog.entity.forum if hasattr(dialog.entity, 'forum') else False
                forum_indicator = " [FORUM]" if is_forum else ""
                print(f"* {dialog.title} (id: {dialog.id}){forum_indicator}")
    except Exception as e:
        print(f"Error processing: {e}")


async def manage_channels():
    while True:
        print("\nMenu:")
        print("[A] Add new channel")
        print("[R] Remove channel")
        print("[S] Scrape all channels")
        print("[M] Toggle media scraping (currently {})".format(
            "enabled" if state['scrape_media'] else "disabled"))
        print("[C] Continuous scraping")
        print("[E] Export data")
        print("[V] View saved channels")
        print("[L] List account channels")
        print("[Q] Quit")

        choice = input("Enter your choice: ").lower()
        match (choice):
            case 'a':
                channel = input("Enter channel ID: ")
                state['channels'][channel] = 0
                save_state(state)
                print(f"Added channel {channel}.")
            case 'r':
                channel = input("Enter channel ID to remove: ")
                if channel in state['channels']:
                    del state['channels'][channel]
                    save_state(state)
                    print(f"Removed channel {channel}.")
                else:
                    print(f"Channel {channel} not found.")
            case 's':
                print("Starting to scrape all channels...")
                if not state['channels']:
                    print("No channels added. Please add channels first using option [A].")
                else:
                    for channel in state['channels']:
                        print(f"Scraping channel: {channel}")
                        # Убедиться, что offset_id не None
                        offset_id = state['channels'][channel] or 0
                        await scrape_channel(channel, offset_id)
            case 'm':
                state['scrape_media'] = not state['scrape_media']
                save_state(state)
                print(
                    f"Media scraping {'enabled' if state['scrape_media'] else 'disabled'}.")
            case 'c':
                global continuous_scraping_active
                continuous_scraping_active = True
                task = asyncio.create_task(continuous_scraping())
                print("Continuous scraping started. Press Ctrl+C to stop.")
                try:
                    await asyncio.sleep(float('inf'))
                except KeyboardInterrupt:
                    continuous_scraping_active = False
                    task.cancel()
                    print("\nStopping continuous scraping...")
                    await task
            case 'e':
                await export_data()
            case 'v':
                await view_channels()
            case 'q':
                print("Quitting...")
                sys.exit()
            case 'l':
                await list_Channels()

            case _:
                print("Invalid option.")

async def main():
    print("Starting Telegram client...")
    await client.start()
    print("Telegram client started successfully.")
    
    # Запуск воркеров для фоновой загрузки медиа (только если включена загрузка медиа и не dry-run)
    if state.get('scrape_media', True) and not (args and args.dry_run):
        await start_media_download_workers()

    # Non-interactive operations via CLI
    if 'args' in globals() and args and (args.scrape or args.rescrape_media or args.export or args.list_dialogs or args.view):
        try:
            # list dialogs
            if args.list_dialogs:
                await list_Channels()
            # view saved channels
            if args.view:
                await view_channels()
            # export
            if args.export:
                await export_data()
            # rescrape media
            if args.rescrape_media:
                targets = []
                if args.channels:
                    targets.extend([c.strip() for c in args.channels.split(',') if c.strip()])
                if args.channels_file:
                    try:
                        with open(args.channels_file, 'r', encoding='utf-8') as f:
                            for line in f:
                                ch = line.strip()
                                if ch:
                                    targets.append(ch)
                    except Exception as e:
                        print(f"Could not read channels file: {e}")
                if not targets:
                    targets = list(state['channels'].keys())
                for ch in targets:
                    print(f"Reprocessing media for {ch}")
                    await rescrape_media(ch)
            # one-shot scrape
            if args.scrape:
                targets = []
                if args.channels:
                    targets.extend([c.strip() for c in args.channels.split(',') if c.strip()])
                if args.channels_file:
                    try:
                        with open(args.channels_file, 'r', encoding='utf-8') as f:
                            for line in f:
                                ch = line.strip()
                                if ch:
                                    targets.append(ch)
                    except Exception as e:
                        print(f"Could not read channels file: {e}")
                if not targets:
                    targets = list(state['channels'].keys())
                for ch in targets:
                    offset = state['channels'].get(ch, 0) or 0
                    print(f"Scraping channel: {ch} from message ID {offset}")
                    await scrape_channel(ch, offset)
                    if args.inspect_db:
                        inspect_db(ch, args.sample)
        finally:
            # Wait for media queue and shutdown workers
            try:
                await media_download_queue.join()
            except Exception:
                pass
            for t in media_download_tasks:
                t.cancel()
            return
    
    while True:
        await manage_channels()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nProgram interrupted. Saving state before exiting...")
        save_state(state)
        sys.exit()
