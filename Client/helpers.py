import time
from pyrogram.enums import ChatMembersFilter
from pyrogram.errors import RPCError
from .cache import GROUP_IDS_CACHE, GROUP_SPEED_CACHE, WELCOME_CACHE
import config

ADMIN_CACHE = {}

async def is_admin(client, chat_id, user_id):
    if user_id == config.OWNER_ID or user_id in config.SUDO_USERS:
        return True
    key = (chat_id, user_id)
    cached = ADMIN_CACHE.get(key)
    if cached and time.time() - cached < 120:
        return True
    try:
        member = await client.get_chat_member(chat_id, user_id)
        if member.status in ("administrator", "owner"):
            ADMIN_CACHE[key] = time.time()
            return True
    except RPCError:
        pass
    return False

async def require_admin(client, message):
    if message.chat and message.from_user and await is_admin(client, message.chat.id, message.from_user.id):
        return True
    await message.reply_text("You must be a group administrator to use this command.")
    return False

async def get_speed(client, chat_id):
    if chat_id not in GROUP_SPEED_CACHE:
        GROUP_SPEED_CACHE[chat_id] = await client.db.get_speed(chat_id)
    return GROUP_SPEED_CACHE[chat_id]

async def set_speed(client, chat_id, seconds):
    GROUP_SPEED_CACHE[chat_id] = seconds
    await client.db.set_speed(chat_id, seconds)

async def track_message(client, message):
    if message.from_user:
        await client.db.ensure_user(message.from_user)
        await client.db.increment_stat(message.from_user.id, "messages")
    if message.chat and message.chat.id < 0:
        await client.db.ensure_group(message.chat)
