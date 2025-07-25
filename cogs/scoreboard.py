import discord
from discord.ext import commands
from discord import app_commands
from config import Config
import asyncio
import json
from openai import OpenAI
import os
import logging
from datetime import datetime
from models.match import Match
from models.stats import Stats
from models.scoreboard_config import ScoreboardConfig
from models.season import Season
from assets.template_generators.scoreboard_generator import ScoreboardResultsTemplateGenerator

logger = logging.getLogger(__name__)

# Initialize OpenAI client
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


class MatchIDModal(discord.ui.Modal, title="Submit Scoreboard"):
    def __init__(self, target_channel_id):
        super().__init__()
        self.target_channel_id = target_channel_id

    async def _delete_screenshot_safely(self, screenshot_message):
        """Helper method to safely delete screenshot messages"""
        if screenshot_message:
            try:
                await screenshot_message.delete()
                logger.info("Deleted screenshot message for failed submission")
            except discord.NotFound:
                pass  # Message already deleted
            except discord.Forbidden:
                logger.warning("No permission to delete screenshot message")
            except Exception as e:
                logger.error(f"Error deleting screenshot message: {e}")

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
                    f"Released submission lock for match {match_id_int} - user {user_discord_id} not authorized"
                )
            except Exception as lock_error:
                logger.error(f"Failed to release submission lock: {lock_error}")

            await interaction.followup.send(
                "You are not authorized to submit results for this match.",
                ephemeral=True,
            )
            return

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

            message = await self.bot.wait_for(
                "message", check=check, timeout=60.0
            )  
            
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

            # Get the image URL
            image_url = message.attachments[0].url

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
                    f"Released submission lock for match {match_data['match_id']} due to timeout"
                )
            except Exception as lock_error:
                logger.error(f"Failed to release submission lock: {lock_error}")

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
        """Process the scoreboard image using advanced OpenAI GPT with player matching"""
        try:
            # Get teams data from database
            teams_data = json.loads(match_data.get("teams", "{}"))
            team1_players = teams_data.get("team1", {}).get("players", [])
            team2_players = teams_data.get("team2", {}).get("players", [])
            
            # Create the expected players list for GPT with discord_ids
            team1_players_info = [{"nickname": p["nickname"], "discord_id": p["discord_id"]} for p in team1_players]
            team2_players_info = [{"nickname": p["nickname"], "discord_id": p["discord_id"]} for p in team2_players]
            
            # Define the advanced OpenAI call function with player matching
            def make_openai_call():
                return client.chat.completions.create(
                    model="gpt-4o",
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": f"""Analyze this Standoff 2 scoreboard screenshot and extract player statistics with smart player matching.

EXPECTED PLAYERS FROM DATABASE:
Team 1: {team1_players_info}
Team 2: {team2_players_info}

INSTRUCTIONS:
1. First verify this is a valid Standoff 2 scoreboard with Counter-Terrorists vs Terrorists teams
2. Extract all visible player names and their stats (kills, assists, deaths)
   - FIRST STEP: Remove ALL clan tags before any processing
   - Clan tags are in format "[TAG] PlayerName" - remove "[TAG] " completely
   - Work ONLY with clean names for all subsequent steps
3. CRITICAL: Player name extraction process
   - When you see "[EXTN] User-1" in screenshot → extract as "User-1" 
   - When you see "[PRO] Player123" in screenshot → extract as "Player123"
   - NEVER include clan tags in any extracted names
   - Use ONLY the clean names for matching and processing
4. Match the clean extracted names directly to database players:
   - Use ONLY clean names (no clan tags) from step 2
   - Try exact name matches first between clean extracted names and database
   - Then try substring matching (database name contained in clean extracted name)
   - Handle variations like special characters, numbers
   - Match case-insensitively
5. Use FIXED team mapping: Team1 = Terrorists, Team2 = Counter-Terrorists
6. For each player in the result, use the EXACT discord_id from the database
7. Assign unmatched database players default stats (0 kills, 0 assists, 13 deaths)

Return ONLY valid JSON in this exact format:
{{
  "game_detected": "standoff2" or "unknown",
  "confidence": number (0.0-1.0 confidence this is valid Standoff 2 scoreboard),
  "teams": {{
    "team1": {{
      "score": integer (Terrorists score),
      "players": [
        {{
          "nickname": "exact_database_player_name",
          "discord_id": "actual_discord_id_from_database",
          "kills": integer,
          "assists": integer, 
          "deaths": integer,
          "matched_from": "clean_extracted_name_from_screenshot" or "default_stats"
        }}
      ]
    }},
    "team2": {{
      "score": integer (Counter-Terrorists score),
      "players": [
        {{
          "nickname": "exact_database_player_name", 
          "discord_id": "actual_discord_id_from_database",
          "kills": integer,
          "assists": integer,
          "deaths": integer,
          "matched_from": "clean_extracted_name_from_screenshot" or "default_stats"
        }}
      ]
    }}
  }},
  "matching_details": [
    "details about each matching attempt for debugging"
  ]
}}

IMPORTANT RULES:
- Include ALL database players in the result (either with matched stats or default stats)
- Use exact database nicknames and discord_ids in the result
- Team1 = Terrorists (T side), Team2 = Counter-Terrorists (CT side)
- If a database player isn't found in screenshot, give them default stats: 0K/0A/13D
- Confidence should be 0.8+ only for clear Standoff 2 scoreboards
- Extract exact numbers for kills, assists, deaths as shown in screenshot
- CRITICAL: Remove "[TAG] " prefixes BEFORE any processing or matching
- Store clean extracted name (without clan tags) in "matched_from"
- VERY IMPORTANT: Return the exact discord_id from the database for each player
- VERY IMPORTANT: Extract players WITHOUT clan tags from the very beginning""",
                                },
                                {"type": "image_url", "image_url": {"url": image_url}},
                            ],
                        }
                    ],
                    max_tokens=2000,
                )

            # Run the OpenAI call in a separate thread to avoid blocking Discord's event loop
            response = await asyncio.to_thread(make_openai_call)

            # Log token usage
            usage = response.usage
            logger.info(
                f"OpenAI Token Usage — Prompt: {usage.prompt_tokens}, "
                f"Completion: {usage.completion_tokens}, "
                f"Total: {usage.total_tokens}"
            )

            # Parse the JSON response
            content = response.choices[0].message.content.strip()

            # Try to extract JSON from the response
            try:
                # Find JSON in the response
                if "```json" in content:
                    json_start = content.find("```json") + 7
                    json_end = content.find("```", json_start)
                    json_content = content[json_start:json_end].strip()
                elif "{" in content:
                    json_start = content.find("{")
                    json_end = content.rfind("}") + 1
                    json_content = content[json_start:json_end]
                else:
                    json_content = content

                extracted_data = json.loads(json_content)
                logger.info(f"GPT extracted data: {json.dumps(extracted_data, indent=2)}")
                
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse OpenAI JSON response: {e}")
                logger.error(f"Raw response: {content}")
                
                # Release the submission lock since parsing failed
                try:
                    await Match.release_submission_lock(match_data["match_id"])
                except Exception as lock_error:
                    logger.error(f"Failed to release submission lock: {lock_error}")

                await self._delete_screenshot_safely(screenshot_message)
                await interaction.followup.send(
                    "Failed to parse scoreboard data. Please try again.", ephemeral=True
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
            logger.error(f"Error processing scoreboard with GPT: {e}")

            # Release the submission lock since an error occurred
            try:
                await Match.release_submission_lock(match_data["match_id"])
                logger.info(
                    f"Released submission lock for match {match_data['match_id']} due to processing error"
                )
            except Exception as lock_error:
                logger.error(f"Failed to release submission lock: {lock_error}")

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
                        f"Released submission lock for match {match_data['match_id']} due to game detection failure"
                    )
                except Exception as lock_error:
                    logger.error(f"Failed to release submission lock: {lock_error}")

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
            if team1_score < 0 or team2_score < 0 or (team1_score == 0 and team2_score == 0):
                # Release the submission lock since scores are invalid
                try:
                    await Match.release_submission_lock(match_data["match_id"])
                except Exception as lock_error:
                    logger.error(f"Failed to release submission lock: {lock_error}")

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
                    logger.error(f"Failed to release submission lock: {lock_error}")

                await self._delete_screenshot_safely(screenshot_message)
                await interaction.followup.send(
                    f"Tied matches are not supported: {team1_score} - {team2_score}\n"
                    f"Please submit results only for completed matches.",
                    ephemeral=True,
                )
                return

            # Get original teams data (nu mai e nevoie pentru matching, doar pentru referință)
            original_teams_data = json.loads(match_data.get("teams", "{}"))

            # Process team1 players (GPT already provided discord_id directly)
            updated_team1 = []
            for gpt_player in team1_data.get("players", []):
                # GPT already provided the discord_id, no need for manual matching
                discord_id = gpt_player["discord_id"]
                
                # Calculate points based on player presence and league type
                match_league = match_data.get("league")
                default_league = await self.get_default_league_from_config()
                was_absent = gpt_player.get("matched_from", "") == "default_stats"

                if was_absent and match_league == default_league:
                    # Player was absent (left match): apply leaving penalty based on their level
                    current_points = await self.get_player_current_points(
                        int(discord_id), match_league
                    )
                    is_challenger = await self.check_challenger_status(
                        int(discord_id), current_points
                    )
                    points_received = await self.calculate_leaving_penalty_points(
                        int(discord_id), match_league, is_challenger
                    )
                elif was_absent:
                    # Player was absent in non-default league: fixed penalty
                    points_received = -45
                elif match_league == default_league:
                    # Player was present: normal win/loss calculation
                    team1_won = team1_score > team2_score
                    current_points = await self.get_player_current_points(
                        int(discord_id), match_league
                    )
                    is_challenger = await self.check_challenger_status(
                        int(discord_id), current_points
                    )
                    points_received = await self.calculate_level_based_points(
                        int(discord_id), team1_won, match_league, is_challenger
                    )
                else:
                    # Player was present in non-default league: normal fixed points
                    team1_won = team1_score > team2_score
                    points_received = 25 if team1_won else -25
                
                # Calculate rating using bot formula: (KILLS + 0.5 × ASSISTS) / max(1, DEATHS)
                kills = gpt_player.get("kills", 0)
                assists = gpt_player.get("assists", 0)
                deaths = max(1, gpt_player.get("deaths", 13))  # Prevent division by zero
                rating = round((kills + 0.5 * assists) / deaths, 1)
                
                updated_player = {
                    "nickname": gpt_player["nickname"],
                    "discord_id": discord_id,  # Direct from GPT, no matching needed
                    "kills": kills,
                    "assists": assists,
                    "deaths": gpt_player.get("deaths", 13),
                    "rating": rating,
                    "points": points_received,  # For generator
                    "points_received": points_received,  # For database
                    "matched_from": gpt_player.get("matched_from", "default_stats")
                }
                updated_team1.append(updated_player)

            # Process team2 players (GPT already provided discord_id directly)
            updated_team2 = []
            for gpt_player in team2_data.get("players", []):
                # GPT already provided the discord_id, no need for manual matching
                discord_id = gpt_player["discord_id"]
                
                # Calculate points based on player presence and league type
                match_league = match_data.get("league")
                default_league = await self.get_default_league_from_config()
                was_absent = gpt_player.get("matched_from", "") == "default_stats"

                if was_absent and match_league == default_league:
                    # Player was absent (left match): apply leaving penalty based on their level
                    current_points = await self.get_player_current_points(
                        int(discord_id), match_league
                    )
                    is_challenger = await self.check_challenger_status(
                        int(discord_id), current_points
                    )
                    points_received = await self.calculate_leaving_penalty_points(
                        int(discord_id), match_league, is_challenger
                    )
                elif was_absent:
                    # Player was absent in non-default league: fixed penalty
                    points_received = -45
                elif match_league == default_league:
                    # Player was present: normal win/loss calculation
                    team2_won = team2_score > team1_score
                    current_points = await self.get_player_current_points(
                        int(discord_id), match_league
                    )
                    is_challenger = await self.check_challenger_status(
                        int(discord_id), current_points
                    )
                    points_received = await self.calculate_level_based_points(
                        int(discord_id), team2_won, match_league, is_challenger
                    )
                else:
                    # Player was present in non-default league: normal fixed points
                    team2_won = team2_score > team1_score
                    points_received = 25 if team2_won else -25
                
                # Calculate rating using bot formula: (KILLS + 0.5 × ASSISTS) / max(1, DEATHS)
                kills = gpt_player.get("kills", 0)
                assists = gpt_player.get("assists", 0)
                deaths = max(1, gpt_player.get("deaths", 13))  # Prevent division by zero
                rating = round((kills + 0.5 * assists) / deaths, 1)
                
                updated_player = {
                    "nickname": gpt_player["nickname"],
                    "discord_id": discord_id,  # Direct from GPT, no matching needed
                    "kills": kills,
                    "assists": assists,
                    "deaths": gpt_player.get("deaths", 13),
                    "rating": rating,
                    "points": points_received,  # For generator
                    "points_received": points_received,  # For database
                    "matched_from": gpt_player.get("matched_from", "default_stats")
                }
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
                    "points_received": player["points_received"]
                }
                team1_for_db.append(db_player)
            
            for player in updated_team2:
                db_player = {
                    "nickname": player["nickname"],
                    "discord_id": player["discord_id"],
                    "kills": player["kills"],
                    "assists": player["assists"],
                    "deaths": player["deaths"],
                    "points_received": player["points_received"]
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
            matching_details = extracted_data.get("matching_details", [])
            matched_count = sum(1 for p in updated_team1 + updated_team2 if p.get("matched_from", "") != "default_stats")
            total_players = len(updated_team1 + updated_team2)
            
            success_embed = discord.Embed(
                title=f"{Config.ICONS['success']} Scoreboard Successfully Processed",
                description="Screenshot analysis completed successfully!",
                color=Config.EMBED_COLORS["light_green"],
            )
            success_embed.add_field(
                name=f"{Config.ICONS['stats']} Match Results",
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
            logger.error(f"Error processing GPT results: {e}")

            # Release the submission lock since an error occurred
            try:
                await Match.release_submission_lock(match_data["match_id"])
                logger.info(
                    f"Released submission lock for match {match_data['match_id']} due to processing error"
                )
            except Exception as lock_error:
                logger.error(f"Failed to release submission lock: {lock_error}")

            # Delete the screenshot since submission failed
            await self._delete_screenshot_safely(screenshot_message)

            await interaction.followup.send(
                "Error processing match results. Please try again.", ephemeral=True
            )

    async def update_user_stats(self, all_players, match_data):
        """Update user statistics in database and manage level roles"""
        # Extract league from match data
        league_name = match_data.get("league")
        logger.info(f"Updating stats for match in league: {league_name}")
        
        # Get the default league from config table to determine if level roles should be updated
        default_league = await self.get_default_league_from_config()
        is_default_league = league_name == default_league
        logger.info(
            f"Default league: {default_league}, Current league: {league_name}, Is default: {is_default_league}"
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
                logger.error(f"Failed to get default league from config: {e}")
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
                logger.error(f"Target channel {target_channel_id} not found")
                return

            # Ensure all players have assists field (for backward compatibility)
            for player in team1 + team2:
                if "assists" not in player:
                    player["assists"] = 0

            # Create scoreboard generator instance
            scoreboard_generator = ScoreboardResultsTemplateGenerator()

            # Generate scoreboard results image using the dedicated template generator
            image_path = await scoreboard_generator.generate_scoreboard_image(
                bot=self.bot,
                match_data=match_data,
                team1=team1,
                team2=team2,
                team1_score=team1_score,
                team2_score=team2_score
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
            logger.error(f"Error sending final results image: {e}")
            # Fallback to text message if image generation fails
            teams_data = match_data.get("teams", {})
            if isinstance(teams_data, str):
                teams_data = json.loads(teams_data)
            
            team1_captain = teams_data.get('team1', {}).get('captain', 'Unknown')
            team2_captain = teams_data.get('team2', {}).get('captain', 'Unknown')
            winner = f"Team {team1_captain}" if team1_score > team2_score else f"Team {team2_captain}"
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
                f"Error getting points for {discord_id} in league {league_name}: {e}"
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
                f"Error calculating leaving penalty for {discord_id} in league {league_name}: {e}"
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
                f"Error calculating level-based points for {discord_id} in league {league_name}: {e}"
            )
            # Fallback to old system
            return 20 if is_win else -10

    async def check_challenger_status(self, discord_id, current_points):
        """Check if player is currently in Challenger tier (only used for default league)"""
        try:
            from cogs.levels import LevelsCog

            levels_cog = LevelsCog(None)
            return await levels_cog.is_challenger_eligible(discord_id, current_points)
        except Exception as e:
            logger.error(f"Error checking challenger status for {discord_id}: {e}")
            return False


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
            logger.warning(f"Failed to recreate scoreboard views on startup, will try again later: {e}")
            # Schedule retry after bot is fully loaded
            self.bot.loop.create_task(self._retry_recreate_views())

    async def _retry_recreate_views(self):
        """Retry recreating persistent views after a delay"""
        await asyncio.sleep(30)  # Wait 30 seconds before retry
        try:
            logger.info("Retrying to recreate scoreboard persistent views...")
            await self.recreate_persistent_views()
        except Exception as e:
            logger.error(f"Retry failed for recreating scoreboard views: {e}")

    async def recreate_persistent_views(self):
        """Recreate persistent views from database configurations"""
        try:
            configs = await ScoreboardConfig.get_all_configs()
            if not configs:
                logger.info("No scoreboard configurations found")
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
                            f"Guild {guild_id} not found, skipping scoreboard config"
                        )
                        continue

                    submission_channel = guild.get_channel(submission_channel_id)
                    if not submission_channel:
                        logger.warning(
                            f"Submission channel {submission_channel_id} not found, skipping"
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
                                f"Recreated persistent scoreboard view for guild {guild_id} on existing message {message_id}"
                            )
                            continue
                        except discord.NotFound:
                            logger.warning(
                                f"Scoreboard message {message_id} not found, will create new one"
                            )
                        except Exception as e:
                            logger.error(
                                f"Error fetching scoreboard message {message_id}: {e}"
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
                        new_message = await submission_channel.send(embed=embed, view=view)

                        # Update database with new message_id
                        await ScoreboardConfig.set_config(
                            guild_id,
                            submission_channel_id,
                            results_channel_id,
                            new_message.id,
                        )

                        logger.info(
                            f"Created new persistent scoreboard view for guild {guild_id} with message {new_message.id}"
                        )

                    except Exception as e:
                        logger.error(
                            f"Error creating scoreboard message for guild {guild_id}: {e}"
                        )

                except Exception as config_error:
                    logger.error(f"Error processing scoreboard config: {config_error}")
                    continue  # Continue with next config

        except Exception as e:
            logger.error(f"Error recreating persistent scoreboard views (will retry later): {e}")
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
                value="1. Click the button below\n2. Enter your match ID\n3. Upload the scoreboard screenshot\n4. Wait for processing",
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
            logger.error(f"Error setting scoreboard channel: {e}")
            embed = discord.Embed(
                title=f"{Config.ICONS['unsuccess']} Setup Error",
                description="An error occurred while setting up the scoreboard channel. Please try again.",
                color=Config.EMBED_COLORS["red"],
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Scoreboard(bot))
