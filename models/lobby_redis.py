import asyncio
import logging
from typing import Set, Dict, List
from config import Config
import json

logger = logging.getLogger(__name__)


class LobbyRedis:
    """
    Redis manager for lobby operations - handles all Redis operations for matchmaking lobbies
    Key patterns:
    - lobby:{category_id}:players -> Set of user IDs currently in lobby
    - lobby:{category_id}:team1 -> Set of user IDs in team 1
    - lobby:{category_id}:team2 -> Set of user IDs in team 2
    - lobby:{category_id}:available -> Set of user IDs available for draft
    - lobby:{category_id}:info -> Hash with phase, next_pick, match_id, etc.
    """

    @classmethod
    def _get_redis(cls):
        """Get Redis client from config"""
        return Config.redis()

    @classmethod
    async def _redis_operation(cls, operation):
        """Execute Redis operation in thread pool to avoid blocking"""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, operation)

    # --- PLAYER OPERATIONS ---

    @classmethod
    async def add_player(cls, category_id: int, user_id: int) -> bool:
        """Add a player to lobby"""
        try:
            redis_client = cls._get_redis()

            def _add():
                return redis_client.sadd(f"lobby:{category_id}:players", str(user_id))

            result = await cls._redis_operation(_add)
            logger.info(f"🔴 Added player {user_id} to lobby {category_id}")
            return bool(result)
        except Exception as e:
            logger.error(f"🔴 Failed to add player {user_id} to lobby {category_id}: {e}")
            return False

    @classmethod
    async def remove_player(cls, category_id: int, user_id: int) -> bool:
        """Remove a player from lobby"""
        try:
            redis_client = cls._get_redis()

            def _remove():
                return redis_client.srem(f"lobby:{category_id}:players", str(user_id))

            result = await cls._redis_operation(_remove)
            logger.info(f"🔴 Removed player {user_id} from lobby {category_id}")
            return bool(result)
        except Exception as e:
            logger.error(f"🔴 Failed to remove player {user_id} from lobby {category_id}: {e}")
            return False

    @classmethod
    async def get_players(cls, category_id: int) -> Set[int]:
        """Get all players in lobby"""
        try:
            redis_client = cls._get_redis()

            def _get():
                return redis_client.smembers(f"lobby:{category_id}:players")

            result = await cls._redis_operation(_get)
            return {int(user_id) for user_id in result}
        except Exception as e:
            logger.error(f"🔴 Failed to get players for lobby {category_id}: {e}")
            return set()

    # --- TEAM OPERATIONS ---

    @classmethod
    async def add_to_team(cls, category_id: int, team: int, user_id: int) -> bool:
        """Add player to team (1 or 2)"""
        try:
            redis_client = cls._get_redis()
            team_key = f"lobby:{category_id}:team{team}"

            def _add():
                return redis_client.sadd(team_key, str(user_id))

            result = await cls._redis_operation(_add)
            logger.info(f"🔴 Added player {user_id} to team {team} in lobby {category_id}")
            return bool(result)
        except Exception as e:
            logger.error(f"🔴 Failed to add player {user_id} to team {team} in lobby {category_id}: {e}")
            return False

    @classmethod
    async def get_team(cls, category_id: int, team: int) -> Set[int]:
        """Get all players in team"""
        try:
            redis_client = cls._get_redis()
            team_key = f"lobby:{category_id}:team{team}"

            def _get():
                return redis_client.smembers(team_key)

            result = await cls._redis_operation(_get)
            return {int(user_id) for user_id in result}
        except Exception as e:
            logger.error(f"🔴 Failed to get team {team} for lobby {category_id}: {e}")
            return set()

    @classmethod
    async def clear_teams(cls, category_id: int) -> bool:
        """Clear both teams"""
        try:
            redis_client = cls._get_redis()

            def _clear():
                redis_client.delete(f"lobby:{category_id}:team1")
                redis_client.delete(f"lobby:{category_id}:team2")
                return True

            result = await cls._redis_operation(_clear)
            logger.info(f"🔴 Cleared teams for lobby {category_id}")
            return result
        except Exception as e:
            logger.error(f"🔴 Failed to clear teams for lobby {category_id}: {e}")
            return False

    # --- AVAILABLE PLAYERS (for draft) ---

    @classmethod
    async def set_available_players(cls, category_id: int, user_ids: List[int]) -> bool:
        """Set the list of available players for draft"""
        try:
            redis_client = cls._get_redis()
            available_key = f"lobby:{category_id}:available"

            def _set():
                # Clear existing and add new
                redis_client.delete(available_key)
                if user_ids:
                    redis_client.sadd(available_key, *[str(uid) for uid in user_ids])
                return True

            result = await cls._redis_operation(_set)
            logger.info(f"🔴 Set {len(user_ids)} available players for lobby {category_id}")
            return result
        except Exception as e:
            logger.error(f"🔴 Failed to set available players for lobby {category_id}: {e}")
            return False

    @classmethod
    async def remove_from_available(cls, category_id: int, user_id: int) -> bool:
        """Remove player from available list"""
        try:
            redis_client = cls._get_redis()

            def _remove():
                return redis_client.srem(f"lobby:{category_id}:available", str(user_id))

            result = await cls._redis_operation(_remove)
            return bool(result)
        except Exception as e:
            logger.error(f"🔴 Failed to remove player {user_id} from available in lobby {category_id}: {e}")
            return False

    @classmethod
    async def get_available_players(cls, category_id: int) -> Set[int]:
        """Get available players for draft"""
        try:
            redis_client = cls._get_redis()

            def _get():
                return redis_client.smembers(f"lobby:{category_id}:available")

            result = await cls._redis_operation(_get)
            return {int(user_id) for user_id in result}
        except Exception as e:
            logger.error(f"🔴 Failed to get available players for lobby {category_id}: {e}")
            return set()

    # --- LOBBY INFO (phase, next_pick, etc.) ---

    @classmethod
    async def set_info(cls, category_id: int, **kwargs) -> bool:
        """Set lobby info fields"""
        try:
            redis_client = cls._get_redis()
            info_key = f"lobby:{category_id}:info"

            def _set():
                # Convert values to strings and filter None values
                data = {}
                for key, value in kwargs.items():
                    if value is not None:
                        data[key] = str(value)
                
                if data:
                    redis_client.hset(info_key, mapping=data)
                return True

            result = await cls._redis_operation(_set)
            logger.info(f"🔴 Set info for lobby {category_id}: {kwargs}")
            return result
        except Exception as e:
            logger.error(f"🔴 Failed to set info for lobby {category_id}: {e}")
            return False

    @classmethod
    async def get_info(cls, category_id: int) -> Dict[str, str]:
        """Get all lobby info"""
        try:
            redis_client = cls._get_redis()

            def _get():
                return redis_client.hgetall(f"lobby:{category_id}:info")

            result = await cls._redis_operation(_get)
            return result or {}
        except Exception as e:
            logger.error(f"🔴 Failed to get info for lobby {category_id}: {e}")
            return {}

    # --- LOBBY MANAGEMENT ---

    @classmethod
    async def create_lobby(cls, category_id: int, ttl_hours: int = 24) -> bool:
        """Initialize Redis structure for a new lobby"""
        try:
            redis_client = cls._get_redis()

            def _create():
                # Set initial info
                redis_client.hset(f"lobby:{category_id}:info", "phase", "1")
                
                # Set TTL on all keys (auto-cleanup)
                ttl_seconds = ttl_hours * 3600
                redis_client.expire(f"lobby:{category_id}:players", ttl_seconds)
                redis_client.expire(f"lobby:{category_id}:info", ttl_seconds)
                
                return True

            result = await cls._redis_operation(_create)
            logger.info(f"🔴 Created Redis structure for lobby {category_id} with {ttl_hours}h TTL")
            return result
        except Exception as e:
            logger.error(f"🔴 Failed to create Redis structure for lobby {category_id}: {e}")
            return False

    @classmethod
    async def delete_lobby(cls, category_id: int) -> bool:
        """Delete all Redis data for a lobby"""
        try:
            redis_client = cls._get_redis()

            def _delete():
                # Get all keys for this lobby
                pattern = f"lobby:{category_id}:*"
                keys = redis_client.keys(pattern)
                if keys:
                    redis_client.delete(*keys)
                return len(keys)

            deleted_count = await cls._redis_operation(_delete)
            logger.info(f"🔴 Deleted {deleted_count} Redis keys for lobby {category_id}")
            return deleted_count > 0
        except Exception as e:
            logger.error(f"🔴 Failed to delete Redis data for lobby {category_id}: {e}")
            return False

    # --- HEALTH CHECK ---

    @classmethod
    async def ping(cls) -> bool:
        """Test Redis connection"""
        try:
            redis_client = cls._get_redis()

            def _ping():
                return redis_client.ping()

            result = await cls._redis_operation(_ping)
            return result == True
        except Exception as e:
            logger.error(f"🔴 Redis ping failed: {e}")
            return False

    # --- BULK OPERATIONS ---

    @classmethod
    async def get_all_lobby_data(cls, category_id: int) -> Dict:
        """Get complete lobby state from Redis"""
        try:
            players = await cls.get_players(category_id)
            team1 = await cls.get_team(category_id, 1)
            team2 = await cls.get_team(category_id, 2)
            available = await cls.get_available_players(category_id)
            info = await cls.get_info(category_id)

            return {
                "players": players,
                "team1": team1,
                "team2": team2,
                "available": available,
                "info": info
            }
        except Exception as e:
            logger.error(f"🔴 Failed to get all lobby data for {category_id}: {e}")
            return {}
