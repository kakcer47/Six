import os
import logging
import asyncio
from telegram import ChatPermissions, Update
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    filters,
    ContextTypes,
)
from sqlalchemy import create_engine, Column, Integer, BigInteger
from sqlalchemy.orm import declarative_base, sessionmaker

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

# Database setup
DATABASE_URL = os.getenv('DATABASE_URL')
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
ping_chat = int(os.getenv('PING_CHAT_ID'))

# Permissions
restrict_perms = ChatPermissions(can_send_messages=False)
allow_perms = ChatPermissions(can_send_messages=True, can_send_other_messages=True)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session = SessionLocal()
    try:
        uid = update.effective_user.id
        mid = update.message.message_id
        if not session.query(MessageRecord).filter_by(message_id=mid).first():
            session.add(MessageRecord(user_id=uid, message_id=mid))
            session.commit()
        count = session.query(MessageRecord).filter_by(user_id=uid).count()
        if count >= 3:
            await context.bot.restrict_chat_member(
                chat_id=update.effective_chat.id,
                user_id=uid,
                permissions=restrict_perms,
            )
            logger.info(f"Restricted {uid} (count={count})")
    except Exception as e:
        logger.error(e)
    finally:
        session.close()

async def handle_deleted(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Placeholder: deletion events not supported by Bot API
    pass

async def ping_task(bot):
    while True:
        try:
            await bot.send_message(chat_id=ping_chat, text='ping')
            logger.info('Ping sent')
        except Exception as e:
            logger.error('Ping failed: %s', e)
        await asyncio.sleep(25 * 60)

async def main():
    app = ApplicationBuilder().token(token).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Start ping task
    asyncio.create_task(ping_task(app.bot))

    await app.run_polling()

if __name__ == '__main__':
    asyncio.run(main())
