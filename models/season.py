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
        """Get all users stats from all league tables for archiving"""
        loop = asyncio.get_running_loop()

        def db_get_leagues():
            # Get all available leagues
            try:
                response = (
                    Config.supabase()
                    .table("leagues")
                    .select("league_name, original_league_name")
                    .execute()
                )
                return response.data if response.data else []
            except Exception as e:
                logger.error(f"Error getting leagues: {e}")
                return []

        def db_get_league_stats(league_name):
            # Get all player stats from a specific league table
            try:
                response = (
                    Config.supabase()
                    .table(league_name)
                    .select("discord_id, nickname, points, kills, deaths, wins, loses")
                    .execute()
                )
                return response.data if response.data else []
            except Exception as e:
                logger.error(f"Error getting stats from league {league_name}: {e}")
                return []

        # Get all leagues
        leagues = await loop.run_in_executor(None, db_get_leagues)

        # Structure: { "league_name": [player_stats], "another_league": [player_stats] }
        all_stats = {}

        for league in leagues:
            league_name = league["league_name"]
            original_name = league.get("original_league_name", league_name)

            # Get stats for this league
            league_stats = await loop.run_in_executor(
                None, db_get_league_stats, league_name
            )
            # Store with original league name as key for better readability
            all_stats[original_name] = {
                "table_name": league_name,
                "players": league_stats,
            }

        return all_stats

    @classmethod
    async def reset_all_players_stats(cls):
        """Reset all players' stats to default values in all league tables when season ends"""
        loop = asyncio.get_running_loop()

        def db_get_leagues():
            # Get all available leagues
            try:
                response = (
                    Config.supabase().table("leagues").select("league_name").execute()
                )
                return response.data if response.data else []
            except Exception as e:
                logger.error(f"Error getting leagues: {e}")
                return []

        def db_reset_league_stats(league_name):
            try:
                # Reset all players' stats in this league table
                response = (
                    Config.supabase()
                    .table(league_name)
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
                logger.info(
                    f"Reset stats for {len(response.data)} players in league {league_name}"
                )
                return len(response.data) if response.data else 0
            except Exception as e:
                logger.error(
                    f"Error resetting player stats in league {league_name}: {e}"
                )
                return 0

        # Get all leagues
        leagues = await loop.run_in_executor(None, db_get_leagues)

        total_reset = 0
        for league in leagues:
            league_name = league["league_name"]
            reset_count = await loop.run_in_executor(
                None, db_reset_league_stats, league_name
            )
            total_reset += reset_count

        logger.info(f"Total stats reset across all leagues: {total_reset}")
        return total_reset

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
