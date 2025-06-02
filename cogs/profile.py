import discord
from discord.ext import commands
from discord import app_commands
from models.user import User
import logging

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
                return await interaction.response.send_message(
                    embed=embed, ephemeral=True
                )

            # Calculate K/D ratio
            kills = user_data.get("kills", 0)
            deaths = user_data.get("deaths", 0)
            if deaths == 0:
                kd_ratio = kills if kills > 0 else 0.0
            else:
                kd_ratio = kills / deaths

            # Create profile embed
            embed = discord.Embed(
                title=f"🎮 {user_data['nickname']}'s Profile", color=EMBED_COLOR
            )

            # Basic info
            embed.add_field(name="Nickname", value=user_data["nickname"], inline=True)
            embed.add_field(name="Player ID", value=user_data["so2_id"], inline=True)
            embed.add_field(
                name="Points", value=user_data.get("points", 200), inline=True
            )

            # Stats
            embed.add_field(name="Wins", value=user_data.get("wins", 0), inline=True)
            embed.add_field(name="Losses", value=user_data.get("loses", 0), inline=True)
            embed.add_field(name="K/D Ratio", value=f"{kd_ratio:.2f}", inline=True)

            embed.add_field(name="Kills", value=kills, inline=True)
            embed.add_field(name="Deaths", value=deaths, inline=True)

            # Calculate win rate
            total_games = user_data.get("wins", 0) + user_data.get("loses", 0)
            if total_games > 0:
                win_rate = (user_data.get("wins", 0) / total_games) * 100
                embed.add_field(name="Win Rate", value=f"{win_rate:.1f}%", inline=True)
            else:
                embed.add_field(name="Win Rate", value="N/A", inline=True)

            embed.set_thumbnail(url=target_user.display_avatar.url)
            embed.set_footer(
                text=f"Registered: {user_data.get('registered_at', 'Unknown')[:10]}"
            )

            await interaction.response.send_message(embed=embed, ephemeral=True)

        except Exception as e:
            logger.error(f"Error in profile command: {e}")
            embed = discord.Embed(
                title="❌ Error",
                description="An error occurred while loading the profile. Please contact an admin.",
                color=discord.Color.red(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Profile(bot))
