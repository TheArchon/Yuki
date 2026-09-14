from pyrogram import Client, filters
from pyrogram.enums import ButtonStyle
from pyrogram.types import InlineKeyboardMarkup

from Client.premium import premium_button, premium_emoji

PAGES = [
    ("Tag System", "tag", [
        ("/utag", "Tag all members. Admin only."),
        ("/tagall", "Alias of /utag. Admin only."),
        ("/atag", "Tag only administrators."),
        ("/admin", "Alias of /atag."),
        ("/cancel", "Stop the active tagging task. Admin only."),
        ("/speed", "Open the tagging speed panel. Admin only."),
    ]),
    ("User Tools", "user", [
        ("/id", "Show your ID or the replied user's ID."),
        ("/ping", "Show the bot response speed."),
        ("/afk [reason]", "Set your AFK status."),
        ("/user_stats", "Show your personal statistics."),
    ]),
    ("Welcome System", "welcome", [
        ("/setwelcome", "Set text or a replied photo/video welcome."),
        ("/welcome on/off", "Enable or disable welcomes."),
        ("/cleanwelcome on/off", "Delete the previous welcome when a new member joins."),
    ]),
]


def page_text(page):
    title, icon, items = PAGES[page]
    lines = [
        f"{premium_emoji('help', '🧊')} <b>Help Center</b> "
        f"<code>{page + 1}/{len(PAGES)}</code>",
        "",
        f"{premium_emoji(icon, '📚')} <b>{title}</b>",
        "",
    ]
    lines.extend(f"<b>{command}</b> — {description}" for command, description in items)
    return "\n".join(lines)


def nav(page):
    controls = []
    if page > 0:
        controls.append(
            premium_button("Prev", "back", ButtonStyle.PRIMARY, callback_data=f"help:{page - 1}")
        )
    controls.append(
        premium_button(
            f"{page + 1}/{len(PAGES)}", "queue", ButtonStyle.DANGER,
            callback_data=f"help:{page}",
        )
    )
    if page < len(PAGES) - 1:
        controls.append(
            premium_button("Next", "skip", ButtonStyle.PRIMARY, callback_data=f"help:{page + 1}")
        )

    return InlineKeyboardMarkup([
        controls,
        [premium_button("Home", "home", ButtonStyle.SUCCESS, callback_data="yuki_home")],
    ])


@Client.on_message(filters.command("help"))
async def help_handler(client, message):
    if message.from_user:
        await client.db.ensure_user(message.from_user)
    await message.reply_text(page_text(0), reply_markup=nav(0))


@Client.on_callback_query(filters.regex(r"^help:(\d+)$"))
async def help_callback(client, query):
    page = int(query.matches[0].group(1))
    if not 0 <= page < len(PAGES):
        await query.answer("Invalid help page.", show_alert=True)
        return
    await query.answer()
    await query.message.edit_text(page_text(page), reply_markup=nav(page))
