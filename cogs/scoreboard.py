import discord
from discord.ext import commands
from discord import app_commands
from config import Config
import asyncio
import json
from openai import OpenAI
import os
import logging
from difflib import SequenceMatcher
from datetime import datetime
from models.match import Match
from models.user import User
from models.scoreboard_config import ScoreboardConfig

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
                title="❌ Results Already Submitted",
                description="Results have already been submitted for this match.",
                color=0xFF0000,
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        # Attempt to atomically lock the match for submission
        submission_locked = await Match.try_lock_for_submission(
            match_id_int, str(interaction.user.id)
        )

        if not submission_locked:
            embed = discord.Embed(
                title="❌ Submission In Progress",
                description="This match is already being processed by another player or has been submitted. Please try again in a moment.",
                color=0xFF4444,
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        # Check if user participated in the match
        user_discord_id = str(interaction.user.id)
        ct_team_data = json.loads(match_data.get("ct_team", "[]"))
        t_team_data = json.loads(match_data.get("t_team", "[]"))

        # Handle both old format (just players array) and new format (with players and score)
        if isinstance(ct_team_data, list):
            # Old format - just an array of players
            ct_team = ct_team_data
        else:
            # New format - object with players array and score
            ct_team = ct_team_data.get("players", [])

        if isinstance(t_team_data, list):
            # Old format - just an array of players
            t_team = t_team_data
        else:
            # New format - object with players array and score
            t_team = t_team_data.get("players", [])

        user_participated = False
        for player in ct_team + t_team:
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
            title="📷 Upload Scoreboard Screenshot",
            description="Please upload the scoreboard screenshot in your next message.\n\n⏰ **Timeout:** 60 seconds",
            color=0x0099FF,
        )
        upload_embed.add_field(
            name="📋 Instructions",
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
            )  # Show processing embed
            processing_embed = discord.Embed(
                title="🔄 Processing Screenshot",
                description="Analyzing screenshot data...",
                color=0xFFAA00,
            )
            processing_embed.add_field(
                name="📊 Analysis Steps",
                value="• Reading player statistics\n• Matching players to database\n• Calculating results\n• Generating final scoreboard",
                inline=False,
            )
            processing_message = await interaction.followup.send(
                embed=processing_embed, ephemeral=True
            )

            # Get the image URL
            image_url = message.attachments[0].url

            # Process the image with OpenAI
            await self.process_scoreboard_image(
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

    async def process_scoreboard_image(
        self,
        interaction,
        image_url,
        match_data,
        target_channel_id,
        screenshot_message=None,
        processing_message=None,
    ):
        """Process the scoreboard image using OpenAI Vision API"""
        try:
            # Define the synchronous OpenAI call function
            def make_openai_call():
                return client.chat.completions.create(
                    model="gpt-4o",
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": """Analyze this scoreboard screenshot and extract player statistics. 

IMPORTANT: First verify this is a Standoff 2 scoreboard with Counter-Terrorists vs Terrorists teams.

Return ONLY valid JSON in this exact format:
{
  "game_detected": "standoff2" or "unknown",
  "confidence": number (0.0-1.0 how confident this is a valid Standoff 2 scoreboard),
  "teams": {
    "Counter-Terrorists": {
      "score": integer,
      "players": [
        {"name": "exact_player_name_without_clan_tag", "kills": integer, "assists": integer, "deaths": integer}
      ]
    },
    "Terrorists": {
      "score": integer, 
      "players": [
        {"name": "exact_player_name_without_clan_tag", "kills": integer, "assists": integer, "deaths": integer}
      ]
    }
  }
}
Extract exact player names, kills, assists, and deaths as shown. Pay attention to special characters, numbers, and capitalization.""",
                                },
                                {"type": "image_url", "image_url": {"url": image_url}},
                            ],
                        }
                    ],
                    max_tokens=1000,
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
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse OpenAI JSON response: {e}")
                await interaction.followup.send(
                    "Failed to parse scoreboard data. Please try again.", ephemeral=True
                )
                return

            # Validate and match players
            await self.validate_and_update_match(
                interaction,
                extracted_data,
                match_data,
                target_channel_id,
                screenshot_message,
                processing_message,
            )

        except Exception as e:
            logger.error(f"Error processing scoreboard image: {e}")

            # Release the submission lock since an error occurred
            try:
                await Match.release_submission_lock(match_data["match_id"])
                logger.info(
                    f"Released submission lock for match {match_data['match_id']} due to an processing error"
                )
            except Exception as lock_error:
                logger.error(f"Failed to release submission lock: {lock_error}")

            # Delete the screenshot since submission failed
            await self._delete_screenshot_safely(screenshot_message)

            await interaction.followup.send(
                "Error processing the scoreboard image. Please try again.",
                ephemeral=True,
            )

    async def validate_and_update_match(
        self,
        interaction,
        extracted_data,
        match_data,
        target_channel_id,
        screenshot_message=None,
        processing_message=None,
    ):
        """Validate extracted data against database and update match results"""
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

            # Get team data from database
            ct_team_data = json.loads(match_data.get("ct_team", "[]"))
            t_team_data = json.loads(match_data.get("t_team", "[]"))

            # Handle both old format (just players array) and new format (with players and score)
            if isinstance(ct_team_data, list):
                # Old format - just an array of players
                ct_team = ct_team_data
            else:
                # New format - object with players array and score
                ct_team = ct_team_data.get("players", [])

            if isinstance(t_team_data, list):
                # Old format - just an array of players
                t_team = t_team_data
            else:
                # New format - object with players array and score
                t_team = t_team_data.get("players", [])

            all_db_players = ct_team + t_team

            # Get all extracted players from both teams (since teams switch at round 15)
            teams_data = extracted_data.get("teams", {})
            ct_data = teams_data.get("Counter-Terrorists", {})
            t_data = teams_data.get("Terrorists", {})
            all_extracted_players = ct_data.get("players", []) + t_data.get(
                "players", []
            )

            # Match all database players against all extracted players (ignore team assignment)
            matched_players, match_details = self.match_all_players(
                all_extracted_players, [p["nickname"] for p in all_db_players]
            )

            # Validate that we matched at least 50% of the players
            total_db_players = len(all_db_players)
            matched_count = len(matched_players)
            match_rate = matched_count / total_db_players if total_db_players > 0 else 0

            if match_rate < 0.5:
                # Release the submission lock since not enough players matched
                try:
                    await Match.release_submission_lock(match_data["match_id"])
                    logger.info(
                        f"Released submission lock for match {match_data['match_id']} due to insufficient player matches ({match_rate:.1%})"
                    )
                except Exception as lock_error:
                    logger.error(f"Failed to release submission lock: {lock_error}")

                # Delete the screenshot since submission failed
                await self._delete_screenshot_safely(screenshot_message)

                # Create embed for player matching error
                embed = discord.Embed(
                    title="❌ Insufficient Player Matches",
                    description=f"Found {matched_count}/{total_db_players} players ({match_rate:.1%}) - Need at least 50% to validate match.",
                    color=0xFF0000,  # Red color
                )

                # Expected players field
                expected_players = ", ".join([p["nickname"] for p in all_db_players])
                embed.add_field(
                    name="Expected Players", value=expected_players, inline=False
                )

                # Extracted players field
                extracted_names = [p["name"] for p in all_extracted_players]
                extracted_players = (
                    ", ".join(extracted_names) if extracted_names else "None"
                )
                embed.add_field(
                    name="Extracted Players", value=extracted_players, inline=False
                )

                # Matching details field
                matching_details = (
                    "\n".join(match_details) if match_details else "No matches found"
                )
                embed.add_field(
                    name="Matching Details",
                    value=f"```\n{matching_details}\n```",
                    inline=False,
                )

                # Solution field
                embed.add_field(
                    name="💡 Solution",
                    value="Please ensure the scoreboard shows players from your match with clear, readable names.",
                    inline=False,
                )

                await interaction.followup.send(embed=embed, ephemeral=True)
                return

            # Validate team structure - ensure teammates stay together
            team_validation = self.validate_team_structure(
                ct_team, t_team, extracted_data, matched_players
            )

            if not team_validation["overall_valid"]:
                # Release the submission lock since team validation failed
                try:
                    await Match.release_submission_lock(match_data["match_id"])
                    logger.info(
                        f"Released submission lock for match {match_data['match_id']} due to team validation failure"
                    )
                except Exception as lock_error:
                    logger.error(f"Failed to release submission lock: {lock_error}")

                # Delete the screenshot since submission failed
                await self._delete_screenshot_safely(screenshot_message)

                # Create embed for team validation failure
                error_embed = discord.Embed(
                    title="❌ Team Structure Validation Failed",
                    description="The team structure in the screenshot does not match the expected team structure for this match.",
                    color=0xFF4444,
                )

                # Show expected team structure
                expected_ct = [p["nickname"] for p in ct_team]
                expected_t = [p["nickname"] for p in t_team]

                error_embed.add_field(
                    name="📋 Expected Team Structure",
                    value=f"**Team 1:** {', '.join(expected_ct)}\n**Team 2:** {', '.join(expected_t)}",
                    inline=False,
                )

                # Show actual team structure from screenshot
                actual_ct = []
                actual_t = []

                for assignment in team_validation["ct_assignments"]:
                    if assignment["screenshot_side"] == "ct_side":
                        actual_ct.append(assignment["db_name"])
                    else:
                        actual_t.append(assignment["db_name"])

                for assignment in team_validation["t_assignments"]:
                    if assignment["screenshot_side"] == "ct_side":
                        actual_ct.append(assignment["db_name"])
                    else:
                        actual_t.append(assignment["db_name"])

                error_embed.add_field(
                    name="📸 Actual Team Structure in Screenshot",
                    value=f"**CT Side:** {', '.join(actual_ct) if actual_ct else 'None'}\n**T Side:** {', '.join(actual_t) if actual_t else 'None'}",
                    inline=False,
                )

                await interaction.followup.send(embed=error_embed, ephemeral=True)
                return

            # Get scores from screenshot
            ct_score = ct_data.get("score", 0)
            t_score = t_data.get("score", 0)

            # Validate score reasonableness
            if ct_score < 0 or t_score < 0 or (ct_score == 0 and t_score == 0):
                # Release the submission lock since scores are invalid
                try:
                    await Match.release_submission_lock(match_data["match_id"])
                    logger.info(
                        f"Released submission lock for match {match_data['match_id']} due to invalid scores"
                    )
                except Exception as lock_error:
                    logger.error(f"Failed to release submission lock: {lock_error}")

                # Delete the screenshot since submission failed
                await self._delete_screenshot_safely(screenshot_message)

                await interaction.followup.send(
                    f"Invalid scores detected: CT({ct_score}) vs T({t_score})\n"
                    f"Please verify the scoreboard is clear and shows the final scores.",
                    ephemeral=True,
                )
                return

            if ct_score == t_score:
                # Release the submission lock since match is a tie
                try:
                    await Match.release_submission_lock(match_data["match_id"])
                    logger.info(
                        f"Released submission lock for match {match_data['match_id']} due to tie score"
                    )
                except Exception as lock_error:
                    logger.error(f"Failed to release submission lock: {lock_error}")

                # Delete the screenshot since submission failed
                await self._delete_screenshot_safely(screenshot_message)

                await interaction.followup.send(
                    "The match appears to be a tie. Please verify the scoreboard.",
                    ephemeral=True,
                )
                return

            # Determine winning team based on which side won more rounds
            ct_team_won = ct_score > t_score  # True if CT side won in screenshot

            # Use GPT-4o's team assignments from screenshot (not original database teams)
            # Create new team structures based on screenshot analysis
            updated_ct_team = []
            updated_t_team = []

            # Process CT team players from screenshot
            for screenshot_player in ct_data.get("players", []):
                player_name = screenshot_player["name"]
                # Find the corresponding database player
                db_player = None
                for p in all_db_players:
                    if (
                        p["nickname"] in matched_players
                        and matched_players[p["nickname"]]["name"] == player_name
                    ):
                        db_player = p.copy()  # Make a copy to avoid modifying original
                        break

                if db_player:
                    # Use screenshot stats and database player info
                    db_player["kills"] = screenshot_player["kills"]
                    db_player["assists"] = screenshot_player.get("assists", 0)
                    db_player["deaths"] = screenshot_player["deaths"]

                    # Calculate level-based points for CT team
                    discord_id = int(db_player["discord_id"])
                    current_points = await self.get_player_current_points(discord_id)
                    is_challenger = await self.check_challenger_status(
                        discord_id, current_points
                    )
                    ct_won = ct_team_won
                    db_player[
                        "points_received"
                    ] = await self.calculate_level_based_points(
                        discord_id, ct_won, is_challenger
                    )

                    updated_ct_team.append(db_player)

            # Process T team players from screenshot
            for screenshot_player in t_data.get("players", []):
                player_name = screenshot_player["name"]
                # Find the corresponding database player
                db_player = None
                for p in all_db_players:
                    if (
                        p["nickname"] in matched_players
                        and matched_players[p["nickname"]]["name"] == player_name
                    ):
                        db_player = p.copy()  # Make a copy to avoid modifying original
                        break

                if db_player:
                    # Use screenshot stats and database player info
                    db_player["kills"] = screenshot_player["kills"]
                    db_player["assists"] = screenshot_player.get("assists", 0)
                    db_player["deaths"] = screenshot_player["deaths"]

                    # Calculate level-based points for T team
                    discord_id = int(db_player["discord_id"])
                    current_points = await self.get_player_current_points(discord_id)
                    is_challenger = await self.check_challenger_status(
                        discord_id, current_points
                    )
                    t_won = not ct_team_won
                    db_player[
                        "points_received"
                    ] = await self.calculate_level_based_points(
                        discord_id, t_won, is_challenger
                    )

                    updated_t_team.append(db_player)

            # Add missing players with default stats to maintain team balance
            # Find players who weren't matched to either team in screenshot
            matched_screenshot_players = {
                p["name"]
                for p in ct_data.get("players", []) + t_data.get("players", [])
            }
            unmatched_db_players = []

            for db_player in all_db_players:
                player_matched = False
                if db_player["nickname"] in matched_players:
                    matched_name = matched_players[db_player["nickname"]]["name"]
                    if matched_name in matched_screenshot_players:
                        player_matched = True

                if not player_matched:
                    # Add to the team that has fewer players to balance teams
                    unmatched_player = db_player.copy()
                    unmatched_player["kills"] = 0
                    unmatched_player["assists"] = 0
                    unmatched_player["deaths"] = 13
                    unmatched_player[
                        "points_received"
                    ] = -10  # Default penalty for missing players

                    if len(updated_ct_team) <= len(updated_t_team):
                        updated_ct_team.append(unmatched_player)
                    else:
                        updated_t_team.append(unmatched_player)

                    logger.info(
                        f"Added missing player '{db_player['nickname']}' with default stats"
                    )

            # Update database
            await Match.complete_submission(
                match_data["match_id"],
                updated_ct_team,
                updated_t_team,
                ct_score,
                t_score,
            )

            # Update user statistics
            await self.update_user_stats(updated_ct_team + updated_t_team)

            # Send final results image to target channel
            await self.send_final_results_image(
                interaction,
                target_channel_id,
                updated_ct_team,
                updated_t_team,
                ct_score,
                t_score,
                match_data,
            )

            # Delete the screenshot message to clean up
            if screenshot_message:
                try:
                    await screenshot_message.delete()
                except discord.NotFound:
                    pass  # Message already deleted
                except discord.Forbidden:
                    logger.warning(
                        "No permission to delete screenshot message"
                    )  # Update processing message or send new success embed
            success_embed = discord.Embed(
                title="✅ Scoreboard Successfully Processed",
                description="Screenshot analysis completed successfully!",
                color=0xB2FA19,
            )
            success_embed.add_field(
                name="📊 Match Results",
                value=f"**Matched:** {matched_count}/{total_db_players} players ({match_rate:.1%})\n"
                f"**CT Team:** {len(updated_ct_team)} players\n"
                f"**T Team:** {len(updated_t_team)} players",
                inline=True,
            )
            success_embed.add_field(
                name="⚙️ Processing Details",
                value="• Missing players assigned default stats (0K/0A/13D)\n"
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
            logger.error(f"Error validating and updating match: {e}")

            # Release the submission lock since an error occurred
            try:
                await Match.release_submission_lock(match_data["match_id"])
                logger.info(
                    f"Released submission lock for match {match_data['match_id']} due to error"
                )
            except Exception as lock_error:
                logger.error(f"Failed to release submission lock: {lock_error}")

            # Delete the screenshot since submission failed
            await self._delete_screenshot_safely(screenshot_message)

            await interaction.followup.send(
                "Error processing match results. Please try again.", ephemeral=True
            )

    def match_all_players(self, extracted_players, db_player_names):
        """Match all database players against all extracted players regardless of team"""
        matched = {}
        used_extracted = set()
        match_details = []

        for db_player in db_player_names:
            best_match = None
            best_ratio = 0
            all_ratios = []

            for extracted_player in extracted_players:
                if extracted_player["name"] in used_extracted:
                    continue

                # Use sequence matcher for fuzzy matching
                ratio = SequenceMatcher(
                    None, db_player.lower(), extracted_player["name"].lower()
                ).ratio()

                all_ratios.append((extracted_player["name"], ratio))

                if ratio > best_ratio and ratio > 0.4:  # 40% similarity threshold
                    best_match = extracted_player
                    best_ratio = ratio

            if best_match:
                matched[db_player] = best_match
                used_extracted.add(best_match["name"])
                match_details.append(
                    f"✅ '{db_player}' → '{best_match['name']}' ({best_ratio:.2f})"
                )
            else:
                # Find closest match for debugging
                if all_ratios:
                    closest = max(all_ratios, key=lambda x: x[1])
                    match_details.append(
                        f"❌ '{db_player}' → closest: '{closest[0]}' ({closest[1]:.2f})"
                    )
                else:
                    match_details.append(
                        f"❌ '{db_player}' → no extracted players found"
                    )

        # Log matching details for debugging
        logger.info(f"Player matching results:\n" + "\n".join(match_details))

        return matched, match_details

    async def update_user_stats(self, all_players):
        """Update user statistics in database and manage level roles"""
        for player in all_players:
            discord_id = int(player["discord_id"])
            kills = player["kills"]
            deaths = player["deaths"]
            points_change = player["points_received"]
            won = points_change > 0

            # Get the guild object from the interaction context
            guild = None
            if hasattr(self, "bot") and hasattr(self, "_guild_id"):
                guild = self.bot.get_guild(self._guild_id)

            await User.update_stats_with_roles(
                discord_id, kills, deaths, points_change, won, guild
            )

    async def send_final_results_image(
        self,
        interaction,
        target_channel_id,
        ct_team,
        t_team,
        ct_score,
        t_score,
        match_data,
    ):
        """Generate and send final results as an image to target channel"""
        try:
            import os
            import tempfile
            from jinja2 import Environment, FileSystemLoader
            from html2image import Html2Image
            from datetime import datetime

            target_channel = interaction.guild.get_channel(target_channel_id)
            if not target_channel:
                logger.error(f"Target channel {target_channel_id} not found")
                return

            # Prepare template variables
            ct_won = ct_score > t_score
            winner = "Counter-Terrorists" if ct_won else "Terrorists"
            current_date = datetime.now().strftime("%Y-%m-%d %H:%M")

            # Ensure all players have assists field (for backward compatibility)
            for player in ct_team + t_team:
                if "assists" not in player:
                    player["assists"] = 0

            template_vars = {
                "match_id": match_data["match_id"],
                "map_name": match_data.get("map", "Unknown"),
                "ct_score": ct_score,
                "t_score": t_score,
                "ct_team": ct_team,
                "t_team": t_team,
                "ct_won": ct_won,
                "winner": winner,
                "current_date": current_date,
            }

            # Render HTML with Jinja2
            template_dir = os.path.dirname(
                os.path.dirname(__file__)
            )  # Go up from cogs/ to project root
            env = Environment(loader=FileSystemLoader(template_dir))
            template = env.get_template("scoreboard_results_template.html")
            html_content = template.render(**template_vars)

            # Render HTML to image
            hti = Html2Image(output_path=tempfile.gettempdir())
            image_path = os.path.join(
                tempfile.gettempdir(),
                f"scoreboard_results_{match_data['match_id']}.png",
            )
            hti.screenshot(
                html_str=html_content,
                save_as=os.path.basename(image_path),
                size=(840, 1000),  # Adjusted size for scoreboard results
            )

            # Upload image to Discord
            file = discord.File(image_path, filename="scoreboard_results.png")
            embed = discord.Embed(
                title=f"🏆 Match {match_data['match_id']} Results", color=0x00FF00
            )
            embed.set_image(url="attachment://scoreboard_results.png")
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
            winner = "Counter-Terrorists" if ct_score > t_score else "Terrorists"
            fallback_msg = f"🏆 **Match {match_data['match_id']} Results**\n"
            fallback_msg += f"**Map:** {match_data.get('map', 'Unknown')}\n"
            fallback_msg += f"**Score:** {ct_score} - {t_score}\n"
            fallback_msg += f"**Winner:** {winner}\n\n"
            fallback_msg += "*(Image generation failed - showing text summary)*"
            await target_channel.send(fallback_msg)

    async def get_player_current_points(self, discord_id):
        """Get current points for a player from database"""
        try:
            user_data = await User.find(discord_id)
            return user_data.get("points", 0) if user_data else 0
        except Exception as e:
            logger.error(f"Error getting points for {discord_id}: {e}")
            return 0

    async def calculate_level_based_points(
        self, discord_id, is_win, is_challenger=False
    ):
        """Calculate points based on player's current level and win/loss"""
        try:
            # Import here to avoid circular imports
            from cogs.levels import LevelsCog

            current_points = await self.get_player_current_points(discord_id)

            if is_challenger:
                return LevelsCog.get_challenger_point_delta(is_win)
            else:
                return LevelsCog.get_point_delta(current_points, is_win)
        except Exception as e:
            logger.error(f"Error calculating level-based points for {discord_id}: {e}")
            # Fallback to old system
            return 20 if is_win else -10

    async def check_challenger_status(self, discord_id, current_points):
        """Check if player is currently in Challenger tier"""
        try:
            from cogs.levels import LevelsCog

            levels_cog = LevelsCog(None)
            return await levels_cog.is_challenger_eligible(discord_id, current_points)
        except Exception as e:
            logger.error(f"Error checking challenger status for {discord_id}: {e}")
            return False

    def validate_team_structure(self, ct_team, t_team, extracted_data, matched_players):
        """
        Validate that players who were on the same team in the database
        are still on the same team in the screenshot (regardless of which side)
        """
        # Get screenshot team assignments
        teams_data = extracted_data.get("teams", {})
        ct_data = teams_data.get("Counter-Terrorists", {})
        t_data = teams_data.get("Terrorists", {})

        # Create mappings of player names to their screenshot teams
        screenshot_team_assignments = {}

        # Map CT side players in screenshot
        for player in ct_data.get("players", []):
            screenshot_team_assignments[player["name"]] = "ct_side"

        # Map T side players in screenshot
        for player in t_data.get("players", []):
            screenshot_team_assignments[player["name"]] = "t_side"

        # Validate that database teammates are still together
        def check_team_grouping(db_team, team_name):
            """Check if all players from a database team are on the same side in screenshot"""
            screenshot_sides = set()
            matched_players_in_team = []

            for db_player in db_team:
                if db_player["nickname"] in matched_players:
                    screenshot_name = matched_players[db_player["nickname"]]["name"]
                    if screenshot_name in screenshot_team_assignments:
                        screenshot_side = screenshot_team_assignments[screenshot_name]
                        screenshot_sides.add(screenshot_side)
                        matched_players_in_team.append(
                            {
                                "db_name": db_player["nickname"],
                                "screenshot_name": screenshot_name,
                                "screenshot_side": screenshot_side,
                            }
                        )

            # If we have matched players, they should all be on the same side
            if len(matched_players_in_team) > 1 and len(screenshot_sides) > 1:
                return False, matched_players_in_team

            return True, matched_players_in_team

        # Check both teams
        ct_valid, ct_assignments = check_team_grouping(ct_team, "CT")
        t_valid, t_assignments = check_team_grouping(t_team, "T")

        validation_details = {
            "ct_team_valid": ct_valid,
            "t_team_valid": t_valid,
            "ct_assignments": ct_assignments,
            "t_assignments": t_assignments,
            "overall_valid": ct_valid and t_valid,
        }

        return validation_details

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
        from models.season import Season

        if not await Season.is_season_active():
            # Get the next season number
            last_season_id = await Season.get_last_season_id()
            next_season_number = last_season_id + 1

            embed = discord.Embed(
                title="❌ Season Not Active",
                description="Submitting results is currently disabled because there is no active season.",
                color=0xFF4444,
            )
            embed.add_field(
                name="💡 Information",
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

    async def cog_load(self):
        """Called when the cog is loaded - recreate persistent views"""
        await self.recreate_persistent_views()

    async def recreate_persistent_views(self):
        """Recreate persistent views from database configurations"""
        try:
            configs = await ScoreboardConfig.get_all_configs()

            for config in configs:
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
                    title="📊 Scoreboard Submission",
                    description=f"Scoreboard results will be posted to {results_channel_mention}",
                    color=0xB2FA19,
                )
                embed.add_field(
                    name="How to submit:",
                    value="1. Click the button below\n2. Enter your match ID\n3. Upload the scoreboard screenshot\n4. Wait for processing",
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

        except Exception as e:
            logger.error(f"Error recreating persistent scoreboard views: {e}")

    @app_commands.command(
        name="setscoreboardchannel",
        description="Set a target channel for scoreboard results",
    )
    @app_commands.describe(
        target_channel="The channel where scoreboard results will be posted"
    )
    async def set_scoreboard_channel(
        self, interaction: discord.Interaction, target_channel: discord.TextChannel
    ):
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
                title="📊 Scoreboard Submission",
                description=f"Scoreboard results will be posted to {target_channel.mention}",
                color=0xB2FA19,
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
                title="✅ Scoreboard Channel Configured",
                description=f"Scoreboard submission set up successfully!\n\n"
                f"**Submission Channel:** {submission_channel.mention}\n"
                f"**Results Channel:** {target_channel.mention}\n\n"
                f"Users can now submit match results using the button above.\n"
                f"This configuration will persist across bot restarts.",
                color=0x00FF00,
            )
            await interaction.response.send_message(embed=success_embed, ephemeral=True)

        except Exception as e:
            logger.error(f"Error setting scoreboard channel: {e}")
            embed = discord.Embed(
                title="❌ Setup Error",
                description="An error occurred while setting up the scoreboard channel. Please try again.",
                color=0xFF0000,
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Scoreboard(bot))
