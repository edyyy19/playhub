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

    @classmethod
    async def update_profile(cls, discord_id: int, nickname: str, so2_id: str):
        """Update user's nickname and Standoff 2 ID"""
        loop = asyncio.get_running_loop()

        def db_update():
            return (
                Config.supabase()
                .table(cls.TABLE)
                .update({"nickname": nickname, "so2_id": so2_id})
                .eq("discord_id", str(discord_id))
                .execute()
            )

        return await loop.run_in_executor(None, db_update)

    @classmethod
    async def find_by_nickname(cls, nickname: str, exclude_discord_id: int = None):
        """Find user by nickname, optionally excluding a specific discord_id"""
        loop = asyncio.get_running_loop()

        def db_select():
            query = (
                Config.supabase().table(cls.TABLE).select("*").eq("nickname", nickname)
            )

            if exclude_discord_id:
                query = query.neq("discord_id", str(exclude_discord_id))

            response = query.execute()
            return response.data[0] if response.data else None

        return await loop.run_in_executor(None, db_select)

    @classmethod
    async def find_by_so2_id(cls, so2_id: str, exclude_discord_id: int = None):
        """Find user by SO2 ID, optionally excluding a specific discord_id"""
        loop = asyncio.get_running_loop()

        def db_select():
            query = Config.supabase().table(cls.TABLE).select("*").eq("so2_id", so2_id)

            if exclude_discord_id:
                query = query.neq("discord_id", str(exclude_discord_id))

            response = query.execute()
            return response.data[0] if response.data else None

        return await loop.run_in_executor(None, db_select)
