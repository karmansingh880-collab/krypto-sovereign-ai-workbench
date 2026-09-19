from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from backend.app.core.config import settings

client: AsyncIOMotorClient = AsyncIOMotorClient(settings.MONGO_URL)
db: AsyncIOMotorDatabase = client.get_default_database()


async def ping() -> bool:
    await client.admin.command("ping")
    return True
