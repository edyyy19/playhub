from config import Config
import asyncio
import logging

logger = logging.getLogger(__name__)


class Stats:
    """Model for handling user statistics in league tables"""

    @classmethod
    async def update_stats(
        cls,
        discord_id: int,
        kills: int,
        deaths: int,
        points_change: int,
        won: bool,
        league_name: str = None,
    ):
        """Update user's game statistics in the specified league table (or default league if not specified)"""
        loop = asyncio.get_running_loop()

        def db_update():
            try:
                # If no league is specified, get the default league from config
                table_name = league_name
                if not table_name:
                    config_response = (
                        Config.supabase().table("config").select("*").execute()
                    )
                    if not config_response.data:
                        logger.error("No config found in database")
                        return None

                    config_data = config_response.data[0]
                    table_name = config_data.get("default_league")

                    if not table_name:
                        logger.error("No default league configured")
                        return None

                # Get current user stats from the league table (table name is directly from config)
                current_user = (
                    Config.supabase()
                    .table(table_name)
                    .select("*")
                    .eq("discord_id", str(discord_id))
                    .execute()
                )

                if not current_user.data:
                    logger.error(
                        f"User {discord_id} not found in league table {table_name}"
                    )
                    return None

                user_data = current_user.data[0]
                new_kills = user_data.get("kills", 0) + kills
                new_deaths = user_data.get("deaths", 0) + deaths
                new_points = user_data.get("points", 0) + points_change

                # Update wins/losses
                new_wins = user_data.get("wins", 0)
                new_losses = user_data.get("loses", 0)

                if won:
                    new_wins += 1
                else:
                    new_losses += 1

                # Update user stats in the league table
                result = (
                    Config.supabase()
                    .table(table_name)
                    .update(
                        {
                            "kills": new_kills,
                            "deaths": new_deaths,
                            "points": new_points,
                            "wins": new_wins,
                            "loses": new_losses,
                        }
                    )
                    .eq("discord_id", str(discord_id))
                    .execute()
                )

                return {
                    "success": True,
                    "new_points": new_points,
                    "new_kills": new_kills,
                    "new_deaths": new_deaths,
                    "new_wins": new_wins,
                    "new_losses": new_losses,
                    "result": result,
                    "league": table_name,
                }
            except Exception as e:
                logger.error(f"Error updating stats in league table {table_name}: {e}")
                return None

        return await loop.run_in_executor(None, db_update)

    @classmethod
    async def update_stats_with_roles(
        cls,
        discord_id: int,
        kills: int,
        deaths: int,
        points_change: int,
        won: bool,
        guild=None,
        league_name: str = None,
    ):
        """Update user's game statistics in the specified league table and manage level roles"""

        # First update the stats in the database
        db_result = await cls.update_stats(
            discord_id, kills, deaths, points_change, won, league_name
        )

        # Then handle role updates if guild is provided and database update was successful
        if guild and db_result and db_result.get("success"):
            try:
                from cogs.levels import LevelsCog

                # Use the actual new points from the database update
                new_points = db_result.get("new_points", 200)

                levels_cog = LevelsCog(None)
                await levels_cog.update_player_roles(guild, discord_id, new_points)
            except Exception as e:
                logger.error(f"Error updating roles for {discord_id}: {e}")

        return db_result

    @classmethod
    async def get_user_stats(cls, discord_id: int, league_name: str = None):
        """Get user's current statistics from the specified league table (or default league if not specified)"""
        loop = asyncio.get_running_loop()

        def db_select():
            try:
                # If no league is specified, get the default league from config
                table_name = league_name
                if not table_name:
                    config_response = (
                        Config.supabase().table("config").select("*").execute()
                    )
                    if not config_response.data:
                        logger.error("No config found in database")
                        return None

                    config_data = config_response.data[0]
                    table_name = config_data.get("default_league")

                    if not table_name:
                        logger.error("No default league configured")
                        return None

                # Get user stats from the league table (table_name is the actual table name)
                response = (
                    Config.supabase()
                    .table(table_name)
                    .select("*")
                    .eq("discord_id", str(discord_id))
                    .execute()
                )

                if response.data:
                    return {
                        "success": True,
                        "stats": response.data[0],
                        "league": table_name,
                    }
                else:
                    logger.warning(
                        f"User {discord_id} not found in league table {table_name}"
                    )
                    return None

            except Exception as e:
                logger.error(f"Error getting stats from league table {table_name}: {e}")
                return None

        return await loop.run_in_executor(None, db_select)

    @classmethod
    async def create_user_stats(
        cls, discord_id: int, nickname: str, league_name: str, initial_points: int = 200
    ):
        """Create initial user stats in a league table"""
        loop = asyncio.get_running_loop()

        def db_insert():
            try:
                table_name = league_name
                result = (
                    Config.supabase()
                    .table(table_name)
                    .insert(
                        {
                            "discord_id": str(discord_id),
                            "nickname": nickname,
                            "points": initial_points,
                            "kills": 0,
                            "deaths": 0,
                            "wins": 0,
                            "loses": 0,
                        }
                    )
                    .execute()
                )

                return {"success": True, "result": result, "league": league_name}
            except Exception as e:
                logger.error(f"Error creating stats in league {league_name}: {e}")
                return None

        return await loop.run_in_executor(None, db_insert)

    @classmethod
    async def delete_user_stats(cls, discord_id: int, league_name: str):
        """Delete user stats from a league table"""
        loop = asyncio.get_running_loop()

        def db_delete():
            try:
                table_name = league_name
                result = (
                    Config.supabase()
                    .table(table_name)
                    .delete()
                    .eq("discord_id", str(discord_id))
                    .execute()
                )

                return {"success": True, "result": result, "league": league_name}
            except Exception as e:
                logger.error(f"Error deleting stats from league {league_name}: {e}")
                return None

        return await loop.run_in_executor(None, db_delete)
