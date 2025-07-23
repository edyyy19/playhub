import discord
from discord.ext import commands
from discord import app_commands
from config import Config
from models.user import User
from models.stats import Stats
from models.match import Match
from assets.template_generators.profile_generator import ProfileTemplateGenerator
import logging
from datetime import datetime
import os

logger = logging.getLogger(__name__)

# Constants
EMBED_COLOR = Config.MAIN_COLOR  # Main brand color


class EditProfileModal(discord.ui.Modal, title="Edit Profile"):
    def __init__(self, current_nickname: str, current_so2_id: str):
        super().__init__()

        self.nickname = discord.ui.TextInput(
            label="In-Game Nickname",
            placeholder="Enter your in game nickname",
            max_length=32,
            default=current_nickname,
        )

        self.so2_id = discord.ui.TextInput(
            label="Standoff 2 Player ID",
            placeholder="Enter your Player ID (numbers only)",
            default=current_so2_id,
        )  # Add the text inputs to the modal
        self.add_item(self.nickname)
        self.add_item(self.so2_id)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            # Validate that SO2 ID contains only numbers
            if not self.so2_id.value.isdigit():
                embed = discord.Embed(
                    title=f"{Config.ICONS['unsuccess']} Invalid Player ID",
                    description="Standoff 2 Player ID must contain only numbers.",
                    color=Config.EMBED_COLORS["red"],
                )
                return await interaction.response.send_message(
                    embed=embed, ephemeral=True
                )

            # Get current user data to compare changes
            current_user_data = await User.find(interaction.user.id)
            # Track what was changed
            changes = []
            nickname_changed = self.nickname.value != current_user_data["nickname"]
            so2_id_changed = self.so2_id.value != current_user_data["so2_id"]

            # Check for duplicates if fields were changed
            duplicate_issues = []
            existing_nickname_user = None
            existing_so2_user = None

            if nickname_changed:
                existing_nickname_user = await User.find_by_nickname(
                    self.nickname.value, exclude_discord_id=interaction.user.id
                )
                if existing_nickname_user:
                    duplicate_issues.append("nickname")

            if so2_id_changed:
                existing_so2_user = await User.find_by_so2_id(
                    self.so2_id.value, exclude_discord_id=interaction.user.id
                )
                if existing_so2_user:
                    duplicate_issues.append("Player ID")

            # Handle duplicate errors
            if duplicate_issues:
                if len(duplicate_issues) == 2:
                    # Both nickname and Player ID are taken
                    embed = discord.Embed(
                        title=f"{Config.ICONS['unsuccess']} Both Nickname and Player ID Already Taken",
                        description=f"Both the nickname '{self.nickname.value}' and Player ID '{self.so2_id.value}' are already being used by other players.",
                        color=Config.EMBED_COLORS["red"],
                    )
                elif "nickname" in duplicate_issues:
                    # Only nickname is taken
                    embed = discord.Embed(
                        title=f"{Config.ICONS['unsuccess']} Nickname Already Taken",
                        description=f"The nickname '{self.nickname.value}' is already being used by another player.",
                        color=Config.EMBED_COLORS["red"],
                    )
                else:
                    # Only Player ID is taken
                    embed = discord.Embed(
                        title=f"{Config.ICONS['unsuccess']} Player ID Already Taken",
                        description=f"The Player ID '{self.so2_id.value}' is already being used by another player.",
                        color=Config.EMBED_COLORS["red"],
                    )

                return await interaction.response.send_message(
                    embed=embed, ephemeral=True
                )

            # Track successful changes
            if nickname_changed:
                changes.append("Nickname")
            if so2_id_changed:
                changes.append("Player ID")

            # Update user profile in database
            await User.update_profile(
                interaction.user.id, self.nickname.value, self.so2_id.value
            )

            # Update Discord nickname only if nickname was changed
            discord_nickname_updated = False
            if nickname_changed:
                try:
                    await interaction.user.edit(nick=self.nickname.value)
                    discord_nickname_updated = True
                except discord.Forbidden:
                    pass
                except discord.HTTPException:
                    pass

            # Create success embed
            embed = discord.Embed(
                title=f"{Config.ICONS['success']} Profile Updated Successfully",
                color=Config.EMBED_COLORS["light_green"],
            )
            embed.add_field(name="Nickname", value=self.nickname.value, inline=True)
            embed.add_field(name="Player ID", value=self.so2_id.value, inline=True)

            # Build status message based on what was changed
            if not changes:
                status_message = "No changes were made"
            else:
                status_message = (
                    f"{Config.ICONS['success']} Updated: {', '.join(changes)}"
                )

                if nickname_changed:
                    if discord_nickname_updated:
                        status_message += f"\n{Config.ICONS['success']} Discord nickname updated successfully"
                    else:
                        status_message += f"\n{Config.ICONS['info']} Discord nickname couldn't be changed (missing permissions)"

            embed.add_field(
                name="Status",
                value=status_message,
                inline=False,
            )

            embed.set_thumbnail(url=interaction.user.display_avatar.url)
            embed.set_footer(text="Profile changes saved!")

            await interaction.response.send_message(embed=embed, ephemeral=True)

        except Exception as e:
            logger.error(f"Profile update error: {e}")
            embed = discord.Embed(
                title=f"{Config.ICONS['unsuccess']} Profile Update Failed",
                description="An error occurred while updating your profile. Please contact an admin.",
                color=Config.EMBED_COLORS["red"],
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)


class Profile(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.profile_generator = ProfileTemplateGenerator()

    def get_leagues_cog(self):
        """Get the leagues cog for accessing league methods"""
        return self.bot.get_cog("Leagues")

    async def league_name_autocomplete(
        self, interaction: discord.Interaction, current: str
    ):
        """
        Autocomplete function for league names - delegates to leagues cog
        """
        leagues_cog = self.get_leagues_cog()
        if leagues_cog:
            return await leagues_cog.league_name_autocomplete(interaction, current)
        return []

    async def get_default_league_from_config(self):
        """Get the default league from the config table"""
        import asyncio

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

    @app_commands.command(
        name="edit_profile", description="Edit your nickname and Standoff 2 Player ID"
    )
    async def edit_profile(self, interaction: discord.Interaction):
        """Allow users to edit their profile information"""
        try:
            # Check if user is registered
            user_data = await User.find(interaction.user.id)

            if not user_data:
                embed = discord.Embed(
                    title=f"{Config.ICONS['unsuccess']} Not Registered",
                    description="You need to register first before editing your profile!",
                    color=Config.EMBED_COLORS["red"],
                )
                embed.add_field(
                    name="How to Register",
                    value="Look for the registration channel and click the Register button.",
                    inline=False,
                )
                return await interaction.response.send_message(
                    embed=embed, ephemeral=True
                )

            # Show modal with current data pre-filled
            modal = EditProfileModal(
                current_nickname=user_data["nickname"],
                current_so2_id=user_data["so2_id"],
            )
            await interaction.response.send_modal(modal)

        except Exception as e:
            logger.error(f"Error in edit_profile command: {e}")
            embed = discord.Embed(
                title=f"{Config.ICONS['unsuccess']} Error",
                description="An error occurred while loading your profile. Please contact an admin.",
                color=Config.EMBED_COLORS["red"],
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="profile", description="View your profile information")
    @app_commands.describe(
        user="The user whose profile to view (optional - defaults to yourself)",
        league_name="The league to view stats for (optional - defaults to default league)",
    )
    @app_commands.autocomplete(league_name=league_name_autocomplete)
    async def view_profile(
        self,
        interaction: discord.Interaction,
        user: discord.Member = None,
        league_name: str = None,
    ):
        """View profile information for yourself or another user"""
        try:
            # Defer the response since image generation takes time
            await interaction.response.defer(ephemeral=True)

            target_user = user or interaction.user
            user_data = await User.find(target_user.id)

            if not user_data:
                if target_user == interaction.user:
                    embed = discord.Embed(
                        title=f"{Config.ICONS['unsuccess']} Not Registered",
                        description="You haven't registered yet!",
                        color=Config.EMBED_COLORS["red"],
                    )
                    embed.add_field(
                        name="How to Register",
                        value="Look for the registration channel and click the Register button.",
                        inline=False,
                    )
                else:
                    embed = discord.Embed(
                        title=f"{Config.ICONS['unsuccess']} User Not Found",
                        description=f"{target_user.mention} is not registered.",
                        color=Config.EMBED_COLORS["red"],
                    )
                return await interaction.followup.send(embed=embed)

            # Determine which league to get stats from
            if league_name:
                # Validate that the specified league exists
                leagues_cog = self.get_leagues_cog()
                if leagues_cog:
                    league_exists = await leagues_cog.league_exists(league_name)
                    if not league_exists:
                        embed = discord.Embed(
                            title=f"{Config.ICONS['unsuccess']} League Not Found",
                            description=f"The league '{league_name}' does not exist.",
                            color=Config.EMBED_COLORS["red"],
                        )
                        return await interaction.followup.send(embed=embed)
                target_league = league_name
            else:
                # Use default league if no league specified
                target_league = await self.get_default_league_from_config()
                if not target_league:
                    embed = discord.Embed(
                        title=f"{Config.ICONS['unsuccess']} Configuration Error",
                        description="No default league configured. Please contact an admin.",
                        color=Config.EMBED_COLORS["red"],
                    )
                    return await interaction.followup.send(embed=embed)
            # Get stats from the specified league using the Stats model
            stats_result = await Stats.get_user_stats(target_user.id, target_league)
            if stats_result and stats_result.get("success"):
                stats_data = stats_result.get("stats", {})
                kills = stats_data.get("kills", 0)
                deaths = stats_data.get("deaths", 0)
                wins = stats_data.get("wins", 0)
                losses = stats_data.get("loses", 0)
                points = stats_data.get("points", 200)
            else:
                # User not found in the specified league table
                leagues_cog = self.get_leagues_cog()
                league_display_name = target_league
                if leagues_cog:
                    league_data = await leagues_cog.get_league_by_name(target_league)
                    if league_data:
                        league_display_name = league_data.get(
                            "original_league_name", target_league
                        )
                # Create informative embed about user not being in the league
                if target_user == interaction.user:
                    description = (
                        f"You are not part of the '**{league_display_name}**' league."
                    )
                    if not league_name:  # If this is the default league
                        description += "\n\nThis means you're registered but haven't been added to the default league yet."
                else:
                    description = f"{target_user.mention} is not part of the '**{league_display_name}**' league."

                embed = discord.Embed(
                    title=f"{Config.ICONS['info']} Not Part of League",
                    description=description,
                    color=Config.EMBED_COLORS["yellow"],
                )
                return await interaction.followup.send(embed=embed)

            total_games = wins + losses

            # Calculate K/D ratio
            if deaths == 0:
                kd_ratio = f"{kills:.2f}" if kills > 0 else "0.00"
            else:
                kd_ratio = f"{kills / deaths:.2f}"

            # Calculate win rate
            if total_games > 0:
                win_rate = f"{(wins / total_games) * 100:.1f}%"
            else:
                win_rate = "N/A"

            # Get last 5 matches for the user
            recent_matches = await Match.get_user_matches(str(target_user.id), limit=5)

            # Format matches for template
            formatted_matches = []
            for match in recent_matches:
                # Format the date
                match_date = "Unknown"
                if match.get("ended_at"):
                    try:
                        from datetime import datetime

                        dt = datetime.fromisoformat(
                            match["ended_at"].replace("Z", "+00:00")
                        )
                        match_date = dt.strftime("%H:%M | %d/%m/%Y")
                    except:
                        match_date = "Unknown"

                # Extract scores from processed match data
                team1_score = match.get("team1_score", 0)
                team2_score = match.get("team2_score", 0)

                formatted_match = {
                    "match_id": match.get("match_id", "Unknown"),
                    "map": match.get("map", "Unknown"),
                    "date": match_date,
                    "final_score": f"{team1_score}-{team2_score}",
                    "user_team": match.get("user_team", "Unknown"),
                    "result": "W" if match.get("user_won", False) else "L",
                    "ended_at": match.get("ended_at", ""),  # Add for sorting
                }
                formatted_matches.append(formatted_match)
            
            # Sort matches by ended_at in descending order to ensure newest first
            formatted_matches.sort(key=lambda x: x.get("ended_at", ""), reverse=True)
            
            # Prepare template variables
            current_date = datetime.now().strftime("%H:%M | %d/%m/%Y")
            registered_date = (
                user_data.get("registered_at", "Unknown")[:10]
                if user_data.get("registered_at")
                else "Unknown"
            )
            # Get league display name
            leagues_cog = self.get_leagues_cog()
            league_display_name = target_league
            if leagues_cog:
                league_data = await leagues_cog.get_league_by_name(target_league)
                if league_data:
                    league_display_name = league_data.get(
                        "original_league_name", target_league
                    )

            # Generate profile image using the dedicated template generator
            image_path = await self.profile_generator.generate_profile_image(
                user_data=user_data,
                target_user=target_user,
                points=points,
                wins=wins,
                losses=losses,
                total_games=total_games,
                kills=kills,
                deaths=deaths,
                kd_ratio=kd_ratio,
                win_rate=win_rate
            )

            # Upload image to Discord
            file = discord.File(image_path, filename="profile.png")
            embed = discord.Embed(
                title=f"{Config.APP_NAME} Matchmaking | {user_data['nickname']}'s Profile",
                color=EMBED_COLOR,
            )
            embed.set_image(url="attachment://profile.png")
            
            # Add recent matches as embed fields
            if formatted_matches:
                matches_text = ""
                for match in formatted_matches:
                    match_line = (
                        f"#{match['match_id']} • {match['result']} ┃ "
                        f"{match['date']} ┃ {match['map']} • {match['final_score']}\n"
                    )
                    matches_text += match_line
                
                embed.add_field(
                    name="Recent Matches",
                    value=matches_text.strip(),
                    inline=False
                )
            else:
                embed.add_field(
                    name="Recent Matches",
                    value="No recent matches found",
                    inline=False
                )
            
            embed.set_footer(text=f"Profile generated at {current_date}")

            await interaction.followup.send(embed=embed, file=file)

            # Clean up the temporary file
            try:
                os.remove(image_path)
            except Exception:
                pass

        except Exception as e:
            logger.error(f"Error in profile command: {e}")
            embed = discord.Embed(
                title=f"{Config.ICONS['unsuccess']} Error",
                description="An error occurred while loading the profile. Please contact an admin.",
                color=Config.EMBED_COLORS["red"],
            )
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Profile(bot))
