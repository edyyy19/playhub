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
        cls, team1: list, team2: list, team1_captain, team2_captain, map_name: str, league: str = None
    ):
        # Batch fetch user data for all players
        all_players = team1 + team2
        all_player_ids = [member.id for member in all_players]
        users_batch = await User.find_multiple(all_player_ids)

        # Process Team 1 players using batch data
        team1_players = []
        for member in team1:
            user_data = users_batch.get(member.id)
            if user_data:
                team1_players.append(
                    {
                        "discord_id": str(member.id),
                        "nickname": user_data.get("nickname", member.display_name),
                    }
                )
            else:
                # Fallback if user not found in database
                team1_players.append(
                    {
                        "discord_id": str(member.id),
                        "nickname": member.display_name,
                    }
                )

        # Process Team 2 players using batch data
        team2_players = []
        for member in team2:
            user_data = users_batch.get(member.id)
            if user_data:
                team2_players.append(
                    {
                        "discord_id": str(member.id),
                        "nickname": user_data.get("nickname", member.display_name),
                    }
                )
            else:
                # Fallback if user not found in database
                team2_players.append(
                    {
                        "discord_id": str(member.id),
                        "nickname": member.display_name,
                    }
                )

        # Create teams structure
        teams_data = {
            "team1": {
                "captain": team1_captain.display_name if hasattr(team1_captain, 'display_name') else str(team1_captain),
                "players": team1_players
            },
            "team2": {
                "captain": team2_captain.display_name if hasattr(team2_captain, 'display_name') else str(team2_captain),
                "players": team2_players
            }
        }

        payload = {
            "teams": json.dumps(teams_data),
            "teams_stats": None,  # Will be populated when results are submitted
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
        cls, match_id: int, team1: list, team2: list, team1_score: int, team2_score: int
    ):
        """Update match results and mark as submitted"""
        loop = asyncio.get_running_loop()

        def db_update():
            # Create teams_stats structure with scores and player statistics
            teams_stats_data = {
                "team1": {
                    "score": team1_score,
                    "players": team1
                },
                "team2": {
                    "score": team2_score,
                    "players": team2
                }
            }

            return (
                Config.supabase()
                .table(cls.TABLE)
                .update(
                    {
                        "teams_stats": json.dumps(teams_stats_data),
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
        cls, match_id: int, team1: list, team2: list, team1_score: int, team2_score: int
    ):
        """
        Complete the submission process after successful lock.
        This should only be called after try_lock_for_submission returns True.
        """
        loop = asyncio.get_running_loop()

        def db_complete():
            # Create teams_stats structure with scores and player statistics
            teams_stats_data = {
                "team1": {
                    "score": team1_score,
                    "players": team1
                },
                "team2": {
                    "score": team2_score,
                    "players": team2
                }
            }

            return (
                Config.supabase()
                .table(cls.TABLE)
                .update(
                    {
                        "teams_stats": json.dumps(teams_stats_data),
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
        
        # Ensure limit is a valid integer
        if limit is None or limit < 0:
            limit = 0  # 0 means no limit

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
                    teams_data = json.loads(match.get("teams", "{}"))
                    teams_stats_data = json.loads(match.get("teams_stats", "{}")) if match.get("teams_stats") else None
                    
                    # Handle backward compatibility - check if old format exists
                    if not teams_data and (match.get("ct_team") or match.get("t_team")):
                        # Old format fallback - convert to new format structure
                        team1_team_data = json.loads(match.get("ct_team", "[]"))
                        team2_team_data = json.loads(match.get("t_team", "[]"))
                        
                        # Handle both old format (just players array) and new format (with players and score)
                        if isinstance(team1_team_data, list):
                            team1_team = team1_team_data
                        else:
                            team1_team = team1_team_data.get("players", [])

                        if isinstance(team2_team_data, list):
                            team2_team = team2_team_data
                        else:
                            team2_team = team2_team_data.get("players", [])
                        
                        all_players = team1_team + team2_team
                        team1_score = team1_team_data.get("score", 0) if isinstance(team1_team_data, dict) else 0
                        team2_score = team2_team_data.get("score", 0) if isinstance(team2_team_data, dict) else 0
                    else:
                        # New format - get players from teams column
                        team1_data = teams_data.get("team1", {})
                        team2_data = teams_data.get("team2", {})
                        
                        team1_players = team1_data.get("players", [])
                        team2_players = team2_data.get("players", [])
                        all_players = team1_players + team2_players
                        
                        # Get scores from teams_stats if available, otherwise 0
                        if teams_stats_data:
                            team1_score = teams_stats_data.get("team1", {}).get("score", 0)
                            team2_score = teams_stats_data.get("team2", {}).get("score", 0)
                        else:
                            team1_score = 0
                            team2_score = 0

                    # Check if user is in either team
                    user_in_match = False
                    user_team = None
                    for player in all_players:
                        if player.get("discord_id") == discord_id:
                            user_in_match = True
                            # Determine which team the user was on
                            if teams_data:  # New format
                                team1_players = teams_data.get("team1", {}).get("players", [])
                                if any(p.get("discord_id") == discord_id for p in team1_players):
                                    user_team = "Team1"
                                else:
                                    user_team = "Team2"
                            else:  # Old format fallback
                                if any(p.get("discord_id") == discord_id for p in team1_team):
                                    user_team = "Team1"
                                else:
                                    user_team = "Team2"
                            break

                    if user_in_match:
                        # Determine if user won
                        user_won = False
                        if teams_stats_data:  # New format - use teams_stats for scores
                            team1_score = teams_stats_data.get("team1", {}).get("score", 0)
                            team2_score = teams_stats_data.get("team2", {}).get("score", 0)
                            user_won = (user_team == "Team1" and team1_score > team2_score) or \
                                      (user_team == "Team2" and team2_score > team1_score)
                        elif teams_data and teams_data.get("team1", {}).get("score") is not None:  # Old format with scores in teams
                            team1_score = teams_data.get("team1", {}).get("score", 0)
                            team2_score = teams_data.get("team2", {}).get("score", 0)
                            user_won = (user_team == "Team1" and team1_score > team2_score) or \
                                      (user_team == "Team2" and team2_score > team1_score)
                        else:  # Old format fallback
                            user_won = (user_team == "Team1" and team1_score > team2_score) or \
                                      (user_team == "Team2" and team2_score > team1_score)

                        # Get user stats from teams_stats if available
                        user_kills = 0
                        user_deaths = 0
                        if teams_stats_data:
                            if user_team == "Team1":
                                team_stats = teams_stats_data.get("team1", {})
                            else:
                                team_stats = teams_stats_data.get("team2", {})
                            
                            players_stats = team_stats.get("players", [])
                            for player_stat in players_stats:
                                if player_stat.get("discord_id") == discord_id:
                                    user_kills = player_stat.get("kills", 0)
                                    user_deaths = player_stat.get("deaths", 0)
                                    break

                        # Add processed match data
                        match_info = {
                            "match_id": match.get("match_id"),
                            "map": match.get("map"),
                            "ended_at": match.get("ended_at"),
                            "team1_score": team1_score,
                            "team2_score": team2_score,
                            "user_team": user_team,
                            "user_won": user_won,
                            "user_kills": user_kills,
                            "user_deaths": user_deaths,
                        }
                        user_matches.append(match_info)

                        # Stop when we have enough matches (only if limit is positive)
                        if limit > 0 and len(user_matches) >= limit:
                            break

                return user_matches

            except Exception as e:
                logger.error(f"Error fetching user matches: {e}")
                return []

        return await loop.run_in_executor(None, db_select)

    @classmethod
    async def revert_match(cls, match_id: int):
        """
        Revert a completed match by resetting teams_stats, ended_at, and results_submitted.
        Used by the revert_game command to undo match completion.
        """
        loop = asyncio.get_running_loop()

        def db_revert():
            return (
                Config.supabase()
                .table(cls.TABLE)
                .update(
                    {
                        "teams_stats": None,
                        "ended_at": None,
                        "results_submitted": False,
                    }
                )
                .eq("match_id", match_id)
                .execute()
            )

        return await loop.run_in_executor(None, db_revert)
