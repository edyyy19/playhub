import discord
from discord.ext import commands
from config import Config
import logging
import asyncio

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    logger.info(f"Logged in as {bot.user} (ID: {bot.user.id})")
    logger.info("------")
    
    # Add persistent view
    from cogs.registration import RegisterView
    bot.add_view(RegisterView())
    
    # Load cogs and sync commands with retry logic
    max_retries = 3
    for attempt in range(max_retries):
        try:
            await bot.load_extension("cogs.registration")
            await bot.load_extension("cogs.matchmaking")
            synced = await bot.tree.sync()
            logger.info(f"Successfully synced {len(synced)} commands")
            for cmd in synced:
                logger.info(f"Synced command: /{cmd.name}")
            break
        except Exception as e:
            logger.error(f"Attempt {attempt + 1} failed: {e}")
            if attempt == max_retries - 1:
                logger.error("Failed to sync commands after multiple attempts")
            await asyncio.sleep(2)

@bot.command()
@commands.is_owner()
async def sync(ctx):
    """Manually sync slash commands"""
    try:
        synced = await bot.tree.sync()
        await ctx.send(f"Synced {len(synced)} commands: {', '.join([cmd.name for cmd in synced])}")
    except Exception as e:
        await ctx.send(f"Failed to sync: {e}")
        logger.error(f"Sync error: {e}")

if __name__ == "__main__":
    bot.run(Config.TOKEN)