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
