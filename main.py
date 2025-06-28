import os
import logging
from telegram import ChatPermissions, Update
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    filters,
    ContextTypes,
)
from sqlalchemy import create_engine, Column, Integer, BigInteger
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

# Database setup
DATABASE_URL = os.getenv('DATABASE_URL')  # External database URL from env
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class MessageRecord(Base):
    __tablename__ = 'message_records'
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(BigInteger, index=True)
    message_id = Column(BigInteger, unique=True, index=True)

# Create tables
Base.metadata.create_all(bind=engine)

# Bot setup
token = os.getenv('TELEGRAM_TOKEN')
ping_chat = int(os.getenv('PING_CHAT_ID'))  # ID of chat to ping

# Permissions objects
restrict_perms = ChatPermissions(can_send_messages=False)
allow_perms = ChatPermissions(
    can_send_messages=True,
    can_send_media_messages=True,
    can_send_other_messages=True,
    can_add_web_page_previews=True,
)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session = SessionLocal()
    try:
        user_id = update.effective_user.id
        msg_id = update.message.message_id
        # Add record if new
        exists = session.query(MessageRecord).filter_by(message_id=msg_id).first()
        if not exists:
            session.add(MessageRecord(user_id=user_id, message_id=msg_id))
            session.commit()
        # Count
        count = session.query(MessageRecord).filter_by(user_id=user_id).count()
        if count >= 3:
            await context.bot.restrict_chat_member(
                chat_id=update.effective_chat.id,
                user_id=user_id,
                permissions=restrict_perms,
            )
            logger.info(f"Restricted {user_id} (count={count})")
    except Exception as e:
        logger.error(e)
    finally:
        session.close()

# NOTE: Telegram Bot API doesn't send deleted_message events. This is a placeholder.
async def handle_deleted(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Real deletion tracking requires client API (Telethon) or webhook processing.
    pass

async def ping(context: ContextTypes.DEFAULT_TYPE):
    try:
        await context.bot.send_message(chat_id=ping_chat, text='ping')
        logger.info('Ping sent')
    except Exception as e:
        logger.error(e)

if __name__ == '__main__':
    app = ApplicationBuilder().token(token).build()

    # Handlers
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    # Placeholder for deletion handler
    # app.add_handler(..., handle_deleted)

    # Schedule ping
    app.job_queue.run_repeating(ping, interval=25 * 60, first=0)

    app.run_polling()
