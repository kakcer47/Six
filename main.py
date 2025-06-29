import asyncio
import logging
import os
import asyncpg
from aiohttp import web, ClientSession
from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, 
    InlineKeyboardButton, BotCommand
)
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

# Настройки
BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")  # PostgreSQL URL
TARGET_CHAT_ID = int(os.getenv("TARGET_CHAT_ID", "-1002827106973"))  # ID супергруппы
MODERATION_CHAT_ID = int(os.getenv("MODERATION_CHAT_ID", "0"))  # ID группы модерации
GROUP_LINK = os.getenv("GROUP_LINK", "https://t.me/your_group")  # Ссылка на группу
EXAMPLE_URL = os.getenv("EXAMPLE_URL", "https://example.com")  # Ссылка на пример
PORT = int(os.getenv("PORT", 8080))  # Порт для веб-сервера

# Логирование
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Состояния
class AdStates(StatesGroup):
    choosing_language = State()
    main_menu = State()
    choosing_topic = State() 
    topic_selected = State()
    writing_ad = State()
    my_ads = State()

# Темы группы - НАСТРОЙТЕ ПОД СВОЮ ГРУППУ
TOPICS = {
    "topic_1": {"name": "💼 Работа", "id": 27},
    "topic_2": {"name": "🏠 Недвижимость", "id": 28},
    "topic_3": {"name": "🚗 Авто", "id": 29},
    "topic_4": {"name": "🛍️ Товары", "id": 30},
    "topic_5": {"name": "💡 Услуги", "id": 31},
    "topic_6": {"name": "📚 Обучение", "id": 32},
}

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# Пул соединений с БД
db_pool = None

# Веб-приложение для Render
app = web.Application()

async def health_check(request):
    """Проверка здоровья сервиса"""
    return web.Response(text="Bot is running!")

async def ping_self():
    """Автопинг для предотвращения засыпания"""
    url = f"https://six-o46c.onrender.com"  # Замените на ваш URL
    try:
        async with ClientSession() as session:
            async with session.get(url) as response:
                logger.info(f"Self ping: {response.status}")
    except Exception as e:
        logger.warning(f"Self ping failed: {e}")

async def start_ping_task():
    """Запуск задачи автопинга каждые 14 минут"""
    while True:
        await asyncio.sleep(840)  # 14 минут
        await ping_self()

async def init_db():
    """Инициализация базы данных PostgreSQL"""
    global db_pool
    
    if not DATABASE_URL:
        raise ValueError("DATABASE_URL не установлен!")
    
    try:
        # Создаем пул соединений
        db_pool = await asyncpg.create_pool(DATABASE_URL)
        
        # Создаем таблицы
        async with db_pool.acquire() as connection:
            # Таблица объявлений пользователей
            await connection.execute("""
                CREATE TABLE IF NOT EXISTS user_ads (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    message_id BIGINT NOT NULL,
                    message_url TEXT NOT NULL,
                    topic_name TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Таблица забаненных пользователей
            await connection.execute("""
                CREATE TABLE IF NOT EXISTS banned_users (
                    user_id BIGINT PRIMARY KEY
                )
            """)
            
            # Таблица лимитов пользователей
            await connection.execute("""
                CREATE TABLE IF NOT EXISTS user_limits (
                    user_id BIGINT PRIMARY KEY,
                    ad_limit INTEGER NOT NULL DEFAULT 4
                )
            """)
            
            # Создаем индексы для оптимизации
            await connection.execute("""
                CREATE INDEX IF NOT EXISTS idx_user_ads_user_id ON user_ads(user_id)
            """)
            await connection.execute("""
                CREATE INDEX IF NOT EXISTS idx_user_ads_message_id ON user_ads(message_id)
            """)
        
        logger.info("✅ База данных успешно инициализирована")
        
    except Exception as e:
        logger.error(f"❌ Ошибка инициализации БД: {e}")
        raise

async def add_user_ad(user_id: int, message_id: int, message_url: str, topic_name: str):
    """Добавить объявление пользователя в БД"""
    try:
        async with db_pool.acquire() as connection:
            await connection.execute(
                "INSERT INTO user_ads (user_id, message_id, message_url, topic_name) VALUES ($1, $2, $3, $4)",
                user_id, message_id, message_url, topic_name
            )
    except Exception as e:
        logger.error(f"Ошибка добавления объявления: {e}")

async def get_user_ads(user_id: int):
    """Получить объявления пользователя"""
    try:
        async with db_pool.acquire() as connection:
            rows = await connection.fetch(
                "SELECT message_id, message_url, topic_name FROM user_ads WHERE user_id = $1 ORDER BY created_at DESC",
                user_id
            )
            return [(row['message_id'], row['message_url'], row['topic_name']) for row in rows]
    except Exception as e:
        logger.error(f"Ошибка получения объявлений: {e}")
        return []

async def get_ad_by_message_id(message_id: int):
    """Получить объявление по message_id"""
    try:
        async with db_pool.acquire() as connection:
            row = await connection.fetchrow(
                "SELECT user_id, message_id, message_url, topic_name FROM user_ads WHERE message_id = $1",
                message_id
            )
            if row:
                return (row['user_id'], row['message_id'], row['message_url'], row['topic_name'])
            return None
    except Exception as e:
        logger.error(f"Ошибка получения объявления: {e}")
        return None

async def delete_user_ad(message_id: int):
    """Удалить объявление из БД"""
    try:
        async with db_pool.acquire() as connection:
            await connection.execute(
                "DELETE FROM user_ads WHERE message_id = $1",
                message_id
            )
    except Exception as e:
        logger.error(f"Ошибка удаления объявления: {e}")

async def delete_all_user_ads(user_id: int):
    """Удалить все объявления пользователя"""
    try:
        async with db_pool.acquire() as connection:
            # Получаем все объявления пользователя для удаления из чата
            rows = await connection.fetch(
                "SELECT message_id FROM user_ads WHERE user_id = $1",
                user_id
            )
            
            # Удаляем из чата
            deleted_count = 0
            for row in rows:
                message_id = row['message_id']
                try:
                    await bot.delete_message(chat_id=TARGET_CHAT_ID, message_id=message_id)
                    deleted_count += 1
                except Exception as e:
                    logger.warning(f"Не удалось удалить сообщение {message_id}: {e}")
            
            # Удаляем из БД
            await connection.execute(
                "DELETE FROM user_ads WHERE user_id = $1",
                user_id
            )
            
            return deleted_count
    except Exception as e:
        logger.error(f"Ошибка удаления всех объявлений: {e}")
        return 0

async def ban_user(user_id: int):
    """Забанить пользователя"""
    try:
        async with db_pool.acquire() as connection:
            await connection.execute(
                "INSERT INTO banned_users (user_id) VALUES ($1) ON CONFLICT (user_id) DO NOTHING",
                user_id
            )
    except Exception as e:
        logger.error(f"Ошибка бана пользователя: {e}")

async def unban_user(user_id: int):
    """Разбанить пользователя"""
    try:
        async with db_pool.acquire() as connection:
            await connection.execute(
                "DELETE FROM banned_users WHERE user_id = $1",
                user_id
            )
    except Exception as e:
        logger.error(f"Ошибка разбана пользователя: {e}")

async def is_user_banned(user_id: int) -> bool:
    """Проверить, забанен ли пользователь"""
    try:
        async with db_pool.acquire() as connection:
            result = await connection.fetchval(
                "SELECT 1 FROM banned_users WHERE user_id = $1",
                user_id
            )
            return result is not None
    except Exception as e:
        logger.error(f"Ошибка проверки бана: {e}")
        return False

async def get_user_ad_count(user_id: int) -> int:
    """Получить количество объявлений пользователя"""
    try:
        async with db_pool.acquire() as connection:
            result = await connection.fetchval(
                "SELECT COUNT(*) FROM user_ads WHERE user_id = $1",
                user_id
            )
            return result if result else 0
    except Exception as e:
        logger.error(f"Ошибка подсчета объявлений: {e}")
        return 0

async def get_user_limit(user_id: int) -> int:
    """Получить лимит объявлений для пользователя"""
    try:
        async with db_pool.acquire() as connection:
            result = await connection.fetchval(
                "SELECT ad_limit FROM user_limits WHERE user_id = $1",
                user_id
            )
            return result if result else 4  # По умолчанию 4
    except Exception as e:
        logger.error(f"Ошибка получения лимита: {e}")
        return 4

async def set_user_limit(user_id: int, limit: int):
    """Установить лимит объявлений для пользователя"""
    try:
        async with db_pool.acquire() as connection:
            await connection.execute(
                "INSERT INTO user_limits (user_id, ad_limit) VALUES ($1, $2) ON CONFLICT (user_id) DO UPDATE SET ad_limit = $2",
                user_id, limit
            )
    except Exception as e:
        logger.error(f"Ошибка установки лимита: {e}")

async def send_to_moderation(user_id: int, username: str, text: str, message_url: str, topic_name: str):
    """Отправить уведомление в группу модерации"""
    if not MODERATION_CHAT_ID or MODERATION_CHAT_ID == 0:
        return
    
    try:
        username_text = f"@{username}" if username else "Нет username"
        moderation_text = (
            f"📋 Новое объявление:\n\n"
            f"👤 ID: {user_id}\n"
            f"🔗 Username: {username_text}\n"
            f"📂 Тема: {topic_name}\n\n"
            f"📝 Текст:\n{text}\n\n"
            f"🔗 Ссылка: {message_url}"
        )
        
        await bot.send_message(
            chat_id=MODERATION_CHAT_ID,
            text=moderation_text
        )
    except Exception as e:
        logger.error(f"Ошибка отправки в модерацию: {e}")

def get_ad_actions_keyboard(message_id: int, message_url: str):
    """Клавиатура действий с объявлением"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🗑 Удалить", callback_data=f"delete_ad_{message_id}"),
            InlineKeyboardButton(text="👁 Посмотреть", url=message_url)
        ],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_my_ads")]
    ])
    return keyboard

def get_language_keyboard():
    """Клавиатура выбора языка"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🇷🇺 Русский", callback_data="lang_ru"),
            InlineKeyboardButton(text="🇬🇧 English", callback_data="lang_en")
        ]
    ])
    return keyboard

def get_main_menu_keyboard():
    """Главное меню после выбора языка"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="➕ Создать", callback_data="create_ad"),
            InlineKeyboardButton(text="📋 Мои объявления", callback_data="my_ads")
        ],
        [InlineKeyboardButton(text="🔗 Перейти в группу", url=GROUP_LINK)],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_language")]
    ])
    return keyboard

def get_back_to_main_keyboard():
    """Клавиатура с кнопкой 'На главную'"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏠 На главную", callback_data="back_to_main")]
    ])
    return keyboard

def get_topics_keyboard():
    """Клавиатура выбора тем (по 2 на линию)"""
    buttons = []
    topic_items = list(TOPICS.items())
    
    for i in range(0, len(topic_items), 2):
        row = []
        # Первая кнопка в ряду
        topic_key, topic_data = topic_items[i]
        row.append(InlineKeyboardButton(
            text=topic_data["name"], 
            callback_data=topic_key
        ))
        
        # Вторая кнопка в ряду (если есть)
        if i + 1 < len(topic_items):
            topic_key2, topic_data2 = topic_items[i + 1]
            row.append(InlineKeyboardButton(
                text=topic_data2["name"], 
                callback_data=topic_key2
            ))
        
        buttons.append(row)
    
    # Добавляем кнопку "Назад"
    buttons.append([
        InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main")
    ])
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    return keyboard

def get_topic_actions_keyboard():
    """Клавиатура действий с темой"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📖 Пример заполнения", url=EXAMPLE_URL)],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_topics")]
    ])
    return keyboard

def get_post_actions_keyboard(user_id: int, message_url: str):
    """Клавиатура после публикации"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="➕ Создать", callback_data="create_new"),
            InlineKeyboardButton(text="👁 Посмотреть", url=message_url)
        ],
        [InlineKeyboardButton(text="📋 Мои объявления", callback_data="my_ads")]
    ])
    return keyboard

def get_contact_keyboard(user_id: int, username: str = None):
    """Кнопка для связи с автором"""
    if username:
        url = f"https://t.me/{username}"
    else:
        url = f"tg://user?id={user_id}"
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✉️ Написать", url=url)]
    ])
    return keyboard

async def get_my_ads_keyboard(user_id: int):
    """Клавиатура с объявлениями пользователя"""
    ads = await get_user_ads(user_id)
    buttons = []
    
    for i, (message_id, message_url, topic_name) in enumerate(ads, 1):
        buttons.append([
            InlineKeyboardButton(
                text=f"📄 Объявление {i}", 
                callback_data=f"view_ad_{message_id}"
            )
        ])
    
    # Кнопка назад
    buttons.append([
        InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main")
    ])
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    return keyboard

@dp.message(Command("start"))
async def start_handler(message: Message, state: FSMContext):
    """Обработка команды /start"""
    # Блокируем команды из группы объявлений
    if message.chat.id == TARGET_CHAT_ID:
        return
    
    # Проверяем бан
    if await is_user_banned(message.from_user.id):
        await message.answer("🚫 Вы заблокированы в этом боте.")
        return
    
    await message.answer(
        "🌍 Выберите язык / Choose language:",
        reply_markup=get_language_keyboard()
    )
    await state.set_state(AdStates.choosing_language)

# Команды модерации
@dp.message(Command("ban"))
async def ban_command(message: Message):
    """Команда бана пользователя"""
    # Блокируем команды из группы объявлений
    if message.chat.id == TARGET_CHAT_ID:
        return
    
    # Работает только в группе модерации
    if not MODERATION_CHAT_ID or message.chat.id != MODERATION_CHAT_ID:
        return
    
    try:
        args = message.text.split()
        if len(args) != 2:
            await message.answer("❌ Использование: /ban <user_id>")
            return
        
        user_id = int(args[1])
        await ban_user(user_id)
        await message.answer(f"✅ Пользователь {user_id} забанен")
        
    except ValueError:
        await message.answer("❌ Неверный ID пользователя")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")

@dp.message(Command("banall"))
async def banall_command(message: Message):
    """Команда бана и удаления всех сообщений пользователя"""
    # Блокируем команды из группы объявлений
    if message.chat.id == TARGET_CHAT_ID:
        return
    
    # Работает только в группе модерации
    if not MODERATION_CHAT_ID or message.chat.id != MODERATION_CHAT_ID:
        return
    
    try:
        args = message.text.split()
        if len(args) != 2:
            await message.answer("❌ Использование: /banall <user_id>")
            return
        
        user_id = int(args[1])
        
        # Удаляем все объявления
        deleted_count = await delete_all_user_ads(user_id)
        
        # Банем пользователя
        await ban_user(user_id)
        
        await message.answer(
            f"✅ Пользователь {user_id} забанен\n"
            f"🗑 Удалено объявлений: {deleted_count}"
        )
        
    except ValueError:
        await message.answer("❌ Неверный ID пользователя")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")

@dp.message(Command("banoff"))
async def banoff_command(message: Message):
    """Команда разбана пользователя"""
    # Блокируем команды из группы объявлений
    if message.chat.id == TARGET_CHAT_ID:
        return
    
    # Работает только в группе модерации
    if not MODERATION_CHAT_ID or message.chat.id != MODERATION_CHAT_ID:
        return
    
    try:
        args = message.text.split()
        if len(args) != 2:
            await message.answer("❌ Использование: /banoff <user_id>")
            return
        
        user_id = int(args[1])
        
        # Проверяем, забанен ли пользователь
        if not await is_user_banned(user_id):
            await message.answer(f"❌ Пользователь {user_id} не был забанен")
            return
        
        # Разбаниваем пользователя
        await unban_user(user_id)
        
        await message.answer(f"✅ Пользователь {user_id} разбанен")
        
    except ValueError:
        await message.answer("❌ Неверный ID пользователя")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")

@dp.message(Command("setlimit"))
async def setlimit_command(message: Message):
    """Команда установки лимита объявлений"""
    # Блокируем команды из группы объявлений
    if message.chat.id == TARGET_CHAT_ID:
        return
    
    # Работает только в группе модерации
    if not MODERATION_CHAT_ID or message.chat.id != MODERATION_CHAT_ID:
        return
    
    try:
        args = message.text.split()
        if len(args) != 3:
            await message.answer("❌ Использование: /setlimit <user_id> <limit>")
            return
        
        user_id = int(args[1])
        limit = int(args[2])
        
        if limit < 0:
            await message.answer("❌ Лимит не может быть отрицательным")
            return
        
        if limit > 50:
            await message.answer("❌ Максимальный лимит: 50 объявлений")
            return
        
        await set_user_limit(user_id, limit)
        await message.answer(f"✅ Лимит для пользователя {user_id} установлен: {limit} объявлений")
        
    except ValueError:
        await message.answer("❌ Неверные параметры. Используйте числа.")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")

@dp.message(Command("getlimit"))
async def getlimit_command(message: Message):
    """Команда получения лимита объявлений"""
    # Блокируем команды из группы объявлений
    if message.chat.id == TARGET_CHAT_ID:
        return
    
    # Работает только в группе модерации
    if not MODERATION_CHAT_ID or message.chat.id != MODERATION_CHAT_ID:
        return
    
    try:
        args = message.text.split()
        if len(args) != 2:
            await message.answer("❌ Использование: /getlimit <user_id>")
            return
        
        user_id = int(args[1])
        current_count = await get_user_ad_count(user_id)
        user_limit = await get_user_limit(user_id)
        
        await message.answer(
            f"👤 Пользователь: {user_id}\n"
            f"📊 Объявлений: {current_count}/{user_limit}\n"
            f"🔢 Лимит: {user_limit}"
        )
        
    except ValueError:
        await message.answer("❌ Неверный ID пользователя")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")

@dp.callback_query(F.data == "lang_ru", StateFilter(AdStates.choosing_language))
async def language_ru_handler(callback: CallbackQuery, state: FSMContext):
    """Выбор русского языка"""
    # Проверяем бан
    if await is_user_banned(callback.from_user.id):
        await callback.answer("🚫 Вы заблокированы в этом боте.", show_alert=True)
        return
    
    await callback.message.edit_text(
        "🏠 Главное меню:",
        reply_markup=get_main_menu_keyboard()
    )
    await state.set_state(AdStates.main_menu)
    await callback.answer()

@dp.callback_query(F.data == "lang_en", StateFilter(AdStates.choosing_language))
async def language_en_handler(callback: CallbackQuery, state: FSMContext):
    """Выбор английского языка (заглушка)"""
    await callback.answer("🚧 English version coming soon!", show_alert=True)

@dp.callback_query(F.data == "create_ad")
async def create_ad_handler(callback: CallbackQuery, state: FSMContext):
    """Создание объявления"""
    # Проверяем бан
    if await is_user_banned(callback.from_user.id):
        await callback.answer("🚫 Вы заблокированы в этом боте.", show_alert=True)
        return
    
    await callback.message.edit_text(
        "📝 В какую тему хотите написать?",
        reply_markup=get_topics_keyboard()
    )
    await state.set_state(AdStates.choosing_topic)
    await callback.answer()

@dp.callback_query(F.data == "my_ads")
async def my_ads_handler(callback: CallbackQuery, state: FSMContext):
    """Мои объявления"""
    # Проверяем бан
    if await is_user_banned(callback.from_user.id):
        await callback.answer("🚫 Вы заблокированы в этом боте.", show_alert=True)
        return
    
    user_id = callback.from_user.id
    ads = await get_user_ads(user_id)
    user_limit = await get_user_limit(user_id)
    
    if not ads:
        await callback.answer("📭 У вас пока нет объявлений", show_alert=True)
        return
    
    keyboard = await get_my_ads_keyboard(user_id)
    await callback.message.edit_text(
        f"📋 Ваши объявления ({len(ads)}/{user_limit}):",
        reply_markup=keyboard
    )
    await state.set_state(AdStates.my_ads)
    await callback.answer()

@dp.callback_query(F.data == "back_to_language")
async def back_to_language_handler(callback: CallbackQuery, state: FSMContext):
    """Возврат к выбору языка"""
    try:
        await callback.message.edit_text(
            "🌍 Выберите язык / Choose language:",
            reply_markup=get_language_keyboard()
        )
        await state.set_state(AdStates.choosing_language)
        await callback.answer()
    except Exception as e:
        logger.warning(f"Ошибка при возврате к языку: {e}")
        try:
            await callback.answer()
        except:
            pass

@dp.callback_query(F.data == "back_to_main")
async def back_to_main_handler(callback: CallbackQuery, state: FSMContext):
    """Возврат в главное меню"""
    try:
        await callback.message.edit_text(
            "🏠 Главное меню:",
            reply_markup=get_main_menu_keyboard()
        )
        await state.set_state(AdStates.main_menu)
        await callback.answer()
    except Exception as e:
        logger.warning(f"Ошибка при возврате в главное меню: {e}")
        try:
            await callback.answer()
        except:
            pass

@dp.callback_query(F.data == "back_to_topics")
async def back_to_topics_handler(callback: CallbackQuery, state: FSMContext):
    """Возврат к выбору тем"""
    await callback.message.edit_text(
        "📝 В какую тему хотите написать?",
        reply_markup=get_topics_keyboard()
    )
    await state.set_state(AdStates.choosing_topic)
    await callback.answer()

@dp.callback_query(F.data == "back_to_my_ads")
async def back_to_my_ads_handler(callback: CallbackQuery, state: FSMContext):
    """Возврат к моим объявлениям"""
    try:
        user_id = callback.from_user.id
        ads = await get_user_ads(user_id)
        user_limit = await get_user_limit(user_id)
        
        if not ads:
            await callback.message.edit_text(
                "🏠 Главное меню:",
                reply_markup=get_main_menu_keyboard()
            )
            await state.set_state(AdStates.main_menu)
        else:
            keyboard = await get_my_ads_keyboard(user_id)
            await callback.message.edit_text(
                f"📋 Ваши объявления ({len(ads)}/{user_limit}):",
                reply_markup=keyboard
            )
            await state.set_state(AdStates.my_ads)
        await callback.answer()
    except Exception as e:
        logger.warning(f"Ошибка при возврате к объявлениям: {e}")
        try:
            await callback.answer()
        except:
            pass

@dp.callback_query(StateFilter(AdStates.choosing_topic))
async def topic_handler(callback: CallbackQuery, state: FSMContext):
    """Выбор темы"""
    topic_key = callback.data
    
    if topic_key in TOPICS:
        # Сохраняем выбранную тему
        await state.update_data(selected_topic=topic_key)
        
        topic_name = TOPICS[topic_key]["name"]
        await callback.message.edit_text(
            f"✍️ Тема: {topic_name}\n\nНапишите объявление и отправьте:",
            reply_markup=get_topic_actions_keyboard()
        )
        await state.set_state(AdStates.writing_ad)
    
    await callback.answer()

@dp.message(StateFilter(AdStates.writing_ad))
async def ad_text_handler(message: Message, state: FSMContext):
    """Обработка текста объявления"""
    # Блокируем из группы объявлений
    if message.chat.id == TARGET_CHAT_ID:
        return
    
    # Проверяем бан
    if await is_user_banned(message.from_user.id):
        await message.answer("🚫 Вы заблокированы в этом боте.")
        await state.clear()
        return
    
    # Проверяем лимит перед публикацией
    user_id = message.from_user.id
    current_count = await get_user_ad_count(user_id)
    user_limit = await get_user_limit(user_id)
    
    if current_count >= user_limit:
        await message.answer(
            f"❌ Превышен лимит объявлений!\n\nУ вас: {current_count}/{user_limit} объявлений\n\nУдалите старые объявления через /start → Мои объявления",
            reply_markup=get_back_to_main_keyboard()
        )
        await state.clear()
        return
    
    user_data_state = await state.get_data()
    selected_topic = user_data_state.get("selected_topic")
    
    if not selected_topic or selected_topic not in TOPICS:
        await message.answer("❌ Ошибка: тема не выбрана. Начните заново с /start")
        await state.clear()
        return
    
    # Получаем данные темы
    topic_data = TOPICS[selected_topic]
    topic_id = topic_data["id"]
    topic_name = topic_data["name"]
    
    # Форматируем объявление
    ad_text = message.text or message.caption or ""
    if not ad_text:
        await message.answer("❌ Объявление не может быть пустым!")
        return
    
    # Разбиваем на строки
    lines = ad_text.split('\n')
    if lines:
        # Первую строку в цитату
        formatted_text = f"<blockquote>{lines[0]}</blockquote>"
        # Остальные строки как есть
        if len(lines) > 1:
            formatted_text += "\n" + "\n".join(lines[1:])
    else:
        formatted_text = f"<blockquote>{ad_text}</blockquote>"
    
    # Добавляем ссылку на автора в тире
    if message.from_user.username:
        contact_url = f"https://t.me/{message.from_user.username}"
    else:
        contact_url = f"tg://user?id={message.from_user.id}"
    
    formatted_text += f'\n\n<a href="{contact_url}">—</a>'
    
    try:
        # Публикуем в группу
        sent_message = await bot.send_message(
            chat_id=TARGET_CHAT_ID,
            text=formatted_text,
            message_thread_id=topic_id,
            parse_mode="HTML"
        )
        
        # Формируем ссылку на сообщение
        message_url = f"https://t.me/c/{str(TARGET_CHAT_ID)[4:]}/{sent_message.message_id}"
        
        # Сохраняем в БД
        await add_user_ad(
            message.from_user.id, 
            sent_message.message_id, 
            message_url, 
            topic_name
        )
        
        # Отправляем в группу модерации
        await send_to_moderation(
            message.from_user.id,
            message.from_user.username,
            ad_text,
            message_url,
            topic_name
        )
        
        # Получаем обновленное количество объявлений
        new_count = await get_user_ad_count(message.from_user.id)
        
        # Уведомляем автора
        await message.answer(
            f"✅ Ваше объявление опубликовано!\n📊 Объявлений: {new_count}/{user_limit}",
            reply_markup=get_post_actions_keyboard(message.from_user.id, message_url)
        )
        
        logger.info(f"Объявление опубликовано: пользователь {message.from_user.id}, тема {selected_topic}")
        
    except Exception as e:
        logger.error(f"Ошибка публикации: {e}")
        if "not enough rights" in str(e):
            error_msg = ("❌ Ошибка: бот не имеет прав для отправки сообщений в эту группу.\n\n"
                        "Обратитесь к администратору для предоставления боту прав администратора.")
        elif "chat not found" in str(e):
            error_msg = "❌ Ошибка: группа не найдена. Проверьте настройки."
        elif "thread not found" in str(e):
            error_msg = f"❌ Ошибка: тема '{topic_name}' не найдена в группе."
        else:
            error_msg = "❌ Ошибка при публикации объявления. Попробуйте позже."
        
        await message.answer(error_msg)
    
    await state.clear()

# Блокировка любых сообщений в группе объявлений
@dp.message()
async def block_target_chat_messages(message: Message):
    """Блокировка сообщений в группе объявлений"""
    if message.chat.id == TARGET_CHAT_ID:
        return
    
    # Если сообщение не из группы объявлений, пропускаем
    pass

@dp.callback_query(F.data == "create_new")
async def create_new_handler(callback: CallbackQuery, state: FSMContext):
    """Создание нового объявления"""
    # Проверяем бан
    if await is_user_banned(callback.from_user.id):
        await callback.answer("🚫 Вы заблокированы в этом боте.", show_alert=True)
        return
    
    await callback.message.edit_text(
        "📝 В какую тему хотите написать?",
        reply_markup=get_topics_keyboard()
    )
    await state.set_state(AdStates.choosing_topic)
    await callback.answer()

@dp.callback_query(F.data.startswith("view_ad_"))
async def view_ad_handler(callback: CallbackQuery, state: FSMContext):
    """Просмотр действий с объявлением"""
    message_id = int(callback.data.split("_")[-1])
    ad_data = await get_ad_by_message_id(message_id)
    
    if not ad_data:
        await callback.answer("❌ Объявление не найдено", show_alert=True)
        return
    
    user_id, message_id, message_url, topic_name = ad_data
    
    # Проверяем, что объявление принадлежит пользователю
    if user_id != callback.from_user.id:
        await callback.answer("❌ Это не ваше объявление", show_alert=True)
        return
    
    await callback.message.edit_text(
        f"📄 Объявление в теме: {topic_name}\n\nВыберите действие:",
        reply_markup=get_ad_actions_keyboard(message_id, message_url)
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("delete_ad_"))
async def delete_ad_handler(callback: CallbackQuery, state: FSMContext):
    """Системное уведомление об удалении объявления"""
    message_id = int(callback.data.split("_")[-1])
    
    # Создаем клавиатуру для системного уведомления
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="❌ Отмена", callback_data=f"cancel_delete_{message_id}"),
            InlineKeyboardButton(text="✅ ОК", callback_data=f"confirm_delete_{message_id}")
        ]
    ])
    
    await callback.message.edit_text(
        "⚠️ Вы точно хотите удалить это объявление?\n\nЭто действие нельзя отменить!",
        reply_markup=keyboard
    )
    await callback.answer("⚠️ Подтвердите удаление объявления", show_alert=True)

@dp.callback_query(F.data.startswith("cancel_delete_"))
async def cancel_delete_handler(callback: CallbackQuery, state: FSMContext):
    """Отмена удаления объявления"""
    # Проверяем бан
    if await is_user_banned(callback.from_user.id):
        await callback.answer("🚫 Вы заблокированы в этом боте.", show_alert=True)
        return
    
    message_id = int(callback.data.split("_")[-1])
    ad_data = await get_ad_by_message_id(message_id)
    
    if not ad_data:
        await callback.answer("❌ Объявление не найдено", show_alert=True)
        return
    
    user_id, message_id, message_url, topic_name = ad_data
    
    # Возвращаемся к просмотру объявления
    await callback.message.edit_text(
        f"📄 Объявление в теме: {topic_name}\n\nВыберите действие:",
        reply_markup=get_ad_actions_keyboard(message_id, message_url)
    )
    await callback.answer("Удаление отменено")

@dp.callback_query(F.data.startswith("confirm_delete_"))
async def confirm_delete_handler(callback: CallbackQuery, state: FSMContext):
    """Подтверждение удаления объявления"""
    try:
        # Проверяем бан
        if await is_user_banned(callback.from_user.id):
            await callback.answer("🚫 Вы заблокированы в этом боте.", show_alert=True)
            return
        
        message_id = int(callback.data.split("_")[-1])
        ad_data = await get_ad_by_message_id(message_id)
        
        if not ad_data:
            await callback.answer("❌ Объявление не найдено", show_alert=True)
            return
        
        user_id, message_id, message_url, topic_name = ad_data
        
        # Проверяем, что объявление принадлежит пользователю
        if user_id != callback.from_user.id:
            await callback.answer("❌ Это не ваше объявление", show_alert=True)
            return
        
        try:
            # Пытаемся удалить сообщение из чата
            await bot.delete_message(chat_id=TARGET_CHAT_ID, message_id=message_id)
        except Exception as e:
            logger.warning(f"Не удалось удалить сообщение {message_id} из чата: {e}")
        
        # Удаляем из БД
        await delete_user_ad(message_id)
        
        # Возвращаемся к списку объявлений
        ads = await get_user_ads(callback.from_user.id)
        user_limit = await get_user_limit(callback.from_user.id)
        
        if not ads:
            await callback.message.edit_text(
                "✅ Объявление удалено!\n\nУ вас больше нет объявлений.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ В главное меню", callback_data="back_to_main")]
                ])
            )
        else:
            keyboard = await get_my_ads_keyboard(callback.from_user.id)
            await callback.message.edit_text(
                f"✅ Объявление удалено!\n\n📋 Ваши объявления ({len(ads)}/{user_limit}):",
                reply_markup=keyboard
            )
        
        await callback.answer("✅ Объявление успешно удалено!", show_alert=True)
        
    except Exception as e:
        logger.error(f"Ошибка при удалении объявления: {e}")
        try:
            await callback.answer("❌ Ошибка при удалении", show_alert=True)
        except:
            pass

async def set_bot_commands():
    """Установка команд бота"""
    commands = [
        BotCommand(command="start", description="🚀 Начать работу с ботом")
    ]
    await bot.set_my_commands(commands)

async def start_web_server():
    """Запуск веб-сервера для Render"""
    app.router.add_get('/health', health_check)
    
    runner = web.AppRunner(app)
    await runner.setup()
    
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    
    logger.info(f"🌐 Веб-сервер запущен на порту {PORT}")

async def main():
    """Главная функция"""
    logger.info("🚀 Запуск бота объявлений...")
    
    try:
        # Инициализируем БД
        await init_db()
        
        # Устанавливаем команды
        await set_bot_commands()
        
        # Запускаем веб-сервер
        await start_web_server()
        
        # Запускаем задачу автопинга
        asyncio.create_task(start_ping_task())
        
        # Запускаем бота
        logger.info("✅ Все сервисы запущены")
        await dp.start_polling(bot)
        
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}")
        raise
    finally:
        # Закрываем пул соединений
        if db_pool:
            await db_pool.close()
            logger.info("📊 Соединение с БД закрыто")

if __name__ == "__main__":
    asyncio.run(main())
