import os
import asyncio
import logging
from pyrogram import Client, filters
from pyrogram.types import Message, ChatPermissions
from pyrogram.errors import (
    ChatAdminRequired, 
    UserAdminInvalid, 
    FloodWait,
    PeerIdInvalid
)

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Конфигурация из переменных окружения
API_ID = int(os.getenv('API_ID'))
API_HASH = os.getenv('API_HASH')
PHONE = os.getenv('PHONE_NUMBER')
TARGET_CHAT_ID = int(os.getenv('TARGET_CHAT_ID'))

# Клиент
app = Client("user_account", api_id=API_ID, api_hash=API_HASH, phone_number=PHONE)

# Настройки прав
restricted = ChatPermissions(can_send_messages=False)
unrestricted = ChatPermissions(
    can_send_messages=True,
    can_send_media_messages=True,
    can_send_polls=True,
    can_send_other_messages=True,
    can_add_web_page_previews=True,
    can_invite_users=True
)

processing_lock = asyncio.Lock()

async def count_user_messages_fast(chat_id: int, user_id: int) -> int:
    """Быстрый подсчет сообщений конкретного пользователя"""
    try:
        count = 0
        logger.info(f"🔍 Поиск сообщений пользователя {user_id}")
        
        # Ищем сообщения только этого пользователя
        async for message in app.search_messages(chat_id, from_user=user_id):
            count += 1
            
            # Логируем каждые 100 сообщений
            if count % 100 == 0:
                logger.info(f"📊 Найдено {count} сообщений от пользователя {user_id}")
        
        logger.info(f"✅ Итого сообщений от пользователя {user_id}: {count}")
        return count
        
    except Exception as e:
        logger.error(f"❌ Ошибка при поиске сообщений для {user_id}: {e}")
        return 0

async def restrict_user(chat_id: int, user_id: int) -> bool:
    """Ограничить пользователя"""
    try:
        await app.restrict_chat_member(chat_id, user_id, restricted)
        logger.info(f"🚫 Ограничен пользователь {user_id}")
        return True
    except ChatAdminRequired:
        logger.error(f"❌ Нет прав администратора в чате {chat_id}")
        return False
    except UserAdminInvalid:
        logger.warning(f"⚠️ Нельзя ограничить администратора {user_id}")
        return False
    except FloodWait as e:
        logger.warning(f"⏳ FloodWait: ждем {e.value} секунд")
        await asyncio.sleep(e.value)
        return await restrict_user(chat_id, user_id)
    except Exception as e:
        logger.error(f"❌ Ошибка при ограничении пользователя {user_id}: {e}")
        return False

async def unrestrict_user(chat_id: int, user_id: int) -> bool:
    """Разрешить пользователю писать"""
    try:
        await app.restrict_chat_member(chat_id, user_id, unrestricted)
        logger.info(f"✅ Разрешено писать пользователю {user_id}")
        return True
    except ChatAdminRequired:
        logger.error(f"❌ Нет прав администратора в чате {chat_id}")
        return False
    except FloodWait as e:
        logger.warning(f"⏳ FloodWait: ждем {e.value} секунд")
        await asyncio.sleep(e.value)
        return await unrestrict_user(chat_id, user_id)
    except Exception as e:
        logger.error(f"❌ Ошибка при разрешении пользователю {user_id}: {e}")
        return False

@app.on_message(filters.chat(TARGET_CHAT_ID) & ~filters.service)
async def handle_new_message(client: Client, message: Message):
    """Обработка новых сообщений в целевом чате"""
    if not message.from_user:
        return
    
    user_id = message.from_user.id
    chat_id = message.chat.id
    
    async with processing_lock:
        try:
            # Быстро считаем сообщения только этого пользователя
            count = await count_user_messages_fast(chat_id, user_id)
            
            username = message.from_user.username or message.from_user.first_name or "Без имени"
            logger.info(f"📝 {username} (ID: {user_id}) написал сообщение. Всего сообщений: {count}")
            
            # Если сообщений >= 4, ограничиваем (добавляем в исключения)
            if count >= 4:
                success = await restrict_user(chat_id, user_id)
                if success:
                    logger.info(f"🚫 Пользователь {user_id} добавлен в исключения ({count} сообщений)")
                
        except Exception as e:
            logger.error(f"❌ Ошибка при обработке нового сообщения: {e}")

@app.on_deleted_messages()
async def handle_deleted_messages(client: Client, messages):
    """Мониторинг ТОЛЬКО удалений сообщений"""
    async with processing_lock:
        processed_users = set()
        
        for message in messages:
            if not message.from_user or not message.chat:
                continue
                
            user_id = message.from_user.id
            chat_id = message.chat.id
            
            # Работаем только в целевом чате и только один раз на пользователя
            if chat_id != TARGET_CHAT_ID or user_id in processed_users:
                continue
                
            processed_users.add(user_id)
            
            try:
                # Быстро пересчитываем сообщения конкретного пользователя
                count = await count_user_messages_fast(chat_id, user_id)
                
                username = message.from_user.username or message.from_user.first_name or "Без имени"
                logger.info(f"🗑️ Удалено сообщение {username} (ID: {user_id}). Осталось: {count}")
                
                # Если стало < 4, разрешаем писать (убираем из исключений)
                if count < 4:
                    success = await unrestrict_user(chat_id, user_id)
                    if success:
                        logger.info(f"✅ Пользователь {user_id} убран из исключений ({count} сообщений)")
                    
            except Exception as e:
                logger.error(f"❌ Ошибка при обработке удаления для {user_id}: {e}")

@app.on_message(filters.command("check") & filters.chat(TARGET_CHAT_ID) & filters.me)
async def check_user_command(client: Client, message: Message):
    """Команда для проверки количества сообщений пользователя"""
    if message.reply_to_message and message.reply_to_message.from_user:
        user = message.reply_to_message.from_user
        count = await count_user_messages_fast(message.chat.id, user.id)
        
        status = "🚫 В исключениях" if count >= 4 else "✅ Может писать"
        
        await message.edit(
            f"👤 **{user.first_name or 'Без имени'}** (@{user.username or 'нет'})\n"
            f"🆔 ID: `{user.id}`\n"
            f"📊 Сообщений: **{count}**\n"
            f"🎯 Статус: {status}"
        )
    else:
        await message.edit("❌ Ответьте на сообщение пользователя для проверки")

@app.on_message(filters.command("stats") & filters.chat(TARGET_CHAT_ID) & filters.me)
async def stats_command(client: Client, message: Message):
    """Статистика работы бота"""
    try:
        chat = await app.get_chat(TARGET_CHAT_ID)
        me = await app.get_me()
        member = await app.get_chat_member(TARGET_CHAT_ID, me.id)
        admin_status = "✅ Администратор" if member.status in ["administrator", "creator"] else "❌ Не администратор"
        
        await message.edit(
            f"📊 **Статистика бота**\n\n"
            f"🏠 Чат: {chat.title}\n"
            f"👑 Статус: {admin_status}\n"
            f"🎯 Лимит сообщений: 4\n"
            f"⚡ Режим: Быстрый поиск по пользователям\n"
            f"🔍 Мониторинг: Новые сообщения + Удаления"
        )
    except Exception as e:
        await message.edit(f"❌ Ошибка получения статистики: {e}")

async def main():
    """Основная функция"""
    logger.info("🚀 Запуск эффективного бота...")
    
    try:
        async with app:
            me = await app.get_me()
            logger.info(f"✅ Авторизован как: {me.first_name} (@{me.username})")
            
            # Проверяем целевой чат
            try:
                chat = await app.get_chat(TARGET_CHAT_ID)
                logger.info(f"🎯 Целевой чат: {chat.title} (ID: {TARGET_CHAT_ID})")
                
                # Проверяем права
                member = await app.get_chat_member(TARGET_CHAT_ID, me.id)
                if member.status in ["administrator", "creator"]:
                    logger.info("✅ Права администратора подтверждены")
                else:
                    logger.warning("⚠️ Нет прав администратора!")
                    
            except Exception as e:
                logger.error(f"❌ Ошибка при проверке чата: {e}")
                return
            
            logger.info("🎯 Логика работы:")
            logger.info("  📝 Новое сообщение → Быстрый поиск по автору → Если ≥4 то блок")
            logger.info("  🗑️ Удаление → Быстрый поиск по автору → Если <4 то разблок")
            logger.info("✅ Бот запущен и готов к работе!")
            
            await app.idle()
            
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}")
        raise

if __name__ == "__main__":
    asyncio.run(main())
