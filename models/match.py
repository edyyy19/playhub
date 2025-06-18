from config import Config
from datetime import datetime
import json
import asyncio
import logging
from models.user import User

logger = logging.getLogger(__name__)


class Match:
    TABLE = "matches"

    @classmethod
    async def create(
        cls, ct_team: list, t_team: list, map_name: str, league: str = None
    ):
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
            "league": league,  # Add league column
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

    @classmethod
    async def try_lock_for_submission(cls, match_id: int, submitter_discord_id: str):
        """
        Atomically attempt to lock a match for submission.
        Returns True if successfully locked, False if already submitted or locked.
        """
        loop = asyncio.get_running_loop()

        def db_atomic_lock():
            try:
                # Use PostgreSQL's UPDATE with WHERE condition to ensure atomicity
                # This will only update if results_submitted is still False
                response = (
                    Config.supabase()
                    .table(cls.TABLE)
                    .update(
                        {
                            "submission_in_progress": True,
                            "submitter_discord_id": submitter_discord_id,
                            "submission_started_at": datetime.now().isoformat(),
                        }
                    )
                    .eq("match_id", match_id)
                    .eq(
                        "results_submitted", False
                    )  # Only update if not already submitted
                    .is_(
                        "submission_in_progress", None
                    )  # Only if not already being processed
                    .execute()
                )

                # Return True if exactly one row was updated (successful lock)
                return len(response.data) == 1

            except Exception as e:
                logger.error(f"Error attempting to lock match {match_id}: {e}")
                return False

        return await loop.run_in_executor(None, db_atomic_lock)

    @classmethod
    async def complete_submission(
        cls, match_id: int, ct_team: list, t_team: list, ct_score: int, t_score: int
    ):
        """
        Complete the submission process after successful lock.
        This should only be called after try_lock_for_submission returns True.
        """
        loop = asyncio.get_running_loop()

        def db_complete():
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
                        "submission_in_progress": None,  # Clear the lock
                        "submitter_discord_id": None,  # Clear submitter info
                        "submission_started_at": None,  # Clear start time
                    }
                )
                .eq("match_id", match_id)
                .execute()
            )

        return await loop.run_in_executor(None, db_complete)

    @classmethod
    async def release_submission_lock(cls, match_id: int):
        """
        Release a submission lock in case of error during processing.
        """
        loop = asyncio.get_running_loop()

        def db_release():
            return (
                Config.supabase()
                .table(cls.TABLE)
                .update(
                    {
                        "submission_in_progress": None,
                        "submitter_discord_id": None,
                        "submission_started_at": None,
                    }
                )
                .eq("match_id", match_id)
                .execute()
            )

        return await loop.run_in_executor(None, db_release)

    @classmethod
    async def get_user_matches(cls, discord_id: str, limit: int = 5):
        """Get the last N matches for a specific user"""
        loop = asyncio.get_running_loop()

        def db_select():
            try:
                # Query matches where the user appears in either CT or T team
                response = (
                    Config.supabase()
                    .table(cls.TABLE)
                    .select("*")
                    .eq("results_submitted", True)  # Only get completed matches
                    .not_.is_("ended_at", None)  # Make sure match has ended
                    .order("ended_at", desc=True)  # Most recent first
                    .execute()
                )

                # Filter matches where the user participated
                user_matches = []
                for match in response.data:
                    ct_team_data = json.loads(match.get("ct_team", "[]"))
                    t_team_data = json.loads(match.get("t_team", "[]"))

                    # Handle both old format (just players array) and new format (with players and score)
                    if isinstance(ct_team_data, list):
                        ct_team = ct_team_data
                    else:
                        ct_team = ct_team_data.get("players", [])

                    if isinstance(t_team_data, list):
                        t_team = t_team_data
                    else:
                        t_team = t_team_data.get("players", [])

                    # Check if user is in either team
                    user_in_match = False
                    for player in ct_team + t_team:
                        if player.get("discord_id") == discord_id:
                            user_in_match = True
                            break

                    if user_in_match:
                        # Extract final scores
                        ct_score = 0
                        t_score = 0

                        if isinstance(ct_team_data, dict) and "score" in ct_team_data:
                            ct_score = ct_team_data["score"]
                        if isinstance(t_team_data, dict) and "score" in t_team_data:
                            t_score = t_team_data["score"]

                        # Add processed match data
                        match_info = {
                            "match_id": match.get("match_id"),
                            "map": match.get("map"),
                            "ended_at": match.get("ended_at"),
                            "ct_score": ct_score,
                            "t_score": t_score,
                            "user_team": "CT"
                            if any(p.get("discord_id") == discord_id for p in ct_team)
                            else "T",
                            "user_won": (
                                ct_score > t_score
                                and any(
                                    p.get("discord_id") == discord_id for p in ct_team
                                )
                            )
                            or (
                                t_score > ct_score
                                and any(
                                    p.get("discord_id") == discord_id for p in t_team
                                )
                            ),
                        }
                        user_matches.append(match_info)

                        # Stop when we have enough matches
                        if len(user_matches) >= limit:
                            break

                return user_matches

            except Exception as e:
                logger.error(f"Error fetching user matches: {e}")
                return []

        return await loop.run_in_executor(None, db_select)
