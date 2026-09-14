from pyrogram.types import InlineKeyboardButton

# Replace these IDs with your own Telegram custom emoji IDs when ready.
# The fallback character is still shown if Telegram cannot render a custom emoji.
PREMIUM_EMOJIS = {
    "default": "5275969776668134187",
    "home": "5413694143601842851",
    "help": "6115973347206501819",
    "tag": "5767288287001580715",
    "user": "6021618194228187816",
    "welcome": "5463122435425448565",
    "update": "6053393539105038045",
    "back": "6030657343744644592",
    "prev": "6023773095284707791",
    "next": "6170455814810112778",
    "close": "5258389041006518073",
    "cancel": "6041720006973067267",
    "confirm": "5463122435425448565",
    "speed": "5318840353510408444",
    "id": "6021618194228187816",
    "ping": "5318840353510408444",
    "afk": "6100546468924364734",
    "stats": "6100546468924364734",
    "add": "5411370291416820975",
    "about": "6294287714887933094",
}

def premium_emoji(key="default", fallback="✨"):
    emoji_id = PREMIUM_EMOJIS.get(key) or PREMIUM_EMOJIS["default"]
    return f"<tg-emoji emoji-id='{emoji_id}'>{fallback}</tg-emoji>"

def premium_button(text, key="default", style=None, **kwargs):
    emoji_id = PREMIUM_EMOJIS.get(key) or PREMIUM_EMOJIS["default"]
    kwargs["icon_custom_emoji_id"] = emoji_id
    if style is not None:
        kwargs["style"] = style
    return InlineKeyboardButton(text=text, **kwargs)
