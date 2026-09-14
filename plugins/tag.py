import asyncio

from pyrogram import Client, filters
from pyrogram.enums import ButtonStyle
from pyrogram.errors import FloodWait, RPCError
from pyrogram.types import InlineKeyboardMarkup

import config
from Client.cache import TAG_TASKS
from Client.helpers import require_admin, get_speed, set_speed, is_admin
from Client.premium import premium_button, premium_emoji


async def collect_members(client, chat_id, admins_only=False):
    members = []
    async for m in client.get_chat_members(chat_id):
        user = m.user
        if not user or user.is_bot or user.is_deleted:
            continue
        status = getattr(m.status, "value", m.status)
        if admins_only and status not in ("administrator", "owner"):
            continue
        members.append(user)
    return members


def speed_panel():
    return InlineKeyboardMarkup([
        [
            premium_button("Turbo • 1s", "speed", ButtonStyle.SUCCESS, callback_data="speed:set:1"),
            premium_button("Fast • 2s", "speed", ButtonStyle.SUCCESS, callback_data="speed:set:2"),
        ],
        [
            premium_button("Normal • 3s", "speed", ButtonStyle.SUCCESS, callback_data="speed:set:3"),
            premium_button("Slow • 5s", "speed", ButtonStyle.DANGER, callback_data="speed:set:5"),
        ],
        [
            premium_button("Custom 1–10s", "speed", ButtonStyle.PRIMARY, callback_data="speed:custom"),
            premium_button("Reset • 3s", "speed", ButtonStyle.DANGER, callback_data="speed:set:3"),
        ],
        [premium_button("Close", "close", ButtonStyle.DANGER, callback_data="speed:close")],
    ])


async def run_tag(client, message, admins_only=False):
    chat_id = message.chat.id
    task = asyncio.current_task()

    # Register before the potentially slow member collection so a second
    # tagging request cannot start in the same group during collection.
    if chat_id in TAG_TASKS:
        await message.reply_text("A tagging task is already running in this group.")
        return
    TAG_TASKS[chat_id] = task

    sent = 0
    members = []
    try:
        members = await collect_members(client, chat_id, admins_only)
        if not members:
            await message.reply_text("No eligible members found.")
            return

        speed = await get_speed(client, chat_id)
        await message.reply_text(
            f"{premium_emoji('tag', '💫')} <b>Tagging started</b> • {len(members)} members • {speed}s interval"
        )

        for user in members:
            if TAG_TASKS.get(chat_id) is not task:
                break
            try:
                await client.send_message(chat_id, f"{premium_emoji('tag', '💫')} {user.mention}")
                sent += 1
                if message.from_user:
                    await client.db.increment_stat(message.from_user.id, "tags")
            except FloodWait as e:
                await asyncio.sleep(max(1, int(e.value)))
                continue
            except RPCError:
                continue
            await asyncio.sleep(speed)

        # If another task replaced ours, do not send a misleading completion.
        if TAG_TASKS.get(chat_id) is task:
            await message.reply_text(
                f"{premium_emoji('confirm', '✅')} <b>Tagging finished.</b> {sent}/{len(members)} members tagged."
            )
    except asyncio.CancelledError:
        try:
            await message.reply_text(
                f"{premium_emoji('cancel', '🛑')} <b>Tagging cancelled.</b> {sent} members tagged."
            )
        finally:
            raise
    except RPCError as exc:
        await message.reply_text(
            f"{premium_emoji('cancel', '❌')} <b>Tagging failed.</b>\n<code>{str(exc)[:500]}</code>"
        )
    finally:
        if TAG_TASKS.get(chat_id) is task:
            TAG_TASKS.pop(chat_id, None)


@Client.on_message(filters.command(["utag", "tagall"]) & filters.group)
async def tag_all(client, message):
    if not await require_admin(client, message):
        return
    await run_tag(client, message, False)


@Client.on_message(filters.command(["atag", "admin"]) & filters.group)
async def tag_admins(client, message):
    await run_tag(client, message, True)


@Client.on_message(filters.command("cancel") & filters.group)
async def cancel_tag(client, message):
    if not await require_admin(client, message):
        return
    task = TAG_TASKS.get(message.chat.id)
    if not task:
        await message.reply_text("No tagging task is running.")
        return
    task.cancel()
    await message.reply_text(f"{premium_emoji('cancel', '🛑')} <b>Cancelling tagging...</b>")


@Client.on_message(filters.command("speed") & filters.group)
async def speed_command(client, message):
    if not await require_admin(client, message):
        return
    parts = message.text.split(maxsplit=1) if message.text else []
    arg = parts[1].strip().lower() if len(parts) > 1 else ""

    if arg == "reset":
        await set_speed(client, message.chat.id, config.DEFAULT_TAG_SPEED)
        await message.reply_text(
            f"{premium_emoji('speed', '⚙️')} <b>Tag speed reset to {config.DEFAULT_TAG_SPEED}s.</b>",
            reply_markup=speed_panel(),
        )
        return

    if arg.isdigit() and config.MIN_TAG_SPEED <= int(arg) <= config.MAX_TAG_SPEED:
        seconds = int(arg)
        await set_speed(client, message.chat.id, seconds)
        await message.reply_text(
            f"{premium_emoji('speed', '⚙️')} <b>Tag speed set to {seconds}s.</b>",
            reply_markup=speed_panel(),
        )
        return

    await message.reply_text(
        f"{premium_emoji('speed', '⚙️')} <b>Tag Speed</b>\nCurrent: <b>{await get_speed(client, message.chat.id)}s</b>",
        reply_markup=speed_panel(),
    )


@Client.on_callback_query(filters.regex(r"^speed:set:(\d+)$"))
async def speed_set(client, cq):
    if not cq.message or not cq.message.chat:
        await cq.answer("This panel is no longer available.", show_alert=True)
        return
    if not await is_admin(client, cq.message.chat.id, cq.from_user.id):
        await cq.answer("Admin only.", show_alert=True)
        return
    seconds = int(cq.matches[0].group(1))
    if not config.MIN_TAG_SPEED <= seconds <= config.MAX_TAG_SPEED:
        await cq.answer("Speed must be between 1 and 10 seconds.", show_alert=True)
        return
    await set_speed(client, cq.message.chat.id, seconds)
    await cq.answer(f"Speed set to {seconds}s")
    await cq.message.edit_text(
        f"{premium_emoji('speed', '⚙️')} <b>Tag Speed</b>\nCurrent: <b>{seconds}s</b>",
        reply_markup=speed_panel(),
    )


@Client.on_callback_query(filters.regex(r"^speed:custom$"))
async def speed_custom(client, cq):
    if not cq.message or not cq.message.chat:
        await cq.answer("This panel is no longer available.", show_alert=True)
        return
    if not await is_admin(client, cq.message.chat.id, cq.from_user.id):
        await cq.answer("Admin only.", show_alert=True)
        return
    await cq.answer("Use /speed 1 through /speed 10.", show_alert=True)


@Client.on_callback_query(filters.regex(r"^speed:close$"))
async def speed_close(client, cq):
    await cq.answer()
    try:
        await cq.message.delete()
    except RPCError:
        pass
