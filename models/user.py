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
    async def update_stats(
        cls, discord_id: int, kills: int, deaths: int, points_change: int, won: bool
    ):
        """Update user's game statistics"""
        loop = asyncio.get_running_loop()

        def db_update():
            # Get current user stats
            current_user = (
                Config.supabase()
                .table(cls.TABLE)
                .select("*")
                .eq("discord_id", str(discord_id))
                .execute()
            )
            if current_user.data:
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

                # Update user stats
                return (
                    Config.supabase()
                    .table(cls.TABLE)
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
    ):
        """Update user's game statistics and manage level roles"""
        loop = asyncio.get_running_loop()

        def db_update():
            # Get current user stats
            current_user = (
                Config.supabase()
                .table(cls.TABLE)
                .select("*")
                .eq("discord_id", str(discord_id))
                .execute()
            )
            if current_user.data:
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

                # Update user stats
                result = (
                    Config.supabase()
                    .table(cls.TABLE)
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
                return result, new_points
            return None, None

        db_result, new_points = await loop.run_in_executor(None, db_update)

        # Update roles if guild is provided and we have new points
        if guild and new_points is not None:
            try:
                from cogs.levels import LevelsCog

                levels_cog = LevelsCog(None)  # Create instance for static methods
                await levels_cog.update_player_roles(guild, discord_id, new_points)
            except Exception as e:
                import logging

                logger = logging.getLogger(__name__)
                logger.error(f"Error updating roles for {discord_id}: {e}")

        return db_result
