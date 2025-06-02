from config import Config
import json
import asyncio
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class LobbyState:
    TABLE = "lobby_states"

    @classmethod
    async def save_lobby(
        cls,
        guild_id: int,
        category_id: int,
        lobby_data: dict,
        locked_lobby: bool = False,
    ):
        """Save lobby state to database"""
        loop = asyncio.get_running_loop()

        # Serialize lobby data for storage
        serialized_data = {
            "guild_id": str(guild_id),
            "category_id": str(category_id),
            "lobby_number": lobby_data.get("lobby_number", 1),
            "voice_channel_id": str(lobby_data["voice"].id)
            if lobby_data.get("voice")
            else None,
            "text_channel_id": str(lobby_data["text"].id)
            if lobby_data.get("text")
            else None,
            "phase": lobby_data.get("phase", 1),
            "player_ids": json.dumps(
                [str(p.id) for p in lobby_data.get("players", set())]
            ),
            "ct_team_ids": json.dumps(
                [str(p.id) for p in lobby_data.get("ct_team", [])]
            ),
            "t_team_ids": json.dumps([str(p.id) for p in lobby_data.get("t_team", [])]),
            "map_pool": json.dumps(lobby_data.get("map_pool", [])),
            "match_id": lobby_data.get("match_id"),
            "message_id": str(lobby_data["message"].id)
            if lobby_data.get("message")
            else None,
            "ct_voice_id": str(lobby_data["ct_voice"].id)
            if lobby_data.get("ct_voice")
            else None,
            "t_voice_id": str(lobby_data["t_voice"].id)
            if lobby_data.get("t_voice")
            else None,
            "required_role_id": str(lobby_data.get("required_role_id"))
            if lobby_data.get("required_role_id")
            else None,
            "required_role_name": lobby_data.get("required_role_name"),
            "locked_lobby": locked_lobby,
            "updated_at": datetime.now().isoformat(),
        }

        def db_upsert():
            try:
                # Try to update first, if no rows affected then insert
                result = (
                    Config.supabase()
                    .table(cls.TABLE)
                    .upsert(serialized_data, on_conflict="category_id")
                    .execute()
                )
                return result
            except Exception as e:
                logger.error(f"Failed to save lobby state: {e}")
                raise

        return await loop.run_in_executor(None, db_upsert)

    @classmethod
    async def load_lobbies(cls, guild_id: int):
        """Load all lobby states for a guild from database"""
        loop = asyncio.get_running_loop()

        def db_select():
            try:
                response = (
                    Config.supabase()
                    .table(cls.TABLE)
                    .select("*")
                    .eq("guild_id", str(guild_id))
                    .execute()
                )
                return response.data if response.data else []
            except Exception as e:
                logger.error(f"Failed to load lobby states: {e}")
                return []

        return await loop.run_in_executor(None, db_select)

    @classmethod
    async def delete_lobby(cls, category_id: int):
        """Delete lobby state from database"""
        loop = asyncio.get_running_loop()

        def db_delete():
            try:
                result = (
                    Config.supabase()
                    .table(cls.TABLE)
                    .delete()
                    .eq("category_id", str(category_id))
                    .execute()
                )
                return result
            except Exception as e:
                logger.error(f"Failed to delete lobby state: {e}")
                raise

        return await loop.run_in_executor(None, db_delete)

    @classmethod
    async def cleanup_old_lobbies(cls, days: int = 7):
        """Clean up lobby states older than specified days"""
        loop = asyncio.get_running_loop()

        def db_cleanup():
            try:
                from datetime import datetime, timedelta

                cutoff_date = (datetime.now() - timedelta(days=days)).isoformat()
                result = (
                    Config.supabase()
                    .table(cls.TABLE)
                    .delete()
                    .lt("updated_at", cutoff_date)
                    .execute()
                )
                return result
            except Exception as e:
                logger.error(f"Failed to cleanup old lobby states: {e}")
                raise

        return await loop.run_in_executor(None, db_cleanup)

    @classmethod
    async def update_lobby_lock_status(cls, guild_id: int, locked: bool):
        """Update the lock status for all lobbies in a guild"""
        loop = asyncio.get_running_loop()

        def db_update():
            try:
                result = (
                    Config.supabase()
                    .table(cls.TABLE)
                    .update({"locked_lobby": locked})
                    .eq("guild_id", str(guild_id))
                    .execute()
                )
                return result
            except Exception as e:
                logger.error(f"Failed to update lobby lock status: {e}")
                raise

        return await loop.run_in_executor(None, db_update)

    @classmethod
    async def get_lobby_lock_status(cls, guild_id: int):
        """Get the lock status for lobbies in a guild (returns True if any lobby is locked)"""
        loop = asyncio.get_running_loop()

        def db_select():
            try:
                response = (
                    Config.supabase()
                    .table(cls.TABLE)
                    .select("locked_lobby")
                    .eq("guild_id", str(guild_id))
                    .limit(1)
                    .execute()
                )
                if response.data and len(response.data) > 0:
                    return response.data[0].get("locked_lobby", False)
                return False
            except Exception as e:
                logger.error(f"Failed to get lobby lock status: {e}")
                return False

        return await loop.run_in_executor(None, db_select)
