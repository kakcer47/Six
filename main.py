import asyncio
import logging
import os
import sqlite3
import aiosqlite
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

# База данных в памяти
async def init_db():
    """Инициализация базы данных SQLite в памяти"""
    async with aiosqlite.connect(":memory:") as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_ads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                message_url TEXT NOT NULL,
                topic_name TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.commit()
        return db

# Глобальная переменная для БД
db_connection = None

async def add_user_ad(user_id: int, message_id: int, message_url: str, topic_name: str):
    """Добавить объявление пользователя в БД"""
    global db_connection
    if db_connection:
        await db_connection.execute(
            "INSERT INTO user_ads (user_id, message_id, message_url, topic_name) VALUES (?, ?, ?, ?)",
            (user_id, message_id, message_url, topic_name)
        )
        await db_connection.commit()

async def get_user_ads(user_id: int):
    """Получить объявления пользователя"""
    global db_connection
    if db_connection:
        async with db_connection.execute(
            "SELECT message_id, message_url, topic_name FROM user_ads WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,)
        ) as cursor:
            return await cursor.fetchall()
    return []

async def get_ad_by_message_id(message_id: int):
    """Получить объявление по message_id"""
    global db_connection
    if db_connection:
        async with db_connection.execute(
            "SELECT user_id, message_id, message_url, topic_name FROM user_ads WHERE message_id = ?",
            (message_id,)
        ) as cursor:
            return await cursor.fetchone()
    return None

async def delete_user_ad(message_id: int):
    """Удалить объявление из БД"""
    global db_connection
    if db_connection:
        await db_connection.execute(
            "DELETE FROM user_ads WHERE message_id = ?",
            (message_id,)
        )
        await db_connection.commit()

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
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_language")]
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
    await message.answer(
        "🌍 Выберите язык / Choose language:",
        reply_markup=get_language_keyboard()
    )
    await state.set_state(AdStates.choosing_language)

@dp.callback_query(F.data == "lang_ru", StateFilter(AdStates.choosing_language))
async def language_ru_handler(callback: CallbackQuery, state: FSMContext):
    """Выбор русского языка"""
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

@dp.callback_query(F.data == "create_ad", StateFilter(AdStates.main_menu))
async def create_ad_handler(callback: CallbackQuery, state: FSMContext):
    """Создание объявления"""
    await callback.message.edit_text(
        "📝 В какую тему хотите написать?",
        reply_markup=get_topics_keyboard()
    )
    await state.set_state(AdStates.choosing_topic)
    await callback.answer()

@dp.callback_query(F.data == "my_ads", StateFilter(AdStates.main_menu))
async def my_ads_handler(callback: CallbackQuery, state: FSMContext):
    """Мои объявления"""
    user_id = callback.from_user.id
    ads = await get_user_ads(user_id)
    
    if not ads:
        await callback.answer("📭 У вас пока нет объявлений", show_alert=True)
        return
    
    keyboard = await get_my_ads_keyboard(user_id)
    await callback.message.edit_text(
        f"📋 Ваши объявления ({len(ads)}):",
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
        
        if not ads:
            await callback.message.edit_text(
                "🏠 Главное меню:",
                reply_markup=get_main_menu_keyboard()
            )
            await state.set_state(AdStates.main_menu)
        else:
            keyboard = await get_my_ads_keyboard(user_id)
            await callback.message.edit_text(
                f"📋 Ваши объявления ({len(ads)}):",
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
        
        # Сохраняем в БД
        await add_user_ad(
            message.from_user.id, 
            sent_message.message_id, 
            message_url, 
            topic_name
        )
        
        # Уведомляем автора
        await message.answer(
            "✅ Ваше объявление опубликовано!",
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

@dp.callback_query(F.data == "create_new")
async def create_new_handler(callback: CallbackQuery, state: FSMContext):
    """Создание нового объявления"""
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
    """Предупреждение об удалении объявления"""
    message_id = int(callback.data.split("_")[-1])
    
    # Создаем клавиатуру подтверждения
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"confirm_delete_{message_id}"),
            InlineKeyboardButton(text="❌ Отмена", callback_data=f"view_ad_{message_id}")
        ]
    ])
    
    await callback.answer("⚠️ Вы точно хотите удалить это объявление?", show_alert=True)
    
    await callback.message.edit_text(
        "⚠️ Подтвердите удаление объявления:\n\nЭто действие нельзя отменить!",
        reply_markup=keyboard
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("confirm_delete_"))
async def confirm_delete_handler(callback: CallbackQuery, state: FSMContext):
    """Подтверждение удаления объявления"""
    try:
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
                f"✅ Объявление удалено!\n\n📋 Ваши объявления ({len(ads)}):",
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

async def main():
    """Главная функция"""
    global db_connection
    
    logger.info("🚀 Запуск бота объявлений...")
    
    # Инициализируем БД
    db_connection = await aiosqlite.connect(":memory:")
    await db_connection.execute("""
        CREATE TABLE IF NOT EXISTS user_ads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            message_url TEXT NOT NULL,
            topic_name TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await db_connection.commit()
    
    # Устанавливаем команды
    await set_bot_commands()
    
    try:
        # Запускаем бота
        await dp.start_polling(bot)
    finally:
        # Закрываем соединение с БД
        if db_connection:
            await db_connection.close()

if __name__ == "__main__":
    asyncio.run(main())
