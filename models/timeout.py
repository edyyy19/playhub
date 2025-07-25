import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from config import Config

logger = logging.getLogger(__name__)


class TimeoutManager:
    TABLE = "player_timeouts"

    def __init__(self):
        self._active_timeouts = {}  # In-memory cache for quick lookup

    @staticmethod
    def _parse_timestamp(timestamp_str: str) -> datetime:
    
        # Parse timestamp from database handling different PostgreSQL timestampz formats
      
        try:
            # Handle PostgreSQL timestampz format: 2025-07-25 12:14:07.194335+00
            if timestamp_str.endswith('+00'):
                timestamp_str = timestamp_str.replace('+00', '+00:00')
            elif timestamp_str.endswith('Z'):
                timestamp_str = timestamp_str.replace('Z', '+00:00')
            
            parsed_datetime = datetime.fromisoformat(timestamp_str)
            return parsed_datetime.replace(tzinfo=None)  # Remove timezone info for consistency
        except ValueError:
            # Fallback: try to parse as ISO format
            parsed_datetime = datetime.fromisoformat(
                timestamp_str.replace("Z", "+00:00")
            )
            return parsed_datetime.replace(tzinfo=None)

    async def add_timeout(
        self,
        user_id: int,
        timeout_minutes: int = 2,
        reason: str = "Left during Phase 2/3",
    ) -> bool:
        """Add a timeout for a user"""
        timeout_until = datetime.utcnow() + timedelta(minutes=timeout_minutes)

        loop = asyncio.get_running_loop()

        def db_operation():
            try:
                # First try to update existing record
                update_result = (
                    Config.supabase()
                    .table(self.TABLE)
                    .update(
                        {
                            "timeout_until": timeout_until.isoformat(),
                            "reason": reason,
                        }
                    )
                    .eq("user_id", user_id)
                    .execute()
                )

                # If no rows were updated, insert new record
                if not update_result.data:
                    Config.supabase().table(self.TABLE).insert(
                        {
                            "user_id": user_id,
                            "timeout_until": timeout_until.isoformat(),
                            "reason": reason,
                        }
                    ).execute()

                return True
            except Exception as e:
                logger.error(f"Failed to add timeout for user {user_id}: {e}")
                return False

        success = await loop.run_in_executor(None, db_operation)

        if success:
            # Update in-memory cache
            self._active_timeouts[user_id] = {
                "timeout_until": timeout_until,
                "reason": reason,
            }
            logger.info(f"Added timeout for user {user_id} until {timeout_until}")

        return success

    async def add_timeout_hours(
        self,
        user_id: int,
        timeout_hours: int = 1,
        reason: str = "Administrative timeout",
    ) -> bool:
        """Add a timeout for a user specified in hours"""
        timeout_until = datetime.utcnow() + timedelta(hours=timeout_hours)

        loop = asyncio.get_running_loop()

        def db_operation():
            try:
                # First try to update existing record
                update_result = (
                    Config.supabase()
                    .table(self.TABLE)
                    .update(
                        {
                            "timeout_until": timeout_until.isoformat(),
                            "reason": reason,
                        }
                    )
                    .eq("user_id", user_id)
                    .execute()
                )

                # If no rows were updated, insert new record
                if not update_result.data:
                    Config.supabase().table(self.TABLE).insert(
                        {
                            "user_id": user_id,
                            "timeout_until": timeout_until.isoformat(),
                            "reason": reason,
                        }
                    ).execute()

                return True
            except Exception as e:
                logger.error(f"Failed to add timeout for user {user_id}: {e}")
                return False

        success = await loop.run_in_executor(None, db_operation)

        if success:
            # Update in-memory cache
            self._active_timeouts[user_id] = {
                "timeout_until": timeout_until,
                "reason": reason,
            }
            logger.info(
                f"Added {timeout_hours} hour timeout for user {user_id} until {timeout_until}"
            )

        return success

    async def is_user_timeouted(self, user_id: int) -> tuple[bool, Optional[datetime]]:
        """Check if a user is currently timeouted"""
        # First check in-memory cache
        if user_id in self._active_timeouts:
            timeout_info = self._active_timeouts[user_id]
            if datetime.utcnow() < timeout_info["timeout_until"]:
                return True, timeout_info["timeout_until"]
            else:
                # Timeout expired, remove from cache
                del self._active_timeouts[user_id]

        # Check database for authoritative state
        loop = asyncio.get_running_loop()

        def db_check():
            try:
                result = (
                    Config.supabase()
                    .table(self.TABLE)
                    .select("*")
                    .eq("user_id", user_id)
                    .execute()
                )
                if result.data:
                    timeout_data = result.data[0]
                    timeout_until = self._parse_timestamp(timeout_data["timeout_until"])

                    if datetime.utcnow() < timeout_until:
                        return (
                            True,
                            timeout_until,
                            timeout_data["reason"],
                        )
                    else:
                        # Timeout expired, clean up database
                        Config.supabase().table(self.TABLE).delete().eq(
                            "user_id", user_id
                        ).execute()
                        return False, None, None
                return False, None, None
            except Exception as e:
                logger.error(f"Failed to check timeout for user {user_id}: {e}")
                return False, None, None

        is_timeouted, timeout_until, reason = await loop.run_in_executor(None, db_check)

        if is_timeouted:
            # Update cache
            self._active_timeouts[user_id] = {
                "timeout_until": timeout_until,
                "reason": reason,
            }

        return is_timeouted, timeout_until

    async def remove_timeout(self, user_id: int) -> bool:
        """Remove a timeout for a user"""
        loop = asyncio.get_running_loop()

        def db_operation():
            try:
                Config.supabase().table(self.TABLE).delete().eq(
                    "user_id", user_id
                ).execute()
                return True
            except Exception as e:
                logger.error(f"Failed to remove timeout for user {user_id}: {e}")
                return False

        success = await loop.run_in_executor(None, db_operation)

        if success:
            # Remove from cache
            self._active_timeouts.pop(user_id, None)
            logger.info(f"Removed timeout for user {user_id}")

        return success

    async def get_all_timeouts(self) -> List[Dict[str, Any]]:
        """Get all current timeouts"""
        loop = asyncio.get_running_loop()

        def db_operation():
            try:
                result = Config.supabase().table(self.TABLE).select("*").execute()
                timeouts = []
                current_time = datetime.utcnow()

                for timeout_data in result.data:
                    timeout_until = self._parse_timestamp(timeout_data["timeout_until"])

                    if current_time < timeout_until:
                        remaining_seconds = (timeout_until - current_time).total_seconds()
                        timeouts.append(
                            {
                                "user_id": timeout_data["user_id"],
                                "timeout_until": timeout_until,
                                "reason": timeout_data["reason"],
                                "remaining_minutes": int(remaining_seconds / 60),
                                "remaining_hours": remaining_seconds / 3600,
                            }
                        )
                    else:
                        # Clean up expired timeout
                        Config.supabase().table(self.TABLE).delete().eq(
                            "user_id", timeout_data["user_id"]
                        ).execute()

                return timeouts
            except Exception as e:
                logger.error(f"Failed to get all timeouts: {e}")
                return []

        return await loop.run_in_executor(None, db_operation)

    async def clear_all_timeouts(self) -> bool:
        """Clear all timeouts"""
        loop = asyncio.get_running_loop()

        def db_operation():
            try:
                Config.supabase().table(self.TABLE).delete().neq("id", 0).execute()
                return True
            except Exception as e:
                logger.error(f"Failed to clear all timeouts: {e}")
                return False

        success = await loop.run_in_executor(None, db_operation)

        if success:
            # Clear cache
            self._active_timeouts.clear()
            logger.info("Cleared all timeouts")

        return success

    async def load_active_timeouts(self):
        """Load active timeouts from database on startup"""
        logger.info("Loading active timeouts from database...")
        timeouts = await self.get_all_timeouts()

        for timeout in timeouts:
            self._active_timeouts[timeout["user_id"]] = {
                "timeout_until": timeout["timeout_until"],
                "reason": timeout["reason"],
            }

        logger.info(f"Loaded {len(timeouts)} active timeouts")

    def get_remaining_time(self, user_id: int) -> Optional[int]:
        """Get remaining timeout time in minutes for a user"""
        if user_id in self._active_timeouts:
            timeout_info = self._active_timeouts[user_id]
            remaining = timeout_info["timeout_until"] - datetime.utcnow()
            if remaining.total_seconds() > 0:
                return int(remaining.total_seconds() / 60) + 1  # Round up
        return None

    def get_remaining_time_hours(self, user_id: int) -> Optional[float]:
        """Get remaining timeout time in hours for a user"""
        if user_id in self._active_timeouts:
            timeout_info = self._active_timeouts[user_id]
            remaining = timeout_info["timeout_until"] - datetime.utcnow()
            if remaining.total_seconds() > 0:
                return remaining.total_seconds() / 3600  # Convert seconds to hours
        return None

    async def get_timeout_channel(self) -> Optional[str]:
        """Get the configured timeout channel ID from config table"""
        loop = asyncio.get_running_loop()

        def db_operation():
            try:
                result = (
                    Config.supabase()
                    .table("config")
                    .select("timeout_channel")
                    .limit(1)
                    .execute()
                )
                if result.data and result.data[0].get("timeout_channel"):
                    return result.data[0]["timeout_channel"]
            except Exception as e:
                logger.error(f"Failed to get timeout channel config: {e}")
            return None

        return await loop.run_in_executor(None, db_operation)


# Global timeout manager instance
timeout_manager = TimeoutManager()
