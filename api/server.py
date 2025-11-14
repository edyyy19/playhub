import uvicorn
import asyncio
import logging
from api.main import app, set_bot_instance

logger = logging.getLogger(__name__)


class FastAPIServer:
    """Wrapper class to run FastAPI server alongside Discord bot"""

    def __init__(self, bot, host="0.0.0.0", port=8000):
        self.bot = bot
        self.host = host
        self.port = port
        self.server = None

    async def start(self):
        """Start the FastAPI server"""
        try:
            # Set the bot instance for API handlers
            set_bot_instance(self.bot)

            # Configure uvicorn
            config = uvicorn.Config(
                app=app,
                host=self.host,
                port=self.port,
                log_level="info",
                access_log=False,  # Disable access logs to reduce noise
            )

            self.server = uvicorn.Server(config)

            logger.info(f"🟢 Starting FastAPI server on {self.host}:{self.port}")

            # Run the server
            await self.server.serve()

        except Exception as e:
            logger.error(f"🟢 Error starting FastAPI server: {e}")
            raise

    async def stop(self):
        """Stop the FastAPI server"""
        if self.server:
            logger.info("🟢 Stopping FastAPI server")
            self.server.should_exit = True
