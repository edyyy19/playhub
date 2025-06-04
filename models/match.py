from config import Config
from datetime import datetime
import json
import asyncio
from models.user import User


class Match:
    TABLE = "matches"

    @classmethod
    async def create(cls, ct_team: list, t_team: list, map_name: str):
        # Get player data for CT team
        ct_team_data = []
        for member in ct_team:
            user_data = await User.find(member.id)
            if user_data:
                ct_team_data.append(
                    {
                        "discord_id": str(member.id),
                        "nickname": user_data.get("nickname", member.display_name),
                        "kills": 0,
                        "deaths": 0,
                        "points_received": 0,
                    }
                )
            else:
                # Fallback if user not found in database
                ct_team_data.append(
                    {
                        "discord_id": str(member.id),
                        "nickname": member.display_name,
                        "kills": 0,
                        "deaths": 0,
                        "points_received": 0,
                    }
                )

        # Get player data for T team
        t_team_data = []
        for member in t_team:
            user_data = await User.find(member.id)
            if user_data:
                t_team_data.append(
                    {
                        "discord_id": str(member.id),
                        "nickname": user_data.get("nickname", member.display_name),
                        "kills": 0,
                        "deaths": 0,
                        "points_received": 0,
                    }
                )
            else:
                # Fallback if user not found in database
                t_team_data.append(
                    {
                        "discord_id": str(member.id),
                        "nickname": member.display_name,
                        "kills": 0,
                        "deaths": 0,
                        "points_received": 0,
                    }
                )

        payload = {
            "ct_team": json.dumps(ct_team_data),
            "t_team": json.dumps(t_team_data),
            "map": map_name,
            "created_at": datetime.now().isoformat(),
            "results_submitted": False,
            "ended_at": None,
        }
        loop = asyncio.get_running_loop()

        def db_insert():
            try:
                return Config.supabase().table(cls.TABLE).insert(payload).execute()
            except Exception as e:
                print(f"Supabase insert error: {e}, payload: {payload}")
                raise

        return await loop.run_in_executor(None, db_insert)

    @classmethod
    async def find(cls, match_id: int):
        """Find a match by ID"""
        loop = asyncio.get_running_loop()

        def db_select():
            response = (
                Config.supabase()
                .table(cls.TABLE)
                .select("*")
                .eq("match_id", match_id)
                .execute()
            )
            return response.data[0] if response.data else None

        return await loop.run_in_executor(None, db_select)

    @classmethod
    async def update_results(
        cls, match_id: int, ct_team: list, t_team: list, ct_score: int, t_score: int
    ):
        """Update match results and mark as submitted"""
        loop = asyncio.get_running_loop()

        def db_update():
            # Include scores within the team data structure
            ct_team_with_score = {"players": ct_team, "score": ct_score}
            t_team_with_score = {"players": t_team, "score": t_score}

            return (
                Config.supabase()
                .table(cls.TABLE)
                .update(
                    {
                        "ct_team": json.dumps(ct_team_with_score),
                        "t_team": json.dumps(t_team_with_score),
                        "results_submitted": True,
                        "ended_at": datetime.now().isoformat(),
                    }
                )
                .eq("match_id", match_id)
                .execute()
            )

        return await loop.run_in_executor(None, db_update)
