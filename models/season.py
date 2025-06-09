from config import Config
from datetime import datetime, timezone
import asyncio
import json
import logging

logger = logging.getLogger(__name__)


class Season:
    TABLE = "seasons"

    @classmethod
    async def create_new_season(cls):
        """Create a new season"""
        loop = asyncio.get_running_loop()

        def db_insert():
            payload = {
                "started_at": datetime.now(timezone.utc).isoformat(),
                "on_going": True,
                "ended_at": None,
                "matches_archieve": None,
                "players_stats": None,
            }
            return Config.supabase().table(cls.TABLE).insert(payload).execute()

        return await loop.run_in_executor(None, db_insert)

    @classmethod
    async def get_ongoing_season(cls):
        """Get the current ongoing season"""
        loop = asyncio.get_running_loop()

        def db_select():
            response = (
                Config.supabase()
                .table(cls.TABLE)
                .select("*")
                .eq("on_going", True)
                .execute()
            )
            return response.data[0] if response.data else None

        return await loop.run_in_executor(None, db_select)

    @classmethod
    async def end_season(
        cls, season_id: int, matches_archive: list, players_stats: list
    ):
        """End a season with archived data"""
        loop = asyncio.get_running_loop()

        def db_update():
            return (
                Config.supabase()
                .table(cls.TABLE)
                .update(
                    {
                        "ended_at": datetime.now(timezone.utc).isoformat(),
                        "on_going": False,
                        "matches_archieve": json.dumps(matches_archive),
                        "players_stats": json.dumps(players_stats),
                    }
                )
                .eq("season_id", season_id)
                .execute()
            )

        return await loop.run_in_executor(None, db_update)

    @classmethod
    async def is_season_active(cls) -> bool:
        """Check if there's an active season"""
        season = await cls.get_ongoing_season()
        return season is not None

    @classmethod
    async def clear_matches_table(cls):
        """Delete all matches and reset sequence"""
        loop = asyncio.get_running_loop()

        def db_operations():
            try:
                # Delete all matches
                Config.supabase().table("matches").delete().neq("match_id", 0).execute()

                # Reset the sequence using RPC function
                Config.supabase().rpc("reset_match_sequence").execute()
                logger.info("All matches deleted and sequence reset using RPC function")

                return True
            except Exception as e:
                logger.error(
                    f"Error clearing matches table and resetting sequence: {e}"
                )
                raise

        return await loop.run_in_executor(None, db_operations)

    @classmethod
    async def get_all_matches(cls):
        """Get all matches for archiving"""
        loop = asyncio.get_running_loop()

        def db_select():
            response = Config.supabase().table("matches").select("*").execute()
            return response.data

        return await loop.run_in_executor(None, db_select)

    @classmethod
    async def get_all_users_stats(cls):
        """Get all users stats for archiving"""
        loop = asyncio.get_running_loop()

        def db_select():
            response = (
                Config.supabase()
                .table("users")
                .select(
                    "discord_id, nickname, so2_id, points, kills, deaths, wins, loses"
                )
                .execute()
            )
            return response.data

        return await loop.run_in_executor(None, db_select)

    @classmethod
    async def reset_all_players_stats(cls):
        """Reset all players' stats to default values when season ends"""
        loop = asyncio.get_running_loop()

        def db_update():
            try:
                # Reset all players' stats to default values
                response = (
                    Config.supabase()
                    .table("users")
                    .update(
                        {
                            "points": 200,
                            "kills": 0,
                            "deaths": 0,
                            "wins": 0,
                            "loses": 0,
                        }
                    )
                    .neq("discord_id", 0)  # Update all users
                    .execute()
                )
                logger.info(f"Reset stats for {len(response.data)} players")
                return response.data
            except Exception as e:
                logger.error(f"Error resetting player stats: {e}")
                raise

        return await loop.run_in_executor(None, db_update)

    @classmethod
    async def get_last_season_id(cls):
        """Get the ID of the most recent season"""
        loop = asyncio.get_running_loop()

        def db_select():
            response = (
                Config.supabase()
                .table(cls.TABLE)
                .select("season_id")
                .order("season_id", desc=True)
                .limit(1)
                .execute()
            )
            return response.data[0]["season_id"] if response.data else 0

        return await loop.run_in_executor(None, db_select)

    @classmethod
    async def get_completed_matches_count(cls):
        """Get the count of completed matches"""
        loop = asyncio.get_running_loop()

        def db_count():
            response = (
                Config.supabase()
                .table("matches")
                .select("match_id", count="exact")
                .eq("results_submitted", True)
                .not_.is_("ended_at", None)
                .execute()
            )
            return response.count if hasattr(response, "count") else 0

        return await loop.run_in_executor(None, db_count)
