import re
import time
from html import escape

from pyrogram import Client, filters

import config
from Client.helpers import track_message
from Client.cache import AFK_CACHE
from Client.premium import premium_emoji

AFK_REPLY_COOLDOWN = 30
AFK_REPLY_CACHE = {}


def _command_name(message):
    text = message.text or message.caption or ""
    if not text.startswith("/"):
        return ""
    return text.split(maxsplit=1)[0].lower()


def _mentioned_user_ids(message):
    targets = set()
    if message.reply_to_message and message.reply_to_message.from_user:
        targets.add(message.reply_to_message.from_user.id)

    text = message.text or message.caption or ""
    for match in re.findall(r"@([A-Za-z0-9_]{4,32})", text):
        # Username lookup is a fallback for plain @mentions.
        targets.add(("username", match))

    entities = message.entities or message.caption_entities or []
    for entity in entities:
        if getattr(entity, "type", "") == "text_mention" and entity.user:
            targets.add(entity.user.id)
    return targets


@Client.on_message(filters.command("id"))
async def id_handler(client, message):
    target = (
        message.reply_to_message.from_user
        if message.reply_to_message and message.reply_to_message.from_user
        else message.from_user
    )
    if not target:
        await message.reply_text("I could not determine the user.")
        return
    await client.db.ensure_user(target)
    await message.reply_text(
        f"{premium_emoji('id', '🆔')} <b>User ID:</b> <code>{target.id}</code>"
    )


@Client.on_message(filters.command("ping"))
async def ping_handler(client, message):
    start = time.perf_counter()
    sent = await message.reply_text(f"{premium_emoji('ping', '🏓')} <b>Pong!</b>")
    ms = (time.perf_counter() - start) * 1000
    try:
        await sent.edit_text(
            f"{premium_emoji('ping', '🏓')} <b>Pong!</b> <code>{ms:.0f} ms</code>"
        )
    except Exception:
        pass


@Client.on_message(filters.command("afk"))
async def afk_handler(client, message):
    if not message.from_user:
        return
    parts = (message.text or "").split(maxsplit=1)
    reason = parts[1].strip() if len(parts) > 1 and parts[1].strip() else "AFK"
    await client.db.ensure_user(message.from_user)
    await client.db.set_afk(message.from_user.id, reason)
    AFK_CACHE[message.from_user.id] = {"reason": reason, "since": time.time()}
    await message.reply_text(
        f"{premium_emoji('afk', '💤')} "
        f"<b>{escape(message.from_user.first_name or 'User')}</b> is now AFK.\n"
        f"Reason: {escape(reason)}"
    )


@Client.on_message(filters.command("user_stats"))
async def user_stats_handler(client, message):
    if not message.from_user:
        return
    stats = await client.db.get_stats(message.from_user.id)
    await message.reply_text(
        f"{premium_emoji('stats', '📊')} <b>Your Stats</b>\n\n"
        f"Messages: <code>{stats['messages']}</code>\n"
        f"Tags: <code>{stats['tags']}</code>\n"
        f"Commands: <code>{stats['commands']}</code>"
    )


@Client.on_message(filters.incoming)
async def activity_watcher(client, message):
    if not message.from_user or message.from_user.is_bot:
        return
    try:
        await track_message(client, message)

        command = _command_name(message)
        # Count every user command exactly once here.
        if command.startswith("/"):
            await client.db.increment_stat(message.from_user.id, "commands")

        # /afk itself must not immediately clear the newly-created AFK state.
        bot_username = (config.BOT_USERNAME or "").lower()
        afk_commands = {"/afk"}
        if bot_username:
            afk_commands.add(f"/afk@{bot_username}")
        if command in afk_commands:
            return

        own = await client.db.get_afk(message.from_user.id)
        if own:
            await client.db.clear_afk(message.from_user.id)
            AFK_CACHE.pop(message.from_user.id, None)
            await message.reply_text(
                f"{premium_emoji('confirm', '👋')} Welcome back, "
                f"<b>{escape(message.from_user.first_name or 'User')}</b>! AFK removed."
            )

        targets = _mentioned_user_ids(message)
        resolved = set()
        for item in targets:
            if isinstance(item, tuple):
                try:
                    user = await client.get_users(item[1])
                    resolved.add(user.id)
                except Exception:
                    continue
            else:
                resolved.add(item)

        now = time.time()
        for uid in resolved:
            if uid == message.from_user.id:
                continue
            afk = await client.db.get_afk(uid)
            if not afk:
                continue
            key = (message.chat.id if message.chat else 0, uid)
            if now - AFK_REPLY_CACHE.get(key, 0) < AFK_REPLY_COOLDOWN:
                continue
            AFK_REPLY_CACHE[key] = now
            reason = afk.get("reason", "AFK")
            try:
                user = await client.get_users(uid)
                name = user.first_name or "User"
            except Exception:
                name = "User"
            await message.reply_text(
                f"{premium_emoji('afk', '💤')} <b>{escape(name)}</b> is currently AFK.\n"
                f"Reason: {escape(str(reason))}"
            )
    except Exception:
        # Tracking/AFK must never break normal message processing.
        return
