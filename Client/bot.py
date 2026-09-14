import asyncio
import logging
from pathlib import Path

from pyrogram import Client

import config
from .database import Database

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("Yuki")


class YukiBot(Client):
    def __init__(self):
        root = Path(__file__).resolve().parent.parent
        super().__init__(
            name="Yuki",
            api_id=config.API_ID,
            api_hash=config.API_HASH,
            bot_token=config.BOT_TOKEN,
            plugins={"root": str(root / "plugins")},
            workdir=str(root),
        )
        self.db = Database(config.MONGO_DB, config.MONGODB_DB_NAME)
        self._update_task = None

    async def start(self, *args, **kwargs):
        await super().start(*args, **kwargs)
        try:
            await self.db.initialize()
            me = await self.get_me()
            config.BOT_USERNAME = me.username or config.BOT_USERNAME
            logger.info("Started as @%s", me.username or "unknown")

            # Same cache prefill idea as BioGuard, with Yuki's extra state.
            from .cache import USER_IDS_CACHE, GROUP_IDS_CACHE
            USER_IDS_CACHE.update(await self.db.get_all_user_ids())
            GROUP_IDS_CACHE.update(await self.db.get_all_group_ids())

            from plugins.updater import auto_update_checker
            self._update_task = asyncio.create_task(auto_update_checker(self))

            if config.LOGGER_GROUP:
                try:
                    await self.send_message(
                        config.LOGGER_GROUP,
                        "<tg-emoji emoji-id='5275969776668134187'>⚡</tg-emoji> "
                        "<b>Yuki started successfully.</b>",
                    )
                except Exception as exc:
                    logger.warning("Startup log failed: %s", exc)
        except Exception:
            # Do not leave a half-started bot running with a broken database.
            await super().stop()
            raise

    async def stop(self, *args, **kwargs):
        if self._update_task:
            self._update_task.cancel()
            try:
                await self._update_task
            except asyncio.CancelledError:
                pass
        try:
            self.db.client.close()
        except Exception:
            pass
        await super().stop(*args, **kwargs)
