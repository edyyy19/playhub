from config import Config
from datetime import datetime
import json
import asyncio


class Match:
    TABLE = "matches"

    @classmethod
    async def create(cls, ct_team: list, t_team: list, map_name: str):
        payload = {
            "ct_team": json.dumps([str(m.id) for m in ct_team]),
            "t_team": json.dumps([str(m.id) for m in t_team]),
            "map": map_name,
            "created_at": datetime.now().isoformat(),
        }
        loop = asyncio.get_running_loop()

        def db_insert():
            try:
                return Config.supabase().table(cls.TABLE).insert(payload).execute()
            except Exception as e:
                print(f"Supabase insert error: {e}, payload: {payload}")
                raise

        return await loop.run_in_executor(None, db_insert)
