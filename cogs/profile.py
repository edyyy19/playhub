import discord
from discord.ext import commands
from discord import app_commands
from config import Config
from models.user import User
from models.match import Match
import logging
import datetime
import os
import tempfile
from jinja2 import Environment, FileSystemLoader
from html2image import Html2Image

logger = logging.getLogger(__name__)

# Constants
EMBED_COLOR = 0xB2FA19  # Vibrant green color


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
                    title="❌ Invalid Player ID",
                    description="Standoff 2 Player ID must contain only numbers.",
                    color=discord.Color.red(),
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
                        title="❌ Both Nickname and Player ID Already Taken",
                        description=f"Both the nickname '{self.nickname.value}' and Player ID '{self.so2_id.value}' are already being used by other players.",
                        color=discord.Color.red(),
                    )
                elif "nickname" in duplicate_issues:
                    # Only nickname is taken
                    embed = discord.Embed(
                        title="❌ Nickname Already Taken",
                        description=f"The nickname '{self.nickname.value}' is already being used by another player.",
                        color=discord.Color.red(),
                    )
                else:
                    # Only Player ID is taken
                    embed = discord.Embed(
                        title="❌ Player ID Already Taken",
                        description=f"The Player ID '{self.so2_id.value}' is already being used by another player.",
                        color=discord.Color.red(),
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
                title="✅ Profile Updated Successfully", color=EMBED_COLOR
            )
            embed.add_field(name="Nickname", value=self.nickname.value, inline=True)
            embed.add_field(name="Player ID", value=self.so2_id.value, inline=True)

            # Build status message based on what was changed
            if not changes:
                status_message = "No changes were made"
            else:
                status_message = f"Updated: {', '.join(changes)}"

                if nickname_changed:
                    if discord_nickname_updated:
                        status_message += "\n✅ Discord nickname updated successfully"
                    else:
                        status_message += "\n⚠️ Discord nickname couldn't be changed (missing permissions)"

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
                title="❌ Profile Update Failed",
                description="An error occurred while updating your profile. Please contact an admin.",
                color=discord.Color.red(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)


class Profile(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

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
                    title="❌ Not Registered",
                    description="You need to register first before editing your profile!",
                    color=discord.Color.red(),
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
                title="❌ Error",
                description="An error occurred while loading your profile. Please contact an admin.",
                color=discord.Color.red(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="profile", description="View your profile information")
    async def view_profile(
        self, interaction: discord.Interaction, user: discord.Member = None
    ):
        """View profile information for yourself or another user"""
        try:
            import os
            import tempfile
            import datetime
            from jinja2 import Environment, FileSystemLoader
            from html2image import Html2Image

            # Defer the response since image generation takes time
            await interaction.response.defer(ephemeral=True)

            target_user = user or interaction.user
            user_data = await User.find(target_user.id)

            if not user_data:
                if target_user == interaction.user:
                    embed = discord.Embed(
                        title="❌ Not Registered",
                        description="You haven't registered yet!",
                        color=discord.Color.red(),
                    )
                    embed.add_field(
                        name="How to Register",
                        value="Look for the registration channel and click the Register button.",
                        inline=False,
                    )
                else:
                    embed = discord.Embed(
                        title="❌ User Not Found",
                        description=f"{target_user.mention} is not registered.",
                        color=discord.Color.red(),
                    )
                return await interaction.followup.send(embed=embed)

            # Calculate stats
            kills = user_data.get("kills", 0)
            deaths = user_data.get("deaths", 0)
            wins = user_data.get("wins", 0)
            losses = user_data.get("loses", 0)
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
                        match_date = dt.strftime("%d/%m/%Y")
                    except:
                        match_date = "Unknown"

                formatted_match = {
                    "match_id": match.get("match_id", "Unknown"),
                    "map": match.get("map", "Unknown"),
                    "date": match_date,
                    "final_score": f"{match.get('ct_score', 0)}-{match.get('t_score', 0)}",
                    "user_team": match.get("user_team", "Unknown"),
                    "result": "W" if match.get("user_won", False) else "L",
                }
                formatted_matches.append(formatted_match)

            # Prepare template variables
            current_date = datetime.datetime.now().strftime("%H:%M | %d/%m/%Y")
            registered_date = (
                user_data.get("registered_at", "Unknown")[:10]
                if user_data.get("registered_at")
                else "Unknown"
            )

            template_vars = {
                "app_name": Config.APP_NAME,
                "nickname": user_data["nickname"],
                "player_id": user_data["so2_id"],
                "avatar_url": str(target_user.display_avatar.url),
                "points": user_data.get("points", 200),
                "wins": wins,
                "losses": losses,
                "total_games": total_games,
                "kills": kills,
                "deaths": deaths,
                "kd_ratio": kd_ratio,
                "win_rate": win_rate,
                "registered_date": registered_date,
                "discord_id": target_user.id,
                "current_date": current_date,
                "recent_matches": formatted_matches,  # Add match history
            }

            # Render HTML with Jinja2
            template_dir = os.path.dirname(
                os.path.dirname(__file__)
            )  # Go up from cogs/ to project root
            env = Environment(loader=FileSystemLoader(template_dir))
            template = env.get_template("profile_template.html")
            html_content = template.render(**template_vars)

            # Render HTML to image
            hti = Html2Image(output_path=tempfile.gettempdir())
            image_path = os.path.join(
                tempfile.gettempdir(), f"profile_{target_user.id}.png"
            )
            hti.screenshot(
                html_str=html_content,
                save_as=os.path.basename(image_path),
                size=(740, 900),
            )

            # Upload image to Discord
            file = discord.File(image_path, filename="profile.png")
            embed = discord.Embed(
                title=f"🎮 {user_data['nickname']}'s Profile", color=EMBED_COLOR
            )
            embed.set_image(url="attachment://profile.png")
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
                title="❌ Error",
                description="An error occurred while loading the profile. Please contact an admin.",
                color=discord.Color.red(),
            )
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Profile(bot))
