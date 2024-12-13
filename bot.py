import os
import re
import asyncio
import shlex
import json
import file_check
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import httpx

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot
from telegram.ext import CallbackContext
from telegram.error import RetryAfter, BadRequest
from telegram.request import HTTPXRequest
from aiocache import cached, caches
from contextlib import asynccontextmanager

app = FastAPI()

# 设置 Telegram Bot 的 API 密钥
TOKEN = 'xxx'
CHANNEL_USERNAME = '@xxx'  # 替换为您的频道用户名
WEBHOOK_URL = 'https://bot.xxx/webhook'
CDN_URL = "https://bkt-sgp-miui-ota-update-alisgp.oss-ap-southeast-1.aliyuncs.com/"
MIUI_URL_REGEX = r"https://(?:bn|bigota|cdnorg|hugeota)\.d\.miui\.com/(.*)"
BLACKLISTED_PARTITIONS = [
    "modem", "modemfirmware", "odm", "product", "system", "system_ext", "vendor"
]
request = HTTPXRequest(connection_pool_size=8)
bot = Bot(token=TOKEN, request=request)
MAX_RETRIES = 3
RETRY_INTERVAL = 5  # 秒

user_data_store = {}  # 全局字典存储用户数据
user_locks = {}  # 每个用户独立的锁

# 全局 http_client
http_client = httpx.AsyncClient(
    limits=httpx.Limits(max_connections=300, max_keepalive_connections=50),
    timeout=httpx.Timeout(90.0)  # 增加超时时间
)

caches.set_config({
    'default': {
        'cache': "aiocache.SimpleMemoryCache",
        'serializer': {
            'class': "aiocache.serializers.StringSerializer"
        }
    }
})

def init_db():
    conn = sqlite3.connect('file_cache.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS file_cache (
            file_name TEXT PRIMARY KEY,
            file_id TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS keyboard_layouts (
            file_name TEXT PRIMARY KEY,
            layout_data TEXT
        )
    ''')
    conn.commit()
    conn.close()

# 在程序启动时初始化数据库
init_db()

# --- 日志设置 ---
# 创建UTC+8时区
utc_8_timezone = timezone(timedelta(hours=8))

# 获取当前UTC+8时间
now = datetime.now(utc_8_timezone)

# 指定日志目录和文件名
log_dir = "logs"
log_filename = f"bot_{now.strftime('%Y%m%d_%H%M%S')}.log"
log_filepath = os.path.join(log_dir, log_filename)

# 确保日志目录存在
os.makedirs(log_dir, exist_ok=True)

# 过滤HTTP请求的过滤器
class HTTPFilter(logging.Filter):
    def filter(self, record):
        if "HTTP REQUEST" in record.getMessage().upper():
            return False
        else:
            return True

# 创建一个自定义的Formatter
class UTC8Formatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):
        dt = datetime.fromtimestamp(record.created, tz=utc_8_timezone)
        if datefmt:
            return dt.strftime(datefmt)
        else:
            return dt.isoformat(sep=' ', timespec='milliseconds')

# 创建一个logger
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# 创建一个handler，用于写入日志文件
file_handler = logging.FileHandler(log_filepath)
file_handler.setLevel(logging.INFO)

# 创建一个formatter，用于格式化日志信息
formatter = UTC8Formatter('%(asctime)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S %Z%z')
file_handler.setFormatter(formatter)

# 将handler添加到logger
logger.addHandler(file_handler)

# 添加过滤器到handler
http_filter = HTTPFilter()
file_handler.addFilter(http_filter)

# 修改日志记录的时间为UTC+8时间
old_factory = logging.getLogRecordFactory()
def record_factory(*args, **kwargs):
    record = old_factory(*args, **kwargs)
    record.created = datetime.fromtimestamp(record.created, tz=timezone.utc).astimezone(utc_8_timezone).timestamp()
    return record
logging.setLogRecordFactory(record_factory)

logger.info(f"Bot started at {now.strftime('%Y-%m-%d %H%M%S')}")

class TelegramUpdate(BaseModel):
    update_id: int
    message: dict = None
    callback_query: dict = None

def display_message(url, partition_name=None, file_name=None):
    """组织消息内容。

    Args:
        url (str): 目标 URL。
        partition_name (str, optional): 目标分区名称。默认为 None。
        file_name (str, optional): 文件名。默认为 None。

    Returns:
        str: 格式化的消息内容。
    """
    message = "<b>Payload Dumper Bot</b>\n"
    message += f"\n🔗URL: \n<code>{url}</code>\n"
    if partition_name:
        message += f"\n💿Partition: <code>{partition_name}</code>\n"
    if file_name:
        message += f"\n📄FILE: \n<code>{file_name}</code>\n"
    return message

async def retry_async(chat_id, status_message_id, coro_function, *args, retry_msg=None, max_retries=3):
    for retry in range(max_retries):
        try:
            return await coro_function(*args)
        except Exception as e:
            if retry_msg:
                logging.warning(retry_msg)
            
            if isinstance(e, RetryAfter):
                retry_after = e.retry_after
                logging.warning(f"Retrying in {retry_after} seconds...")
            else:
                retry_after = RETRY_INTERVAL
                logging.warning(f"Error occurred: {e}. Retrying in {retry_after} seconds...")

            if retry < max_retries - 1:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=status_message_id,
                    text=f"Upload error, attempt {retry + 1}\n上传错误，第{retry + 1}次尝试",
                    parse_mode="HTML"
                )
                await asyncio.sleep(retry_after)
            else:
                logging.error(f"Error occurred: {e}. Maximum retries reached.")
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=status_message_id,
                    text=f"Upload error, failed to retry {max_retries} times. Please try again later\n上传错误，重试{max_retries}次失败。请稍后再试",
                    parse_mode="HTML"
                )
                raise error(f"Error occurred: {e}. Maximum retries reached.")
                raise e

async def send_inline_message(chat_id, text, inline_keyboard=None):
    if inline_keyboard is None:
        inline_keyboard = []

    logging.info(f"Inline keyboard passed: {inline_keyboard}")

    if isinstance(inline_keyboard, InlineKeyboardMarkup):
        inline_keyboard_markup = inline_keyboard
    else:
        try:
            inline_keyboard_markup = InlineKeyboardMarkup(inline_keyboard)
        except TypeError as e:
            logging.error(f"Failed to create inline keyboard markup: {e}")
            inline_keyboard_markup = None

    logging.info(f"Sending message to chat {chat_id}: {text}")
    try:
        message = await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=inline_keyboard_markup,
            parse_mode='HTML'
        )
    except Exception as e:
        logging.error(f"Failed to send message to chat {chat_id}: {e}")
        return None
    return message

async def edit_message(chat_id, message_id, text, reply_markup=None):
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            parse_mode="HTML",
            reply_markup=reply_markup,
        )
    except Exception as e:
        logging.warning(f"Failed to edit message content: {e}")

def create_partition_keyboard(partitions_info, page=1):
    priority_partitions = ["boot", "init_boot", "vbmeta", "vbmeta_system"]
    partitions_info = sorted(
        partitions_info,
        key=lambda x: (x["partition_name"] not in priority_partitions, x["partition_name"]),
    )

    per_page_first = 12
    per_page_other = 14

    if len(partitions_info) <= per_page_first:
        total_pages = 1
    else:
        total_pages = ((len(partitions_info) - per_page_first) + per_page_other - 1) // per_page_other + 1

    start_index = (page - 1) * per_page_other
    if page == 1:
        per_page = per_page_first
        start_index = 0
    else:
        per_page = per_page_other
        start_index = per_page_first + (page - 2) * per_page_other

    end_index = min(start_index + per_page, len(partitions_info))

    keyboard = []
    if page == 1:
        keyboard.append([InlineKeyboardButton(text="🏷️Fetch metadata", callback_data="metadata")])

    row = []
    for i in range(start_index, end_index):
        p = partitions_info[i]
        row.append(
            InlineKeyboardButton(text=f"{p['partition_name']}({p['size_readable']})", callback_data=f"{p['partition_name']}")
        )
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    prev_button = InlineKeyboardButton(text="⬅️", callback_data=f"page {page - 1}") if page > 1 else InlineKeyboardButton(text="⏹️", callback_data=" ")
    next_button = InlineKeyboardButton(text="➡️", callback_data=f"page {page + 1}") if page < total_pages else InlineKeyboardButton(text="⏹️", callback_data=" ")

    keyboard.append(
        [prev_button, InlineKeyboardButton(text=f"📄{page}/{total_pages}", callback_data=" "), next_button]
    )

    return keyboard, total_pages

def store_keyboard_layout(file_name, layout_data):
    if file_name is None or not isinstance(file_name, str):
        logging.error("Invalid file_name: cannot store keyboard layout for None or non-string value")
        return
    if not file_name.endswith(".zip"):
        file_name += ".zip"
    conn = sqlite3.connect('file_cache.db')
    cursor = conn.cursor()
    cursor.execute('INSERT OR REPLACE INTO keyboard_layouts (file_name, layout_data) VALUES (?, ?)', (file_name, json.dumps(layout_data)))
    conn.commit()
    conn.close()
    
    logging.info(f"Keyboard layout data stored for {file_name}")

def get_keyboard_layout(file_name, page=1):
    if file_name is None or not isinstance(file_name, str):
        logging.error("Invalid file_name: cannot get keyboard layout for None or non-string value")
        return None
    if not file_name.endswith(".zip"):
        file_name += ".zip"
    conn = sqlite3.connect('file_cache.db')
    cursor = conn.cursor()
    cursor.execute('SELECT layout_data FROM keyboard_layouts WHERE file_name = ?', (file_name,))
    result = cursor.fetchone()
    conn.close()
    if result:
        layout_data = json.loads(result[0])
        if page <= layout_data["total_pages"]:
            return layout_data["pages"][page - 1]["keyboard"]
    return None
    
@cached(ttl=60)
async def cache_subscription_status():
    return True

async def check_user_subscription(user_id):
    async with httpx.AsyncClient() as client:
        try:
            member = await bot.get_chat_member(CHANNEL_USERNAME, user_id)
            if member.status in ['member', 'administrator', 'creator']:
                return await cache_subscription_status()
        except Exception as e:
            logging.error(f"Failed to check user subscription for user {user_id}: {e}")
    return False

def is_valid_url(url):
    try:
        result = urlparse(url)
        return all([result.scheme, result.netloc])
    except ValueError:
        return False

async def get_user_lock(user_id):
    if user_id not in user_locks:
        user_locks[user_id] = asyncio.Lock()
    return user_locks[user_id]

async def handle_url(update: Update, context: CallbackContext):
    if update.message and update.message.new_chat_members:
        return

    if not update.message or not update.message.text:
        logging.warning("handle_url: Received update without message or text.")
        return

    user_id = update.message.from_user.id
    user_lock = await get_user_lock(user_id)
    async with user_lock:
        # 清空之前的文件名和分区名
        if user_id in user_data_store:
            user_data_store[user_id].pop("file_name", None)
            user_data_store[user_id].pop("partition_name", None)
            user_data_store[user_id].pop("partitions_info", None)
            user_data_store[user_id].pop("partition_file_path", None)
            user_data_store[user_id].pop("ROM_file_name", None)

        if user_id not in user_data_store:
            user_data_store[user_id] = {}

    logging.info(f"Received message from user: {update.message.text}")
    text = update.message.text.strip()

    if text.startswith('/dump'):
        parts = shlex.split(text)
        if len(parts) != 2:
            await send_inline_message(
                update.message.chat_id, "Usage: /dump [url]\n用法: /dump [url]"
            )
            return
        url = parts[1]
    else:
        url = text

    if not is_valid_url(url):
        logging.warning(f"Invalid URL: {url}")
        await send_inline_message(
            update.message.chat_id,
            "Invalid URL. Please provide a valid HTTP or HTTPS URL.\n无效的网址。 请提供有效的 HTTP 或 HTTPS URL。",
        )
        return

    match = re.match(MIUI_URL_REGEX, url)
    if match:
        url = CDN_URL + match.group(1)
        await send_inline_message(
            update.message.chat_id,
            f"The link you provided has been officially speed-limited by Xiaomi and has been replaced with a high-speed CDN link.\n\n你提供的链接被小米官方限速，已替换为高速CDN链接。\n\nCDN URL: \n<code>{url}</code>",
        )

    async with user_lock:
        user_data_store[user_id]["url"] = url
        user_data_store[user_id]["ROM_file_name"] = file_check.get_filename_from_url(url)

    file_name = os.path.basename(url)
    user_data_store[user_id]["file_name"] = file_name

    if file_name:
        layout_data = get_keyboard_layout(user_data_store[user_id]["ROM_file_name"])
        if layout_data:
            logging.info("Found stored keyboard layout, using it.")
            await send_inline_message(
                update.message.chat_id,
                display_message(url=user_data_store[user_id]["url"], file_name=file_name),
                InlineKeyboardMarkup(layout_data)
            )
            return

    logging.info(f"Running payload_dumper command with --list argument for URL: {url}")
    await run_payload_dumper_command(update, context, "--list", [url])

async def handle_unknown_command(update: Update, context: CallbackContext):
    if update.message and update.message.new_chat_members:
        return

    if not update.message or not update.message.text:
        logging.warning("handle_unknown_command: Received update without message or text.")
        return

    logging.warning(f"Unknown command received: {update.message.text}")
    await send_inline_message(
        update.message.chat_id,
        "I don't understand this command. Please use /help for available commands.\n我不理解这个命令。 请使用 /help 获取可用的命令。\n \n Feedback 反馈: @Pillboard",
    )

async def help(update: Update, context: CallbackContext):
    if not update.message or not update.message.from_user:
        logging.warning("help: Received update without message or from_user.")
        return

    user_id = update.message.from_user.id
    if not await check_user_subscription(user_id):
        await send_inline_message(
            update.message.chat_id,
            f"Please subscribe to our channel to use this bot.\n请订阅我们的频道以使用此机器人。\n\nChannel: {CHANNEL_USERNAME}",
        )
        return

    user_lock = await get_user_lock(user_id)
    async with user_lock:
        if user_id not in user_data_store:
            user_data_store[user_id] = {}

    logging.info("Help command received.")
    help_message = (
        "Help:\n"
        "Extract Android ROM partition image form URL.\n"
        "Only supports payload.bin packed format, and only supports flashable zip packages.\n"
        "Just send your ROM URL or use /dump [url]\n"
        "Due to server limitations, the following partitions cannot be extracted:"
        "modem, modemfirmware, odm, product, system, system_ext, vendor\n"
        "Provide timely feedback when encountering problems or get more details at @Pillboard\n\n"
        "帮助:\n"
        "从Android ROM链接中提取分区镜像。\n"
        "仅支持payload.bin打包模式，仅支持zip格式卡刷包。\n"
        "发送你的ROM URL即可，或者使用 /dump [url]\n"
        "由于服务器受限，下列分区无法提取：modem, modemfirmware, odm, product, system, system_ext, vendor\n"
        "更多说明及反馈: @Pillboard"
    )
    await send_inline_message(
        update.message.chat_id,
        help_message
    )

async def button_callback(update: Update, context: CallbackContext):
    if not update.callback_query or not update.callback_query.from_user:
        logging.warning("button_callback: Received update without callback_query or from_user.")
        return

    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    user_lock = await get_user_lock(user_id)
    async with user_lock:
        if user_id not in user_data_store:
            user_data_store[user_id] = {}

    logging.info(f"Button callback received: {query.data}")

    async with user_lock:
        url = user_data_store[user_id].get("url")
        partitions_info = user_data_store[user_id].get("partitions_info", [])
        current_page = user_data_store[user_id].get("current_page", 1)
        file_name = user_data_store[user_id].get("file_name")
        ROM_file_name = user_data_store[user_id].get("ROM_file_name")

    if query.data == "return":
        if not url:
            logging.warning("No URL found for user.")
            await send_inline_message(
                query.message.chat.id,
                "No URL found for this session. Please start over.\n未找到URL，请重新开始。"
            )
            return

        # 从数据库中读取键盘布局数据
        keyboard = get_keyboard_layout(ROM_file_name, 1)
        if keyboard is None:
            logging.warning("No stored keyboard layout found, regenerating layout.")
            await run_payload_dumper_command(update, context, "--list", [url])
        else:
            logging.info(f"Found stored keyboard layout for {ROM_file_name}, using it.")
            reply_markup = InlineKeyboardMarkup([
                [InlineKeyboardButton(**button) for button in row]
                for row in keyboard
            ])

            await edit_message(
                query.message.chat.id,
                query.message.message_id,
                display_message(url=user_data_store[user_id]["url"], file_name=file_name),
                reply_markup=reply_markup,
            )
    elif query.data.startswith("page"):
        requested_page = int(query.data.split(" ")[1])

        logging.info(f"Current file name: {file_name}")
        logging.info(f"Requested page: {requested_page}")

        keyboard = get_keyboard_layout(ROM_file_name, requested_page)
        logging.info(f"Layout data for {ROM_file_name} page {requested_page}: {keyboard}")
        if keyboard:
            reply_markup = InlineKeyboardMarkup([
                [InlineKeyboardButton(**button) for button in row]
                for row in keyboard
            ])

            await edit_message(
                query.message.chat.id,
                query.message.message_id,
                display_message(
                    url=user_data_store[user_id]["url"],
                    file_name=file_name,
                    partition_name=user_data_store[user_id].get("partition_name"),
                ),
                reply_markup=reply_markup,
            )

            async with user_lock:
                user_data_store[user_id]["current_page"] = requested_page
        else:
            logging.warning("Invalid page number or no keyboard layout found for the file.")
            await send_inline_message(
                query.message.chat.id,
                "Invalid page number or no keyboard layout found for this file. Please start over.\n无效页码或未找到此文件的键盘布局，请重新开始。"
            )
    elif query.data == "metadata":
        if not url:
            logging.warning("No URL found for user.")
            await send_inline_message(
                query.message.chat.id,
                "No URL found for this session. Please start over.\n未找到URL，请重新开始。"
            )
            return
        await edit_message(
            query.message.chat.id,
            query.message.message_id,
            f"{display_message(url=user_data_store[user_id]['url'], file_name=file_name, partition_name=user_data_store[user_id].get('partition_name'))}\nFetching metadata, please wait...\n正在获取元数据，请稍候...",
        )
        logging.info(f"Running payload_dumper command with --metadata argument for URL: {url}")
        await run_payload_dumper_command(update, context, "--metadata", [url])
    elif query.data in ["⏹️", " "]:
        return
    else:
        partition_name = query.data
        if partition_name in BLACKLISTED_PARTITIONS:
            logging.warning(f"Partition '{partition_name}' is not supported.")
            await edit_message(
                query.message.chat.id,
                query.message.message_id,
                text=f"{display_message(url=user_data_store[user_id]['url'], file_name=file_name, partition_name=user_data_store[user_id].get('partition_name'))}\nServer restricted, partition {partition_name} is not supported.\n\n由于服务器限制，不支持'{partition_name}'分区",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("返回", callback_data="return")]]),
            )
        else:
            await edit_message(
                query.message.chat.id,
                query.message.message_id,
                f"{display_message(url=user_data_store[user_id]['url'], file_name=file_name, partition_name=partition_name)}\nDumping partition '{partition_name}', please wait...\n正在提取分区 '{partition_name}'，请稍候...",
            )
            logging.info(f"Running payload_dumper command with --dump argument for URL: {url} and partition: {partition_name}")
            await run_payload_dumper_command(update, context, "--dump", [url, partition_name])


def get_file_id(file_name):
    conn = sqlite3.connect('file_cache.db')
    cursor = conn.cursor()
    cursor.execute('SELECT file_id FROM file_cache WHERE file_name = ?', (file_name,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else None

# 存储文件ID
def store_file_id(file_name, file_id):
    conn = sqlite3.connect('file_cache.db')
    cursor = conn.cursor()
    cursor.execute('INSERT OR REPLACE INTO file_cache (file_name, file_id) VALUES (?, ?)', (file_name, file_id))
    conn.commit()
    conn.close()
    
async def handle_subprocess_output(process, status_message, update, context, command):
    file_path = None
    file_name = None
    multi_line_message = False
    message_buffer = []

    async def read_stream(stream):
        while True:
            output = await stream.readline()
            if output:
                output_str = output.decode().strip()
                logging.error(f"Subprocess {stream.pipe} output: {output_str}")
            else:
                break

    asyncio.create_task(read_stream(process.stderr))

    user_id = update.message.from_user.id if update.message else update.callback_query.from_user.id
    chat_id = update.message.chat.id if update.message else update.callback_query.message.chat.id

    while True:
        try:
            output = await asyncio.wait_for(process.stdout.readline(), timeout=0.1)
        except asyncio.TimeoutError:
            continue

        if output:
            output_str = output.decode().strip()
            logging.info(f"Subprocess output: {output_str}")
            if output_str.startswith("STATUS:"):
                multi_line_message = True
                output_str = output_str[7:]
            elif output_str.startswith("ERROR:"):
                multi_line_message = True
                output_str = output_str[6:]
                logging.error(output_str)
            elif output_str.startswith("STATUS_END") or output_str.startswith("ERROR_END"):
                multi_line_message = False
                message = "\n".join(message_buffer[1:])
                if output_str.startswith("ERROR_END"):
                    logging.error(message)
                else:
                    logging.info(message)
                await edit_message(
                    chat_id,
                    status_message.message_id,
                    f"{display_message(url=user_data_store[user_id]['url'], file_name=user_data_store[user_id].get("file_name"), partition_name=user_data_store[user_id]['partition_name'])}\n{message}",
                )
                message_buffer = []
                if output_str.startswith("ERROR_END"):
                    return
            elif output_str.startswith("FILE:"):
                file_path = output_str.split(":", 1)[1].strip()
                logging.info(f"File path received: {file_path}")
                if file_path.startswith("output/partitions/"):
                    file_name = os.path.splitext(os.path.basename(file_path))[0]
                else:
                    file_name = os.path.basename(file_path)
                logging.info(f"Setting file name: {file_name}")
                async with user_locks[user_id]:
                    user_data_store[user_id]["file_name"] = file_name
                break
            if multi_line_message:
                message_buffer.append(output_str)
        else:
            break

    return_markup = InlineKeyboardMarkup([[InlineKeyboardButton("Return", callback_data="return")]])

    if command == "--list" and file_path:
        try:
            await edit_message(
                chat_id,
                status_message.message_id,
                f"{display_message(url=user_data_store[user_id]['url'], file_name=user_data_store[user_id]['file_name'])}\nLoading partition, please wait...\n正在加载分区，请稍候...",
            )
            with open(file_path, "r") as f:
                partitions_info = json.load(f)
            async with user_locks[user_id]:
                user_data_store[user_id]["partitions_info"] = partitions_info
                user_data_store[user_id]["partition_file_path"] = file_path

            layout_data = {
                "file_name": file_name,
                "total_pages": 1,
                "pages": []
            }
            page_number = 1
            while True:
                keyboard, total_pages = create_partition_keyboard(partitions_info, page=page_number)
                layout_data["pages"].append({
                    "page_number": page_number,
                    "keyboard": [[button.to_dict() for button in row] for row in keyboard]
                })
                layout_data["total_pages"] = total_pages
                if page_number >= total_pages:
                    break
                page_number += 1

            store_keyboard_layout(user_data_store[user_id]["ROM_file_name"], layout_data)

            logging.info(f"Attempting to retrieve keyboard layout for {user_data_store[user_id]['ROM_file_name']}")
            keyboard = get_keyboard_layout(user_data_store[user_id]["ROM_file_name"], 1)
            if keyboard is None:
                logging.error(f"Failed to retrieve keyboard layout for {user_data_store[user_id]['ROM_file_name']} page 1")
            else:
                logging.info(f"Retrieved layout data for {user_data_store[user_id]['ROM_file_name']} page 1: {keyboard}")
                reply_markup = InlineKeyboardMarkup([
                    [InlineKeyboardButton(**button) for button in row]
                    for row in keyboard
                ])

                await edit_message(
                    chat_id,
                    status_message.message_id,
                    display_message(url=user_data_store[user_id]["url"], file_name=user_data_store[user_id]["file_name"]),
                    reply_markup=reply_markup,
                )
        except (IOError, json.JSONDecodeError) as e:
            logging.error(f"Error reading or parsing partition info: {e}")
            await edit_message(
                chat_id,
                status_message.message_id,
                f"{display_message(url=user_data_store[user_id]['url'], file_name=user_data_store[user_id].get("file_name"), partition_name=user_data_store[user_id].get('partition_name'))}\nError reading or parsing partition info: {e}\n读取或解析分区信息时出错: {e}",
            )
    elif command == "--metadata" and file_path:
        try:
            with open(file_path, "r") as f:
                metadata_content = f.read()
            await edit_message(
                chat_id,
                status_message.message_id,
                f"{display_message(url=user_data_store[user_id]['url'], file_name=user_data_store[user_id].get("file_name"), partition_name=user_data_store[user_id].get('partition_name'))}\n🏷️Metadata:\n<code>{metadata_content}</code>",
                reply_markup=return_markup,
            )
        except IOError as e:
            logging.error(f"Error reading metadata file: {e}")
            await edit_message(
                chat_id,
                status_message.message_id,
                f"{display_message(url=user_data_store[user_id]['url'], file_name=user_data_store[user_id].get("file_name"), partition_name=user_data_store[user_id].get('partition_name'))}\nError reading metadata file: {e}\n读取元数据文件时出错: {e}",
                reply_markup=return_markup,
            )
    elif file_path:
        try:
            if os.path.getsize(file_path) == 0:
                raise ValueError("File is empty")
            
            # Check for cached file ID
            cached_file_id = get_file_id(file_name)
            if cached_file_id:
                await bot.send_document(chat_id=chat_id, document=cached_file_id)
                await edit_message(
                    chat_id,
                    status_message.message_id,
                    f"{display_message(url=user_data_store[user_id]['url'], file_name=user_data_store[user_id].get('file_name'), partition_name=user_data_store[user_id]['partition_name'])}\nFile sent successfully.\n文件上传成功。",
                    reply_markup=return_markup,
                )
                return

            async def send_document():
                with open(file_path, "rb") as f:
                    await edit_message(
                        chat_id,
                        status_message.message_id,
                        f"{display_message(url=user_data_store[user_id]['url'], file_name=user_data_store[user_id].get('file_name'), partition_name=user_data_store[user_id]['partition_name'])}\nUploading...\n上传中...",
                    )
                    message = await bot.send_document(chat_id=chat_id, document=f)
                    return message.document.file_id

            file_id = await retry_async(
                chat_id,
                status_message.message_id,
                send_document,
                retry_msg="Error occurred while sending document.",
            )
            if file_id:
                logging.info("File uploaded successfully.")
                store_file_id(file_name, file_id)
                await edit_message(
                    chat_id,
                    status_message.message_id,
                    f"{display_message(url=user_data_store[user_id]['url'], file_name=user_data_store[user_id].get('file_name'), partition_name=user_data_store[user_id]['partition_name'])}\nFile uploaded successfully.\n文件上传成功。",
                    reply_markup=return_markup,
                )
                return  # Ensure we return here to avoid error message
                
            else:
                logging.error("Failed to upload file.")
                await edit_message(
                    chat_id,
                    status_message.message_id,
                    f"{display_message(url=user_data_store[user_id]['url'], file_name=user_data_store[user_id].get('file_name'), partition_name=user_data_store[user_id]['partition_name'])}\nFailed to upload file.\n文件上传失败。",
                    reply_markup=return_markup,
                )
        except IOError as e:
            logging.error(f"Error reading file: {e}")
            await edit_message(
                chat_id,
                status_message.message_id,
                f"{display_message(url=user_data_store[user_id]['url'], file_name=user_data_store[user_id].get('file_name'), partition_name=user_data_store[user_id].get('partition_name'))}\nError reading file: {e}\n读取文件时出错: {e}",
                reply_markup=return_markup,
            )
        except ValueError as e:
            logging.error(f"Error: {e}")
            await edit_message(
                chat_id,
                status_message.message_id,
                f"{display_message(url=user_data_store[user_id]['url'], file_name=user_data_store[user_id].get('file_name'), partition_name=user_data_store[user_id].get('partition_name'))}\nError: {e}\n错误: {e}",
                reply_markup=return_markup,
            )
    else:
        logging.error("Payload dumper execution failed.")
        await edit_message(
            chat_id,
            status_message.message_id,
            f"{display_message(url=user_data_store[user_id]['url'], file_name=user_data_store[user_id].get('file_name'), partition_name=user_data_store[user_id].get('partition_name'))}\nPayload dumper execution failed.\nPayload dumper 执行失败。",
            reply_markup=return_markup,
        )
async def run_payload_dumper_command(update: Update, context: CallbackContext, command: str, args: list):
    url = args[0]
    if len(args) > 1:
        partition = args[1]
    else:
        partition = ""

    user_id = update.message.from_user.id if update.message else update.callback_query.from_user.id
    user_lock = await get_user_lock(user_id)
    async with user_lock:
        if user_id not in user_data_store:
            user_data_store[user_id] = {}
        # 在处理新 URL 时清空相关用户数据
        user_data_store[user_id]["file_name"] = None
        user_data_store[user_id]["partition_name"] = None

    chat_id = update.message.chat.id if update.message else update.callback_query.message.chat.id

    logging.info(f"Running payload_dumper command: {command} with arguments: {args}")

    if update.message:
        status_message = await update.message.reply_text(
            text="Parsing...\n解析中...",
            reply_markup=InlineKeyboardMarkup([]),
        )
    else:
        status_message = update.callback_query.message

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    try:
        if partition:
            async with user_lock:
                user_data_store[user_id]["partition_name"] = partition
            await edit_message(
                chat_id,
                status_message.message_id,
                display_message(url=url, file_name=None, partition_name=partition),
            )
            command_args = (
                ["python3", "queue_scripts.py", command]
                + [f"{partition}"]
                + [f'"{url}"']
            )
        else:
            async with user_lock:
                user_data_store[user_id]["partition_name"] = None
            await edit_message(
                chat_id,
                status_message.message_id,
                display_message(url=url, file_name=None),
            )
            command_args = ["python3", "concurrent_scripts.py", command] + [f'"{url}"']

        process = await asyncio.create_subprocess_exec(
            *command_args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env
        )

        logging.info(f"Subprocess created with command: {command_args}")

        asyncio.create_task(handle_subprocess_output(process, status_message, update, context, command))

    except Exception as e:
        logging.error(f"An error occurred: {e}")
        await edit_message(
            chat_id,
            status_message.message_id,
            f"{display_message(url=user_data_store[user_id].get('url', 'Unknown URL'), file_name=None, partition_name=user_data_store[user_id].get('partition_name'))}\nAn error occurred: {e}\n发生错误: {e}",
        )


        
@asynccontextmanager
async def lifespan(app: FastAPI):
    webhook_url = f"https://api.telegram.org/bot{TOKEN}/setWebhook"
    data = {"url": WEBHOOK_URL}
    async with http_client as client:
        try:
            response = await client.post(webhook_url, data=data)
            response.raise_for_status()
            logging.info(f"Webhook set successfully with URL: {WEBHOOK_URL}")
        except httpx.RequestError as e:
            logging.error(f"Failed to set webhook: {e}")
    
    yield

    await http_client.aclose()  # 关闭全局http_client连接

app = FastAPI(lifespan=lifespan)

@app.post("/webhook")
async def webhook(request: Request):
    try:
        data = await request.json()
        update = Update.de_json(data, bot)
        context = CallbackContext.from_update(update, bot)

        # 记录接收到的更新
        logging.info(f"Webhook received data: {data}")

        user_id = None
        chat_type = None

        if update.message:
            user_id = update.message.from_user.id
            chat_type = update.message.chat.type
        elif update.callback_query:
            user_id = update.callback_query.from_user.id
            chat_type = update.callback_query.message.chat.type if update.callback_query.message else None
        elif update.my_chat_member:
            user_id = update.my_chat_member.from_user.id
            chat_type = update.my_chat_member.chat.type

        # 仅处理私聊和群组消息，忽略其他类型的消息
        if chat_type not in ['private', 'group', 'supergroup']:
            logging.info(f"Ignored update from chat type: {chat_type}")
            return JSONResponse(content={"status": "ignored"}, status_code=200)

        if user_id is None:
            logging.error("Invalid update: user_id is None")
            logging.error(f"Update content: {data}")
            raise HTTPException(status_code=400, detail="Invalid update")

        user_lock = await get_user_lock(user_id)
        async with user_lock:
            if user_id not in user_data_store:
                user_data_store[user_id] = {}

        if update.message:
            if update.message.text and (update.message.text == '/start' or update.message.text == '/help'):
                await help(update, context)
            else:
                await handle_url(update, context)
        elif update.callback_query:
            await button_callback(update, context)
        elif update.my_chat_member:
            # 处理 my_chat_member 更新
            logging.info(f"Received my_chat_member update: {update.my_chat_member}")
            # 可以在此处添加更多处理逻辑，例如记录日志或执行某些操作
    except Exception as e:
        logging.error(f"An error occurred in webhook: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    return JSONResponse(content={"status": "ok"})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=6400)
