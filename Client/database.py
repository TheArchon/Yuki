from datetime import datetime, timezone
from motor.motor_asyncio import AsyncIOMotorClient


class Database:
    """Small MongoDB layer, following BioGuard's centralized DB pattern."""

    def __init__(self, uri: str, name: str):
        if not uri:
            raise RuntimeError("MONGO_DB is not configured.")
        self.client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=10000)
        self.db = self.client[name]
        self.users = self.db.users
        self.groups = self.db.groups
        self.afk = self.db.afk
        self.welcomes = self.db.welcomes
        self.stats = self.db.stats

    @staticmethod
    def now():
        return datetime.now(timezone.utc)

    async def initialize(self):
        await self.client.admin.command("ping")
        await self.users.create_index("user_id", unique=True)
        await self.groups.create_index("chat_id", unique=True)
        await self.afk.create_index("user_id", unique=True)
        await self.welcomes.create_index("chat_id", unique=True)
        await self.stats.create_index("user_id", unique=True)

    async def ensure_user(self, user):
        if not user:
            return
        now = self.now()
        await self.users.update_one(
            {"user_id": user.id},
            {
                "$set": {
                    "username": user.username,
                    "first_name": user.first_name or "",
                    "last_name": user.last_name or "",
                    "last_seen": now,
                },
                "$setOnInsert": {"joined": now},
            },
            upsert=True,
        )

    async def ensure_group(self, chat):
        if not chat:
            return
        await self.groups.update_one(
            {"chat_id": chat.id},
            {
                "$set": {"title": chat.title or "", "last_seen": self.now()},
                "$setOnInsert": {
                    "tag_speed": 3,
                    "welcome_enabled": False,
                    "clean_welcome": False,
                },
            },
            upsert=True,
        )

    async def get_speed(self, chat_id):
        doc = await self.groups.find_one({"chat_id": chat_id}, {"tag_speed": 1})
        try:
            return int(doc.get("tag_speed", 3)) if doc else 3
        except (TypeError, ValueError):
            return 3

    async def set_speed(self, chat_id, seconds):
        await self.groups.update_one(
            {"chat_id": chat_id},
            {"$set": {"tag_speed": int(seconds)}},
            upsert=True,
        )

    async def get_welcome(self, chat_id):
        return await self.welcomes.find_one({"chat_id": chat_id})

    async def set_welcome(self, chat_id, data):
        payload = dict(data)
        payload["chat_id"] = chat_id
        payload["updated_at"] = self.now()
        await self.welcomes.update_one(
            {"chat_id": chat_id}, {"$set": payload}, upsert=True
        )

    async def set_afk(self, user_id, reason):
        now = self.now()
        await self.afk.update_one(
            {"user_id": user_id},
            {"$set": {"reason": reason, "since": now}},
            upsert=True,
        )

    async def clear_afk(self, user_id):
        await self.afk.delete_one({"user_id": user_id})

    async def get_afk(self, user_id):
        return await self.afk.find_one({"user_id": user_id})

    async def increment_stat(self, user_id, field, amount=1):
        allowed = {"messages", "tags", "commands"}
        if field not in allowed:
            raise ValueError(f"Unsupported stat field: {field}")
        await self.stats.update_one(
            {"user_id": user_id}, {"$inc": {field: int(amount)}}, upsert=True
        )

    async def get_stats(self, user_id):
        doc = await self.stats.find_one({"user_id": user_id}) or {}
        return {
            "messages": int(doc.get("messages", 0)),
            "tags": int(doc.get("tags", 0)),
            "commands": int(doc.get("commands", 0)),
        }

    async def get_all_user_ids(self):
        cursor = self.users.find({}, {"user_id": 1})
        docs = await cursor.to_list(length=None)
        return [doc["user_id"] for doc in docs if "user_id" in doc]

    async def get_all_group_ids(self):
        cursor = self.groups.find({}, {"chat_id": 1})
        docs = await cursor.to_list(length=None)
        return [doc["chat_id"] for doc in docs if "chat_id" in doc]
