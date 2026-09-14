from pyrogram import Client, filters
from pyrogram.enums import ButtonStyle, ChatType
from pyrogram.types import InlineKeyboardMarkup

import config
from Client.premium import premium_button, premium_emoji


def start_text(first_name="there"):
    return (
        f"{premium_emoji('home', '👋')} <b>Hello {first_name}!</b>\n\n"
        f"I am <b>Yuki</b>, a clean and reliable Telegram group utility bot.\n\n"
        f"{premium_emoji('tag', '💫')} I can help you tag members, manage AFK status, "
        f"configure welcomes, and keep your bot updated.\n\n"
        f"{premium_emoji('help', '💡')} Use the buttons below to explore my features."
    )


def private_menu(bot_username):
    rows = []
    if bot_username:
        rows.append([
            premium_button(
                "Add me", "add", ButtonStyle.SUCCESS,
                url=f"https://t.me/{bot_username}?startgroup=true",
            )
        ])
    if config.UPDATES_URL:
        updates_button = premium_button("Updates", "updates", ButtonStyle.DANGER, url=config.UPDATES_URL)
    else:
        updates_button = premium_button("Updates", "updates", ButtonStyle.DANGER, callback_data="yuki_updates")
    if config.SUPPORT_URL:
        support_button = premium_button("Support", "support", ButtonStyle.PRIMARY, url=config.SUPPORT_URL)
    else:
        support_button = premium_button("Support", "support", ButtonStyle.PRIMARY, callback_data="yuki_support")
    rows.append([updates_button, support_button])
    rows.append([
        premium_button("User Guide", "help", ButtonStyle.PRIMARY, callback_data="help:0"),
        premium_button("About Bot", "source", ButtonStyle.DANGER, callback_data="yuki_about"),
    ])
    rows.append([
        premium_button("Help", "help", ButtonStyle.SUCCESS, callback_data="help:0")
    ])
    return InlineKeyboardMarkup(rows)


def back_menu():
    return InlineKeyboardMarkup([
        [premium_button("Back", "back", ButtonStyle.DANGER, callback_data="yuki_home")]
    ])


@Client.on_message(filters.command("start"))
async def start_handler(client, message):
    if message.from_user:
        await client.db.ensure_user(message.from_user)

    me = await client.get_me()
    config.BOT_USERNAME = me.username or config.BOT_USERNAME

    if message.chat and message.chat.type == ChatType.PRIVATE:
        await message.reply_text(
            start_text(message.from_user.first_name if message.from_user else "there"),
            reply_markup=private_menu(me.username),
        )
        return

    # BioGuard-style group start: keep the group message short and take the
    # full introduction to private chat.
    if message.chat:
        await client.db.ensure_group(message.chat)
    if me.username:
        group_button = premium_button(
            "Start in Private", "add", ButtonStyle.SUCCESS,
            url=f"https://t.me/{me.username}?start=start",
        )
    else:
        group_button = premium_button(
            "Start in Private", "add", ButtonStyle.SUCCESS,
            callback_data="yuki_home",
        )
    keyboard = InlineKeyboardMarkup([[group_button]])
    await message.reply_text(
        f"{premium_emoji('home', '👋')} <b>Welcome!</b>\n\n"
        f"Open my private chat to see the full Yuki guide.",
        reply_markup=keyboard,
    )


@Client.on_callback_query(filters.regex(r"^yuki_home$"))
async def home(client, query):
    await query.answer()
    me = await client.get_me()
    config.BOT_USERNAME = me.username or config.BOT_USERNAME
    await query.message.edit_text(
        start_text(query.from_user.first_name if query.from_user else "there"),
        reply_markup=private_menu(me.username),
    )


@Client.on_callback_query(filters.regex(r"^yuki_about$"))
async def about(client, query):
    await query.answer()
    text = (
        f"{premium_emoji('source', '🛡️')} <b>About Yuki</b>\n\n"
        f"Yuki is a modular Telegram group utility bot focused on a clean interface, "
        f"reliable group tools, persistent settings, and safe updates.\n\n"
        f"{premium_emoji('help', '📚')} <b>Use Help</b> to see every currently available command."
    )
    await query.message.edit_text(text, reply_markup=back_menu())


@Client.on_callback_query(filters.regex(r"^yuki_support$"))
async def support(client, query):
    await query.answer(
        "Support is not configured yet." if not config.SUPPORT_URL else "Open the Support button.",
        show_alert=True,
    )


@Client.on_callback_query(filters.regex(r"^yuki_updates$"))
async def updates(client, query):
    await query.answer()
    text = (
        f"{premium_emoji('updates', '📢')} <b>Updates</b>\n\n"
        f"Yuki updates and announcements will appear here.\n\n"
        f"{premium_emoji('help', '💡')} The updater can check GitHub for a newer version."
    )
    await query.message.edit_text(text, reply_markup=back_menu())
