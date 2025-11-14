from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
import logging
import asyncio
import os

logger = logging.getLogger(__name__)

# Get allowed origins from environment variable
ALLOWED_ORIGINS = os.getenv(
    "CORS_ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"
).split(",")

# Create FastAPI app
app = FastAPI(title="PlayHub Bot API", version="1.0.0")

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global bot instance - will be set from main.py
bot_instance = None


def set_bot_instance(bot):
    """Set the bot instance for API handlers to use"""
    global bot_instance
    bot_instance = bot
    logger.info("🟢 Bot instance set for FastAPI")


# ========== REQUEST/RESPONSE MODELS ==========


class NewMatchSubmissionRequest(BaseModel):
    match_id: int


class RefreshSubmissionsRequest(BaseModel):
    pass  # Empty body, just triggers refresh


class PlayerStats(BaseModel):
    nickname: str
    discord_id: str
    kills: int
    assists: int
    deaths: int


class TeamStats(BaseModel):
    score: int
    players: List[PlayerStats]


class MatchStatsRequest(BaseModel):
    team1: TeamStats
    team2: TeamStats


# ========== API ENDPOINTS ==========


@app.get("/")
async def root():
    """Health check endpoint"""
    return {"status": "ok", "service": "PlayHub Bot API", "version": "1.0.0"}


@app.post("/api/matches/new-match-submission")
async def new_match_submission(request: NewMatchSubmissionRequest):
    """
    Endpoint called by frontend when bot creates a new submission.
    Frontend will generate a unique link and update the database.

    This is actually received FROM the bot TO the frontend,
    but defined here for documentation purposes.
    """
    return {
        "success": True,
        "message": "Submission received",
        "match_id": request.match_id,
    }


@app.post("/api/matches/refresh-match-submissions")
async def refresh_match_submissions(background_tasks: BackgroundTasks):
    """
    Endpoint called by frontend after updating submission_link.
    Triggers bot to refresh the submissions view embed.
    """
    if not bot_instance:
        raise HTTPException(status_code=503, detail="Bot not ready")

    try:
        # Import here to avoid circular imports
        from cogs import manual_scoreboard

        # Schedule the embed update in the background
        background_tasks.add_task(
            manual_scoreboard.update_submissions_view_embed, bot_instance
        )

        logger.info("🟢 Scheduled submissions view embed refresh")

        return {"success": True, "message": "Submissions view refresh scheduled"}

    except Exception as e:
        logger.error(f"🟢 Error refreshing submissions view: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/matches/{match_id}/match-stats")
async def submit_match_stats(match_id: int, stats: MatchStatsRequest):
    """
    Endpoint called by frontend when admin submits match statistics.
    Processes the stats exactly like dynamic scoreboard does.
    """
    if not bot_instance:
        raise HTTPException(status_code=503, detail="Bot not ready")

    try:
        # Import here to avoid circular imports
        from cogs import manual_scoreboard

        # Process the stats
        await manual_scoreboard.process_manual_stats(
            bot=bot_instance,
            match_id=match_id,
            team1_data=stats.team1.dict(),
            team2_data=stats.team2.dict(),
        )

        logger.info(f"🟢 Processed manual stats for match {match_id}")

        return {
            "success": True,
            "message": f"Match {match_id} stats processed successfully",
        }

    except Exception as e:
        logger.error(f"🟢 Error processing match stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/matches/submissions")
async def get_all_submissions():
    """
    Get all pending match submissions.
    Used by frontend dashboard to display list of pending reviews.
    """
    try:
        from models.match_submission import MatchSubmission

        submissions = await MatchSubmission.get_all()

        return {"success": True, "submissions": submissions}

    except Exception as e:
        logger.error(f"🟢 Error fetching submissions: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/matches/{match_id}/submission")
async def get_submission_by_match_id(match_id: int):
    """
    Get submission details for a specific match.
    """
    try:
        from models.match_submission import MatchSubmission

        submission = await MatchSubmission.get(match_id)

        if not submission:
            raise HTTPException(status_code=404, detail="Submission not found")

        return {"success": True, "submission": submission}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"🟢 Error fetching submission: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ========== STARTUP/SHUTDOWN EVENTS ==========


@app.on_event("startup")
async def startup_event():
    logger.info("🟢 FastAPI server started")


@app.on_event("shutdown")
async def shutdown_event():
    logger.info("🟢 FastAPI server shutting down")
