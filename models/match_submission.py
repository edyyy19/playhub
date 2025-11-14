import asyncio
import logging
from config import Config

logger = logging.getLogger(__name__)


class MatchSubmission:
    """Model for managing manual match submissions"""

    @staticmethod
    async def create(match_id: int, screenshot_cloudinary: str):
        """
        Create a new match submission entry.

        Args:
            match_id: The match ID
            screenshot_cloudinary: Cloudinary URL of the screenshot

        Returns:
            dict: Created submission data or None if failed
        """
        loop = asyncio.get_running_loop()

        def db_insert():
            try:
                response = (
                    Config.supabase()
                    .table("matches_submissions")
                    .insert(
                        {
                            "match_id": match_id,
                            "screenshot_cloudinary": screenshot_cloudinary,
                            "submission_link": None,
                        }
                    )
                    .execute()
                )

                if response.data and len(response.data) > 0:
                    logger.info(f"🟢 Created submission for match {match_id}")
                    return response.data[0]
                return None
            except Exception as e:
                logger.error(f"🟢 Error creating match submission: {e}")
                return None

        return await loop.run_in_executor(None, db_insert)

    @staticmethod
    async def get(match_id: int):
        """
        Get a submission by match ID.

        Args:
            match_id: The match ID

        Returns:
            dict: Submission data or None if not found
        """
        loop = asyncio.get_running_loop()

        def db_get():
            try:
                response = (
                    Config.supabase()
                    .table("matches_submissions")
                    .select("*")
                    .eq("match_id", match_id)
                    .execute()
                )

                if response.data and len(response.data) > 0:
                    return response.data[0]
                return None
            except Exception as e:
                logger.error(f"🟢 Error getting match submission: {e}")
                return None

        return await loop.run_in_executor(None, db_get)

    @staticmethod
    async def get_all():
        """
        Get all pending submissions.

        Returns:
            list: List of all submissions
        """
        loop = asyncio.get_running_loop()

        def db_get_all():
            try:
                response = (
                    Config.supabase()
                    .table("matches_submissions")
                    .select("*")
                    .order("created_at", desc=True)
                    .execute()
                )

                if response.data:
                    logger.info(
                        f"🟢 Retrieved {len(response.data)} pending submissions"
                    )
                    return response.data
                return []
            except Exception as e:
                logger.error(f"🟢 Error getting all submissions: {e}")
                return []

        return await loop.run_in_executor(None, db_get_all)

    @staticmethod
    async def update_submission_link(match_id: int, submission_link: str):
        """
        Update the submission link for a match.

        Args:
            match_id: The match ID
            submission_link: The unique frontend URL

        Returns:
            bool: True if successful, False otherwise
        """
        loop = asyncio.get_running_loop()

        def db_update():
            try:
                response = (
                    Config.supabase()
                    .table("matches_submissions")
                    .update({"submission_link": submission_link})
                    .eq("match_id", match_id)
                    .execute()
                )

                if response.data and len(response.data) > 0:
                    logger.info(f"🟢 Updated submission link for match {match_id}")
                    return True
                return False
            except Exception as e:
                logger.error(f"🟢 Error updating submission link: {e}")
                return False

        return await loop.run_in_executor(None, db_update)

    @staticmethod
    async def delete(match_id: int):
        """
        Delete a submission after processing.

        Args:
            match_id: The match ID

        Returns:
            bool: True if successful, False otherwise
        """
        loop = asyncio.get_running_loop()

        def db_delete():
            try:
                response = (
                    Config.supabase()
                    .table("matches_submissions")
                    .delete()
                    .eq("match_id", match_id)
                    .execute()
                )

                logger.info(f"🟢 Deleted submission for match {match_id}")
                return True
            except Exception as e:
                logger.error(f"🟢 Error deleting match submission: {e}")
                return False

        return await loop.run_in_executor(None, db_delete)

    @staticmethod
    async def get_by_link(submission_link: str):
        """
        Get a submission by its unique link.

        Args:
            submission_link: The unique frontend URL

        Returns:
            dict: Submission data or None if not found
        """
        loop = asyncio.get_running_loop()

        def db_get():
            try:
                response = (
                    Config.supabase()
                    .table("matches_submissions")
                    .select("*")
                    .eq("submission_link", submission_link)
                    .execute()
                )

                if response.data and len(response.data) > 0:
                    return response.data[0]
                return None
            except Exception as e:
                logger.error(f"🟢 Error getting submission by link: {e}")
                return None

        return await loop.run_in_executor(None, db_get)
