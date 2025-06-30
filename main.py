"""
Упрощенный продакшн Telegram бот для объявлений
Архитектура: webhook + aiohttp + PostgreSQL + Memory cache
Оптимизирован для Render.com без Redis и мониторинга
"""

import asyncio
import logging
import os
import json
from datetime import datetime
from typing import Optional, List, Dict, Any, Tuple
from contextlib import asynccontextmanager
import asyncpg
from aiohttp import web, ClientSession
from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, 
    InlineKeyboardButton, BotCommand, Update
)
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from pydantic import BaseModel, Field
import re
from functools import wraps
from collections import defaultdict
import time

# ==================== КОНФИГУРАЦИЯ ====================

class Config:
    """Централизованная конфигурация"""
    BOT_TOKEN = os.getenv("BOT_TOKEN")
    DATABASE_URL = os.getenv("DATABASE_URL")
    
    # Telegram настройки
    TARGET_CHAT_ID = int(os.getenv("TARGET_CHAT_ID", "-1002827106973"))
    MODERATION_CHAT_ID = int(os.getenv("MODERATION_CHAT_ID", "0"))
    
    # Web настройки
    WEBHOOK_HOST = os.getenv("WEBHOOK_HOST", "https://six-01tj.onrender.com")
    WEBHOOK_PATH = "/webhook"
    WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"
    PORT = int(os.getenv("PORT", "10000"))
    
    # Бизнес настройки
    GROUP_LINK = os.getenv("GROUP_LINK", "https://t.me/sfetg4/215")
    EXAMPLE_URL = os.getenv("EXAMPLE_URL", "https://t.me/sfetg4/215")
    DEFAULT_AD_LIMIT = 4
    RATE_LIMIT_WINDOW = 60
    RATE_LIMIT_MAX_REQUESTS = 10
    
    # Database pool настройки
    DB_MIN_SIZE = 2
    DB_MAX_SIZE = 8
    DB_COMMAND_TIMEOUT = 30
    
    @classmethod
    def validate(cls):
        """Валидация обязательных настроек"""
        if not cls.BOT_TOKEN:
            raise ValueError("BOT_TOKEN не установлен!")
        if not cls.DATABASE_URL:
            raise ValueError("DATABASE_URL не установлен!")

# ==================== МОДЕЛИ ДАННЫХ ====================

class UserAd(BaseModel):
    """Модель объявления пользователя"""
    id: Optional[int] = None
    user_id: int
    message_id: int
    message_url: str
    topic_name: str
    created_at: Optional[datetime] = None

class TopicInfo(BaseModel):
    """Модель информации о теме"""
    name: str
    id: int

class ValidationResult(BaseModel):
    """Результат валидации текста"""
    is_valid: bool
    error_message: str = ""

# ==================== СОСТОЯНИЯ FSM ====================

class AdStates(StatesGroup):
    choosing_language = State()
    main_menu = State()
    choosing_topic = State() 
    writing_ad = State()
    my_ads = State()
    editing_ad = State()

# ==================== КОНФИГУРАЦИЯ ТЕМ ====================

TOPICS: Dict[str, TopicInfo] = {
    "topic_1": TopicInfo(name="💼 Работа", id=27),
    "topic_2": TopicInfo(name="🏠 Недвижимость", id=28),
    "topic_3": TopicInfo(name="🚗 Авто", id=29),
    "topic_4": TopicInfo(name="🛍️ Товары", id=30),
    "topic_5": TopicInfo(name="💡 Услуги", id=31),
    "topic_6": TopicInfo(name="📚 Обучение", id=32),
}

# ==================== ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ ====================

bot: Bot = None
dp: Dispatcher = None
db_pool: asyncpg.Pool = None
app: web.Application = None

# Rate limiting и кэширование в памяти
rate_limiter = defaultdict(list)
memory_cache = {}

# ==================== ЛОГИРОВАНИЕ ====================

def setup_logging():
    """Настройка логирования"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[logging.StreamHandler()]
    )
    # Отключаем verbose логи
    logging.getLogger('aiogram').setLevel(logging.WARNING)
    logging.getLogger('aiohttp').setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

# ==================== ДЕКОРАТОРЫ ====================

def rate_limit(max_requests: int = Config.RATE_LIMIT_MAX_REQUESTS, 
               window: int = Config.RATE_LIMIT_WINDOW):
    """Декоратор для rate limiting"""
    def decorator(func):
        @wraps(func)
        async def wrapper(event, *args, **kwargs):
            user_id = getattr(event.from_user, 'id', None)
            if not user_id:
                return await func(event, *args, **kwargs)
            
            now = time.time()
            requests = rate_limiter[user_id]
            
            # Очищаем старые запросы
            requests[:] = [req_time for req_time in requests if now - req_time < window]
            
            if len(requests) >= max_requests:
                if hasattr(event, 'answer'):
                    await event.answer("⏱ Слишком много запросов. Попробуйте позже.", show_alert=True)
                return
            
            requests.append(now)
            return await func(event, *args, **kwargs)
        return wrapper
    return decorator

def ban_check(func):
    """Декоратор для проверки бана"""
    @wraps(func)
    async def wrapper(event, *args, **kwargs):
        user_id = getattr(event.from_user, 'id', None)
        if user_id and await is_user_banned(user_id):
            if hasattr(event, 'answer'):
                await event.answer("🚫 Вы заблокированы в этом боте.", show_alert=True)
            return
        return await func(event, *args, **kwargs)
    return wrapper

# ==================== КЭШИРОВАНИЕ В ПАМЯТИ ====================

class CacheService:
    """Сервис кэширования в памяти"""
    
    @staticmethod
    async def get(key: str) -> Optional[Any]:
        """Получить значение из кэша"""
        cache_data = memory_cache.get(key)
        if cache_data:
            value, expire_time = cache_data
            if time.time() < expire_time:
                return value
            else:
                # Удаляем просроченный кэш
                memory_cache.pop(key, None)
        return None
    
    @staticmethod
    async def set(key: str, value: Any, ttl: int = 300) -> bool:
        """Установить значение в кэш"""
        expire_time = time.time() + ttl
        memory_cache[key] = (value, expire_time)
        return True
    
    @staticmethod
    async def delete(key: str) -> bool:
        """Удалить значение из кэша"""
        memory_cache.pop(key, None)
        return True

# ==================== БАЗА ДАННЫХ ====================

@asynccontextmanager
async def get_db_connection():
    """Context manager для получения соединения с БД"""
    async with db_pool.acquire() as connection:
        try:
            yield connection
        except Exception as e:
            logger.error(f"Database error: {e}")
            raise

class DatabaseService:
    """Сервис работы с базой данных"""
    
    @staticmethod
    async def init_database():
        """Инициализация базы данных"""
        global db_pool
        
        Config.validate()
        
        try:
            db_pool = await asyncpg.create_pool(
                Config.DATABASE_URL,
                min_size=Config.DB_MIN_SIZE,
                max_size=Config.DB_MAX_SIZE,
                command_timeout=Config.DB_COMMAND_TIMEOUT
            )
            
            async with get_db_connection() as conn:
                # Создание основных таблиц
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS user_ads (
                        id SERIAL PRIMARY KEY,
                        user_id BIGINT NOT NULL,
                        message_id BIGINT NOT NULL UNIQUE,
                        message_url TEXT NOT NULL,
                        topic_name TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS banned_users (
                        user_id BIGINT PRIMARY KEY,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS user_limits (
                        user_id BIGINT PRIMARY KEY,
                        ad_limit INTEGER NOT NULL DEFAULT 4,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                
                # Создание индексов
                indexes = [
                    "CREATE INDEX IF NOT EXISTS idx_user_ads_user_id ON user_ads(user_id)",
                    "CREATE INDEX IF NOT EXISTS idx_user_ads_message_id ON user_ads(message_id)",
                    "CREATE INDEX IF NOT EXISTS idx_user_ads_created_at ON user_ads(created_at DESC)",
                ]
                
                for index_sql in indexes:
                    try:
                        await conn.execute(index_sql)
                    except Exception as e:
                        if "already exists" not in str(e):
                            logger.warning(f"Index creation warning: {e}")
            
            logger.info("✅ База данных успешно инициализирована")
            
        except Exception as e:
            logger.error(f"❌ Ошибка инициализации БД: {e}")
            raise
    
    @staticmethod
    async def add_user_ad(user_ad: UserAd) -> bool:
        """Добавить объявление пользователя"""
        try:
            async with get_db_connection() as conn:
                await conn.execute(
                    """INSERT INTO user_ads (user_id, message_id, message_url, topic_name) 
                       VALUES ($1, $2, $3, $4)""",
                    user_ad.user_id, user_ad.message_id, user_ad.message_url, user_ad.topic_name
                )
                
                # Инвалидируем кэш
                await CacheService.delete(f"user_ads:{user_ad.user_id}")
                await CacheService.delete(f"user_ad_count:{user_ad.user_id}")
                return True
        except Exception as e:
            logger.error(f"Ошибка добавления объявления: {e}")
            return False
    
    @staticmethod
    async def get_user_ads(user_id: int) -> List[Tuple[int, str, str]]:
        """Получить объявления пользователя с кэшированием"""
        cache_key = f"user_ads:{user_id}"
        cached = await CacheService.get(cache_key)
        if cached:
            return cached
        
        try:
            async with get_db_connection() as conn:
                rows = await conn.fetch(
                    """SELECT message_id, message_url, topic_name 
                       FROM user_ads WHERE user_id = $1 
                       ORDER BY created_at DESC""",
                    user_id
                )
                result = [(row['message_id'], row['message_url'], row['topic_name']) for row in rows]
                await CacheService.set(cache_key, result, 300)
                return result
        except Exception as e:
            logger.error(f"Ошибка получения объявлений: {e}")
            return []
    
    @staticmethod
    async def get_user_ads_with_counts(user_id: int) -> List[Tuple[int, str, str, str]]:
        """Получить объявления с нумерацией по темам"""
        ads = await DatabaseService.get_user_ads(user_id)
        
        topic_counts = defaultdict(int)
        result = []
        
        for message_id, message_url, topic_name in ads:
            topic_counts[topic_name] += 1
            topic_display = f"{topic_name} {topic_counts[topic_name]}"
            result.append((message_id, message_url, topic_display, topic_name))
        
        return result
    
    @staticmethod
    async def get_ad_by_message_id(message_id: int) -> Optional[Tuple[int, int, str, str]]:
        """Получить объявление по message_id с кэшированием"""
        cache_key = f"ad:{message_id}"
        cached = await CacheService.get(cache_key)
        if cached:
            return tuple(cached)
        
        try:
            async with get_db_connection() as conn:
                row = await conn.fetchrow(
                    """SELECT user_id, message_id, message_url, topic_name 
                       FROM user_ads WHERE message_id = $1""",
                    message_id
                )
                if row:
                    result = (row['user_id'], row['message_id'], row['message_url'], row['topic_name'])
                    await CacheService.set(cache_key, result, 600)
                    return result
                return None
        except Exception as e:
            logger.error(f"Ошибка получения объявления: {e}")
            return None
    
    @staticmethod
    async def delete_user_ad(message_id: int) -> bool:
        """Удалить объявление"""
        try:
            async with get_db_connection() as conn:
                # Получаем user_id для инвалидации кэша
                row = await conn.fetchrow(
                    "SELECT user_id FROM user_ads WHERE message_id = $1", message_id
                )
                
                if row:
                    user_id = row['user_id']
                    await conn.execute(
                        "DELETE FROM user_ads WHERE message_id = $1", message_id
                    )
                    
                    # Инвалидируем кэш
                    await CacheService.delete(f"user_ads:{user_id}")
                    await CacheService.delete(f"user_ad_count:{user_id}")
                    await CacheService.delete(f"ad:{message_id}")
                    return True
                return False
        except Exception as e:
            logger.error(f"Ошибка удаления объявления: {e}")
            return False
    
    @staticmethod
    async def get_user_ad_count(user_id: int) -> int:
        """Получить количество объявлений с кэшированием"""
        cache_key = f"user_ad_count:{user_id}"
        cached = await CacheService.get(cache_key)
        if cached is not None:
            return cached
        
        try:
            async with get_db_connection() as conn:
                result = await conn.fetchval(
                    "SELECT COUNT(*) FROM user_ads WHERE user_id = $1", user_id
                )
                count = result if result else 0
                await CacheService.set(cache_key, count, 60)
                return count
        except Exception as e:
            logger.error(f"Ошибка подсчета объявлений: {e}")
            return 0
    
    @staticmethod
    async def ban_user(user_id: int) -> bool:
        """Забанить пользователя"""
        try:
            async with get_db_connection() as conn:
                await conn.execute(
                    """INSERT INTO banned_users (user_id) 
                       VALUES ($1) ON CONFLICT (user_id) DO NOTHING""",
                    user_id
                )
                await CacheService.delete(f"banned:{user_id}")
                return True
        except Exception as e:
            logger.error(f"Ошибка бана пользователя: {e}")
            return False
    
    @staticmethod
    async def unban_user(user_id: int) -> bool:
        """Разбанить пользователя"""
        try:
            async with get_db_connection() as conn:
                await conn.execute(
                    "DELETE FROM banned_users WHERE user_id = $1", user_id
                )
                await CacheService.delete(f"banned:{user_id}")
                return True
        except Exception as e:
            logger.error(f"Ошибка разбана пользователя: {e}")
            return False

# ==================== ВАЛИДАЦИЯ ====================

class ValidationService:
    """Сервис валидации данных"""
    
    @staticmethod
    def validate_message_text(text: str) -> ValidationResult:
        """Валидация текста сообщения"""
        if not text or not text.strip():
            return ValidationResult(is_valid=False, error_message="❌ Сообщение не может быть пустым!")
        
        if len(text) > 4000:
            return ValidationResult(is_valid=False, error_message="❌ Сообщение слишком длинное (максимум 4000 символов)!")
        
        # Проверяем на @username
        if '@' in text:
            return ValidationResult(is_valid=False, error_message="❌ @username не принимаются, мы сами вставим ссылку на вас.")
        
        # Проверяем на URL
        if re.search(r'https?://', text, re.IGNORECASE):
            return ValidationResult(is_valid=False, error_message="❌ Ссылки не принимаются, мы сами вставим ссылку на вас.")
        
        # Проверяем на хэштеги
        if '#' in text:
            return ValidationResult(is_valid=False, error_message="❌ Хэштеги не принимаются, мы сами вставим ссылку на вас.")
        
        # Проверяем на домены
        domain_pattern = r'\b[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.([a-zA-Z]{2,})\b'
        if re.search(domain_pattern, text):
            return ValidationResult(is_valid=False, error_message="❌ Сайты не принимаются, мы сами вставим ссылку на вас.")
        
        return ValidationResult(is_valid=True)

# ==================== УТИЛИТЫ ====================

async def is_user_banned(user_id: int) -> bool:
    """Проверить бан пользователя с кэшированием"""
    cache_key = f"banned:{user_id}"
    cached = await CacheService.get(cache_key)
    if cached is not None:
        return cached
    
    try:
        async with get_db_connection() as conn:
            result = await conn.fetchval(
                "SELECT 1 FROM banned_users WHERE user_id = $1", user_id
            )
            is_banned = result is not None
            await CacheService.set(cache_key, is_banned, 300)
            return is_banned
    except Exception as e:
        logger.error(f"Ошибка проверки бана: {e}")
        return False

async def get_user_limit(user_id: int) -> int:
    """Получить лимит пользователя с кэшированием"""
    cache_key = f"user_limit:{user_id}"
    cached = await CacheService.get(cache_key)
    if cached is not None:
        return cached
    
    try:
        async with get_db_connection() as conn:
            result = await conn.fetchval(
                "SELECT ad_limit FROM user_limits WHERE user_id = $1", user_id
            )
            limit = result if result else Config.DEFAULT_AD_LIMIT
            await CacheService.set(cache_key, limit, 600)
            return limit
    except Exception as e:
        logger.error(f"Ошибка получения лимита: {e}")
        return Config.DEFAULT_AD_LIMIT

async def set_user_limit(user_id: int, limit: int):
    """Установить лимит объявлений для пользователя"""
    try:
        async with get_db_connection() as conn:
            await conn.execute(
                """INSERT INTO user_limits (user_id, ad_limit) 
                   VALUES ($1, $2) 
                   ON CONFLICT (user_id) 
                   DO UPDATE SET ad_limit = $2, updated_at = CURRENT_TIMESTAMP""",
                user_id, limit
            )
            # Инвалидируем кэш
            await CacheService.delete(f"user_limit:{user_id}")
    except Exception as e:
        logger.error(f"Ошибка установки лимита: {e}")

async def notify_user(user_id: int, message: str) -> bool:
    """Уведомить пользователя"""
    try:
        await bot.send_message(chat_id=user_id, text=message)
        return True
    except Exception as e:
        logger.error(f"Ошибка уведомления пользователя {user_id}: {e}")
        return False

# ==================== КЛАВИАТУРЫ ====================

class KeyboardService:
    """Сервис создания клавиатур"""
    
    @staticmethod
    def get_language_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="🇷🇺 Русский", callback_data="lang_ru"),
                InlineKeyboardButton(text="🇬🇧 English", callback_data="lang_en")
            ]
        ])
    
    @staticmethod
    def get_main_menu_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="➕ Создать", callback_data="create_ad"),
                InlineKeyboardButton(text="📋 Мои объявления", callback_data="my_ads")
            ],
            [InlineKeyboardButton(text="🔗 Перейти в группу", url=Config.GROUP_LINK)],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_language")]
        ])
    
    @staticmethod
    def get_topics_keyboard() -> InlineKeyboardMarkup:
        buttons = []
        topic_items = list(TOPICS.items())
        
        for i in range(0, len(topic_items), 2):
            row = []
            topic_key, topic_data = topic_items[i]
            row.append(InlineKeyboardButton(
                text=topic_data.name, 
                callback_data=topic_key
            ))
            
            if i + 1 < len(topic_items):
                topic_key2, topic_data2 = topic_items[i + 1]
                row.append(InlineKeyboardButton(
                    text=topic_data2.name, 
                    callback_data=topic_key2
                ))
            
            buttons.append(row)
        
        buttons.append([
            InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main")
        ])
        
        return InlineKeyboardMarkup(inline_keyboard=buttons)

async def get_my_ads_keyboard(user_id: int):
    """Клавиатура с объявлениями пользователя"""
    ads = await DatabaseService.get_user_ads_with_counts(user_id)
    buttons = []
    
    for message_id, message_url, topic_display, _ in ads:
        buttons.append([
            InlineKeyboardButton(
                text=f"📄 {topic_display}", 
                callback_data=f"view_ad_{message_id}"
            )
        ])
    
    # Кнопка назад
    buttons.append([
        InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# ==================== ОБРАБОТЧИКИ ====================

@dp.message(Command("start"))
@rate_limit()
async def start_handler(message: Message, state: FSMContext):
    """Стартовый обработчик"""
    if message.chat.id == Config.TARGET_CHAT_ID:
        return
    
    if await is_user_banned(message.from_user.id):
        await message.answer("🚫 Вы заблокированы в этом боте.")
        return
    
    await message.answer(
        "🌍 Выберите язык / Choose language:",
        reply_markup=KeyboardService.get_language_keyboard()
    )
    await state.set_state(AdStates.choosing_language)

@dp.callback_query(F.data == "lang_ru", StateFilter(AdStates.choosing_language))
@rate_limit()
@ban_check
async def language_ru_handler(callback: CallbackQuery, state: FSMContext):
    """Выбор русского языка"""
    await callback.message.edit_text(
        "🏠 Главное меню:",
        reply_markup=KeyboardService.get_main_menu_keyboard()
    )
    await state.set_state(AdStates.main_menu)
    await callback.answer()

@dp.callback_query(F.data == "lang_en", StateFilter(AdStates.choosing_language))
@rate_limit()
async def language_en_handler(callback: CallbackQuery, state: FSMContext):
    """Выбор английского языка (заглушка)"""
    await callback.answer("🚧 English version coming soon!", show_alert=True)

@dp.callback_query(F.data == "create_ad")
@rate_limit()
@ban_check
async def create_ad_handler(callback: CallbackQuery, state: FSMContext):
    """Создание объявления"""
    await callback.message.edit_text(
        "📝 В какую тему хотите написать?",
        reply_markup=KeyboardService.get_topics_keyboard()
    )
    await state.set_state(AdStates.choosing_topic)
    await callback.answer()

@dp.callback_query(F.data == "my_ads")
@rate_limit()
@ban_check
async def my_ads_handler(callback: CallbackQuery, state: FSMContext):
    """Мои объявления"""
    user_id = callback.from_user.id
    ads = await DatabaseService.get_user_ads(user_id)
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

@dp.callback_query(StateFilter(AdStates.choosing_topic))
@rate_limit()
@ban_check
async def topic_handler(callback: CallbackQuery, state: FSMContext):
    """Выбор темы"""
    topic_key = callback.data
    
    if topic_key in TOPICS:
        await state.update_data(selected_topic=topic_key)
        
        topic_name = TOPICS[topic_key].name
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📖 Пример заполнения", url=Config.EXAMPLE_URL)],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_topics")]
        ])
        
        await callback.message.edit_text(
            f"✍️ Тема: {topic_name}\n\nНапишите объявление и отправьте:",
            reply_markup=keyboard
        )
        await state.set_state(AdStates.writing_ad)
    
    await callback.answer()

@dp.message(StateFilter(AdStates.writing_ad))
@rate_limit()
@ban_check
async def ad_text_handler(message: Message, state: FSMContext):
    """Обработка текста объявления"""
    if message.chat.id == Config.TARGET_CHAT_ID:
        return
    
    # Проверяем лимит
    user_id = message.from_user.id
    current_count = await DatabaseService.get_user_ad_count(user_id)
    user_limit = await get_user_limit(user_id)
    
    if current_count >= user_limit:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🏠 На главную", callback_data="back_to_main")]
        ])
        await message.answer(
            f"❌ Превышен лимит объявлений!\n\nУ вас: {current_count}/{user_limit} объявлений\n\nУдалите старые объявления через 'Мои объявления'",
            reply_markup=keyboard
        )
        await state.clear()
        return
    
    user_data = await state.get_data()
    selected_topic = user_data.get("selected_topic")
    
    if not selected_topic or selected_topic not in TOPICS:
        await message.answer("❌ Ошибка: тема не выбрана. Начните заново с /start")
        await state.clear()
        return
    
    topic_data = TOPICS[selected_topic]
    ad_text = message.text or message.caption or ""
    
    # Валидация
    validation = ValidationService.validate_message_text(ad_text)
    if not validation.is_valid:
        await message.answer(validation.error_message)
        return
    
    # Форматирование
    lines = ad_text.split('\n')
    formatted_text = f"<blockquote>{lines[0]}</blockquote>"
    if len(lines) > 1:
        formatted_text += "\n" + "\n".join(lines[1:])
    
    # Добавляем ссылку на автора
    contact_url = (f"https://t.me/{message.from_user.username}" 
                  if message.from_user.username 
                  else f"tg://user?id={message.from_user.id}")
    formatted_text += f'\n\n<a href="{contact_url}">—</a>'
    
    try:
        # Публикуем
        sent_message = await bot.send_message(
            chat_id=Config.TARGET_CHAT_ID,
            text=formatted_text,
            message_thread_id=topic_data.id,
            parse_mode="HTML"
        )
        
        # Сохраняем
        message_url = f"https://t.me/c/{str(Config.TARGET_CHAT_ID)[4:]}/{sent_message.message_id}"
        
        user_ad = UserAd(
            user_id=user_id,
            message_id=sent_message.message_id,
            message_url=message_url,
            topic_name=topic_data.name
        )
        
        await DatabaseService.add_user_ad(user_ad)
        
        new_count = await DatabaseService.get_user_ad_count(user_id)
        
        # Клавиатура после публикации
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="➕ Создать еще", callback_data="create_ad"),
                InlineKeyboardButton(text="👁 Посмотреть", url=message_url)
            ],
            [InlineKeyboardButton(text="📋 Мои объявления", callback_data="my_ads")],
            [InlineKeyboardButton(text="🏠 На главную", callback_data="back_to_main")]
        ])
        
        await message.answer(
            f"✅ Объявление опубликовано!\n📊 Объявлений: {new_count}/{user_limit}",
            reply_markup=keyboard
        )
        
        logger.info(f"Объявление опубликовано: пользователь {user_id}, тема {selected_topic}")
        
    except Exception as e:
        logger.error(f"Ошибка публикации: {e}")
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🏠 На главную", callback_data="back_to_main")]
        ])
        
        if "not enough rights" in str(e):
            error_msg = "❌ Ошибка: бот не имеет прав для отправки сообщений в группу"
        elif "chat not found" in str(e):
            error_msg = "❌ Ошибка: группа не найдена"
        elif "thread not found" in str(e):
            error_msg = f"❌ Ошибка: тема '{topic_data.name}' не найдена в группе"
        else:
            error_msg = "❌ Ошибка при публикации. Попробуйте позже."
        
        await message.answer(error_msg, reply_markup=keyboard)
    
    await state.clear()

# ==================== НАВИГАЦИЯ ====================

@dp.callback_query(F.data == "back_to_language")
async def back_to_language_handler(callback: CallbackQuery, state: FSMContext):
    """Возврат к выбору языка"""
    try:
        await callback.message.edit_text(
            "🌍 Выберите язык / Choose language:",
            reply_markup=KeyboardService.get_language_keyboard()
        )
        await state.set_state(AdStates.choosing_language)
        await callback.answer()
    except Exception as e:
        logger.warning(f"Ошибка при возврате к языку: {e}")
        await callback.answer()

@dp.callback_query(F.data == "back_to_main")
async def back_to_main_handler(callback: CallbackQuery, state: FSMContext):
    """Возврат в главное меню"""
    try:
        await callback.message.edit_text(
            "🏠 Главное меню:",
            reply_markup=KeyboardService.get_main_menu_keyboard()
        )
        await state.set_state(AdStates.main_menu)
        await callback.answer()
    except Exception as e:
        logger.warning(f"Ошибка при возврате в главное меню: {e}")
        await callback.answer()

@dp.callback_query(F.data == "back_to_topics")
async def back_to_topics_handler(callback: CallbackQuery, state: FSMContext):
    """Возврат к выбору тем"""
    await callback.message.edit_text(
        "📝 В какую тему хотите написать?",
        reply_markup=KeyboardService.get_topics_keyboard()
    )
    await state.set_state(AdStates.choosing_topic)
    await callback.answer()

# ==================== УПРАВЛЕНИЕ ОБЪЯВЛЕНИЯМИ ====================

@dp.callback_query(F.data.startswith("view_ad_"))
async def view_ad_handler(callback: CallbackQuery, state: FSMContext):
    """Просмотр действий с объявлением"""
    message_id = int(callback.data.split("_")[-1])
    ad_data = await DatabaseService.get_ad_by_message_id(message_id)
    
    if not ad_data:
        await callback.answer("❌ Объявление не найдено", show_alert=True)
        return
    
    user_id, message_id, message_url, topic_name = ad_data
    
    # Проверяем принадлежность
    if user_id != callback.from_user.id:
        await callback.answer("❌ Это не ваше объявление", show_alert=True)
        return
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🗑 Удалить", callback_data=f"delete_ad_{message_id}"),
        ],
        [InlineKeyboardButton(text="👁 Посмотреть", url=message_url)],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_my_ads")]
    ])
    
    await callback.message.edit_text(
        f"📄 Объявление в теме: {topic_name}\n\nВыберите действие:",
        reply_markup=keyboard
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("delete_ad_"))
async def delete_ad_handler(callback: CallbackQuery, state: FSMContext):
    """Подтверждение удаления объявления"""
    message_id = int(callback.data.split("_")[-1])
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="❌ Отмена", callback_data=f"cancel_delete_{message_id}"),
            InlineKeyboardButton(text="✅ Удалить", callback_data=f"confirm_delete_{message_id}")
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
    message_id = int(callback.data.split("_")[-1])
    ad_data = await DatabaseService.get_ad_by_message_id(message_id)
    
    if not ad_data:
        await callback.answer("❌ Объявление не найдено", show_alert=True)
        return
    
    user_id, message_id, message_url, topic_name = ad_data
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🗑 Удалить", callback_data=f"delete_ad_{message_id}"),
        ],
        [InlineKeyboardButton(text="👁 Посмотреть", url=message_url)],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_my_ads")]
    ])
    
    await callback.message.edit_text(
        f"📄 Объявление в теме: {topic_name}\n\nВыберите действие:",
        reply_markup=keyboard
    )
    await callback.answer("Удаление отменено")

@dp.callback_query(F.data.startswith("confirm_delete_"))
async def confirm_delete_handler(callback: CallbackQuery, state: FSMContext):
    """Подтверждение удаления объявления"""
    try:
        message_id = int(callback.data.split("_")[-1])
        ad_data = await DatabaseService.get_ad_by_message_id(message_id)
        
        if not ad_data:
            await callback.answer("❌ Объявление не найдено", show_alert=True)
            return
        
        user_id, message_id, message_url, topic_name = ad_data
        
        # Проверяем принадлежность
        if user_id != callback.from_user.id:
            await callback.answer("❌ Это не ваше объявление", show_alert=True)
            return
        
        try:
            # Пытаемся удалить сообщение из чата
            await bot.delete_message(chat_id=Config.TARGET_CHAT_ID, message_id=message_id)
        except Exception as e:
            logger.warning(f"Не удалось удалить сообщение {message_id} из чата: {e}")
        
        # Удаляем из БД
        await DatabaseService.delete_user_ad(message_id)
        
        # Возвращаемся к списку объявлений
        ads = await DatabaseService.get_user_ads_with_counts(callback.from_user.id)
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
        await callback.answer("❌ Ошибка при удалении", show_alert=True)

@dp.callback_query(F.data == "back_to_my_ads")
async def back_to_my_ads_handler(callback: CallbackQuery, state: FSMContext):
    """Возврат к моим объявлениям"""
    try:
        user_id = callback.from_user.id
        ads = await DatabaseService.get_user_ads(user_id)
        user_limit = await get_user_limit(user_id)
        
        if not ads:
            await callback.message.edit_text(
                "🏠 Главное меню:",
                reply_markup=KeyboardService.get_main_menu_keyboard()
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
        await callback.answer()

# ==================== КОМАНДЫ МОДЕРАЦИИ ====================

@dp.message(Command("ban"))
async def ban_command(message: Message):
    """Команда бана пользователя"""
    if message.chat.id == Config.TARGET_CHAT_ID:
        return
    
    if not Config.MODERATION_CHAT_ID or message.chat.id != Config.MODERATION_CHAT_ID:
        return
    
    try:
        args = message.text.split()
        if len(args) != 2:
            await message.answer("❌ Использование: /ban <user_id>")
            return
        
        user_id = int(args[1])
        await DatabaseService.ban_user(user_id)
        await notify_user(user_id, "🚫 Вы были заблокированы администрацией.")
        await message.answer(f"✅ Пользователь {user_id} забанен")
        
    except ValueError:
        await message.answer("❌ Неверный ID пользователя")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")

@dp.message(Command("banoff"))
async def banoff_command(message: Message):
    """Команда разбана пользователя"""
    if message.chat.id == Config.TARGET_CHAT_ID:
        return
    
    if not Config.MODERATION_CHAT_ID or message.chat.id != Config.MODERATION_CHAT_ID:
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
        await DatabaseService.unban_user(user_id)
        await notify_user(user_id, "✅ Ваша блокировка снята. Теперь вы можете снова размещать объявления.")
        
        await message.answer(f"✅ Пользователь {user_id} разбанен")
        
    except ValueError:
        await message.answer("❌ Неверный ID пользователя")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")

@dp.message(Command("setlimit"))
async def setlimit_command(message: Message):
    """Команда установки лимита объявлений"""
    if message.chat.id == Config.TARGET_CHAT_ID:
        return
    
    if not Config.MODERATION_CHAT_ID or message.chat.id != Config.MODERATION_CHAT_ID:
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
        
        old_limit = await get_user_limit(user_id)
        await set_user_limit(user_id, limit)
        
        # Уведомляем пользователя
        if limit > old_limit:
            await notify_user(user_id, f"📈 Ваш лимит объявлений увеличен с {old_limit} до {limit}.")
        elif limit < old_limit:
            await notify_user(user_id, f"📉 Ваш лимит объявлений уменьшен с {old_limit} до {limit}.")
        
        await message.answer(f"✅ Лимит для пользователя {user_id} установлен: {limit} объявлений")
        
    except ValueError:
        await message.answer("❌ Неверные параметры. Используйте числа.")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")

@dp.message(Command("getlimit"))
async def getlimit_command(message: Message):
    """Команда получения лимита объявлений"""
    if message.chat.id == Config.TARGET_CHAT_ID:
        return
    
    if not Config.MODERATION_CHAT_ID or message.chat.id != Config.MODERATION_CHAT_ID:
        return
    
    try:
        args = message.text.split()
        if len(args) != 2:
            await message.answer("❌ Использование: /getlimit <user_id>")
            return
        
        user_id = int(args[1])
        current_count = await DatabaseService.get_user_ad_count(user_id)
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

# ==================== БЛОКИРОВКА СООБЩЕНИЙ ИЗ ГРУППЫ ====================

@dp.message()
async def block_target_chat_messages(message: Message):
    """Блокировка всех сообщений в группе объявлений"""
    if message.chat.id == Config.TARGET_CHAT_ID:
        return  # Игнорируем сообщения в группе объявлений

# ==================== АВТОПИНГ ====================

async def ping_self():
    """Автопинг для предотвращения засыпания"""
    try:
        async with ClientSession() as session:
            async with session.get(f"{Config.WEBHOOK_HOST}/health", timeout=10) as response:
                if response.status == 200:
                    logger.info("✅ Self ping successful")
                else:
                    logger.warning(f"⚠️ Self ping failed: {response.status}")
    except Exception as e:
        logger.warning(f"⚠️ Self ping error: {e}")

async def start_ping_task():
    """Запуск задачи автопинга каждые 25 минут"""
    while True:
        await asyncio.sleep(1500)  # 25 минут
        await ping_self()

# ==================== ИНИЦИАЛИЗАЦИЯ ====================

async def init_bot():
    """Инициализация бота"""
    global bot, dp
    
    # Memory storage для FSM
    storage = MemoryStorage()
    
    bot = Bot(token=Config.BOT_TOKEN)
    dp = Dispatcher(storage=storage)
    
    # Установка команд
    commands = [
        BotCommand(command="start", description="🚀 Начать работу с ботом")
    ]
    await bot.set_my_commands(commands)
    
    # Установка webhook
    await bot.set_webhook(Config.WEBHOOK_URL)
    logger.info(f"✅ Webhook установлен: {Config.WEBHOOK_URL}")

async def init_web_app():
    """Инициализация веб-приложения"""
    global app
    
    app = web.Application()
    
    # Health check
    async def health(request):
        return web.json_response({
            "status": "ok",
            "timestamp": datetime.now().isoformat(),
            "bot_id": (await bot.get_me()).id if bot else None
        })
    
    app.router.add_get('/health', health)
    
    # Настройка webhook
    webhook_requests_handler = SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
    )
    webhook_requests_handler.register(app, path=Config.WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)
    
    logger.info("✅ Веб-приложение инициализировано")

async def cleanup():
    """Очистка ресурсов"""
    try:
        if bot:
            await bot.delete_webhook()
            await bot.session.close()
        
        if db_pool:
            await db_pool.close()
        
        logger.info("✅ Ресурсы очищены")
    except Exception as e:
        logger.error(f"❌ Ошибка очистки: {e}")

# ==================== ГЛАВНАЯ ФУНКЦИЯ ====================

async def main():
    """Главная функция приложения"""
    setup_logging()
    logger.info("🚀 Запуск упрощенного продакшн бота...")
    
    try:
        # Инициализация компонентов
        await DatabaseService.init_database()
        await init_bot()
        await init_web_app()
        
        # Запуск сервера
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', Config.PORT)
        await site.start()
        
        logger.info(f"🌐 Сервер запущен на порту {Config.PORT}")
        
        # Запуск автопинга
        asyncio.create_task(start_ping_task())
        logger.info("🔄 Автопинг запущен (каждые 25 минут)")
        
        logger.info("✅ Все сервисы успешно запущены")
        
        # Ожидание завершения
        try:
            await asyncio.Event().wait()
        except KeyboardInterrupt:
            logger.info("🛑 Получен сигнал завершения")
    
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}")
        raise
    finally:
        await cleanup()

if __name__ == "__main__":
    asyncio.run(main())
