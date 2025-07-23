import discord
from discord.ext import commands, tasks
from discord import app_commands
from config import Config
from models.user import User
from assets.template_generators.leaderboard_generator import LeaderboardTemplateGenerator
import logging
from datetime import datetime
import asyncio
import os

logger = logging.getLogger(__name__)

EMBED_COLOR = Config.MAIN_COLOR  # Main brand color


class Leaderboard(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.leaderboard_messages = {}  # Store message references for auto-updates
        self.leaderboard_generator = LeaderboardTemplateGenerator()

        # Initialize database table for persistent storage
        self.bot.loop.create_task(self.init_database())

        # Load existing leaderboards from database after bot is ready
        self.bot.loop.create_task(self.load_persistent_leaderboards())

        self.update_leaderboards.start()  # Start the auto-update task

    def cog_unload(self):
        """Clean up when the cog is unloaded"""
        self.update_leaderboards.cancel()

    async def init_database(self):
        """Initialize the database table for storing leaderboard message references"""
        await self.bot.wait_until_ready()

        try:
            loop = asyncio.get_running_loop()

            def create_table_if_not_exists():
                try:
                    # Try to select from table first
                    Config.supabase().table("leaderboard_messages").select("*").limit(
                        1
                    ).execute()
                    logger.info("Leaderboard messages table already exists")
                except Exception:
                    # Table doesn't exist, we'll handle this gracefully
                    logger.info("Leaderboard messages table handling initialized")

            await loop.run_in_executor(None, create_table_if_not_exists)

        except Exception as e:
            logger.error(f"Failed to initialize leaderboard database: {e}")

    async def load_persistent_leaderboards(self):
        """Load existing leaderboard messages from database after bot restart"""
        await self.bot.wait_until_ready()

        try:
            loop = asyncio.get_running_loop()

            def get_leaderboards():
                try:
                    response = (
                        Config.supabase()
                        .table("leaderboard_messages")
                        .select("*")
                        .execute()
                    )
                    return response.data if response.data else []
                except Exception:
                    # Table might not exist yet, that's okay
                    return []

            leaderboard_data = await loop.run_in_executor(None, get_leaderboards)

            if not leaderboard_data:
                logger.info("No persistent leaderboards found")
                return

            # Reconstruct message references
            valid_leaderboards = []
            for data in leaderboard_data:
                try:
                    guild_id = data.get("guild_id")
                    channel_id = data.get("channel_id")
                    message_id = data.get("message_id")
                    league_name = data.get("league_name")

                    if not all([guild_id, channel_id, message_id]):
                        continue

                    # Get guild and channel
                    guild = self.bot.get_guild(guild_id)
                    if not guild:
                        logger.warning(
                            f"Guild {guild_id} not found, skipping leaderboard"
                        )
                        continue

                    channel = guild.get_channel(channel_id)
                    if not channel:
                        logger.warning(
                            f"Channel {channel_id} not found, skipping leaderboard"
                        )
                        continue

                    # Try to get the message
                    message = await channel.fetch_message(message_id)
                    if not message:
                        logger.warning(
                            f"Message {message_id} not found, skipping leaderboard"
                        )
                        continue

                    # Store the message reference using a compound key
                    storage_key = (
                        f"{channel_id}_{league_name}"
                        if league_name
                        else str(channel_id)
                    )
                    self.leaderboard_messages[storage_key] = {
                        "message": message,
                        "channel": channel,
                        "guild_id": guild_id,
                        "league_name": league_name,
                        "channel_id": channel_id,  # Keep for reference
                    }

                    valid_leaderboards.append(data)
                    logger.info(f"Restored leaderboard in {guild.name}#{channel.name}")

                except Exception as e:
                    logger.warning(f"Failed to restore leaderboard {data}: {e}")

            logger.info(f"Restored {len(valid_leaderboards)} persistent leaderboards")

            # Clean up any invalid entries from database
            invalid_count = len(leaderboard_data) - len(valid_leaderboards)
            if invalid_count > 0:
                await self.cleanup_invalid_leaderboards()

        except Exception as e:
            logger.error(f"Failed to load persistent leaderboards: {e}")

    async def save_leaderboard_to_database(
        self, guild_id, channel_id, message_id, league_name=None
    ):
        """Save a leaderboard message reference to the database"""
        try:
            loop = asyncio.get_running_loop()

            def save_to_db():
                try:
                    # Check for existing record with both channel_id and league_name
                    query = (
                        Config.supabase()
                        .table("leaderboard_messages")
                        .select("*")
                        .eq("channel_id", channel_id)
                    )

                    # Add league_name filter if provided
                    if league_name:
                        query = query.eq("league_name", league_name)
                    else:
                        query = query.is_("league_name", "null")

                    existing = query.execute()

                    leaderboard_data = {
                        "guild_id": guild_id,
                        "channel_id": channel_id,
                        "message_id": message_id,
                        "league_name": league_name,
                        "created_at": datetime.now().isoformat(),
                    }

                    if existing.data:
                        # Update existing record for this specific channel + league combination
                        update_query = (
                            Config.supabase()
                            .table("leaderboard_messages")
                            .update(leaderboard_data)
                            .eq("channel_id", channel_id)
                        )

                        if league_name:
                            update_query = update_query.eq("league_name", league_name)
                        else:
                            update_query = update_query.is_("league_name", "null")

                        update_query.execute()
                    else:
                        # Insert new record
                        Config.supabase().table("leaderboard_messages").insert(
                            leaderboard_data
                        ).execute()

                    return True
                except Exception as e:
                    logger.error(f"Database save error: {e}")
                    return False

            success = await loop.run_in_executor(None, save_to_db)
            if success:
                logger.info(
                    f"Saved leaderboard reference to database: guild={guild_id}, channel={channel_id}, league={league_name}"
                )

        except Exception as e:
            logger.error(f"Failed to save leaderboard to database: {e}")

    async def remove_leaderboard_from_database(self, channel_id):
        """Remove a leaderboard message reference from the database"""
        try:
            loop = asyncio.get_running_loop()

            def remove_from_db():
                try:
                    Config.supabase().table("leaderboard_messages").delete().eq(
                        "channel_id", channel_id
                    ).execute()
                    return True
                except Exception as e:
                    logger.error(f"Database delete error: {e}")
                    return False

            success = await loop.run_in_executor(None, remove_from_db)
            if success:
                logger.info(
                    f"Removed leaderboard reference from database: channel={channel_id}"
                )

        except Exception as e:
            logger.error(f"Failed to remove leaderboard from database: {e}")

    async def cleanup_invalid_leaderboards(self):
        """Clean up invalid leaderboard references from database"""
        try:
            # Extract valid channel+league combinations from memory
            valid_combinations = set()
            for storage_key, data in self.leaderboard_messages.items():
                channel_id = data.get("channel_id")
                league_name = data.get("league_name")
                if channel_id:
                    valid_combinations.add((channel_id, league_name))

            loop = asyncio.get_running_loop()

            def cleanup_db():
                try:
                    # Get all records
                    all_records = (
                        Config.supabase()
                        .table("leaderboard_messages")
                        .select("channel_id, league_name, message_id")
                        .execute()
                    )

                    if not all_records.data:
                        return 0

                    # Find invalid records
                    invalid_records = []
                    for record in all_records.data:
                        channel_id = record["channel_id"]
                        league_name = record.get("league_name")
                        combination = (channel_id, league_name)

                        if combination not in valid_combinations:
                            invalid_records.append(record)

                    # Remove invalid records
                    removed_count = 0
                    for record in invalid_records:
                        try:
                            query = (
                                Config.supabase()
                                .table("leaderboard_messages")
                                .delete()
                                .eq("channel_id", record["channel_id"])
                                .eq("message_id", record["message_id"])
                            )

                            # Add league_name filter
                            if record.get("league_name"):
                                query = query.eq("league_name", record["league_name"])
                            else:
                                query = query.is_("league_name", "null")

                            query.execute()
                            removed_count += 1
                        except Exception as e:
                            logger.error(
                                f"Failed to remove invalid record {record}: {e}"
                            )

                    return removed_count
                except Exception as e:
                    logger.error(f"Database cleanup error: {e}")
                    return 0

            cleaned_count = await loop.run_in_executor(None, cleanup_db)
            if cleaned_count > 0:
                logger.info(
                    f"Cleaned up {cleaned_count} invalid leaderboard references from database"
                )

        except Exception as e:
            logger.error(f"Failed to cleanup invalid leaderboards: {e}")

    async def league_name_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """
        Autocomplete function for league names - uses centralized league data function
        """
        try:
            # Get all leagues using centralized function
            leagues_data = await self.get_league_data()

            if not leagues_data:
                return []  # Filter based on current input and create choices
            choices = []
            for league_name, league_info in leagues_data.items():
                # Use original_league_name if available, fallback to formatted name
                display_name = (
                    league_info["original_league_name"]
                    or league_name.replace("_", " ").title()
                )

                # Filter based on current input
                if (
                    current.lower() in display_name.lower()
                    or current.lower() in league_name.lower()
                ):
                    choices.append(
                        app_commands.Choice(name=display_name, value=league_name)
                    )
                # Limit to 25 choices (Discord limit)
                if len(choices) >= 25:
                    break

            return choices

        except Exception as e:
            logger.error(f"Error in league autocomplete: {e}")
            return []

    async def leaderboard_exists_for_league_and_channel(
        self, channel_id: int, league_name: str
    ) -> bool:
        """
        Check if a leaderboard already exists for a specific league in a specific channel
        """
        try:
            loop = asyncio.get_running_loop()

            def check_exists():
                try:
                    response = (
                        Config.supabase()
                        .table("leaderboard_messages")
                        .select("*")
                        .eq("channel_id", channel_id)
                        .eq("league_name", league_name)
                        .execute()
                    )
                    return len(response.data) > 0
                except Exception as e:
                    logger.error(f"Failed to check leaderboard existence: {e}")
                    return False

            return await loop.run_in_executor(None, check_exists)

        except Exception as e:
            logger.error(f"Failed to check leaderboard existence: {e}")
            return False

    @app_commands.command(
        name="leaderboard",
        description="Display the league-specific leaderboard in the specified channel.",
    )
    @app_commands.describe(
        channel="The channel where the leaderboard should be posted",
        league_name="The league to display the leaderboard for",
    )
    @app_commands.autocomplete(league_name=league_name_autocomplete)
    @app_commands.default_permissions(administrator=True)
    async def leaderboard(
        self,
        interaction: discord.Interaction,
        league_name: str,
        channel: discord.TextChannel = None,
    ):
        """Create or update a league-specific leaderboard embed in the specified channel"""
        # Verifică autorizația utilizatorului
        if not await Config.check_authorization(interaction):
            return

        # Validate league name
        if not league_name or len(league_name.strip()) == 0:
            embed = discord.Embed(
                title=f"{Config.ICONS['unsuccess']} Invalid League Name",
                description="League name cannot be empty.",
                color=Config.EMBED_COLORS["red"],
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        league_name = league_name.strip()

        # Get league data from leagues cog
        from cogs.leagues import Leagues

        leagues_cog = self.bot.get_cog("Leagues")
        if not leagues_cog:
            embed = discord.Embed(
                title=f"{Config.ICONS['unsuccess']} System Error",
                description="Leagues system is not available.",
                color=Config.EMBED_COLORS["red"],
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        league_data = await leagues_cog.get_league_by_name(league_name)
        if not league_data:
            embed = discord.Embed(
                title=f"{Config.ICONS['unsuccess']} League Not Found",
                description=f"No league found with the name '{league_name}'.",
                color=Config.EMBED_COLORS["red"],
            )
            embed.add_field(
                name="Available Leagues",
                value="Use the autocomplete to select from available leagues.",
                inline=False,
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        sanitized_name = league_data["league_name"]
        # Get the original league name for display purposes
        original_league_name = league_data.get("original_league_name") or league_name

        # Check if there's an active season
        from models.season import Season

        if not await Season.is_season_active():
            # Get the next season number
            last_season_id = await Season.get_last_season_id()
            next_season_number = last_season_id + 1

            embed = discord.Embed(
                title=f"{Config.ICONS['unsuccess']} Season Not Active",
                description="Leaderboard is currently unavailable.",
                color=0xFF4444,
            )
            embed.add_field(
                name="💡 Information",
                value=f"Please wait for Season {next_season_number} to start.",
                inline=False,
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # Use current channel if none specified
        target_channel = channel or interaction.channel

        # Check if bot has permissions in the target channel
        if not target_channel.permissions_for(interaction.guild.me).send_messages:
            await interaction.response.send_message(
                f"{Config.ICONS['unsuccess']} I don't have permission to send messages in {target_channel.mention}",
                ephemeral=True,
            )
            return

        # Check if a leaderboard for this league already exists in this channel
        if await self.leaderboard_exists_for_league_and_channel(
            target_channel.id, sanitized_name
        ):
            embed = discord.Embed(
                title=f"{Config.ICONS['unsuccess']} Leaderboard Already Exists",
                description=f"A leaderboard for league '{original_league_name}' already exists in {target_channel.mention}.",
                color=Config.EMBED_COLORS["red"],
            )
            embed.add_field(
                name="Tip",
                value="Use `/leaderboard_stop` in that channel first to remove the existing leaderboard.",
                inline=False,
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        try:
            # Create the league-specific leaderboard embed
            embed, file, error_msg = await self.create_league_leaderboard_embed(
                sanitized_name, original_league_name
            )

            if error_msg:
                await interaction.followup.send(
                    f"{Config.ICONS['unsuccess']} {error_msg}", ephemeral=True
                )
                return

            # Send the leaderboard to the target channel
            if file:
                message = await target_channel.send(embed=embed, file=file)
            else:
                message = await target_channel.send(embed=embed)

            # Store the message reference for auto-updates using compound key
            storage_key = f"{target_channel.id}_{sanitized_name}"
            self.leaderboard_messages[storage_key] = {
                "message": message,
                "channel": target_channel,
                "guild_id": interaction.guild.id,
                "league_name": sanitized_name,  # Store league name for future reference
                "channel_id": target_channel.id,  # Keep for reference
            }

            # Save to database for persistence
            await self.save_leaderboard_to_database(
                interaction.guild.id, target_channel.id, message.id, sanitized_name
            )

            # Success response
            embed_response = discord.Embed(
                title=f"{Config.ICONS['success']} Leaderboard Posted",
                description=f"Leaderboard for **{original_league_name}** has been posted in {target_channel.mention}",
                color=Config.EMBED_COLORS["light_green"],
            )
            embed_response.add_field(
                name="Auto-Update",
                value="The leaderboard will automatically update every 1 hour",
                inline=False,
            )
            await interaction.followup.send(embed=embed_response, ephemeral=True)

        except Exception as e:
            logger.error(f"Failed to create leaderboard: {e}")
            await interaction.followup.send(
                f"{Config.ICONS['unsuccess']} An error occurred while creating the leaderboard. Please try again.",
                ephemeral=True,
            )

    async def create_league_leaderboard_embed(
        self, league_table_name: str, display_name: str
    ):
        """Create the leaderboard using Pillow instead of HTML template"""
        try:
            # Fetch all users from the specific league table
            users_data = await self.get_league_users(league_table_name)

            # Process and sort users by points
            processed_users = []
            for user in users_data:
                try:
                    # Calculate total games, kills, deaths
                    wins = user.get("wins", 0)
                    losses = user.get("loses", 0)  # Note: database uses 'loses' not 'losses'
                    total_games = wins + losses
                    kills = user.get("kills", 0)
                    deaths = user.get("deaths", 0)

                    processed_users.append(
                        {
                            "nickname": user.get("nickname", "Unknown"),
                            "discord_id": user.get("discord_id"),
                            "points": user.get("points", 0),
                            "wins": wins,
                            "losses": losses,
                            "total_games": total_games,
                            "kills": kills,
                            "deaths": deaths,
                        }
                    )
                except Exception as e:
                    logger.warning(f"Error processing user data: {user}, error: {e}")
                    continue

            # Sort by points (descending) and take top 10
            processed_users.sort(key=lambda x: x["points"], reverse=True)
            top_players = processed_users[:10]

            # Generate leaderboard image using the dedicated template generator
            image_path = await self.leaderboard_generator.generate_leaderboard_image(
                self.bot, top_players, display_name, league_table_name
            )

            if not image_path or not os.path.exists(image_path):
                return None, None, "Failed to generate leaderboard image"

            # Create Discord file
            file = discord.File(image_path, filename="leaderboard.png")

            # Schedule cleanup of temp file after a delay
            asyncio.create_task(self.cleanup_temp_file(image_path, delay=10))

            # Create simple embed to accompany the image
            embed = discord.Embed(
                title=f"{Config.ICONS['league']} {display_name} Leaderboard",
                description="Top players ranked by points",
                color=EMBED_COLOR,
            )
            embed.set_image(url="attachment://leaderboard.png")
            embed.set_footer(
                text=f"Last Updated: {datetime.now().strftime('%H:%M | %d/%m/%Y')}"
            )

            return embed, file, None

        except Exception as e:
            logger.error(f"Failed to create league leaderboard image: {e}")
            return None, None, f"Error generating leaderboard: {str(e)}"

    async def get_league_users(self, league_table_name: str):
        """Fetch all users from a specific league table"""
        try:
            # Get users data from league table (without nickname)
            response = (
                Config.supabase()
                .table(league_table_name)
                .select("discord_id, points, wins, loses")
                .order("points", desc=True)
                .execute()
            )

            league_users = response.data if response.data else []

            # Get all discord_ids for batch nickname lookup
            discord_ids = [
                int(user["discord_id"])
                for user in league_users
                if user.get("discord_id")
            ]

            # Get nicknames in batch from users table
            nicknames_dict = await User.get_nicknames_by_discord_ids(discord_ids)

            # Add nicknames to user data
            for user in league_users:
                discord_id = user.get("discord_id")
                user["nickname"] = nicknames_dict.get(discord_id, "Unknown")

            return league_users
        except Exception as e:
            logger.error(
                f"Failed to fetch users from league table {league_table_name}: {e}"
            )
            raise

    async def cleanup_temp_file(self, file_path: str, delay: int = 10):
        """Safely cleanup a temporary file after a delay to allow Discord to process it"""
        try:
            await asyncio.sleep(delay)  # Wait for Discord to process the file
            if os.path.exists(file_path):
                os.remove(file_path)
                logger.debug(f"Cleaned up temporary file: {file_path}")
        except Exception as e:
            logger.warning(f"Failed to cleanup temporary file {file_path}: {e}")

    @tasks.loop(hours=1)
    async def update_leaderboards(self):
        """Automatically update all active leaderboard messages every 1 hour"""
        if not self.leaderboard_messages:
            return logger.info(
                f"Updating {len(self.leaderboard_messages)} leaderboard messages"
            )

        # Update each active leaderboard message by deleting and recreating
        messages_to_remove = []
        messages_to_update = []
        for storage_key, data in self.leaderboard_messages.items():
            try:
                message = data["message"]
                channel = data["channel"]
                channel_id = data.get("channel_id", channel.id if channel else None)
                league_name = data.get("league_name")
                guild_id = data.get("guild_id")

                # Check if message and channel still exist
                if not channel or not message:
                    messages_to_remove.append(storage_key)
                    continue

                # Validate league_name exists
                if not league_name:
                    logger.warning(
                        f"Leaderboard with key {storage_key} has no league_name, removing"
                    )
                    messages_to_remove.append(storage_key)
                    continue

                # Check if we have necessary data for recreation
                if not guild_id or not channel_id:
                    logger.warning(
                        f"Leaderboard with key {storage_key} missing guild_id or channel_id, removing"
                    )
                    messages_to_remove.append(storage_key)
                    continue

                # Store info for recreation
                messages_to_update.append(
                    {
                        "storage_key": storage_key,
                        "channel": channel,
                        "channel_id": channel_id,
                        "league_name": league_name,
                        "guild_id": guild_id,
                        "old_message": message,
                    }
                )

            except Exception as e:
                logger.error(f"Error processing leaderboard {storage_key}: {e}")
                messages_to_remove.append(storage_key)

        # Delete old messages and create new ones
        for update_data in messages_to_update:
            try:
                storage_key = update_data["storage_key"]
                channel = update_data["channel"]
                channel_id = update_data["channel_id"]
                league_name = update_data["league_name"]
                guild_id = update_data["guild_id"]
                old_message = update_data["old_message"]

                # Create new leaderboard image
                # Get the original league name using centralized function
                league_data = await self.get_league_data(league_name=league_name)
                # Use original_league_name if available, fallback to formatted name
                display_name = (
                    league_data["original_league_name"]
                    or league_name.replace("_", " ").title()
                )

                embed, file, error_msg = await self.create_league_leaderboard_embed(
                    league_name, display_name
                )

                if error_msg:
                    logger.error(
                        f"Failed to create leaderboard for auto-update: {error_msg}"
                    )
                    continue

                # Delete old message first
                try:
                    await old_message.delete()
                    logger.info(f"Deleted old leaderboard message in {channel.name}")
                except discord.NotFound:
                    # Message was already deleted
                    pass
                except Exception as e:
                    logger.warning(f"Failed to delete old message: {e}")

                # Send new leaderboard
                if file:
                    new_message = await channel.send(embed=embed, file=file)
                else:
                    new_message = await channel.send(embed=embed)

                # Update storage with new message reference
                self.leaderboard_messages[storage_key] = {
                    "message": new_message,
                    "channel": channel,
                    "guild_id": guild_id,
                    "league_name": league_name,
                    "channel_id": channel_id,
                }

                # Update database with new message_id
                await self.save_leaderboard_to_database(
                    guild_id, channel_id, new_message.id, league_name
                )

                logger.info(f"Recreated leaderboard in channel {channel.name}")

            except discord.NotFound:
                # Channel was deleted
                logger.info(f"Channel for leaderboard {storage_key} was deleted")
                messages_to_remove.append(storage_key)
            except discord.Forbidden:
                # No permission
                logger.warning(f"No permission to update leaderboard in {storage_key}")
                messages_to_remove.append(storage_key)
            except Exception as e:
                logger.error(f"Failed to recreate leaderboard {storage_key}: {e}")
                messages_to_remove.append(storage_key)

        # Remove invalid message references
        for storage_key in messages_to_remove:
            data = self.leaderboard_messages.get(storage_key, {})
            channel_id = data.get("channel_id")

            del self.leaderboard_messages[storage_key]

            # Remove from database using channel_id
            if channel_id:
                await self.remove_leaderboard_from_database(channel_id)

    @update_leaderboards.before_loop
    async def before_update_leaderboards(self):
        """Wait for the bot to be ready before starting the loop"""
        await self.bot.wait_until_ready()

    @app_commands.command(
        name="leaderboard_stop",
        description="Stop auto-updating leaderboards in this channel.",
    )
    @app_commands.default_permissions(administrator=True)
    async def stop_leaderboard(self, interaction: discord.Interaction):
        """Stop auto-updating leaderboards in the current channel and delete the messages"""
        # Verifică autorizația utilizatorului
        if not await Config.check_authorization(interaction):
            return
            
        channel_id = interaction.channel.id

        # Get all leaderboard messages for this channel from database
        try:
            loop = asyncio.get_running_loop()

            def get_channel_leaderboards():
                try:
                    response = (
                        Config.supabase()
                        .table("leaderboard_messages")
                        .select("*")
                        .eq("channel_id", channel_id)
                        .execute()
                    )
                    return response.data if response.data else []
                except Exception as e:
                    logger.error(f"Failed to fetch channel leaderboards: {e}")
                    return []

            channel_leaderboards = await loop.run_in_executor(
                None, get_channel_leaderboards
            )

            # Also check in memory for this channel
            memory_keys_to_remove = []
            for storage_key, data in self.leaderboard_messages.items():
                if data.get("channel_id") == channel_id:
                    memory_keys_to_remove.append(storage_key)

            if not channel_leaderboards and not memory_keys_to_remove:
                embed = discord.Embed(
                    title=f"{Config.ICONS['unsuccess']} No Active Leaderboard",
                    description="There is no active auto-updating leaderboard in this channel.",
                    color=Config.EMBED_COLORS["red"],
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return

            deleted_count = 0

            # Delete messages and remove from memory
            for leaderboard in channel_leaderboards:
                try:
                    message_id = leaderboard.get("message_id")
                    if message_id:
                        try:
                            message = await interaction.channel.fetch_message(
                                message_id
                            )
                            await message.delete()
                            deleted_count += 1
                        except discord.NotFound:
                            # Message was already deleted
                            pass
                        except Exception as e:
                            logger.warning(
                                f"Failed to delete message {message_id}: {e}"
                            )

                except Exception as e:
                    logger.warning(f"Error processing leaderboard deletion: {e}")

            # Remove from memory
            for storage_key in memory_keys_to_remove:
                if storage_key in self.leaderboard_messages:
                    del self.leaderboard_messages[storage_key]

            # Remove all entries for this channel from database
            def remove_channel_leaderboards():
                try:
                    Config.supabase().table("leaderboard_messages").delete().eq(
                        "channel_id", channel_id
                    ).execute()
                    return True
                except Exception as e:
                    logger.error(f"Database delete error: {e}")
                    return False

            await loop.run_in_executor(None, remove_channel_leaderboards)

            embed = discord.Embed(
                title=f"{Config.ICONS['success']} Leaderboards Stopped",
                description=f"All leaderboards in this channel have been stopped and removed.",
                color=Config.EMBED_COLORS["light_green"],
            )
            if deleted_count > 0:
                embed.add_field(
                    name="Messages Deleted",
                    value=f"{deleted_count} leaderboard message(s) were deleted.",
                    inline=False,
                )
            await interaction.response.send_message(embed=embed, ephemeral=True)

        except Exception as e:
            logger.error(f"Error in stop_leaderboard: {e}")
            embed = discord.Embed(
                title=f"{Config.ICONS['unsuccess']} Error",
                description="An error occurred while stopping the leaderboards.",
                color=Config.EMBED_COLORS["red"],
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name="leaderboard_status",
        description="Check the status of active leaderboards.",
    )
    @app_commands.default_permissions(administrator=True)
    async def leaderboard_status(self, interaction: discord.Interaction):
        """Show status of all active leaderboards"""
        # Verifică autorizația utilizatorului
        if not await Config.check_authorization(interaction):
            return
            
        if not self.leaderboard_messages:
            embed = discord.Embed(
                title=f"{Config.ICONS['info']} Leaderboard Status",
                description="No active auto-updating leaderboards found.",
                color=Config.EMBED_COLORS["yellow"],
            )
        else:
            embed = discord.Embed(
                title=f"{Config.ICONS['stats']} Leaderboard Status",
                description=f"**{len(self.leaderboard_messages)}** active auto-updating leaderboard(s)",
                color=EMBED_COLOR,
            )

            # Fetch original league names for all active leagues
            league_names_to_fetch = set()
            for data in self.leaderboard_messages.values():
                league_name = data.get("league_name")
                if league_name:
                    league_names_to_fetch.add(league_name)

                # Get original league names from database using centralized function
                league_display_names = {}
                if league_names_to_fetch:
                    try:
                        leagues_data = await self.get_league_data(
                            league_names=list(league_names_to_fetch)
                        )
                        # Extract display names for quick lookup
                        for league_name, league_info in leagues_data.items():
                            # Use original_league_name if available, fallback to formatted name
                            display_name = (
                                league_info["original_league_name"]
                                or league_name.replace("_", " ").title()
                            )
                            league_display_names[league_name] = display_name

                    except Exception as e:
                        logger.error(f"Error fetching league display names: {e}")

            channels_list = []
            for storage_key, data in self.leaderboard_messages.items():
                channel = data["channel"]
                league_name = data.get("league_name")
                if channel:
                    if league_name:
                        # Use original league name if available, fallback to formatted name
                        display_name = league_display_names.get(
                            league_name, league_name.replace("_", " ").title()
                        )
                        league_info = f" ({display_name})"
                    else:
                        league_info = ""
                    channels_list.append(f"• {channel.mention}{league_info}")
                else:
                    channels_list.append(f"• Storage key: {storage_key} (deleted)")

            if channels_list:
                embed.add_field(
                    name="Active Channels", value="\n".join(channels_list), inline=False
                )

            embed.add_field(name="Update Frequency", value="Every 1 hour", inline=True)

            if hasattr(self.update_leaderboards, "next_iteration"):
                next_update = self.update_leaderboards.next_iteration
                if next_update:
                    embed.add_field(
                        name="Next Update",
                        value=f"<t:{int(next_update.timestamp())}:R>",
                        inline=True,
                    )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def get_league_data(self, league_name: str = None, league_names: list = None):
        """
        Centralized function to get league data including original_league_name.

        Args:
            league_name: Single league name to fetch (mutually exclusive with league_names)
            league_names: List of league names to fetch (mutually exclusive with league_name)

        Returns:
            dict: For single league - {"league_name": str, "original_league_name": str}
            dict: For multiple leagues - {league_name: {"league_name": str, "original_league_name": str}}
        """
        try:
            loop = asyncio.get_running_loop()

            def fetch_leagues():
                try:
                    query = (
                        Config.supabase()
                        .table("leagues")
                        .select("league_name, original_league_name")
                    )

                    if league_name:
                        query = query.eq("league_name", league_name)
                    elif league_names:
                        query = query.in_("league_name", league_names)
                    else:
                        # Fetch all leagues if no specific names provided
                        query = query.order("created_at", desc=True)

                    response = query.execute()
                    return response.data if response.data else []
                except Exception as e:
                    logger.error(f"Failed to fetch league data: {e}")
                    return []

            leagues_data = await loop.run_in_executor(None, fetch_leagues)

            # Process the data
            processed_leagues = {}
            for league in leagues_data:
                table_name = league["league_name"]
                original_name = league.get("original_league_name")

                processed_leagues[table_name] = {
                    "league_name": table_name,
                    "original_league_name": original_name,
                }

            # Return format based on input
            if league_name:
                # Single league requested
                return processed_leagues.get(
                    league_name,
                    {
                        "league_name": league_name,
                        "original_league_name": None,
                    },
                )
            else:
                # Multiple leagues or all leagues
                return processed_leagues

        except Exception as e:
            logger.error(f"Error in get_league_data: {e}")
            # Return fallback data
            if league_name:
                return {
                    "league_name": league_name,
                    "original_league_name": None,
                }
            else:
                return {}


async def setup(bot):
    await bot.add_cog(Leaderboard(bot))
