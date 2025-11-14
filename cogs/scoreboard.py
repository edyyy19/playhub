import discord
from discord.ext import commands
from discord import app_commands
from config import Config
import asyncio
import json
import os
import logging
from datetime import datetime
from models.match import Match
from models.stats import Stats
from models.scoreboard_config import ScoreboardConfig, ScoreboardConfigManager
from models.season import Season
from assets.template_generators.scoreboard_generator import (
    ScoreboardResultsTemplateGenerator,
)
from cogs import manual_scoreboard
from cogs import dynamic_scoreboard

logger = logging.getLogger(__name__)


class MatchIDModal(discord.ui.Modal, title="Submit Scoreboard"):
    def __init__(self, target_channel_id):
        super().__init__()
        self.target_channel_id = target_channel_id

    async def _delete_screenshot_safely(self, screenshot_message):
        """Helper method to safely delete screenshot messages"""
        if screenshot_message:
            try:
                await screenshot_message.delete()
                logger.info("🟢 Deleted screenshot message for failed submission")
            except discord.NotFound:
                pass  # Message already deleted
            except discord.Forbidden:
                logger.warning("🟢 No permission to delete screenshot message")
            except Exception as e:
                logger.error(f"🟢 Error deleting screenshot message: {e}")

    match_id = discord.ui.TextInput(
        label="Match ID",
        placeholder="Enter the match ID...",
        required=True,
        max_length=10,
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        try:
            # Validate match ID is a number
            match_id_int = int(self.match_id.value)
        except ValueError:
            await interaction.followup.send(
                "Please enter a valid match ID number.", ephemeral=True
            )
            return

        # Look up match in database
        match_data = await Match.find(match_id_int)

        if not match_data:
            await interaction.followup.send(
                f"Match with ID {match_id_int} not found.", ephemeral=True
            )
            return

        # Check if results already submitted
        if match_data.get("results_submitted", False):
            embed = discord.Embed(
                title=f"{Config.ICONS['unsuccess']} Results Already Submitted",
                description="Results have already been submitted for this match.",
                color=Config.EMBED_COLORS["red"],
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        # Attempt to atomically lock the match for submission
        submission_locked = await Match.try_lock_for_submission(
            match_id_int, str(interaction.user.id)
        )

        if not submission_locked:
            embed = discord.Embed(
                title=f"{Config.ICONS['unsuccess']} Submission In Progress",
                description="This match is already being processed by another player or has been submitted. Please try again in a moment.",
                color=Config.EMBED_COLORS["red"],
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        # Check if user participated in the match
        user_discord_id = str(interaction.user.id)

        # Get teams data from new structure
        teams_data = json.loads(match_data.get("teams", "{}"))
        team1_players = teams_data.get("team1", {}).get("players", [])
        team2_players = teams_data.get("team2", {}).get("players", [])

        all_players = team1_players + team2_players

        user_participated = False
        for player in all_players:
            if player.get("discord_id") == user_discord_id:
                user_participated = True
                break

        if not user_participated:
            # Release the submission lock since user is not authorized
            try:
                await Match.release_submission_lock(match_id_int)
                logger.info(
                    f"🟢 Released submission lock for match {match_id_int} - user {user_discord_id} not authorized"
                )
            except Exception as lock_error:
                logger.error(f"🟢 Failed to release submission lock: {lock_error}")

            await interaction.followup.send(
                "You are not authorized to submit results for this match.",
                ephemeral=True,
            )
            return

        # Check scoreboard submission method (dynamic vs manual)
        guild_id = interaction.guild.id
        submission_method = ScoreboardConfigManager.get_method(guild_id)

        logger.info(f"Guild {guild_id} scoreboard method: {submission_method}")

        # Ask for screenshot upload
        upload_embed = discord.Embed(
            title=f"{Config.ICONS['camera']} Upload Scoreboard Screenshot",
            description=f"Please upload the scoreboard screenshot in your next message.\n\n{Config.ICONS['clock']} **Timeout:** 60 seconds",
            color=0x0099FF,
        )
        upload_embed.add_field(
            name=f"{Config.ICONS['instructions']} Instructions",
            value="• Make sure the scoreboard shows all player stats\n• Supported formats: PNG, JPG, JPEG,\n• Clear image quality for best results",
            inline=False,
        )
        await interaction.followup.send(embed=upload_embed, ephemeral=True)

        # Store the match data and target channel for the next step
        self.bot = interaction.client
        self.match_data = match_data
        self.user_id = interaction.user.id
        self.submission_method = submission_method  # Store the method for later use

        # Wait for image upload
        try:

            def check(message):
                return (
                    message.author.id == interaction.user.id
                    and message.attachments
                    and any(
                        attachment.filename.lower().endswith(
                            (".png", ".jpg", ".jpeg", ".gif", ".webp")
                        )
                        for attachment in message.attachments
                    )
                )

            message = await self.bot.wait_for("message", check=check, timeout=60.0)

            # Get the image URL
            image_url = message.attachments[0].url

            # Route based on submission method
            if submission_method == "manual":
                # Manual mode: Upload to Cloudinary and create ticket channel
                # Lock remains active - will be released when staff processes the submission
                await manual_scoreboard.handle_manual_submission(
                    interaction, str(match_id_int), match_data, image_url, message
                )
            else:
                # Dynamic mode: Process with OpenAI
                # Show processing embed
                processing_embed = discord.Embed(
                    title=f"{Config.ICONS['updating']} Processing Screenshot",
                    description="Analyzing screenshot data...",
                    color=Config.EMBED_COLORS["yellow"],
                )
                processing_embed.add_field(
                    name=f"{Config.ICONS['stats_yellow']} Analysis Steps",
                    value="• Reading player statistics\n• Matching players to database\n• Calculating results\n• Generating final scoreboard",
                    inline=False,
                )
                processing_message = await interaction.followup.send(
                    embed=processing_embed, ephemeral=True
                )

                # Process the image with OpenAI GPT
                await self.process_scoreboard_with_gpt(
                    interaction,
                    image_url,
                    match_data,
                    self.target_channel_id,
                    message,
                    processing_message,
                )

        except asyncio.TimeoutError:
            # Release the submission lock since user didn't upload in time
            try:
                await Match.release_submission_lock(match_data["match_id"])
                logger.info(
                    f"🟢 Released submission lock for match {match_data['match_id']} due to timeout"
                )
            except Exception as lock_error:
                logger.error(f"🟢 Failed to release submission lock: {lock_error}")

            await interaction.followup.send(
                "Timeout: No screenshot uploaded within 60 seconds.", ephemeral=True
            )

    async def process_scoreboard_with_gpt(
        self,
        interaction,
        image_url,
        match_data,
        target_channel_id,
        screenshot_message=None,
        processing_message=None,
    ):
        """Process the scoreboard image using dynamic_scoreboard module with OpenAI GPT"""
        try:
            # Get teams data from database
            teams_data = json.loads(match_data.get("teams", "{}"))
            team1_players = teams_data.get("team1", {}).get("players", [])
            team2_players = teams_data.get("team2", {}).get("players", [])

            # Call the dynamic_scoreboard module to process with GPT
            try:
                extracted_data = await dynamic_scoreboard.process_with_gpt(
                    image_url, team1_players, team2_players
                )
            except Exception as gpt_error:
                logger.error(f"🟢 Dynamic scoreboard processing failed: {gpt_error}")

                # Release the submission lock since processing failed
                try:
                    await Match.release_submission_lock(match_data["match_id"])
                except Exception as lock_error:
                    logger.error(f"🟢 Failed to release submission lock: {lock_error}")

                await self._delete_screenshot_safely(screenshot_message)
                await interaction.followup.send(
                    "Failed to process scoreboard data. Please try again.",
                    ephemeral=True,
                )
                return

            # Validate and process the GPT results
            await self.process_gpt_results(
                interaction,
                extracted_data,
                match_data,
                target_channel_id,
                screenshot_message,
                processing_message,
            )

        except Exception as e:
            logger.error(f"🟢 Error processing scoreboard with GPT: {e}")

            # Release the submission lock since an error occurred
            try:
                await Match.release_submission_lock(match_data["match_id"])
                logger.info(
                    f"🟢 Released submission lock for match {match_data['match_id']} due to processing error"
                )
            except Exception as lock_error:
                logger.error(f"🟢 Failed to release submission lock: {lock_error}")

            # Delete the screenshot since submission failed
            await self._delete_screenshot_safely(screenshot_message)

            await interaction.followup.send(
                "Error processing the scoreboard image. Please try again.",
                ephemeral=True,
            )

    async def process_gpt_results(
        self,
        interaction,
        extracted_data,
        match_data,
        target_channel_id,
        screenshot_message=None,
        processing_message=None,
    ):
        """Process and validate GPT results, then update match"""
        try:
            # Store guild ID for later use in role management
            self._guild_id = interaction.guild.id

            # Check game detection confidence
            game_detected = extracted_data.get("game_detected", "unknown")
            confidence = extracted_data.get("confidence", 0.0)

            if game_detected != "standoff2" or confidence < 0.5:
                # Release the submission lock since game detection failed
                try:
                    await Match.release_submission_lock(match_data["match_id"])
                    logger.info(
                        f"🟢 Released submission lock for match {match_data['match_id']} due to game detection failure"
                    )
                except Exception as lock_error:
                    logger.error(f"🟢 Failed to release submission lock: {lock_error}")

                # Delete the screenshot since submission failed
                await self._delete_screenshot_safely(screenshot_message)

                await interaction.followup.send(
                    f"Screenshot validation failed:\n"
                    f"Game detected: {game_detected}\n"
                    f"Confidence: {confidence:.1%}\n\n"
                    f"Please ensure you uploaded a clear Standoff 2 scoreboard screenshot.",
                    ephemeral=True,
                )
                return

            # Get teams data from GPT response
            teams = extracted_data.get("teams", {})
            team1_data = teams.get("team1", {})
            team2_data = teams.get("team2", {})

            # Get scores
            team1_score = team1_data.get("score", 0)
            team2_score = team2_data.get("score", 0)

            # Validate score reasonableness
            if (
                team1_score < 0
                or team2_score < 0
                or (team1_score == 0 and team2_score == 0)
            ):
                # Release the submission lock since scores are invalid
                try:
                    await Match.release_submission_lock(match_data["match_id"])
                except Exception as lock_error:
                    logger.error(f"🟢 Failed to release submission lock: {lock_error}")

                await self._delete_screenshot_safely(screenshot_message)
                await interaction.followup.send(
                    f"Invalid scores detected: {team1_score} - {team2_score}\n"
                    f"Please ensure the scoreboard shows valid final scores.",
                    ephemeral=True,
                )
                return

            if team1_score == team2_score:
                # Release the submission lock since it's a tie
                try:
                    await Match.release_submission_lock(match_data["match_id"])
                except Exception as lock_error:
                    logger.error(f"🟢 Failed to release submission lock: {lock_error}")

                await self._delete_screenshot_safely(screenshot_message)
                await interaction.followup.send(
                    f"Tied matches are not supported: {team1_score} - {team2_score}\n"
                    f"Please submit results only for completed matches.",
                    ephemeral=True,
                )
                return

            # 🔍 VERIFICARE SCOREBOARD - Nouă funcționalitate
            # Verifică dacă meciul necesită verificare manuală (niciuna din echipe nu are 13)
            verification_data = {"teams": teams, "match_data": match_data}

            needs_verification = await self.process_scoreboard_verification(
                match_data["match_id"],
                team1_score,
                team2_score,
                verification_data,
                interaction,
                processing_message,
            )

            if needs_verification:
                return

            # Continuă cu procesarea normală pentru meciuri cu scor normal (13-X)

            # 🚀 OPTIMIZARE BATCH: Obține stats pentru toți jucătorii deodată
            all_player_ids = []
            all_gpt_players = []

            # Colectează toți ID-urile de jucători
            for gpt_player in team1_data.get("players", []):
                all_player_ids.append(int(gpt_player["discord_id"]))
                all_gpt_players.append(
                    (gpt_player, 1, team1_score > team2_score)
                )  # team, won

            for gpt_player in team2_data.get("players", []):
                all_player_ids.append(int(gpt_player["discord_id"]))
                all_gpt_players.append(
                    (gpt_player, 2, team2_score > team1_score)
                )  # team, won

            # Fetch stats în batch pentru toți jucătorii deodată
            league_name = match_data.get("league")
            stats_batch = await Stats.get_multiple_user_stats(
                all_player_ids, league_name
            )
            logger.info(
                f"🟢 Fetched stats in batch for {len(all_player_ids)} players in league {league_name}"
            )

            # Process all players cu datele din batch
            updated_team1 = []
            updated_team2 = []

            for gpt_player, team_num, team_won in all_gpt_players:
                discord_id = gpt_player["discord_id"]

                # Get current points from batch stats (before match)
                stats_result = stats_batch.get(discord_id)
                old_points = 0
                if stats_result and stats_result.get("success"):
                    stats_data = stats_result.get("stats", {})
                    old_points = stats_data.get("points", 0)

                # Calculate points using batch stats data
                was_absent = gpt_player.get("matched_from", "") == "default_stats"
                points_received = await self._calculate_player_points_batch(
                    discord_id, league_name, was_absent, team_won, stats_batch
                )

                # Calculate total points after this match
                current_points = old_points + points_received

                # Calculate rating using bot formula: (KILLS + 0.5 × ASSISTS) / max(1, DEATHS)
                kills = gpt_player.get("kills", 0)
                assists = gpt_player.get("assists", 0)
                deaths = max(
                    1, gpt_player.get("deaths", 13)
                )  # Prevent division by zero
                rating = round((kills + 0.5 * assists) / deaths, 1)

                updated_player = {
                    "nickname": gpt_player["nickname"],
                    "discord_id": discord_id,
                    "kills": kills,
                    "assists": assists,
                    "deaths": gpt_player.get("deaths", 13),
                    "rating": rating,
                    "points": points_received,  # Delta for display
                    "points_received": points_received,  # Delta for database
                    "current_points": current_points,  # Total points after match for level icon
                    "matched_from": gpt_player.get("matched_from", "default_stats"),
                }

                if team_num == 1:
                    updated_team1.append(updated_player)
                else:
                    updated_team2.append(updated_player)

            # Update database - create copies without extra fields for database storage
            team1_for_db = []
            team2_for_db = []

            for player in updated_team1:
                db_player = {
                    "nickname": player["nickname"],
                    "discord_id": player["discord_id"],
                    "kills": player["kills"],
                    "assists": player["assists"],
                    "deaths": player["deaths"],
                    "points_received": player["points_received"],
                }
                team1_for_db.append(db_player)

            for player in updated_team2:
                db_player = {
                    "nickname": player["nickname"],
                    "discord_id": player["discord_id"],
                    "kills": player["kills"],
                    "assists": player["assists"],
                    "deaths": player["deaths"],
                    "points_received": player["points_received"],
                }
                team2_for_db.append(db_player)

            await Match.complete_submission(
                match_data["match_id"],
                team1_for_db,
                team2_for_db,
                team1_score,
                team2_score,
            )

            # Update user statistics
            await self.update_user_stats(updated_team1 + updated_team2, match_data)

            # Send final results image to target channel
            await self.send_final_results_image(
                interaction,
                target_channel_id,
                updated_team1,
                updated_team2,
                team1_score,
                team2_score,
                match_data,
            )

            # Delete the screenshot message to clean up
            await self._delete_screenshot_safely(screenshot_message)

            # Show success message
            matched_count = sum(
                1
                for p in updated_team1 + updated_team2
                if p.get("matched_from", "") != "default_stats"
            )
            total_players = len(updated_team1 + updated_team2)

            success_embed = discord.Embed(
                title=f"{Config.ICONS['success']} Scoreboard Successfully Processed",
                description="Screenshot analysis completed successfully!",
                color=Config.EMBED_COLORS["light_green"],
            )
            success_embed.add_field(
                name=f"{Config.ICONS['stats_lightgreen']} Match Results",
                value=f"**Matched:** {matched_count}/{total_players} players\n"
                f"**Team 1:** {len(updated_team1)} players\n"
                f"**Team 2:** {len(updated_team2)} players",
                inline=True,
            )
            success_embed.add_field(
                name="⚙️ Processing Details",
                value="• Players automatically matched\n"
                "• Missing players assigned default stats\n"
                "• Results posted to scoreboard channel\n"
                "• Player statistics updated",
                inline=True,
            )
            success_embed.set_footer(text="Screenshot deleted for privacy")

            if processing_message:
                try:
                    await processing_message.edit(embed=success_embed)
                except discord.NotFound:
                    await interaction.followup.send(embed=success_embed, ephemeral=True)
            else:
                await interaction.followup.send(embed=success_embed, ephemeral=True)

        except Exception as e:
            logger.error(f"🟢 Error processing GPT results: {e}")

            # Release the submission lock since an error occurred
            try:
                await Match.release_submission_lock(match_data["match_id"])
                logger.info(
                    f"🟢 Released submission lock for match {match_data['match_id']} due to processing error"
                )
            except Exception as lock_error:
                logger.error(f"🟢 Failed to release submission lock: {lock_error}")

            # Delete the screenshot since submission failed
            await self._delete_screenshot_safely(screenshot_message)

            await interaction.followup.send(
                "Error processing match results. Please try again.", ephemeral=True
            )

    async def update_user_stats(self, all_players, match_data):
        """Update user statistics in database and manage level roles"""
        # Extract league from match data
        league_name = match_data.get("league")
        logger.info(f"🟢 Updating stats for match in league: {league_name}")

        # Get the default league from config table to determine if level roles should be updated
        default_league = await self.get_default_league_from_config()
        is_default_league = league_name == default_league
        logger.info(
            f"🟢 Default league: {default_league}, Current league: {league_name}, Is default: {is_default_league}"
        )

        for player in all_players:
            discord_id = int(player["discord_id"])
            kills = player["kills"]
            deaths = player["deaths"]
            points_change = player["points_received"]
            won = points_change > 0

            if is_default_league:
                # For default league: update stats AND manage level roles
                guild = None
                if hasattr(self, "bot") and hasattr(self, "_guild_id"):
                    guild = self.bot.get_guild(self._guild_id)

                await Stats.update_stats_with_roles(
                    discord_id, kills, deaths, points_change, won, guild, league_name
                )
            else:
                # For non-default leagues: only update stats, no role management
                await Stats.update_stats(
                    discord_id, kills, deaths, points_change, won, league_name
                )

    async def get_default_league_from_config(self):
        """Get the default league from the config table"""
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
                logger.error(f"🟢 Failed to get default league from config: {e}")
                return None

        return await loop.run_in_executor(None, db_get)

    async def send_final_results_image(
        self,
        interaction,
        target_channel_id,
        team1,
        team2,
        team1_score,
        team2_score,
        match_data,
    ):
        """Generate and send final results as an image to target channel"""
        try:
            target_channel = interaction.guild.get_channel(target_channel_id)
            if not target_channel:
                logger.error(f"🟢 Target channel {target_channel_id} not found")
                return

            # Ensure all players have assists field (for backward compatibility)
            for player in team1 + team2:
                if "assists" not in player:
                    player["assists"] = 0

            # Determine MVP (player with most kills)
            all_players = team1 + team2
            mvp_player = (
                max(all_players, key=lambda p: p.get("kills", 0))
                if all_players
                else None
            )

            # Add MVP data to match_data for the generator
            if mvp_player:
                match_data["mvp"] = {
                    "nickname": mvp_player.get("nickname", "Unknown"),
                    "points": mvp_player.get(
                        "current_points", 0
                    ),  # Use current_points for level calculation
                    "kills": mvp_player.get("kills", 0),
                }

            # Create scoreboard generator instance
            scoreboard_generator = ScoreboardResultsTemplateGenerator()

            # Generate scoreboard results image using the dedicated template generator
            image_path = await scoreboard_generator.generate_scoreboard_image(
                bot=self.bot,
                match_data=match_data,
                team1=team1,
                team2=team2,
                team1_score=team1_score,
                team2_score=team2_score,
            )

            if not image_path or not os.path.exists(image_path):
                raise Exception("Failed to generate scoreboard results image")

            # Upload image to Discord
            file = discord.File(image_path, filename="scoreboard_results.png")
            embed = discord.Embed(
                title=f"{Config.ICONS['success_playcolor']} Match #{match_data['match_id']} Results",
                color=Config.MAIN_COLOR,
            )
            embed.set_image(url="attachment://scoreboard_results.png")

            current_date = datetime.now().strftime("%H:%M | %d/%m/%Y")
            embed.set_footer(text=f"Results processed at {current_date}")

            await target_channel.send(embed=embed, file=file)

            # Clean up the temporary file
            try:
                os.remove(image_path)
            except Exception:
                pass

        except Exception as e:
            logger.error(f"🟢 Error sending final results image: {e}")
            # Fallback to text message if image generation fails
            teams_data = match_data.get("teams", {})
            if isinstance(teams_data, str):
                teams_data = json.loads(teams_data)

            team1_captain = teams_data.get("team1", {}).get("captain", "Unknown")
            team2_captain = teams_data.get("team2", {}).get("captain", "Unknown")
            winner = (
                f"Team {team1_captain}"
                if team1_score > team2_score
                else f"Team {team2_captain}"
            )
            fallback_msg = f"{Config.ICONS['success']} **Match {match_data['match_id']} Results**\n"
            fallback_msg += f"**Map:** {match_data.get('map', 'Unknown')}\n"
            fallback_msg += f"**Score:** {team1_score} - {team2_score}\n"
            fallback_msg += f"**Winner:** {winner}\n\n"
            fallback_msg += "*(Image generation failed - showing text summary)*"

            target_channel = interaction.guild.get_channel(target_channel_id)
            if target_channel:
                await target_channel.send(fallback_msg)

    async def get_player_current_points(self, discord_id, league_name):
        """Get current points for a player from the specified league table"""
        try:
            stats_result = await Stats.get_user_stats(discord_id, league_name)
            if stats_result and stats_result.get("success"):
                stats_data = stats_result.get("stats", {})
                return stats_data.get("points", 0)
            return 0
        except Exception as e:
            logger.error(
                f"🟢 Error getting points for {discord_id} in league {league_name}: {e}"
            )
            return 0

    async def calculate_leaving_penalty_points(
        self, discord_id, league_name, is_challenger=False
    ):
        """Calculate leaving penalty points based on player's current level"""
        try:
            # Import here to avoid circular imports
            from cogs.levels import LevelsCog

            if is_challenger:
                return LevelsCog.CHALLENGER_CONFIG["leaving_ss_points"]
            else:
                current_points = await self.get_player_current_points(
                    discord_id, league_name
                )
                level = LevelsCog.get_level_from_points(current_points)
                return LevelsCog.LEVEL_CONFIG[level]["leaving_ss_points"]
        except Exception as e:
            logger.error(
                f"🟢 Error calculating leaving penalty for {discord_id} in league {league_name}: {e}"
            )
            # Fallback penalty
            return -30

    async def calculate_level_based_points(
        self, discord_id, is_win, league_name, is_challenger=False
    ):
        """Calculate points based on player's current level and win/loss (only for default league)"""
        try:
            # Import here to avoid circular imports
            from cogs.levels import LevelsCog

            current_points = await self.get_player_current_points(
                discord_id, league_name
            )

            if is_challenger:
                return LevelsCog.get_challenger_point_delta(is_win)
            else:
                return LevelsCog.get_point_delta(current_points, is_win)
        except Exception as e:
            logger.error(
                f"🟢 Error calculating level-based points for {discord_id} in league {league_name}: {e}"
            )
            # Fallback to old system
            return 20 if is_win else -10

    async def _calculate_player_points_batch(
        self, discord_id, match_league, was_absent, team_won, stats_batch
    ):
        """Optimized helper method to calculate points using batch stats data"""
        default_league = await self.get_default_league_from_config()
        discord_id_int = int(discord_id)

        if was_absent and match_league == default_league:
            # Player was absent (left match): apply leaving penalty based on their level
            current_points = self._get_points_from_batch(discord_id_int, stats_batch)
            is_challenger = await self.check_challenger_status(
                discord_id_int, current_points
            )
            return await self.calculate_leaving_penalty_points(
                discord_id_int, match_league, is_challenger
            )
        elif was_absent:
            # Player was absent in non-default league: fixed penalty
            return -45
        elif match_league == default_league:
            # Player was present: normal win/loss calculation
            current_points = self._get_points_from_batch(discord_id_int, stats_batch)
            is_challenger = await self.check_challenger_status(
                discord_id_int, current_points
            )
            return await self.calculate_level_based_points(
                discord_id_int, team_won, match_league, is_challenger
            )
        else:
            # Player was present in non-default league: normal fixed points
            return 25 if team_won else -25

    def _get_points_from_batch(self, discord_id, stats_batch):
        """Helper to extract points from batch stats result"""
        stats_result = stats_batch.get(discord_id)
        if stats_result and stats_result.get("success"):
            stats_data = stats_result.get("stats", {})
            return stats_data.get("points", 0)
        return 0

    async def _calculate_player_points(
        self, discord_id, match_league, was_absent, team_won
    ):
        """Helper method to calculate points for a player based on league and participation"""
        default_league = await self.get_default_league_from_config()

        if was_absent and match_league == default_league:
            # Player was absent (left match): apply leaving penalty based on their level
            current_points = await self.get_player_current_points(
                int(discord_id), match_league
            )
            is_challenger = await self.check_challenger_status(
                int(discord_id), current_points
            )
            return await self.calculate_leaving_penalty_points(
                int(discord_id), match_league, is_challenger
            )
        elif was_absent:
            # Player was absent in non-default league: fixed penalty
            return -45
        elif match_league == default_league:
            # Player was present: normal win/loss calculation
            current_points = await self.get_player_current_points(
                int(discord_id), match_league
            )
            is_challenger = await self.check_challenger_status(
                int(discord_id), current_points
            )
            return await self.calculate_level_based_points(
                int(discord_id), team_won, match_league, is_challenger
            )
        else:
            # Player was present in non-default league: normal fixed points
            return 25 if team_won else -25

    async def check_challenger_status(self, discord_id, current_points):
        """Check if player is currently in Challenger tier (only used for default league)"""
        try:
            from cogs.levels import LevelsCog

            levels_cog = LevelsCog(None)
            return await levels_cog.is_challenger_eligible(discord_id, current_points)
        except Exception as e:
            logger.error(f"🟢 Error checking challenger status for {discord_id}: {e}")
            return False

    async def _process_teams_data(
        self, team1_data, team2_data, match_data, team1_score, team2_score
    ):
        """Process team data and calculate points - optimized helper method for verification"""

        # 🚀 OPTIMIZARE BATCH: Colectează toți jucătorii pentru batch query
        all_player_ids = []
        all_gpt_players = []

        # Colectează jucătorii din team1
        for gpt_player in team1_data.get("players", []):
            all_player_ids.append(int(gpt_player["discord_id"]))
            all_gpt_players.append(
                (gpt_player, 1, team1_score > team2_score)
            )  # team, won

        # Colectează jucătorii din team2
        for gpt_player in team2_data.get("players", []):
            all_player_ids.append(int(gpt_player["discord_id"]))
            all_gpt_players.append(
                (gpt_player, 2, team2_score > team1_score)
            )  # team, won

        # Fetch stats în batch pentru toți jucătorii
        league_name = match_data.get("league")
        stats_batch = await Stats.get_multiple_user_stats(all_player_ids, league_name)
        logger.info(
            f"🟢 [VERIFICATION] Fetched stats in batch for {len(all_player_ids)} players in league {league_name}"
        )

        all_players = []

        # Process all players cu datele din batch
        for gpt_player, team_num, team_won in all_gpt_players:
            discord_id = gpt_player["discord_id"]

            # Calculate points using batch data
            was_absent = gpt_player.get("matched_from", "") == "default_stats"
            points_received = await self._calculate_player_points_batch(
                discord_id, league_name, was_absent, team_won, stats_batch
            )

            kills = gpt_player.get("kills", 0)
            assists = gpt_player.get("assists", 0)
            deaths = max(1, gpt_player.get("deaths", 13))
            rating = round((kills + 0.5 * assists) / deaths, 1)

            updated_player = {
                "nickname": gpt_player["nickname"],
                "discord_id": discord_id,
                "kills": kills,
                "assists": assists,
                "deaths": gpt_player.get("deaths", 13),
                "rating": rating,
                "points": points_received,
                "points_received": points_received,
                "matched_from": gpt_player.get("matched_from", "default_stats"),
                "team": team_num,
            }
            all_players.append(updated_player)

        return all_players

    async def _prepare_teams_for_db(self, all_players):
        """Prepare team data for database storage"""
        team1_for_db = []
        team2_for_db = []

        for player in all_players:
            db_player = {
                "nickname": player["nickname"],
                "discord_id": player["discord_id"],
                "kills": player["kills"],
                "assists": player["assists"],
                "deaths": player["deaths"],
                "points_received": player["points_received"],
            }

            if player["team"] == 1:
                team1_for_db.append(db_player)
            else:
                team2_for_db.append(db_player)

        return team1_for_db, team2_for_db

    async def process_scoreboard_verification(
        self,
        match_id: int,
        team1_score: int,
        team2_score: int,
        extracted_data: dict,
        interaction: discord.Interaction,
        processing_message=None,
    ):
        """Verifică dacă meciul necesită aprobare manuală"""

        # Verifică dacă una din echipe are scorul 13 (meci terminat normal)
        if team1_score == 13 or team2_score == 13:
            # Meci normal - returnează False pentru a continua procesarea normală
            return False

        # Meci necesită verificare - creează canal de verificare
        try:
            guild = interaction.guild

            # Setează permisiunile pentru canalul de verificare
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                guild.me: discord.PermissionOverwrite(
                    view_channel=True, send_messages=True, manage_channels=True
                ),
            }

            # Adaugă permisiuni pentru utilizatori autorizați
            for user_id in Config.AUTHORIZED_USERS:
                try:
                    user = guild.get_member(user_id)
                    if user:
                        overwrites[user] = discord.PermissionOverwrite(
                            view_channel=True, send_messages=True
                        )
                except:
                    pass

            # Adaugă permisiuni pentru utilizatorul care a submis screenshot-ul
            submitter = interaction.user
            overwrites[submitter] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
                embed_links=True,
            )

            # Creează canalul specific pentru acest meci direct în guild (fără categorie)
            channel_name = f"🆔{match_id}┃match_verification"
            verification_channel = await guild.create_text_channel(
                name=channel_name,
                overwrites=overwrites,
                reason=f"Verification needed for match #{match_id} with unusual score: {team1_score}-{team2_score}",
            )

            # Creează embed-ul pentru verificare
            verification_embed = discord.Embed(
                title=f"{Config.ICONS['search_yellow']} Match Verification Required",
                description=f"**Match ID:** {match_id}\n**Unusual Score:** {team1_score} - {team2_score}",
                color=Config.EMBED_COLORS["yellow"],
            )

            verification_embed.add_field(
                name=f"{Config.ICONS['info']} Why Verification?",
                value="Neither team reached 13 rounds, which indicates the match may have ended early, had disconnections, or other issues that require manual review.",
                inline=False,
            )

            verification_embed.add_field(
                name=f"{Config.ICONS['stats_yellow']} Extracted Scores",
                value=f"**Team 1 (T):** {team1_score} rounds\n**Team 2 (CT):** {team2_score} rounds",
                inline=True,
            )

            verification_embed.add_field(
                name="Submitted by",
                value=f"{interaction.user.mention}\n({interaction.user.display_name})",
                inline=True,
            )

            verification_embed.add_field(
                name="Action Required",
                value="Review the screenshot and determine if this match should be accepted or denied.",
                inline=False,
            )

            verification_embed.set_footer(
                text="Only authorized staff can approve or deny. Channel auto-deletes after decision."
            )

            # Stochează datele pentru procesarea ulterioară
            verification_data = {
                "teams": extracted_data.get("teams", {}),
                "match_data": extracted_data.get("match_data"),
            }

            # Creează view-ul cu butoane
            verification_view = ScoreboardVerificationView(
                match_id=match_id,
                team1_score=team1_score,
                team2_score=team2_score,
                extracted_data=verification_data,
                original_interaction=interaction,
                scoreboard_cog=self,
            )

            # Găsește mesajul cu screenshot pentru a-l face forward
            screenshot_message = None
            try:
                # Găsește ultimul mesaj cu attachment de la user
                async for message in interaction.channel.history(limit=50):
                    if (
                        message.author.id == interaction.user.id
                        and message.attachments
                        and any(
                            att.filename.lower().endswith(
                                (".png", ".jpg", ".jpeg", ".webp", ".gif")
                            )
                            for att in message.attachments
                        )
                    ):
                        screenshot_message = message
                        break
            except Exception as screenshot_error:
                logger.error(f"🟢 Error finding screenshot message: {screenshot_error}")

            # Trimite embed-ul cu butoanele în canalul de verificare
            verification_message = await verification_channel.send(
                embed=verification_embed, view=verification_view
            )

            # Fă forward la mesajul cu screenshot-ul în canalul de verificare
            if screenshot_message:
                try:
                    # Forward attachment-ul direct fără embed
                    await verification_channel.send(
                        files=[
                            await att.to_file()
                            for att in screenshot_message.attachments
                            if att.filename.lower().endswith(
                                (".png", ".jpg", ".jpeg", ".webp", ".gif")
                            )
                        ]
                    )
                    logger.info(
                        f"🟢 Successfully forwarded screenshot for match {match_id} to verification channel"
                    )

                    # Șterge screenshot-ul original după forward
                    try:
                        await screenshot_message.delete()
                        logger.info(
                            f"🟢 Deleted original screenshot message after forwarding to verification"
                        )
                    except Exception as delete_error:
                        logger.warning(
                            f"🟢 Could not delete original screenshot message: {delete_error}"
                        )

                except Exception as forward_error:
                    logger.error(f"🟢 Error forwarding screenshot: {forward_error}")
                    # Fallback - trimite un mesaj cu link-ul
                    await verification_channel.send(
                        f"{Config.ICONS['info']} **Could not forward screenshot automatically.**\n"
                        f"Original screenshot: {screenshot_message.jump_url}"
                    )
            else:
                # Nu s-a găsit screenshot
                await verification_channel.send(
                    f"{Config.ICONS['info']} **Screenshot not found.** Please check the original submission manually."
                )

            verification_view.message = verification_message

            # Trimite embed de confirmare în interaction cu logica nouă de embeduri
            verification_embed_user = discord.Embed(
                title=f"{Config.ICONS['info']} Match Submitted for Verification",
                description="Your match has been submitted and will require manual verification before being processed.",
                color=Config.EMBED_COLORS["yellow"],
            )
            verification_embed_user.add_field(
                name=f"{Config.ICONS['question_yellow']} Why verification?",
                value=f"Your match ended with score **{team1_score}-{team2_score}**. Since neither team reached 13 rounds, this requires manual staff approval to ensure fairness.",
                inline=False,
            )
            verification_embed_user.add_field(
                name="What happens next?",
                value="• Staff will review your match results\n• You will be notified of the decision\n• **Estimated review time:** 15-60 minutes\n• You can monitor and participate in the verification process",
                inline=False,
            )
            verification_embed_user.add_field(
                name=f"{Config.ICONS['info']} Verification Channel",
                value=f"You can participate in the verification process here: {verification_channel.mention}\n*Note: You can view, send messages, and provide additional context if needed.*",
                inline=False,
            )
            verification_embed_user.set_footer(
                text="Screenshot processed and ready for review"
            )

            if processing_message:
                try:
                    await processing_message.edit(embed=verification_embed_user)
                except discord.NotFound:
                    await interaction.followup.send(
                        embed=verification_embed_user, ephemeral=True
                    )
            else:
                await interaction.followup.send(
                    embed=verification_embed_user, ephemeral=True
                )

            logger.info(
                f"🟢 Created verification channel for match {match_id}: {verification_channel.name} (score: {team1_score}-{team2_score})"
            )

            return True  # Indică că meciul este în verificare

        except Exception as e:
            logger.error(
                f"🟢 Failed to create verification channel for match {match_id}: {e}"
            )

            # Fallback - respinge meciul și trimite doar un followup message
            error_embed = discord.Embed(
                title=f"{Config.ICONS['unsuccess']} Verification Setup Failed",
                description="Could not create verification channel. Match was not processed.",
                color=Config.EMBED_COLORS["red"],
            )
            error_embed.add_field(
                name="Error Details",
                value="The verification system encountered an error. Please try resubmitting or contact an administrator.",
                inline=False,
            )
            await interaction.followup.send(embed=error_embed, ephemeral=True)

            # Release submission lock
            try:
                await Match.release_submission_lock(match_id)
            except Exception as lock_error:
                logger.error(
                    f"🟢 Failed to release submission lock after verification error: {lock_error}"
                )

            return True  # Indică că procesarea s-a oprit (chiar dacă cu eroare)


class DenyReasonModal(discord.ui.Modal, title="Deny Match - Provide Reason"):
    def __init__(self, verification_view):
        super().__init__()
        self.verification_view = verification_view

    reason = discord.ui.TextInput(
        label="Reason for denial",
        placeholder="Enter the reason why this match is being denied...",
        required=True,
        max_length=500,
        style=discord.TextStyle.paragraph,
    )

    async def on_submit(self, interaction: discord.Interaction):
        # Procesează meciul ca fiind respins cu motivul specificat
        await self.verification_view._process_verified_match(
            interaction, approved=False, deny_reason=self.reason.value
        )


class ScoreboardVerificationView(discord.ui.View):
    def __init__(
        self,
        match_id: int,
        team1_score: int,
        team2_score: int,
        extracted_data: dict,
        original_interaction: discord.Interaction,
        scoreboard_cog,
    ):
        super().__init__(timeout=3600)  # 1 oră timeout pentru verificare
        self.match_id = match_id
        self.team1_score = team1_score
        self.team2_score = team2_score
        self.extracted_data = extracted_data
        self.original_interaction = original_interaction
        self.scoreboard_cog = scoreboard_cog

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.green)
    async def accept_match(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        try:
            # Verifică dacă utilizatorul este autorizat
            if not await Config.check_authorization(interaction):
                logger.warning(
                    f"🟢 Unauthorized user {interaction.user} ({interaction.user.id}) attempted to approve match {self.match_id}"
                )
                await interaction.response.send_message(
                    f"{Config.ICONS['not_allowed']} You are not authorized to approve match results.",
                    ephemeral=True,
                )
                return

            # Procesează meciul ca fiind valid
            await self._process_verified_match(interaction, approved=True)

        except Exception as e:
            logger.warning(
                f"🟢 Error in accept_match for user {interaction.user.id}: {e}"
            )
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "An error occurred while processing your request.",
                        ephemeral=True,
                    )
            except:
                pass  # Ignore if already responded

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.red)
    async def deny_match(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        try:
            # Verifică dacă utilizatorul este autorizat
            if not await Config.check_authorization(interaction):
                logger.warning(
                    f"🟢 Unauthorized user {interaction.user} ({interaction.user.id}) attempted to deny match {self.match_id}"
                )
                await interaction.response.send_message(
                    f"{Config.ICONS['not_allowed']} You are not authorized to deny match results.",
                    ephemeral=True,
                )
                return

            # Afișează modalul pentru motivul de respingere
            deny_modal = DenyReasonModal(self)
            await interaction.response.send_modal(deny_modal)

        except Exception as e:
            logger.warning(
                f"🟢 Error in deny_match for user {interaction.user.id}: {e}"
            )
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "An error occurred while processing your request.",
                        ephemeral=True,
                    )
            except:
                pass  # Ignore if already responded

    async def _process_verified_match(
        self, interaction: discord.Interaction, approved: bool, deny_reason: str = None
    ):
        """Procesează meciul după verificare"""
        try:
            await interaction.response.defer()

            if approved:
                # Procesează meciul normal cu datele extrase
                logger.info(f"🟢 Match {self.match_id} approved by {interaction.user}")

                # Folosește funcția existentă de procesare din scoreboard_cog
                await self._complete_match_processing()

                embed = discord.Embed(
                    title=f"{Config.ICONS['success']} Match Approved",
                    description=f"Match {self.match_id} has been approved and processed.",
                    color=Config.EMBED_COLORS["light_green"],
                )
                embed.add_field(
                    name="Final Score",
                    value=f"{self.team1_score} - {self.team2_score}",
                    inline=True,
                )
                embed.add_field(
                    name="Approved by", value=interaction.user.mention, inline=True
                )
            else:
                logger.info(
                    f"🟢 Match {self.match_id} denied by {interaction.user} - Reason: {deny_reason}"
                )

                # Release the submission lock pentru meciul respins
                try:
                    await Match.release_submission_lock(self.match_id)
                except Exception as lock_error:
                    logger.error(f"🟢 Failed to release submission lock: {lock_error}")

                embed = discord.Embed(
                    title=f"{Config.ICONS['unsuccess']} Match Denied",
                    description=f"Match {self.match_id} has been denied by {interaction.user.mention} and will not be processed.",
                    color=Config.EMBED_COLORS["red"],
                )
                embed.add_field(
                    name="Reason",
                    value=(
                        deny_reason
                        if deny_reason
                        else "Score verification failed - results not processed"
                    ),
                    inline=False,
                )

            # Dezactivează butoanele
            for item in self.children:
                item.disabled = True

            await interaction.edit_original_response(embed=embed, view=self)

            # Notifică utilizatorul original despre decizie
            try:
                user = self.original_interaction.user
                decision = "approved" if approved else "denied"
                notification_embed = discord.Embed(
                    title=f"{Config.ICONS['bell_lightgreen'] if approved else Config.ICONS['bell_red']} Match Verification Update",
                    description=f"Match ID {self.match_id} has been **{decision}** by staff.",
                    color=(
                        Config.EMBED_COLORS["light_green"]
                        if approved
                        else Config.EMBED_COLORS["red"]
                    ),
                )

                # Adaugă motivul dacă meciul a fost respins
                if not approved and deny_reason:
                    notification_embed.add_field(
                        name="Reason for denial", value=deny_reason, inline=False
                    )

                # Adaugă footer la notification embed
                current_time = datetime.now().strftime("%H:%M | %d/%m/%Y")
                notification_embed.set_footer(
                    text=f"Verification completed at {current_time}"
                )

                await user.send(embed=notification_embed)
            except Exception as dm_error:
                logger.warning(f"🟢 Could not send DM notification to user: {dm_error}")

            # Șterge canalul de verificare după un delay
            await asyncio.sleep(10)
            try:
                await interaction.channel.delete(
                    reason=f"Match {self.match_id} verification completed"
                )
            except Exception as delete_error:
                logger.error(f"🟢 Error deleting verification channel: {delete_error}")

        except Exception as e:
            logger.error(f"🟢 Error processing verified match {self.match_id}: {e}")
            error_embed = discord.Embed(
                title=f"{Config.ICONS['unsuccess']} Processing Error",
                description="An error occurred while processing the verification.",
                color=Config.EMBED_COLORS["red"],
            )
            await interaction.followup.send(embed=error_embed, ephemeral=True)

    async def _complete_match_processing(self):
        """Completează procesarea meciului cu datele extrase"""
        try:
            # Folosește match_data din extracted_data pentru a procesa complet meciul
            match_data = self.extracted_data.get("match_data")

            # Procesează teams data exact cum se face în funcția normală
            teams = self.extracted_data.get("teams", {})
            team1_data = teams.get("team1", {})
            team2_data = teams.get("team2", {})

            # Recalculează și procesează toți jucătorii
            all_players = await self.scoreboard_cog._process_teams_data(
                team1_data, team2_data, match_data, self.team1_score, self.team2_score
            )

            # Update database
            team1_for_db, team2_for_db = (
                await self.scoreboard_cog._prepare_teams_for_db(all_players)
            )

            await Match.complete_submission(
                self.match_id,
                team1_for_db,
                team2_for_db,
                self.team1_score,
                self.team2_score,
            )

            # Update user statistics
            await self.scoreboard_cog.update_user_stats(all_players, match_data)

            # Send final results image - găsește canalul din configurație
            try:
                configs = await ScoreboardConfig.get_all_configs()
                target_channel_id = None
                guild_id = self.original_interaction.guild.id

                for config in configs:
                    if config["guild_id"] == guild_id:
                        target_channel_id = config["results_channel_id"]
                        break

                if target_channel_id:
                    # Extrage echipele procesate
                    team1_players = [p for p in all_players if p.get("team") == 1]
                    team2_players = [p for p in all_players if p.get("team") == 2]

                    await self.scoreboard_cog.send_final_results_image(
                        self.original_interaction,
                        target_channel_id,
                        team1_players,
                        team2_players,
                        self.team1_score,
                        self.team2_score,
                        match_data,
                    )
                else:
                    logger.error(
                        f"🟢 Could not find target channel for guild {guild_id}"
                    )

            except Exception as results_error:
                logger.error(
                    f"🟢 Error sending results after verification: {results_error}"
                )

        except Exception as e:
            logger.error(f"🟢 Error in _complete_match_processing: {e}")
            raise

    async def on_timeout(self):
        """Handelează timeout-ul pentru verificare - șterge canalul și notifică utilizatorul"""
        try:
            # Release submission lock pentru meciul cu timeout
            try:
                await Match.release_submission_lock(self.match_id)
            except Exception as lock_error:
                logger.error(
                    f"🟢 Failed to release submission lock on timeout: {lock_error}"
                )

            # Notifică utilizatorul că niciun admin nu este activ
            try:
                user = self.original_interaction.user
                timeout_embed = discord.Embed(
                    title=f"{Config.ICONS['clock_yellow']} No Active Administrators",
                    description=f"Your match #{self.match_id} could not be verified because no administrators are currently active.",
                    color=Config.EMBED_COLORS["yellow"],
                )
                timeout_embed.add_field(
                    name=f"{Config.ICONS['info']} What happened?",
                    value="Your match required manual verification, but no staff member was available to review it within 1 hour.",
                    inline=False,
                )
                timeout_embed.add_field(
                    name=f"{Config.ICONS['question_yellow']} What should you do?",
                    value="• Please try resubmitting your match results later when administrators are active\n"
                    "• Check back during peak hours for faster verification",
                    inline=False,
                )
                timeout_embed.set_footer(
                    text="The verification channel has been automatically deleted"
                )

                await user.send(embed=timeout_embed)
                logger.info(
                    f"🟢 Sent timeout notification to user {user.display_name} for match {self.match_id}"
                )
            except Exception as dm_error:
                logger.warning(
                    f"🟢 Could not send timeout DM notification to user: {dm_error}"
                )

            # Șterge canalul de verificare automat
            try:
                if hasattr(self, "message") and self.message:
                    channel = self.message.channel
                    if channel and hasattr(channel, "delete"):
                        await channel.delete(
                            reason=f"Auto-delete after timeout for match {self.match_id}"
                        )
                        logger.info(
                            f"🟢 Deleted verification channel for match {self.match_id} due to timeout"
                        )
            except Exception as delete_error:
                logger.error(
                    f"🟢 Failed to delete verification channel on timeout: {delete_error}"
                )

            logger.info(
                f"🟢 Match {self.match_id} verification timed out - channel deleted, user notified"
            )

        except Exception as e:
            logger.error(
                f"🟢 Error handling verification timeout for match {self.match_id}: {e}"
            )


class SubmitScoreboardView(discord.ui.View):
    def __init__(self, target_channel_id):
        super().__init__(timeout=None)  # Make the view persistent
        self.target_channel_id = target_channel_id

    @discord.ui.button(
        label="Submit Results",
        style=discord.ButtonStyle.primary,
        custom_id="submit_scoreboard_button",  # Persistent custom_id
    )
    async def submit_scoreboard(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        # Check if there's an active season
        if not await Season.is_season_active():
            # Get the next season number
            last_season_id = await Season.get_last_season_id()
            next_season_number = last_season_id + 1

            embed = discord.Embed(
                title=f"{Config.ICONS['unsuccess']} Season Not Active",
                description="Submitting results is currently disabled because there is no active season.",
                color=Config.EMBED_COLORS["red"],
            )
            embed.add_field(
                name=f"{Config.ICONS['info']} Info",
                value=f"Please wait for Season {next_season_number} to start.",
                inline=False,
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        modal = MatchIDModal(self.target_channel_id)
        await interaction.response.send_modal(modal)


class Scoreboard(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.scoreboard_generator = ScoreboardResultsTemplateGenerator()

    async def cog_load(self):
        """Called when the cog is loaded - recreate persistent views"""
        # Try to recreate views, but don't block bot startup if it fails
        try:
            await self.recreate_persistent_views()
        except Exception as e:
            logger.warning(
                f"🟢 Failed to recreate scoreboard views on startup, will try again later: {e}"
            )
            # Schedule retry after bot is fully loaded
            self.bot.loop.create_task(self._retry_recreate_views())

    async def _retry_recreate_views(self):
        """Retry recreating persistent views after a delay"""
        await asyncio.sleep(30)  # Wait 30 seconds before retry
        try:
            logger.info("🟢 Retrying to recreate scoreboard persistent views...")
            await self.recreate_persistent_views()
        except Exception as e:
            logger.error(f"🟢 Retry failed for recreating scoreboard views: {e}")

    async def recreate_persistent_views(self):
        """Recreate persistent views from database configurations"""
        try:
            configs = await ScoreboardConfig.get_all_configs()
            if not configs:
                logger.info("🟢 No scoreboard configurations found")
                return

            for config in configs:
                try:
                    guild_id = config["guild_id"]
                    submission_channel_id = config["submission_channel_id"]
                    results_channel_id = config["results_channel_id"]
                    message_id = config.get("message_id")

                    guild = self.bot.get_guild(guild_id)
                    if not guild:
                        logger.warning(
                            f"🟢 Guild {guild_id} not found, skipping scoreboard config"
                        )
                        continue

                    submission_channel = guild.get_channel(submission_channel_id)
                    if not submission_channel:
                        logger.warning(
                            f"🟢 Submission channel {submission_channel_id} not found, skipping"
                        )
                        continue

                    # Create the persistent view
                    view = SubmitScoreboardView(results_channel_id)

                    # If we have a message_id, try to get the existing message
                    if message_id:
                        try:
                            message = await submission_channel.fetch_message(message_id)
                            # Update the view on the existing message
                            await message.edit(view=view)
                            logger.info(
                                f"🟢 Recreated persistent scoreboard view for guild {guild_id} on existing message {message_id}"
                            )
                            continue
                        except discord.NotFound:
                            logger.warning(
                                f"🟢 Scoreboard message {message_id} not found, will create new one"
                            )
                        except Exception as e:
                            logger.error(
                                f"🟢 Error fetching scoreboard message {message_id}: {e}"
                            )

                    # Create new message with embed and view
                    results_channel = guild.get_channel(results_channel_id)
                    results_channel_mention = (
                        results_channel.mention
                        if results_channel
                        else f"<#{results_channel_id}>"
                    )

                    embed = discord.Embed(
                        title=f"{Config.ICONS['stats']} Scoreboard Submission",
                        description=f"Scoreboard results will be posted to {results_channel_mention}",
                        color=Config.MAIN_COLOR,
                    )
                    embed.add_field(
                        name="How to submit:",
                        value="1. Click the button below (Submit Results)\n2. Enter your match ID\n3. Upload the scoreboard screenshot (In Game Results)\n4. Wait for processing",
                        inline=False,
                    )
                    embed.set_footer(text="Only match participants can submit results")

                    try:
                        new_message = await submission_channel.send(
                            embed=embed, view=view
                        )

                        # Update database with new message_id
                        await ScoreboardConfig.set_config(
                            guild_id,
                            submission_channel_id,
                            results_channel_id,
                            new_message.id,
                        )

                        logger.info(
                            f"🟢 Created new persistent scoreboard view for guild {guild_id} with message {new_message.id}"
                        )

                    except Exception as e:
                        logger.error(
                            f"🟢 Error creating scoreboard message for guild {guild_id}: {e}"
                        )

                except Exception as config_error:
                    logger.error(
                        f"🟢 Error processing scoreboard config: {config_error}"
                    )
                    continue  # Continue with next config

        except Exception as e:
            logger.error(
                f"🟢 Error recreating persistent scoreboard views (will retry later): {e}"
            )
            # Don't let database connection errors prevent the bot from starting
            # The views can be recreated manually later with the set_scoreboard_channel command

    @app_commands.command(
        name="set_scoreboard_channel",
        description="Set a target channel for scoreboard results",
    )
    @app_commands.describe(
        target_channel="The channel where scoreboard results will be posted"
    )
    @app_commands.default_permissions(administrator=True)
    async def set_scoreboard_channel(
        self, interaction: discord.Interaction, target_channel: discord.TextChannel
    ):
        # Verifică autorizația utilizatorului
        if not await Config.check_authorization(interaction):
            return

        # Check if user has admin permissions
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "You need administrator permissions to use this command.",
                ephemeral=True,
            )
            return

        try:
            submission_channel = interaction.channel
            guild_id = interaction.guild.id

            # Create embed with button
            embed = discord.Embed(
                title=f"{Config.ICONS['stats']} Scoreboard Submission",
                description=f"Scoreboard results will be posted to {target_channel.mention}",
                color=Config.MAIN_COLOR,
            )
            embed.add_field(
                name="How to submit:",
                value="1. Click the button below (Submit Results)\n2. Enter your match ID\n3. Upload the scoreboard screenshot (In Game Results)\n4. Wait for processing",
                inline=False,
            )
            embed.set_footer(text="Only match participants can submit results")

            # Create persistent view with button
            view = SubmitScoreboardView(target_channel.id)

            # Send the message
            message = await submission_channel.send(embed=embed, view=view)

            # Save configuration to database
            await ScoreboardConfig.set_config(
                guild_id, submission_channel.id, target_channel.id, message.id
            )

            # Confirm to admin
            success_embed = discord.Embed(
                title=f"{Config.ICONS['success']} Scoreboard Channel Configured",
                description=f"Scoreboard submission set up successfully!\n\n"
                f"**Submission Channel:** {submission_channel.mention}\n"
                f"**Results Channel:** {target_channel.mention}\n\n"
                f"Users can now submit match results using the button above.\n"
                f"Screenshots will be automatically analyzed and processed.\n"
                f"This configuration will persist across bot restarts.",
                color=Config.EMBED_COLORS["light_green"],
            )
            await interaction.response.send_message(embed=success_embed, ephemeral=True)

        except Exception as e:
            logger.error(f"🟢 Error setting scoreboard channel: {e}")
            embed = discord.Embed(
                title=f"{Config.ICONS['unsuccess']} Setup Error",
                description="An error occurred while setting up the scoreboard channel. Please try again.",
                color=Config.EMBED_COLORS["red"],
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Scoreboard(bot))
