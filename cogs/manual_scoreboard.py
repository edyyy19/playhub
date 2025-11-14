import discord
from discord.ext import commands
from discord import app_commands
import logging
import aiohttp
import os
import json
import asyncio
from datetime import datetime
from config import Config
from models.match_submission import MatchSubmission
from models.match import Match
from models.stats import Stats
from models.scoreboard_config import ScoreboardConfig
from assets.template_generators.scoreboard_generator import (
    ScoreboardResultsTemplateGenerator,
)

logger = logging.getLogger(__name__)

CLOUDINARY_CLOUD_NAME = "dzhebhupq"
CLOUDINARY_UPLOAD_PRESET = "ml_default"
FRONTEND_API_URL = os.getenv("FRONTEND_API_URL", "http://localhost:3000")


async def upload_to_cloudinary(image_url: str) -> str:
    """
    Upload an image to Cloudinary and return the public URL.

    Args:
        image_url: Discord attachment URL

    Returns:
        Cloudinary public URL
    """
    try:
        async with aiohttp.ClientSession() as session:
            # Download the image from Discord
            async with session.get(image_url) as resp:
                if resp.status != 200:
                    raise Exception(f"Failed to download image: {resp.status}")
                image_data = await resp.read()

            # Upload to Cloudinary
            upload_url = (
                f"https://api.cloudinary.com/v1_1/{CLOUDINARY_CLOUD_NAME}/image/upload"
            )

            form_data = aiohttp.FormData()
            form_data.add_field("file", image_data, filename="screenshot.png")
            form_data.add_field("upload_preset", CLOUDINARY_UPLOAD_PRESET)

            async with session.post(upload_url, data=form_data) as upload_resp:
                if upload_resp.status != 200:
                    error_text = await upload_resp.text()
                    raise Exception(f"Cloudinary upload failed: {error_text}")

                result = await upload_resp.json()
                cloudinary_url = result.get("secure_url")

                logger.info(
                    f"🟢 Successfully uploaded image to Cloudinary: {cloudinary_url}"
                )
                return cloudinary_url

    except Exception as e:
        logger.error(f"🟢 Error uploading to Cloudinary: {e}", exc_info=True)
        raise


async def handle_manual_submission(
    interaction: discord.Interaction,
    match_id: str,
    match_data: dict,
    screenshot_url: str,
    screenshot_message: discord.Message,
):
    """
    Handle manual scoreboard submission by uploading screenshot to Cloudinary,
    storing in database, and notifying the frontend dashboard.

    Args:
        interaction: The Discord interaction object
        match_id: The match ID for this submission
        match_data: Match data from Supabase containing team and player info
        screenshot_url: Discord URL of the uploaded screenshot
        screenshot_message: The message containing the screenshot (to delete later)
    """
    try:
        user = interaction.user

        # Upload screenshot to Cloudinary
        try:
            cloudinary_url = await upload_to_cloudinary(screenshot_url)
        except Exception as e:
            embed = discord.Embed(
                title=f"{Config.ICONS['unsuccess']} Upload Failed",
                description="Failed to upload screenshot to Cloudinary. Please try again.",
                color=Config.EMBED_COLORS["red"],
            )
            embed.add_field(
                name=f"{Config.ICONS['info']} Error Details",
                value=f"Technical error: {str(e)[:200]}",
                inline=False,
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        # Delete the original screenshot message from Discord
        try:
            await screenshot_message.delete()
            logger.info(f"🟢 Deleted screenshot message for match {match_id}")
        except Exception as e:
            logger.warning(f"🟢 Could not delete screenshot message: {e}")

        # Create submission entry in database
        submission = await MatchSubmission.create(
            match_id=int(match_id), screenshot_cloudinary=cloudinary_url
        )

        if not submission:
            embed = discord.Embed(
                title=f"{Config.ICONS['unsuccess']} Database Error",
                description="Failed to save submission to database. Please try again.",
                color=Config.EMBED_COLORS["red"],
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        # Send notification to frontend to generate unique link
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{FRONTEND_API_URL}/api/matches/new-match-submission",
                    json={"match_id": int(match_id)},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status != 200:
                        logger.warning(
                            f"🟢 Frontend notification failed: {resp.status}"
                        )
                    else:
                        logger.info(
                            f"🟢 Notified frontend about match {match_id} submission"
                        )
        except Exception as frontend_error:
            logger.error(f"🟢 Error notifying frontend: {frontend_error}")
            # Continue even if frontend notification fails - admin can still access via view channel

        # Send confirmation to user
        confirmation_embed = discord.Embed(
            title=f"{Config.ICONS['success']} Screenshot Submitted Successfully!",
            description="Your scoreboard screenshot has been forwarded to the admin team for review.",
            color=Config.EMBED_COLORS["light_green"],
        )

        confirmation_embed.add_field(
            name=f"{Config.ICONS['info']} What's Next?",
            value=(
                "• Admins will review your submission via the dashboard\n"
                "• Results will be processed and posted\n"
                "• You'll be notified once complete\n"
                "• Check the submissions view channel for updates"
            ),
            inline=False,
        )

        confirmation_embed.set_footer(text=f"Match ID: {match_id}")

        await interaction.followup.send(embed=confirmation_embed, ephemeral=True)

        logger.info(
            f"🟢 Created manual submission for match {match_id} by user {user.id} "
            f"with Cloudinary URL: {cloudinary_url}"
        )

    except Exception as e:
        logger.error(f"🟢 Error handling manual submission: {e}", exc_info=True)

        try:
            embed = discord.Embed(
                title=f"{Config.ICONS['unsuccess']} Submission Error",
                description="An error occurred while processing your submission.",
                color=Config.EMBED_COLORS["red"],
            )
            embed.add_field(
                name=f"{Config.ICONS['info']} Next Steps",
                value="Please contact a staff member for assistance.",
                inline=False,
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
        except:
            pass


async def update_submissions_view_embed(bot):
    """
    Update the submissions view embed with all pending submissions.
    Called on bot startup and when frontend sends refresh request.
    """
    try:
        # Get all scoreboard configs
        loop = asyncio.get_running_loop()

        def get_configs():
            try:
                response = (
                    Config.supabase().table("scoreboard_config").select("*").execute()
                )
                return response.data if response.data else []
            except Exception as e:
                logger.error(f"🟢 Error fetching scoreboard configs: {e}")
                return []

        configs = await loop.run_in_executor(None, get_configs)

        for config in configs:
            view_channel_id = config.get("view_submissions_channel_id")
            view_message_id = config.get("view_submissions_message_id")
            guild_id = config.get("guild_id")

            if not view_channel_id:
                continue  # No view channel configured for this guild

            guild = bot.get_guild(guild_id)
            if not guild:
                logger.warning(
                    f"🟢 Guild {guild_id} not found for submissions view update"
                )
                continue

            view_channel = guild.get_channel(view_channel_id)
            if not view_channel:
                logger.warning(f"🟢 View channel {view_channel_id} not found")
                continue

            # Get all pending submissions
            submissions = await MatchSubmission.get_all()

            # Create embed
            embed = discord.Embed(
                title=f"{Config.ICONS['stats']} Pending Match Submissions",
                description=(
                    "Manual submissions awaiting admin review"
                    if submissions
                    else "No pending submissions"
                ),
                color=(Config.MAIN_COLOR),
            )

            if submissions:
                # Add each submission as a field
                for idx, submission in enumerate(
                    submissions[:25], 1
                ):  # Discord limit: 25 fields
                    match_id = submission.get("match_id")
                    submission_link = submission.get("submission_link")
                    created_at = submission.get("created_at", "Unknown")

                    # Format timestamp
                    try:
                        from datetime import datetime

                        timestamp_obj = datetime.fromisoformat(
                            created_at.replace("Z", "+00:00")
                        )
                        formatted_time = timestamp_obj.strftime("%H:%M | %d/%m/%Y")
                    except:
                        formatted_time = "Unknown time"

                    field_value = f"**Match ID:** {match_id}\n"
                    field_value += f"**Submitted:** {formatted_time}\n"

                    if submission_link:
                        field_value += (
                            f"**Review:** [Open Dashboard]({submission_link})"
                        )
                    else:
                        field_value += f"**Status:** Generating link..."

                    embed.add_field(
                        name=f"{Config.ICONS['game']} Submission #{idx}",
                        value=field_value,
                        inline=False,
                    )

                embed.set_footer(
                    text=f"Total pending: {len(submissions)} | Updates automatically"
                )

            # Update or create message
            try:
                if view_message_id:
                    # Try to edit existing message
                    try:
                        message = await view_channel.fetch_message(view_message_id)
                        await message.edit(embed=embed)
                        logger.info(
                            f"🟢 Updated submissions view embed for guild {guild_id}"
                        )
                    except discord.NotFound:
                        # Message was deleted, create new one
                        message = await view_channel.send(embed=embed)

                        # Update config with new message ID
                        def update_message_id():
                            Config.supabase().table("scoreboard_config").update(
                                {"view_submissions_message_id": message.id}
                            ).eq("guild_id", guild_id).execute()

                        await loop.run_in_executor(None, update_message_id)
                        logger.info(
                            f"🟢 Created new submissions view embed for guild {guild_id}"
                        )
                else:
                    # No message ID, create new message
                    message = await view_channel.send(embed=embed)

                    # Update config with new message ID
                    def update_message_id():
                        Config.supabase().table("scoreboard_config").update(
                            {"view_submissions_message_id": message.id}
                        ).eq("guild_id", guild_id).execute()

                    await loop.run_in_executor(None, update_message_id)
                    logger.info(
                        f"🟢 Created new submissions view embed for guild {guild_id}"
                    )

            except Exception as msg_error:
                logger.error(f"🟢 Error updating/creating view message: {msg_error}")

    except Exception as e:
        logger.error(f"🟢 Error in update_submissions_view_embed: {e}", exc_info=True)


async def process_manual_stats(bot, match_id: int, team1_data: dict, team2_data: dict):
    """
    Process manually submitted stats from admin dashboard.
    Reuses the same logic as dynamic scoreboard after GPT extraction.

    Args:
        bot: Discord bot instance
        match_id: The match ID
        team1_data: {"score": int, "players": [{"nickname", "discord_id", "kills", "assists", "deaths"}]}
        team2_data: {"score": int, "players": [{"nickname", "discord_id", "kills", "assists", "deaths"}]}
    """
    try:
        # Get match data
        match_data = await Match.find(match_id)
        if not match_data:
            logger.error(f"🟢 Match {match_id} not found for manual stats processing")
            return

        # Extract scores
        team1_score = team1_data.get("score", 0)
        team2_score = team2_data.get("score", 0)

        # Get league
        league_name = match_data.get("league")

        # Get default league for level calculations
        default_league = await _get_default_league_from_config()

        # Collect all player IDs for batch query
        all_player_ids = []
        all_players_data = []

        for player in team1_data.get("players", []):
            discord_id = int(player["discord_id"])
            all_player_ids.append(discord_id)
            all_players_data.append(
                (player, 1, team1_score > team2_score)
            )  # player, team, won

        for player in team2_data.get("players", []):
            discord_id = int(player["discord_id"])
            all_player_ids.append(discord_id)
            all_players_data.append(
                (player, 2, team2_score > team1_score)
            )  # player, team, won

        # Fetch stats in batch
        stats_batch = await Stats.get_multiple_user_stats(all_player_ids, league_name)
        logger.info(
            f"🟢 [MANUAL] Fetched stats in batch for {len(all_player_ids)} players in league {league_name}"
        )

        # Process all players
        updated_team1 = []
        updated_team2 = []

        for player_data, team_num, team_won in all_players_data:
            discord_id = int(player_data["discord_id"])

            # Get current points from batch stats (before match)
            stats_result = stats_batch.get(discord_id)
            old_points = 0
            if stats_result and stats_result.get("success"):
                stats_data = stats_result.get("stats", {})
                old_points = stats_data.get("points", 0)

            # Calculate points using batch stats
            points_received = await _calculate_player_points_batch(
                discord_id, league_name, False, team_won, stats_batch, default_league
            )

            # Calculate total points after this match
            current_points = old_points + points_received

            # Calculate rating
            kills = player_data.get("kills", 0)
            assists = player_data.get("assists", 0)
            deaths = max(1, player_data.get("deaths", 1))
            rating = round((kills + 0.5 * assists) / deaths, 1)

            updated_player = {
                "nickname": player_data["nickname"],
                "discord_id": str(discord_id),
                "kills": kills,
                "assists": assists,
                "deaths": player_data.get("deaths", 0),
                "rating": rating,
                "points": points_received,  # Delta for display
                "points_received": points_received,  # Delta for database
                "current_points": current_points,  # Total points after match for level icon
            }

            if team_num == 1:
                updated_team1.append(updated_player)
            else:
                updated_team2.append(updated_player)

        # Prepare data for database
        team1_for_db = []
        team2_for_db = []

        for player in updated_team1:
            team1_for_db.append(
                {
                    "nickname": player["nickname"],
                    "discord_id": player["discord_id"],
                    "kills": player["kills"],
                    "assists": player["assists"],
                    "deaths": player["deaths"],
                    "points_received": player["points_received"],
                }
            )

        for player in updated_team2:
            team2_for_db.append(
                {
                    "nickname": player["nickname"],
                    "discord_id": player["discord_id"],
                    "kills": player["kills"],
                    "assists": player["assists"],
                    "deaths": player["deaths"],
                    "points_received": player["points_received"],
                }
            )

        # Update match in database
        await Match.complete_submission(
            match_id, team1_for_db, team2_for_db, team1_score, team2_score
        )

        # Update user statistics
        await _update_user_stats(
            bot, updated_team1 + updated_team2, match_data, default_league
        )

        # Get scoreboard config for results channel
        guild_id = None
        results_channel_id = None

        loop = asyncio.get_running_loop()

        def get_config():
            try:
                response = (
                    Config.supabase()
                    .table("scoreboard_config")
                    .select("*")
                    .limit(1)
                    .execute()
                )
                if response.data and len(response.data) > 0:
                    return response.data[0]
                return None
            except Exception as e:
                logger.error(f"🟢 Error fetching scoreboard config: {e}")
                return None

        config = await loop.run_in_executor(None, get_config)

        if config:
            guild_id = config.get("guild_id")
            results_channel_id = config.get("results_channel_id")

        # Send final results image
        if guild_id and results_channel_id:
            guild = bot.get_guild(guild_id)
            if guild:
                target_channel = guild.get_channel(results_channel_id)
                if target_channel:
                    await _send_final_results_image(
                        bot,
                        target_channel,
                        updated_team1,
                        updated_team2,
                        team1_score,
                        team2_score,
                        match_data,
                    )

        # Delete submission from matches_submissions
        await MatchSubmission.delete(match_id)
        logger.info(f"🟢 Deleted submission entry for match {match_id}")

        # Update submissions view embed
        await update_submissions_view_embed(bot)

        logger.info(f"🟢 Successfully processed manual stats for match {match_id}")

    except Exception as e:
        logger.error(f"🟢 Error processing manual stats: {e}", exc_info=True)
        raise


async def _get_default_league_from_config():
    """Get the default league from config table"""
    loop = asyncio.get_running_loop()

    def db_get():
        try:
            response = (
                Config.supabase().table("config").select("default_league").execute()
            )
            if response.data and len(response.data) > 0:
                return response.data[0].get("default_league")
            return None
        except Exception as e:
            logger.error(f"🟢 Failed to get default league: {e}")
            return None

    return await loop.run_in_executor(None, db_get)


async def _calculate_player_points_batch(
    discord_id, match_league, was_absent, team_won, stats_batch, default_league
):
    """Calculate points for a player using batch stats data"""
    discord_id_int = int(discord_id)

    # Get current points from batch
    stats_result = stats_batch.get(discord_id_int)
    current_points = 0
    if stats_result and stats_result.get("success"):
        stats_data = stats_result.get("stats", {})
        current_points = stats_data.get("points", 0)

    if was_absent and match_league == default_league:
        # Player was absent - apply leaving penalty
        from cogs.levels import LevelsCog

        is_challenger = await _check_challenger_status(discord_id_int, current_points)
        if is_challenger:
            return LevelsCog.CHALLENGER_CONFIG["leaving_ss_points"]
        else:
            level = LevelsCog.get_level_from_points(current_points)
            return LevelsCog.LEVEL_CONFIG[level]["leaving_ss_points"]
    elif was_absent:
        # Absent in non-default league
        return -45
    elif match_league == default_league:
        # Present in default league - level-based points
        from cogs.levels import LevelsCog

        is_challenger = await _check_challenger_status(discord_id_int, current_points)
        if is_challenger:
            return LevelsCog.get_challenger_point_delta(team_won)
        else:
            return LevelsCog.get_point_delta(current_points, team_won)
    else:
        # Present in non-default league
        return 25 if team_won else -25


async def _check_challenger_status(discord_id, current_points):
    """Check if player is in Challenger tier"""
    try:
        from cogs.levels import LevelsCog

        levels_cog = LevelsCog(None)
        return await levels_cog.is_challenger_eligible(discord_id, current_points)
    except Exception as e:
        logger.error(f"🟢 Error checking challenger status: {e}")
        return False


async def _update_user_stats(bot, all_players, match_data, default_league):
    """Update user statistics in database"""
    league_name = match_data.get("league")
    is_default_league = league_name == default_league

    # Get guild for role management
    guild = None
    if is_default_league:
        # Try to get guild from first available config
        loop = asyncio.get_running_loop()

        def get_guild_id():
            try:
                response = (
                    Config.supabase()
                    .table("scoreboard_config")
                    .select("guild_id")
                    .limit(1)
                    .execute()
                )
                if response.data and len(response.data) > 0:
                    return response.data[0].get("guild_id")
                return None
            except:
                return None

        guild_id = await loop.run_in_executor(None, get_guild_id)
        if guild_id:
            guild = bot.get_guild(guild_id)

    for player in all_players:
        discord_id = int(player["discord_id"])
        kills = player["kills"]
        deaths = player["deaths"]
        points_change = player["points_received"]
        won = points_change > 0

        if is_default_league and guild:
            await Stats.update_stats_with_roles(
                discord_id, kills, deaths, points_change, won, guild, league_name
            )
        else:
            await Stats.update_stats(
                discord_id, kills, deaths, points_change, won, league_name
            )


async def _send_final_results_image(
    bot, target_channel, team1, team2, team1_score, team2_score, match_data
):
    """Generate and send final results image"""
    try:
        # Ensure assists field exists
        for player in team1 + team2:
            if "assists" not in player:
                player["assists"] = 0

        # Determine MVP
        all_players = team1 + team2
        mvp_player = (
            max(all_players, key=lambda p: p.get("kills", 0)) if all_players else None
        )

        if mvp_player:
            match_data["mvp"] = {
                "nickname": mvp_player.get("nickname", "Unknown"),
                "points": mvp_player.get("current_points", 0),
                "kills": mvp_player.get("kills", 0),
            }

        # Generate image
        scoreboard_generator = ScoreboardResultsTemplateGenerator()
        image_path = await scoreboard_generator.generate_scoreboard_image(
            bot=bot,
            match_data=match_data,
            team1=team1,
            team2=team2,
            team1_score=team1_score,
            team2_score=team2_score,
        )

        if not image_path or not os.path.exists(image_path):
            raise Exception("Failed to generate scoreboard image")

        # Send to channel
        file = discord.File(image_path, filename="scoreboard_results.png")
        embed = discord.Embed(
            title=f"{Config.ICONS['success_playcolor']} Match #{match_data['match_id']} Results",
            color=Config.MAIN_COLOR,
        )
        embed.set_image(url="attachment://scoreboard_results.png")

        current_date = datetime.now().strftime("%H:%M | %d/%m/%Y")
        embed.set_footer(text=f"Results processed at {current_date}")

        await target_channel.send(embed=embed, file=file)

        # Cleanup
        try:
            os.remove(image_path)
        except:
            pass

        logger.info(f"🟢 Sent manual results image for match {match_data['match_id']}")

    except Exception as e:
        logger.error(f"🟢 Error sending results image: {e}", exc_info=True)


class ManualScoreboard(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        """Called when cog is loaded - update submissions view"""
        try:
            await update_submissions_view_embed(self.bot)
        except Exception as e:
            logger.warning(f"🟢 Failed to update submissions view on cog load: {e}")

    @app_commands.command(
        name="set_submissions_view_channel",
        description="Set channel for viewing pending manual submissions",
    )
    @app_commands.describe(
        view_channel="The channel where pending submissions will be displayed"
    )
    @app_commands.default_permissions(administrator=True)
    async def set_submissions_view_channel(
        self, interaction: discord.Interaction, view_channel: discord.TextChannel
    ):
        # Check authorization
        if not await Config.check_authorization(interaction):
            return

        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "You need administrator permissions to use this command.",
                ephemeral=True,
            )
            return

        try:
            guild_id = interaction.guild.id

            # Update scoreboard_config with view channel
            loop = asyncio.get_running_loop()

            def update_config():
                try:
                    Config.supabase().table("scoreboard_config").update(
                        {"view_submissions_channel_id": view_channel.id}
                    ).eq("guild_id", guild_id).execute()
                    return True
                except Exception as e:
                    logger.error(f"🟢 Error updating config: {e}")
                    return False

            success = await loop.run_in_executor(None, update_config)

            if not success:
                raise Exception("Failed to update configuration")

            # Create initial embed
            await update_submissions_view_embed(self.bot)

            # Confirm to admin
            success_embed = discord.Embed(
                title=f"{Config.ICONS['success']} Submissions View Configured",
                description=f"Submissions view channel set successfully!\n\n"
                f"**View Channel:** {view_channel.mention}\n\n"
                f"This channel will automatically display all pending manual submissions with their review links.",
                color=Config.EMBED_COLORS["light_green"],
            )
            await interaction.response.send_message(embed=success_embed, ephemeral=True)

        except Exception as e:
            logger.error(f"🟢 Error setting submissions view channel: {e}")
            embed = discord.Embed(
                title=f"{Config.ICONS['unsuccess']} Setup Error",
                description="An error occurred while setting up the submissions view channel.",
                color=Config.EMBED_COLORS["red"],
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(ManualScoreboard(bot))
