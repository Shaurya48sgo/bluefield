import asyncio
from datetime import datetime, timedelta, timezone
from config import Config


class AsyncCursor:
    def __init__(self, items):
        self._items = items

    def sort(self, key, direction=1):
        reverse = direction < 0
        self._items.sort(key=lambda d: d.get(key, 0), reverse=reverse)
        return self

    def limit(self, n):
        self._items = self._items[:n]
        return self

    async def to_list(self, _length):
        return self._items

    def __aiter__(self):
        return self._AsyncIterator(self._items)

    class _AsyncIterator:
        def __init__(self, items):
            self._iter = iter(items)
        def __aiter__(self):
            return self
        async def __anext__(self):
            try:
                return next(self._iter)
            except StopIteration:
                raise StopAsyncIteration


class AsyncCollection:
    def __init__(self, sync_collection):
        self._col = sync_collection

    async def find_one(self, filter=None):
        return await asyncio.to_thread(self._col.find_one, filter)

    async def insert_one(self, doc):
        return await asyncio.to_thread(self._col.insert_one, doc)

    async def update_one(self, filter, update, upsert=False):
        if upsert:
            existing = await self.find_one(filter)
            if existing:
                return await asyncio.to_thread(self._col.update_one, filter, update)
            doc = {}
            doc.update(filter)
            for op in ("$set", "$inc", "$setOnInsert"):
                if op in update:
                    vals = update[op]
                    if op == "$inc":
                        for k, v in vals.items():
                            doc[k] = doc.get(k, 0) + v
                    else:
                        doc.update(vals)
            return await self.insert_one(doc)
        return await asyncio.to_thread(self._col.update_one, filter, update)

    async def find(self, filter=None):
        items = await asyncio.to_thread(lambda: list(self._col.find(filter)))
        return AsyncCursor(items)

    async def delete_one(self, filter):
        return await asyncio.to_thread(self._col.delete_one, filter)

    async def delete_many(self, filter):
        cursor = await self.find(filter)
        for item in await cursor.to_list(None):
            await self.delete_one({"_id": item["_id"]})

    async def count_documents(self, filter):
        return await asyncio.to_thread(self._col.count_documents, filter)

    async def create_index(self, key_or_list, unique=False):
        return await asyncio.to_thread(self._col.create_index, key_or_list, unique=unique)

    async def sort(self, key, direction):
        return self

    async def limit(self, n):
        return self


class Database:
    def __init__(self):
        self._connected = False
        self.db = None
        self.guilds = None
        self.users = None
        self.devs = None
        self.active_items = None
        self.jail_logs = None

    async def connect(self):
        if self._connected:
            return

        try:
            import motor.motor_asyncio
            client = motor.motor_asyncio.AsyncIOMotorClient(
                Config.MONGO_URI,
                serverSelectionTimeoutMS=3000,
            )
            await client.admin.command("ping")
            mongo_db = client[Config.DB_NAME]
            await mongo_db.guilds.create_index("guild_id", unique=True)
            await mongo_db.users.create_index([("guild_id", 1), ("user_id", 1)], unique=True)
            await mongo_db.devs.create_index([("guild_id", 1), ("user_id", 1)], unique=True)
            await mongo_db.active_items.create_index([("guild_id", 1), ("user_id", 1)])
            await mongo_db.active_items.create_index("expires")
            await mongo_db.jail_logs.create_index([("guild_id", 1), ("timestamp", -1)])

            self.db = mongo_db
            self.guilds = mongo_db.guilds
            self.users = mongo_db.users
            self.devs = mongo_db.devs
            self.active_items = mongo_db.active_items
            self.jail_logs = mongo_db.jail_logs
            print(f"Connected to MongoDB: {Config.MONGO_URI}")
        except Exception as e:
            print(f"MongoDB failed ({e}), using local mongita storage")
            from mongita import MongitaClientDisk
            sync_client = MongitaClientDisk()
            sync_db = sync_client[Config.DB_NAME]

            self.db = type("LocalDB", (), {})()
            for name in ["guilds", "users", "devs", "active_items", "jail_logs"]:
                setattr(self.db, name, AsyncCollection(sync_db[name]))
                setattr(self, name, getattr(self.db, name))

        self._connected = True

    async def get_guild_config(self, guild_id):
        config = await self.guilds.find_one({"guild_id": guild_id})
        if not config:
            config = {
                "guild_id": guild_id,
                "silence_role": None,
                "log_channel": None,
                "reverse_immunity_roles": [],
                "reverse_immunity_messages": {},
                "full_immunity_roles": [],
                "full_immunity_messages": {},
                "role_order": [],
                "setup_complete": False,
                "heckers": [],
            }
            await self.guilds.insert_one(config)
        return config

    async def update_guild_config(self, guild_id, updates):
        await self.guilds.update_one(
            {"guild_id": guild_id},
            {"$set": updates},
            upsert=True,
        )

    async def get_user(self, guild_id, user_id):
        user = await self.users.find_one({"guild_id": guild_id, "user_id": user_id})
        if not user:
            user = {
                "guild_id": guild_id,
                "user_id": user_id,
                "balance": Config.STARTING_BALANCE,
                "inventory": {},
                "total_jailed": 0,
                "total_jails_done": 0,
            }
            await self.users.insert_one(user)
        return user

    async def update_user(self, guild_id, user_id, updates):
        await self.users.update_one(
            {"guild_id": guild_id, "user_id": user_id},
            {"$set": updates},
            upsert=True,
        )

    async def add_to_inventory(self, guild_id, user_id, item_id, quantity=1):
        await self.users.update_one(
            {"guild_id": guild_id, "user_id": user_id},
            {"$inc": {f"inventory.{item_id}": quantity}},
            upsert=True,
        )

    async def remove_from_inventory(self, guild_id, user_id, item_id, quantity=1):
        user = await self.get_user(guild_id, user_id)
        current = user.get("inventory", {}).get(item_id, 0)
        if current >= quantity:
            await self.users.update_one(
                {"guild_id": guild_id, "user_id": user_id},
                {"$inc": {f"inventory.{item_id}": -quantity}},
            )
            return True
        return False

    async def is_dev(self, guild_id, user_id):
        if Config.OWNER_ID and user_id == Config.OWNER_ID:
            return True
        dev = await self.devs.find_one({"guild_id": guild_id, "user_id": user_id})
        return dev is not None

    async def add_dev(self, guild_id, user_id):
        await self.devs.update_one(
            {"guild_id": guild_id, "user_id": user_id},
            {"$set": {"guild_id": guild_id, "user_id": user_id}},
            upsert=True,
        )

    async def remove_dev(self, guild_id, user_id):
        await self.devs.delete_one({"guild_id": guild_id, "user_id": user_id})

    async def get_devs(self, guild_id):
        return await self.devs.find({"guild_id": guild_id}).to_list(None)

    async def activate_item(self, guild_id, user_id, item_type, duration_hours=24):
        expires = datetime.now(timezone.utc) + timedelta(hours=duration_hours)
        await self.active_items.update_one(
            {"guild_id": guild_id, "user_id": user_id, "type": item_type},
            {"$set": {"expires": expires, "activated": datetime.now(timezone.utc)}},
            upsert=True,
        )

    async def get_active_items(self, guild_id, user_id):
        now = datetime.now(timezone.utc)
        return await self.active_items.find({
            "guild_id": guild_id,
            "user_id": user_id,
            "expires": {"$gt": now},
        }).to_list(None)

    async def get_all_active_items(self, guild_id):
        now = datetime.now(timezone.utc)
        return await self.active_items.find({
            "guild_id": guild_id,
            "expires": {"$gt": now},
        }).to_list(None)

    async def consume_divine_eye(self, guild_id, user_id):
        return await self.remove_from_inventory(guild_id, user_id, "divine_eye")

    async def log_jail(self, guild_id, data):
        await self.jail_logs.insert_one(data)

    async def add_balance(self, guild_id, user_id, amount):
        await self.users.update_one(
            {"guild_id": guild_id, "user_id": user_id},
            {"$inc": {"balance": amount}},
            upsert=True,
        )

    async def is_hecker(self, guild_id, user_id):
        config = await self.get_guild_config(guild_id)
        return user_id in config.get("heckers", [])

    async def add_hecker(self, guild_id, user_id):
        config = await self.get_guild_config(guild_id)
        heckers = config.get("heckers", [])
        if user_id not in heckers:
            heckers.append(user_id)
        await self.update_guild_config(guild_id, {"heckers": heckers})

    async def remove_hecker(self, guild_id, user_id):
        config = await self.get_guild_config(guild_id)
        heckers = config.get("heckers", [])
        if user_id in heckers:
            heckers.remove(user_id)
        await self.update_guild_config(guild_id, {"heckers": heckers})

    async def cleanup_expired_items(self):
        now = datetime.now(timezone.utc)
        await self.active_items.delete_many({"expires": {"$lte": now}})


db = Database()
