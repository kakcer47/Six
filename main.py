import os
import logging
import asyncio
from pyrogram import Client, filters
from pyrogram.types import Message, ChatPermissions
from sqlalchemy import create_engine, Column, Integer, BigInteger, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', 
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Pyrogram Client setup
API_ID = int(os.getenv('API_ID'))  # Получить на my.telegram.org
API_HASH = os.getenv('API_HASH')   # Получить на my.telegram.org
BOT_TOKEN = os.getenv('TELEGRAM_TOKEN')
PING_CHAT_ID = int(os.getenv('PING_CHAT_ID'))

# Database setup
DATABASE_URL = os.getenv('DATABASE_URL')
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class MessageRecord(Base):
    __tablename__ = 'message_records'
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(BigInteger, index=True)
    message_id = Column(BigInteger, index=True)
    chat_id = Column(BigInteger, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)

# Create tables
Base.metadata.create_all(bind=engine)

# Create Pyrogram client
app = Client(
    "message_tracker_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

# Permissions
restricted_permissions = ChatPermissions(
    can_send_messages=False,
    can_send_media_messages=False,
    can_send_polls=False,
    can_send_other_messages=False,
    can_add_web_page_previews=False,
    can_change_info=False,
    can_invite_users=False,
    can_pin_messages=False
)

unrestricted_permissions = ChatPermissions(
    can_send_messages=True,
    can_send_media_messages=True,
    can_send_polls=True,
    can_send_other_messages=True,
    can_add_web_page_previews=True,
    can_change_info=False,
    can_invite_users=False,
    can_pin_messages=False
)

def get_message_count(user_id: int, chat_id: int) -> int:
    """Получить количество сообщений пользователя в чате"""
    session = SessionLocal()
    try:
        count = session.query(MessageRecord).filter_by(
            user_id=user_id, 
            chat_id=chat_id
        ).count()
        return count
    finally:
        session.close()

async def restrict_user(chat_id: int, user_id: int):
    """Заблокировать пользователя"""
    try:
        await app.restrict_chat_member(
            chat_id=chat_id,
            user_id=user_id,
            permissions=restricted_permissions
        )
        logger.info(f"✅ Restricted user {user_id} in chat {chat_id}")
    except Exception as e:
        logger.error(f"❌ Failed to restrict user {user_id}: {e}")

async def unrestrict_user(chat_id: int, user_id: int):
    """Разблокировать пользователя"""
    try:
        await app.restrict_chat_member(
            chat_id=chat_id,
            user_id=user_id,
            permissions=unrestricted_permissions
        )
        logger.info(f"✅ Unrestricted user {user_id} in chat {chat_id}")
    except Exception as e:
        logger.error(f"❌ Failed to unrestrict user {user_id}: {e}")

@app.on_message(filters.group & filters.text & ~filters.command)
async def handle_new_message(client: Client, message: Message):
    """Обработчик новых сообщений"""
    session = SessionLocal()
    try:
        user_id = message.from_user.id
        message_id = message.id
        chat_id = message.chat.id
        
        # Проверяем, есть ли уже такое сообщение
        existing = session.query(MessageRecord).filter_by(
            message_id=message_id,
            chat_id=chat_id
        ).first()
        
        if not existing:
            # Добавляем новую запись
            new_record = MessageRecord(
                user_id=user_id,
                message_id=message_id,
                chat_id=chat_id
            )
            session.add(new_record)
            session.commit()
            
            logger.info(f"📝 Added message {message_id} from user {user_id} in chat {chat_id}")
            
            # Проверяем количество сообщений
            count = get_message_count(user_id, chat_id)
            
            if count >= 3:
                await restrict_user(chat_id, user_id)
                logger.info(f"🚫 User {user_id} restricted (messages: {count})")
                
    except Exception as e:
        logger.error(f"❌ Error in handle_new_message: {e}")
        session.rollback()
    finally:
        session.close()

@app.on_deleted_messages()
async def handle_deleted_messages(client: Client, messages):
    """Обработчик удаленных сообщений"""
    session = SessionLocal()
    try:
        for message in messages:
            # Находим запись в базе данных
            record = session.query(MessageRecord).filter_by(
                message_id=message.id,
                chat_id=message.chat.id
            ).first()
            
            if record:
                user_id = record.user_id
                chat_id = record.chat_id
                
                # Удаляем запись
                session.delete(record)
                session.commit()
                
                logger.info(f"🗑️ Deleted message {message.id} from user {user_id}")
                
                # Проверяем новое количество сообщений
                count = get_message_count(user_id, chat_id)
                
                if count < 3:
                    await unrestrict_user(chat_id, user_id)
                    logger.info(f"🔓 User {user_id} unrestricted (messages: {count})")
                    
    except Exception as e:
        logger.error(f"❌ Error in handle_deleted_messages: {e}")
        session.rollback()
    finally:
        session.close()

async def ping_task():
    """Задача отправки ping сообщений"""
    await asyncio.sleep(10)  # Ждем инициализации
    
    while True:
        try:
            await app.send_message(chat_id=PING_CHAT_ID, text="🏓 Ping")
            logger.info("📡 Ping sent successfully")
        except Exception as e:
            logger.error(f"❌ Ping failed: {e}")
        
        await asyncio.sleep(25 * 60)  # 25 минут

@app.on_message(filters.command("status"))
async def status_command(client: Client, message: Message):
    """Команда для проверки статуса пользователя"""
    if not message.reply_to_message:
        await message.reply("↩️ Reply to a user's message to check their status")
        return
    
    user_id = message.reply_to_message.from_user.id
    chat_id = message.chat.id
    count = get_message_count(user_id, chat_id)
    
    status = "🚫 Restricted" if count >= 3 else "✅ Allowed"
    
    await message.reply(
        f"👤 User: {message.reply_to_message.from_user.first_name}\n"
        f"📊 Messages: {count}/3\n"
        f"🎭 Status: {status}"
    )

@app.on_message(filters.command("reset"))
async def reset_command(client: Client, message: Message):
    """Команда для сброса счетчика пользователя (только для админов)"""
    # Проверяем права администратора
    chat_member = await app.get_chat_member(message.chat.id, message.from_user.id)
    if chat_member.status not in ["creator", "administrator"]:
        await message.reply("❌ Only admins can use this command")
        return
    
    if not message.reply_to_message:
        await message.reply("↩️ Reply to a user's message to reset their counter")
        return
    
    session = SessionLocal()
    try:
        user_id = message.reply_to_message.from_user.id
        chat_id = message.chat.id
        
        # Удаляем все записи пользователя
        deleted = session.query(MessageRecord).filter_by(
            user_id=user_id,
            chat_id=chat_id
        ).delete()
        
        session.commit()
        
        if deleted > 0:
            await unrestrict_user(chat_id, user_id)
            await message.reply(f"🔄 Reset {deleted} messages for user {message.reply_to_message.from_user.first_name}")
        else:
            await message.reply("📭 No messages found for this user")
            
    except Exception as e:
        logger.error(f"❌ Error in reset_command: {e}")
        await message.reply("❌ Error occurred while resetting")
        session.rollback()
    finally:
        session.close()

async def main():
    """Основная функция"""
    logger.info("🚀 Starting Message Tracker Bot...")
    
    # Запускаем бота
    await app.start()
    logger.info("✅ Bot started successfully")
    
    # Запускаем ping задачу
    asyncio.create_task(ping_task())
    logger.info("📡 Ping task started")
    
    # Держим бота активным
    await app.idle()

if __name__ == "__main__":
    try:
        app.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 Bot stopped by user")
    except Exception as e:
        logger.error(f"❌ Bot crashed: {e}")
        raise
