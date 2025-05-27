from config import Config
from datetime import datetime
import asyncio


class User:
    TABLE = "users"

    @classmethod
    async def register(cls, discord_id: int, nickname: str, so2_id: str):
        loop = asyncio.get_running_loop()

        def db_insert():
            return (
                Config.supabase()
                .table(cls.TABLE)
                .insert(
                    {
                        "discord_id": str(discord_id),
                        "nickname": nickname,
                        "so2_id": so2_id,
                        "points": 200,
                        "kills": 0,
                        "deaths": 0,
                        "wins": 0,
                        "loses": 0,
                        "registered_at": datetime.now().isoformat(),
                    }
                )
                .execute()
            )

        return await loop.run_in_executor(None, db_insert)

    @classmethod
    async def find(cls, discord_id: int):
        loop = asyncio.get_running_loop()

        def db_select():
            response = (
                Config.supabase()
                .table(cls.TABLE)
                .select("*")
                .eq("discord_id", str(discord_id))
                .execute()
            )
            return response.data[0] if response.data else None

        return await loop.run_in_executor(None, db_select)
