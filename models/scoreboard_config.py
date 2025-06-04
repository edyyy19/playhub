import asyncio
import logging
from datetime import datetime
from config import Config

logger = logging.getLogger(__name__)


class ScoreboardConfig:
    """Model for managing scoreboard channel configurations"""

    @classmethod
    async def set_config(
        cls,
        guild_id: int,
        submission_channel_id: int,
        results_channel_id: int,
        message_id: int = None,
    ):
        """Set or update scoreboard configuration for a guild"""
        loop = asyncio.get_running_loop()

        def db_operation():
            try:
                config_data = {
                    "guild_id": guild_id,
                    "submission_channel_id": submission_channel_id,
                    "results_channel_id": results_channel_id,
                    "message_id": message_id,
                    "updated_at": datetime.now().isoformat(),
                }

                # Use upsert to insert or update
                response = (
                    Config.supabase()
                    .table("scoreboard_config")
                    .upsert(config_data)
                    .execute()
                )

                return response.data[0] if response.data else None

            except Exception as e:
                logger.error(f"Error setting scoreboard config: {e}")
                return None

        return await loop.run_in_executor(None, db_operation)

    @classmethod
    async def get_config(cls, guild_id: int):
        """Get scoreboard configuration for a specific guild"""
        loop = asyncio.get_running_loop()

        def db_operation():
            try:
                response = (
                    Config.supabase()
                    .table("scoreboard_config")
                    .select("*")
                    .eq("guild_id", guild_id)
                    .execute()
                )

                return response.data[0] if response.data else None

            except Exception as e:
                logger.error(f"Error getting scoreboard config: {e}")
                return None

        return await loop.run_in_executor(None, db_operation)

    @classmethod
    async def get_all_configs(cls):
        """Get all scoreboard configurations"""
        loop = asyncio.get_running_loop()

        def db_operation():
            try:
                response = (
                    Config.supabase().table("scoreboard_config").select("*").execute()
                )

                return response.data if response.data else []

            except Exception as e:
                logger.error(f"Error getting all scoreboard configs: {e}")
                return []

        return await loop.run_in_executor(None, db_operation)

    @classmethod
    async def delete_config(cls, guild_id: int):
        """Delete scoreboard configuration for a guild"""
        loop = asyncio.get_running_loop()

        def db_operation():
            try:
                response = (
                    Config.supabase()
                    .table("scoreboard_config")
                    .delete()
                    .eq("guild_id", guild_id)
                    .execute()
                )

                return True

            except Exception as e:
                logger.error(f"Error deleting scoreboard config: {e}")
                return False

        return await loop.run_in_executor(None, db_operation)
