import os
from dotenv import load_dotenv

load_dotenv()

def env_int(name, default=None):
    value = os.getenv(name)
    try:
        return int(value) if value is not None and value.strip() else default
    except ValueError:
        return default

def env_list(name):
    value = os.getenv(name, "")
    result = []
    for item in value.replace("[", "").replace("]", "").split(","):
        item = item.strip()
        if item:
            try:
                result.append(int(item))
            except ValueError:
                pass
    return result

API_ID = env_int("API_ID")
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = env_int("OWNER_ID")
SUDO_USERS = env_list("SUDO_USERS")
if OWNER_ID and OWNER_ID not in SUDO_USERS:
    SUDO_USERS.insert(0, OWNER_ID)

MONGO_DB = os.getenv("MONGO_DB")
MONGODB_DB_NAME = os.getenv("MONGODB_DB_NAME", "Yuki")
LOGGER_GROUP = env_int("LOGGER_GROUP")
GITHUB_REPO = os.getenv("GITHUB_REPO", "TheArchon/Yuki")
GITHUB_BRANCH = os.getenv("GITHUB_BRANCH", "main")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
UPDATE_CHECK_INTERVAL = env_int("UPDATE_CHECK_INTERVAL", 1800)
BOT_USERNAME = os.getenv("BOT_USERNAME", "")
SUPPORT_URL = os.getenv("SUPPORT_URL", "")
UPDATES_URL = os.getenv("UPDATES_URL", "")

DEFAULT_TAG_SPEED = 3
MIN_TAG_SPEED = 1
MAX_TAG_SPEED = 10
SPEED_PRESETS = {"turbo": 1, "fast": 2, "normal": 3, "slow": 5}
