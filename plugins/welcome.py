import re
import asyncio
from html import escape

from pyrogram import Client, filters
from pyrogram.enums import ButtonStyle, ChatMemberStatus
from pyrogram.types import InlineKeyboardMarkup

from Client.helpers import require_admin
from Client.cache import WELCOME_CACHE
from Client.premium import premium_button, premium_emoji

URL_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
WELCOME_LOCKS = {}


def _lock(chat_id):
    return WELCOME_LOCKS.setdefault(chat_id, asyncio.Lock())


def parse_buttons(text):
    """Parse [Label](https://...) buttons, with | separating buttons in a row."""
    if not text:
        return "", None

    lines = text.splitlines()
    cleaned_lines = []
    rows = []

    for line in lines:
        matches = list(URL_RE.finditer(line))
        if not matches:
            cleaned_lines.append(line)
            continue

        # Only treat a line as a button row when every non-whitespace part
        # is a valid markdown-style URL button separated by |.
        parts = [part.strip() for part in line.split("|")]
        parsed = []
        valid_row = True
        for part in parts:
            match = URL_RE.fullmatch(part)
            if not match:
                valid_row = False
                break
            parsed.append((match.group(1).strip(), match.group(2).strip()))

        if valid_row and parsed:
            rows.append([
                premium_button(label, "default", ButtonStyle.SUCCESS, url=url)
                for label, url in parsed
            ])
            continue

        # Support a line containing one or more URL buttons even if it has
        # surrounding text; remove only the button markup from the message.
        remaining = URL_RE.sub("", line).strip(" |")
        if remaining:
            cleaned_lines.append(remaining)
        if matches:
            rows.append([
                premium_button(m.group(1).strip(), "default", ButtonStyle.SUCCESS, url=m.group(2).strip())
                for m in matches
            ])

    cleaned = "\n".join(cleaned_lines).strip()
    return cleaned, InlineKeyboardMarkup(rows) if rows else None


def render(text, user, chat):
    values = {
        "first_name": escape(user.first_name or ""),
        "username": f"@{escape(user.username)}" if user.username else "",
        "id": str(user.id),
        "mention": user.mention,
        "title": escape(chat.title or ""),
    }
    for key, value in values.items():
        text = text.replace("{" + key + "}", value)
    return text


async def get_enabled(client, chat_id):
    doc = await client.db.get_welcome(chat_id)
    return bool(doc and doc.get("enabled", False)), doc


async def save_welcome(client, chat_id, data):
    await client.db.set_welcome(chat_id, data)
    WELCOME_CACHE[chat_id] = data


@Client.on_message(filters.command("setwelcome") & filters.group)
async def setwelcome(client, message):
    if not await require_admin(client, message):
        return

    target = message.reply_to_message
    parts = message.text.split(maxsplit=1) if message.text else []
    arg = parts[1].strip() if len(parts) > 1 else ""

    if target and (target.text or target.caption or target.photo or target.video):
        source_text = target.caption or target.text or ""
        if not source_text and (target.photo or target.video):
            source_text = "Welcome, {mention}!"
        clean, _ = parse_buttons(source_text)
        data = {
            "enabled": True,
            "type": "photo" if target.photo else "video" if target.video else "text",
            "text": clean,
            "buttons_text": source_text,
        }
        if target.photo:
            data["file_id"] = target.photo.file_id
        elif target.video:
            data["file_id"] = target.video.file_id
    elif arg:
        clean, _ = parse_buttons(arg)
        data = {"enabled": True, "type": "text", "text": clean, "buttons_text": arg}
    else:
        await message.reply_text(
            "Use /setwelcome <text> or reply to a text/photo/video with /setwelcome."
        )
        return

    await save_welcome(client, message.chat.id, data)
    await message.reply_text(
        f"{premium_emoji('confirm', '✅')} <b>Welcome message saved and enabled.</b>"
    )


@Client.on_message(filters.command("welcome") & filters.group)
async def welcome_toggle(client, message):
    if not await require_admin(client, message):
        return
    parts = message.text.split() if message.text else []
    if len(parts) != 2 or parts[1].lower() not in ("on", "off"):
        await message.reply_text("Usage: /welcome on or /welcome off")
        return

    enabled = parts[1].lower() == "on"
    doc = await client.db.get_welcome(message.chat.id) or {
        "type": "text",
        "text": "Welcome, {mention}!",
        "buttons_text": "Welcome, {mention}!",
    }
    doc["enabled"] = enabled
    await save_welcome(client, message.chat.id, doc)
    await message.reply_text(
        f"{premium_emoji('confirm', '✅')} Welcome system <b>{'enabled' if enabled else 'disabled'}</b>."
    )


@Client.on_message(filters.command("cleanwelcome") & filters.group)
async def cleanwelcome_toggle(client, message):
    if not await require_admin(client, message):
        return
    parts = message.text.split() if message.text else []
    if len(parts) != 2 or parts[1].lower() not in ("on", "off"):
        await message.reply_text("Usage: /cleanwelcome on or /cleanwelcome off")
        return

    enabled = parts[1].lower() == "on"
    doc = await client.db.get_welcome(message.chat.id) or {
        "enabled": False,
        "type": "text",
        "text": "Welcome, {mention}!",
        "buttons_text": "Welcome, {mention}!",
    }
    doc["clean_welcome"] = enabled
    if not enabled:
        doc.pop("last_message_id", None)
    await save_welcome(client, message.chat.id, doc)
    await message.reply_text(
        f"{premium_emoji('confirm', '✅')} Clean welcome <b>{'enabled' if enabled else 'disabled'}</b>."
    )


@Client.on_chat_member_updated()
async def welcome_new_member(client, update):
    if not update.chat or not update.new_chat_member:
        return

    member = update.new_chat_member
    user = member.user
    if not user or user.is_bot:
        return

    new_status = getattr(member.status, "value", member.status)
    old_member = update.old_chat_member
    old_status = getattr(old_member.status, "value", old_member.status) if old_member else None

    member_status = getattr(ChatMemberStatus.MEMBER, "value", ChatMemberStatus.MEMBER)
    restricted_status = getattr(ChatMemberStatus.RESTRICTED, "value", ChatMemberStatus.RESTRICTED)
    old_left = old_status in (None, "left", "kicked", getattr(ChatMemberStatus.LEFT, "value", ChatMemberStatus.LEFT), getattr(ChatMemberStatus.BANNED, "value", ChatMemberStatus.BANNED))
    became_member = new_status in (member_status, restricted_status) and old_left
    if not became_member:
        return

    enabled, doc = await get_enabled(client, update.chat.id)
    if not enabled or not doc:
        return

    async with _lock(update.chat.id):
        # Re-read so concurrent joins use the latest configuration/message id.
        enabled, doc = await get_enabled(client, update.chat.id)
        if not enabled or not doc:
            return

        raw_buttons = doc.get("buttons_text") or doc.get("text", "Welcome, {mention}!")
        _, markup = parse_buttons(raw_buttons)
        text = render(doc.get("text", "Welcome, {mention}!"), user, update.chat)

        try:
            if doc.get("type") == "photo" and doc.get("file_id"):
                sent = await client.send_photo(
                    update.chat.id, doc["file_id"], caption=text, reply_markup=markup
                )
            elif doc.get("type") == "video" and doc.get("file_id"):
                sent = await client.send_video(
                    update.chat.id, doc["file_id"], caption=text, reply_markup=markup
                )
            else:
                sent = await client.send_message(
                    update.chat.id, text, reply_markup=markup
                )

            if doc.get("clean_welcome"):
                old_id = doc.get("last_message_id")
                if old_id and old_id != sent.id:
                    try:
                        await client.delete_messages(update.chat.id, old_id)
                    except Exception:
                        pass
                doc["last_message_id"] = sent.id
                await save_welcome(client, update.chat.id, doc)
        except Exception:
            # A broken welcome must never interfere with other bot handlers.
            return
