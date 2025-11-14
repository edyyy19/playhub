import asyncio
import logging
from datetime import datetime
from threading import Lock
from config import Config

logger = logging.getLogger(__name__)


class ScoreboardConfigManager:
    """Singleton manager for scoreboard configurations with in-memory caching."""
    
    _instance = None
    _lock = Lock()
    _configs_cache = {}  # guild_id -> config dict
    _initialized = False
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    @classmethod
    async def initialize(cls):
        """Initialize the config cache by loading all scoreboard configs from database."""
        if cls._initialized:
            logger.warning("ScoreboardConfigManager already initialized")
            return
        
        try:
            logger.info("Initializing ScoreboardConfigManager...")
            all_configs = await ScoreboardConfig.get_all_configs()
            
            cls._configs_cache = {
                config['guild_id']: config
                for config in all_configs
            }
            
            cls._initialized = True
            logger.info(
                f"ScoreboardConfigManager initialized with {len(cls._configs_cache)} "
                f"guild configurations"
            )
            
        except Exception as e:
            logger.error(f"Error initializing ScoreboardConfigManager: {e}", exc_info=True)
            cls._configs_cache = {}
    
    @classmethod
    def get_method(cls, guild_id: int) -> str:
        """Get the scoreboard method for a guild (O(1) lookup)."""
        config = cls._configs_cache.get(guild_id)
        if config:
            return config.get('method', 'dynamic')
        return 'dynamic'  # Default to dynamic mode
    
    @classmethod
    def update_cache(cls, guild_id: int, config_data: dict):
        """Update a single guild's config in cache."""
        cls._configs_cache[guild_id] = config_data
        logger.debug(f"Updated cache for guild {guild_id}")
    
    @classmethod
    def remove_from_cache(cls, guild_id: int):
        """Remove a guild's config from cache."""
        if guild_id in cls._configs_cache:
            del cls._configs_cache[guild_id]
            logger.debug(f"Removed guild {guild_id} from cache")


class ScoreboardConfig:
    """Model for managing scoreboard channel configurations"""

    @classmethod
    async def set_config(
        cls,
        guild_id: int,
        submission_channel_id: int,
        results_channel_id: int,
        message_id: int = None,
        method: str = 'dynamic',
    ):
        """Set or update scoreboard configuration for a guild"""
        loop = asyncio.get_running_loop()

        def db_operation():
            try:
                config_data = {
                    "guild_id": guild_id,
                    "submission_channel_id": submission_channel_id,
                    "results_channel_id": results_channel_id,
                    "message_id": message_id,
                    "method": method,
                    "updated_at": datetime.now().isoformat(),
                }

                # Use upsert to insert or update
                response = (
                    Config.supabase()
                    .table("scoreboard_config")
                    .upsert(config_data)
                    .execute()
                )

                result = response.data[0] if response.data else None
                
                # Update cache
                if result:
                    ScoreboardConfigManager.update_cache(guild_id, result)
                
                return result

            except Exception as e:
                logger.error(f"Error setting scoreboard config: {e}")
                return None

        return await loop.run_in_executor(None, db_operation)

    @classmethod
    async def get_config(cls, guild_id: int):
        """Get scoreboard configuration for a specific guild"""
        loop = asyncio.get_running_loop()

        def db_operation():
            try:
                response = (
                    Config.supabase()
                    .table("scoreboard_config")
                    .select("*")
                    .eq("guild_id", guild_id)
                    .execute()
                )

                return response.data[0] if response.data else None

            except Exception as e:
                logger.error(f"Error getting scoreboard config: {e}")
                return None

        return await loop.run_in_executor(None, db_operation)

    @classmethod
    async def get_all_configs(cls):
        """Get all scoreboard configurations"""
        loop = asyncio.get_running_loop()

        def db_operation():
            try:
                response = (
                    Config.supabase().table("scoreboard_config").select("*").execute()
                )

                return response.data if response.data else []

            except Exception as e:
                logger.error(f"Error getting all scoreboard configs: {e}")
                return []

        return await loop.run_in_executor(None, db_operation)

    @classmethod
    async def delete_config(cls, guild_id: int):
        """Delete scoreboard configuration for a guild"""
        loop = asyncio.get_running_loop()

        def db_operation():
            try:
                response = (
                    Config.supabase()
                    .table("scoreboard_config")
                    .delete()
                    .eq("guild_id", guild_id)
                    .execute()
                )

                # Remove from cache
                ScoreboardConfigManager.remove_from_cache(guild_id)
                
                return True

            except Exception as e:
                logger.error(f"Error deleting scoreboard config: {e}")
                return False

        return await loop.run_in_executor(None, db_operation)
