from config import Config
from datetime import datetime
import asyncio
import logging

logger = logging.getLogger(__name__)


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

    @classmethod
    async def get_nickname_by_discord_id(cls, discord_id: int):
        """Get user's nickname by discord_id"""
        loop = asyncio.get_running_loop()

        def db_select():
            try:
                response = (
                    Config.supabase()
                    .table(cls.TABLE)
                    .select("nickname")
                    .eq("discord_id", str(discord_id))
                    .execute()
                )
                return response.data[0]["nickname"] if response.data else None
            except Exception as e:
                logger.error(f"Failed to get nickname for discord_id {discord_id}: {e}")
                return None

        return await loop.run_in_executor(None, db_select)

    @classmethod
    async def get_nicknames_by_discord_ids(cls, discord_ids: list):
        """Get multiple users' nicknames by their discord_ids"""
        loop = asyncio.get_running_loop()

        def db_select():
            try:
                response = (
                    Config.supabase()
                    .table(cls.TABLE)
                    .select("discord_id, nickname")
                    .in_("discord_id", [str(id) for id in discord_ids])
                    .execute()
                )
                # Convert to dict for easy lookup
                return (
                    {item["discord_id"]: item["nickname"] for item in response.data}
                    if response.data
                    else {}
                )
            except Exception as e:
                logger.error(
                    f"Failed to get nicknames for discord_ids {discord_ids}: {e}"
                )
                return {}

        return await loop.run_in_executor(None, db_select)
