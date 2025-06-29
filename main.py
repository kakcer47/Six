import asyncio
import logging
import os
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
TARGET_CHAT_ID = int(os.getenv("TARGET_CHAT_ID", "-1002827106973"))  # ID супергруппы
EXAMPLE_URL = "https://example.com"  # Замените на свою ссылку

# Логирование
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Состояния
class AdStates(StatesGroup):
    choosing_language = State()
    choosing_topic = State() 
    writing_ad = State()

# Темы группы - НАСТРОЙТЕ ПОД СВОЮ ГРУППУ
TOPICS = {
    "topic_1": {"name": "💼 Работа", "id": 27},  # ID темы в группе
    "topic_2": {"name": "🏠 Недвижимость", "id": 29},
    "topic_3": {"name": "🚗 Авто", "id": 30},
    "topic_4": {"name": "🛍️ Товары", "id": 31},
    "topic_5": {"name": "💡 Услуги", "id": 32},
    "topic_6": {"name": "📚 Обучение", "id": 38},
}

# Хранилище пользователей (в продакшене используйте БД)
user_data = {}
started_users = set()

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

def get_language_keyboard():
    """Клавиатура выбора языка"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🇷🇺 Русский", callback_data="lang_ru"),
            InlineKeyboardButton(text="🇬🇧 English", callback_data="lang_en")
        ]
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
            callback_data=f"topic_{topic_key}"
        ))
        
        # Вторая кнопка в ряду (если есть)
        if i + 1 < len(topic_items):
            topic_key2, topic_data2 = topic_items[i + 1]
            row.append(InlineKeyboardButton(
                text=topic_data2["name"], 
                callback_data=f"topic_{topic_key2}"
            ))
        
        buttons.append(row)
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    return keyboard

def get_example_keyboard():
    """Клавиатура с примером заполнения"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📖 Пример заполнения", url=EXAMPLE_URL)]
    ])
    return keyboard

def get_post_actions_keyboard(user_id: int, message_url: str):
    """Клавиатура после публикации"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="➕ Создать", callback_data="create_new"),
            InlineKeyboardButton(text="👁 Посмотреть", url=message_url)
        ]
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

@dp.message(Command("start"))
async def start_handler(message: Message, state: FSMContext):
    """Обработка команды /start"""
    user_id = message.from_user.id
    
    # Если пользователь уже стартовал - игнорируем
    if user_id in started_users:
        return
    
    # Добавляем в список стартовавших
    started_users.add(user_id)
    
    await message.answer(
        "🌍 Выберите язык / Choose language:",
        reply_markup=get_language_keyboard()
    )
    await state.set_state(AdStates.choosing_language)

@dp.callback_query(F.data == "lang_ru", StateFilter(AdStates.choosing_language))
async def language_ru_handler(callback: CallbackQuery, state: FSMContext):
    """Выбор русского языка"""
    await callback.message.edit_text(
        "📝 В какую тему хотите написать?",
        reply_markup=get_topics_keyboard()
    )
    await state.set_state(AdStates.choosing_topic)
    await callback.answer()

@dp.callback_query(F.data == "lang_en", StateFilter(AdStates.choosing_language))
async def language_en_handler(callback: CallbackQuery, state: FSMContext):
    """Выбор английского языка (заглушка)"""
    await callback.answer("🚧 English version coming soon!", show_alert=True)

@dp.callback_query(F.data.startswith("topic_"), StateFilter(AdStates.choosing_topic))
async def topic_handler(callback: CallbackQuery, state: FSMContext):
    """Выбор темы"""
    topic_key = callback.data.replace("topic_", "")
    
    if topic_key in TOPICS:
        # Сохраняем выбранную тему
        await state.update_data(selected_topic=topic_key)
        
        await callback.message.edit_text(
            "✍️ Напишите объявление и отправьте:",
            reply_markup=get_example_keyboard()
        )
        await state.set_state(AdStates.writing_ad)
    
    await callback.answer()

@dp.message(StateFilter(AdStates.writing_ad))
async def ad_text_handler(message: Message, state: FSMContext):
    """Обработка текста объявления"""
    user_data_state = await state.get_data()
    selected_topic = user_data_state.get("selected_topic")
    
    if not selected_topic or selected_topic not in TOPICS:
        await message.answer("❌ Ошибка: тема не выбрана. Начните заново с /start")
        await state.clear()
        return
    
    # Получаем данные темы
    topic_data = TOPICS[selected_topic]
    topic_id = topic_data["id"]
    
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
    
    try:
        # Публикуем в группу
        sent_message = await bot.send_message(
            chat_id=TARGET_CHAT_ID,
            text=formatted_text,
            message_thread_id=topic_id,
            parse_mode="HTML",
            reply_markup=get_contact_keyboard(
                message.from_user.id, 
                message.from_user.username
            )
        )
        
        # Формируем ссылку на сообщение
        message_url = f"https://t.me/c/{str(TARGET_CHAT_ID)[4:]}/{sent_message.message_id}"
        
        # Уведомляем автора
        await message.answer(
            "✅ Ваше объявление опубликовано!",
            reply_markup=get_post_actions_keyboard(message.from_user.id, message_url)
        )
        
        logger.info(f"Объявление опубликовано: пользователь {message.from_user.id}, тема {selected_topic}")
        
    except Exception as e:
        logger.error(f"Ошибка публикации: {e}")
        await message.answer("❌ Ошибка при публикации объявления. Попробуйте позже.")
    
    await state.clear()

@dp.callback_query(F.data == "create_new")
async def create_new_handler(callback: CallbackQuery, state: FSMContext):
    """Создание нового объявления"""
    await callback.message.edit_text(
        "📝 В какую тему хотите написать?",
        reply_markup=get_topics_keyboard()
    )
    await state.set_state(AdStates.choosing_topic)
    await callback.answer()

async def set_bot_commands():
    """Установка команд бота"""
    commands = [
        BotCommand(command="start", description="🚀 Начать работу с ботом")
    ]
    await bot.set_my_commands(commands)

async def main():
    """Главная функция"""
    logger.info("🚀 Запуск бота объявлений...")
    
    # Устанавливаем команды
    await set_bot_commands()
    
    # Запускаем бота
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
