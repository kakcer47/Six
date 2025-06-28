import os
import logging
from telegram import Update, ChatPermissions
from telegram.ext import Updater, MessageHandler, Filters, CallbackContext
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
ping_chat = int(os.getenv('PING_CHAT_ID'))  # ID of chat to ping (bot's own or group)
updater = Updater(token=token, use_context=True)
dispatcher = updater.dispatcher
job_queue = updater.job_queue

# Permissions for restricted users: no sending messages
restricted_permissions = ChatPermissions(can_send_messages=False)

# Handler for incoming messages
def handle_message(update: Update, context: CallbackContext):
    session = SessionLocal()
    user_id = update.effective_user.id
    msg_id = update.message.message_id
    # Add record if new
    exists = session.query(MessageRecord).filter_by(message_id=msg_id).first()
    if not exists:
        record = MessageRecord(user_id=user_id, message_id=msg_id)
        session.add(record)
        session.commit()

    # Count user's messages
    count = session.query(MessageRecord).filter_by(user_id=user_id).count()
    if count >= 3:
        try:
            context.bot.restrict_chat_member(chat_id=update.effective_chat.id,
                                             user_id=user_id,
                                             permissions=restricted_permissions)
            logger.info(f"User {user_id} restricted (count={count})")
        except Exception as e:
            logger.error("Failed to restrict user %s: %s", user_id, e)
    session.close()

# Handler for deleted messages (requires proper update settings)
def handle_deleted(update: Update, context: CallbackContext):
    session = SessionLocal()
    # Telegram doesn\'t directly send deleted message IDs; placeholder logic
    msg = update.effective_message
    if not msg:
        return
    msg_id = msg.message_id
    record = session.query(MessageRecord).filter_by(message_id=msg_id).first()
    if record:
        user_id = record.user_id
        session.delete(record)
        session.commit()
        # Recount
        count = session.query(MessageRecord).filter_by(user_id=user_id).count()
        if count < 3:
            try:
                # Lift restriction: allow send messages
                context.bot.restrict_chat_member(chat_id=update.effective_chat.id,
                                                 user_id=user_id,
                                                 permissions=ChatPermissions(can_send_messages=True,
                                                                             can_send_media_messages=True,
                                                                             can_send_other_messages=True,
                                                                             can_add_web_page_previews=True))
                logger.info(f"User {user_id} unrestricted (count={count})")
            except Exception as e:
                logger.error("Failed to unrestrict user %s: %s", user_id, e)
    session.close()

# Periodic ping job every 25 minutes
def ping(context: CallbackContext):
    try:
        context.bot.send_message(chat_id=ping_chat, text='ping')
        logger.info('Ping sent to %s', ping_chat)
    except Exception as e:
        logger.error('Ping failed: %s', e)

# Setup handlers
dispatcher.add_handler(MessageHandler(Filters.text & (~Filters.command), handle_message))
# Note: message deletion updates may not be supported by default
dispatcher.add_handler(MessageHandler(Filters.status_update, handle_deleted))

# Schedule ping every 25 minutes
job_queue.run_repeating(ping, interval=25 * 60, first=0)

if __name__ == '__main__':
    updater.start_polling()
    updater.idle()
