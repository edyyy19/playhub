from config import Config
from datetime import datetime
import asyncio
import logging

logger = logging.getLogger(__name__)


class User:
    TABLE = "users"

    @staticmethod
    def format_registration_date(registered_at: str) -> str:
        """
        Format registration date from ISO format to DD/MM/YYYY format
        Args:
            registered_at: ISO format date string (e.g., '2025-01-19T10:30:00.000Z')
        Returns:
            Formatted date string (e.g., '19/01/2025')
        """
        try:
            if not registered_at:
                return "Unknown"
            
            # Parse ISO format datetime
            # Handle both with and without microseconds and timezone
            if registered_at.endswith('Z'):
                registered_at = registered_at[:-1] + '+00:00'
            
            # Try parsing with different formats
            try:
                dt = datetime.fromisoformat(registered_at)
            except ValueError:
                # Fallback: try parsing just the date part
                date_part = registered_at[:10]  # Take YYYY-MM-DD part
                dt = datetime.strptime(date_part, "%Y-%m-%d")
            
            return dt.strftime("%d/%m/%Y")
            
        except Exception as e:
            logger.error(f"Error formatting registration date '{registered_at}': {e}")
            return "Unknown"

    @staticmethod
    def format_current_datetime() -> str:
        """
        Format current datetime to HH:MM | DD/MM/YYYY format
        Returns:
            Formatted datetime string (e.g., '14:30 | 19/01/2025')
        """
        return datetime.now().strftime("%H:%M | %d/%m/%Y")

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
    async def find_multiple(cls, discord_ids: list):
        """Batch find multiple users by discord IDs"""
        if not discord_ids:
            return {}
            
        loop = asyncio.get_running_loop()

        def db_select():
            # Convert all IDs to strings for consistency
            str_ids = [str(discord_id) for discord_id in discord_ids]
            response = (
                Config.supabase()
                .table(cls.TABLE)
                .select("*")
                .in_("discord_id", str_ids)
                .execute()
            )
            # Create a dictionary mapping discord_id -> user data
            result = {}
            for user in response.data:
                result[int(user["discord_id"])] = user
            return result

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
