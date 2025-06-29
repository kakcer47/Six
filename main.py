import os
from pyrogram import Client, filters
from pyrogram.types import Message, ChatPermissions

# API данные
API_ID = int(os.getenv('API_ID'))
API_HASH = os.getenv('API_HASH')
BOT_TOKEN = os.getenv('TELEGRAM_TOKEN')

# Создаем клиента
app = Client("simple_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# Права для блокировки
restricted = ChatPermissions(can_send_messages=False)
unrestricted = ChatPermissions(can_send_messages=True)

async def count_user_messages(chat_id, user_id):
    """Считаем сообщения пользователя в чате"""
    count = 0
    async for message in app.get_chat_history(chat_id):
        if message.from_user and message.from_user.id == user_id:
            count += 1
    return count

@app.on_message(filters.group & filters.text)
async def handle_message(client, message: Message):
    """Обработка нового сообщения"""
    user_id = message.from_user.id
    chat_id = message.chat.id
    
    # Считаем сообщения пользователя
    count = await count_user_messages(chat_id, user_id)
    
    # Если 4 или больше - блокируем
    if count >= 4:
        await app.restrict_chat_member(chat_id, user_id, restricted)

@app.on_deleted_messages()
async def handle_deleted(client, messages):
    """Обработка удаленных сообщений"""
    for message in messages:
        if message.from_user:
            user_id = message.from_user.id
            chat_id = message.chat.id
            
            # Пересчитываем сообщения
            count = await count_user_messages(chat_id, user_id)
            
            # Если меньше 4 - разблокируем
            if count < 4:
                await app.restrict_chat_member(chat_id, user_id, unrestricted)

if __name__ == "__main__":
    app.run()
