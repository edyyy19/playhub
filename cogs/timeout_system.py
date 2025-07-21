import discord
from discord.ext import commands
from discord import app_commands
from typing import Optional
import logging
from datetime import datetime
from models.timeout import timeout_manager
from config import Config

logger = logging.getLogger(__name__)


class TimeoutModal(discord.ui.Modal, title="Timeout Player"):
    def __init__(self, target_user: discord.Member):
        super().__init__()
        self.target_user = target_user

    time_hours = discord.ui.TextInput(
        label="Time in Hours",
        placeholder="Enter timeout duration in hours (e.g., 2)",
        required=True,
        max_length=10,
    )

    reason = discord.ui.TextInput(
        label="Reason",
        placeholder="Enter the reason for timeout",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=500,
    )

    async def on_submit(self, interaction: discord.Interaction):
        try:
            # Parse hours as integer to only allow whole numbers
            hours = int(self.time_hours.value)

            if hours <= 0:
                embed = discord.Embed(
                    title=f"{Config.ICONS['unsuccess']} Invalid Input",
                    description="Hours must be a positive number.",
                    color=Config.EMBED_COLORS["red"],
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return

            # Add timeout using the new hour-based method
            success = await timeout_manager.add_timeout_hours(
                user_id=self.target_user.id,
                timeout_hours=hours,
                reason=self.reason.value,
            )

            if success:  # Create embed for confirmation
                # Format hours display
                hours_text = "hour" if hours == 1 else "hours"

                embed = discord.Embed(
                    title=f"{Config.ICONS['success']} Player Successfully Timeouted",
                    description=f"**Player:** {self.target_user.display_name}\n"
                    f"**Duration:** {hours} {hours_text}\n"
                    f"**Reason:** {self.reason.value}",
                    color=Config.EMBED_COLORS["light_green"],
                )

                await interaction.response.send_message(
                    embed=embed, ephemeral=True
                )  # Check if there's a configured timeout channel
                timeout_channel_id = await timeout_manager.get_timeout_channel()
                if timeout_channel_id:
                    try:
                        channel = interaction.guild.get_channel(int(timeout_channel_id))
                        if channel:  # Create public embed for timeout channel
                            public_embed = discord.Embed(
                                title=f"{Config.ICONS['clock']} Player Timeout",
                                description=f"**Player:** {self.target_user.display_name}\n"
                                f"**Duration:** {hours} {hours_text}\n"
                                f"**Reason:** {self.reason.value}\n"
                                f"**By:** {interaction.user.mention}",
                                color=Config.EMBED_COLORS["red"],
                            )
                            public_embed.set_thumbnail(
                                url=self.target_user.display_avatar.url
                            )

                            await channel.send(embed=public_embed)
                    except Exception as e:
                        logger.error(
                            f"Failed to send timeout notification to channel: {e}"
                        )
            else:
                embed = discord.Embed(
                    title=f"{Config.ICONS['unsuccess']} Timeout Failed",
                    description="Failed to add timeout. Please try again.",
                    color=Config.EMBED_COLORS["red"],
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)

        except ValueError:
            embed = discord.Embed(
                title=f"{Config.ICONS['unsuccess']} Invalid Format",
                description="Invalid hours format. Please enter a whole number (e.g., 2).",
                color=Config.EMBED_COLORS["red"],
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

        except Exception as e:
            logger.error(f"Error in timeout modal submission: {e}")
            embed = discord.Embed(
                title=f"{Config.ICONS['unsuccess']} Error",
                description="An error occurred while processing the timeout.",
                color=Config.EMBED_COLORS["red"],
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)


class TimeoutSystem(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="timeout", description="Timeout a player for a specified duration"
    )
    @app_commands.describe(player="The player to timeout")
    @app_commands.default_permissions(administrator=True)
    async def timeout_player(
        self, interaction: discord.Interaction, player: discord.Member
    ):
        """Timeout a player using a modal form"""

        # Check if user has admin permissions
        if not interaction.user.guild_permissions.administrator:
            embed = discord.Embed(
                title=f"{Config.ICONS['unsuccess']} Permission Denied",
                description="You need administrator permissions to use this command.",
                color=Config.EMBED_COLORS["red"],
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # Check if trying to timeout themselves
        if player.id == interaction.user.id:
            embed = discord.Embed(
                title=f"{Config.ICONS['unsuccess']} Invalid Target",
                description="You cannot timeout yourself.",
                color=Config.EMBED_COLORS["red"],
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # Check if trying to timeout a bot
        if player.bot:
            embed = discord.Embed(
                title=f"{Config.ICONS['unsuccess']} Invalid Target",
                description="You cannot timeout bots.",
                color=Config.EMBED_COLORS["red"],
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # Show the timeout modal
        modal = TimeoutModal(player)
        await interaction.response.send_modal(modal)

    @app_commands.command(
        name="set_timeout_channel",
        description="Set the channel for timeout notifications",
    )
    @app_commands.describe(
        channel="The channel where timeout notifications will be sent"
    )
    @app_commands.default_permissions(administrator=True)
    async def set_timeout_channel(
        self, interaction: discord.Interaction, channel: discord.TextChannel
    ):
        """Set the timeout notification channel"""
        # Verifică autorizația utilizatorului
        if not await Config.check_authorization(interaction):
            return

        # Check if user has admin permissions
        if not interaction.user.guild_permissions.administrator:
            embed = discord.Embed(
                title=f"{Config.ICONS['unsuccess']} Permission Denied",
                description="You need administrator permissions to use this command.",
                color=Config.EMBED_COLORS["red"],
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        try:
            # Update the config table with the timeout channel
            result = Config.supabase().table("config").select("*").limit(1).execute()
            if result.data:
                # Update existing config
                update_result = (
                    Config.supabase()
                    .table("config")
                    .update({"timeout_channel": str(channel.id)})
                    .eq("id", result.data[0]["id"])
                    .execute()
                )
            else:
                # Insert new config
                insert_result = (
                    Config.supabase()
                    .table("config")
                    .insert({"timeout_channel": str(channel.id)})
                    .execute()
                )

            embed = discord.Embed(
                title=f"{Config.ICONS['success']} Timeout Channel Configured",
                description="Timeout system set up successfully!\n\n"
                f"**Notification Channel:** {channel.mention}\n\n"
                "All timeout notifications will be sent to the configured channel.\n"
                "This configuration will persist across bot restarts.",
                color=Config.EMBED_COLORS["light_green"],
            )

            await interaction.response.send_message(embed=embed, ephemeral=True)
            logger.info(f"Timeout channel set to {channel.id} by {interaction.user.id}")

        except Exception as e:
            logger.error(f"Failed to set timeout channel: {e}")
            embed = discord.Embed(
                title=f"{Config.ICONS['unsuccess']} Configuration Failed",
                description="Failed to set timeout channel. Please try again.",
                color=Config.EMBED_COLORS["red"],
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name="timeout_status",
        description="Check your current timeout status or view all timeouts (admin only).",
    )
    async def timeout_status(
        self, interaction: discord.Interaction, user: discord.Member = None
    ):
        """Check timeout status for a user or view all timeouts if admin"""
        # Check if user wants to see all timeouts (admin only)
        if user is None:
            # Check own timeout status
            target_user = interaction.user
        else:
            # Admin checking another user's status
            if not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message(
                    f"{Config.ICONS['unsuccess']} You don't have permission to check other users' timeout status.",
                    ephemeral=True,
                )
                return
            target_user = user

        await interaction.response.defer(ephemeral=True)

        try:  # Check if specific user or all users
            if user is None and interaction.user.guild_permissions.administrator:
                # Admin viewing all timeouts
                timeouts = await timeout_manager.get_all_timeouts()

                if not timeouts:
                    embed = discord.Embed(
                        title=f"{Config.ICONS['clock_lightgreen']} Timeout Status",
                        description="No users are currently timeouted.",
                        color=Config.EMBED_COLORS["light_green"],
                    )
                else:
                    embed = discord.Embed(
                        title=f"{Config.ICONS['clock_yellow']} Active Timeouts",
                        description=f"Found {len(timeouts)} active timeout(s):",
                        color=Config.EMBED_COLORS["yellow"],
                    )

                    for timeout in timeouts[:10]:  # Limit to 10 to avoid embed limits
                        try:
                            # Try to get member (with nickname) first, fallback to user
                            try:
                                member_obj = interaction.guild.get_member(
                                    timeout["user_id"]
                                )
                                if member_obj:
                                    user_name = member_obj.display_name
                                else:
                                    # Fallback to fetching user if not in guild
                                    user_obj = await self.bot.fetch_user(
                                        timeout["user_id"]
                                    )
                                    user_name = (
                                        user_obj.display_name
                                        if user_obj
                                        else f"User {timeout['user_id']}"
                                    )
                            except:
                                # Final fallback
                                user_name = f"User {timeout['user_id']}"
                        except:
                            user_name = f"User {timeout['user_id']}"

                        embed.add_field(
                            name=user_name,
                            value=f"**Reason:** {timeout['reason']}\n**Remaining:** {timeout['remaining_minutes']} min(s)",
                            inline=False,
                        )

                    if len(timeouts) > 10:
                        embed.set_footer(text=f"Showing 10 of {len(timeouts)} timeouts")
            else:
                # Check specific user's timeout
                is_timeouted, timeout_until = await timeout_manager.is_user_timeouted(
                    target_user.id
                )

                if is_timeouted:
                    remaining_minutes = timeout_manager.get_remaining_time(
                        target_user.id
                    )
                    embed = discord.Embed(
                        title=f"{Config.ICONS['clock']} Timeout Status",
                        description=f"{'You are' if target_user == interaction.user else f'{target_user.display_name} is'} currently timeouted.",
                        color=Config.EMBED_COLORS["red"],
                    )
                    embed.add_field(
                        name="Time Remaining",
                        value=f"{remaining_minutes} minute(s)",
                        inline=True,
                    )
                    embed.add_field(
                        name="Expires At",
                        value=f"<t:{int(timeout_until.timestamp())}:T>",
                        inline=True,
                    )
                else:
                    embed = discord.Embed(
                        title=f"{Config.ICONS['clock_lightgreen']} Timeout Status",
                        description=f"{'You are' if target_user == interaction.user else f'{target_user.display_name} is'} not currently timeouted.",
                        color=Config.EMBED_COLORS["light_green"],
                    )

            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as e:
            logger.error(f"Failed to check timeout status: {e}")
            embed = discord.Embed(
                title=f"{Config.ICONS['unsuccess']} Error",
                description="Failed to check timeout status. Please try again.",
                color=Config.EMBED_COLORS["red"],
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(
        name="clear_timeout", description="Admin command to clear a user's timeout."
    )
    @app_commands.describe(user="The user to clear timeout from")
    @app_commands.default_permissions(administrator=True)
    async def clear_timeout(
        self, interaction: discord.Interaction, user: discord.Member
    ):
        """Clear a specific user's timeout"""
        # Check if user has admin permissions
        if not interaction.user.guild_permissions.administrator:
            embed = discord.Embed(
                title=f"{Config.ICONS['unsuccess']} Permission Denied",
                description="You need administrator permissions to use this command.",
                color=Config.EMBED_COLORS["red"],
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        try:
            # Check if user is actually timeouted
            is_timeouted, timeout_until = await timeout_manager.is_user_timeouted(
                user.id
            )

            if not is_timeouted:
                embed = discord.Embed(
                    title=f"{Config.ICONS['clock_yellow']} Timeout Status",
                    description=f"{user.display_name} is not currently timeouted.",
                    color=Config.EMBED_COLORS["yellow"],
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
                return

            # Remove the timeout
            success = await timeout_manager.remove_timeout(user.id)

            if success:
                embed = discord.Embed(
                    title=f"{Config.ICONS['success']} Timeout Cleared",
                    description=f"Successfully cleared timeout for {user.display_name}.",
                    color=Config.EMBED_COLORS["light_green"],
                )

                # Try to notify the user
                try:
                    # Create timeout cleared notification embed
                    user_embed = discord.Embed(
                        title=f"{Config.ICONS['success']} Timeout Cleared",
                        description="Your timeout has been cleared by an administrator.",
                        color=Config.EMBED_COLORS["light_green"],
                    )
                    user_embed.add_field(
                        name="Status",
                        value=f"{Config.ICONS['success']} You can now join lobbies again",
                        inline=False,
                    )
                    user_embed.set_footer(
                        text=f"Welcome back to {Config.APP_NAME} matchmaking!"
                    )

                    await user.send(embed=user_embed)
                    embed.set_footer(text="User has been notified.")
                except:
                    embed.set_footer(text="User could not be notified (DMs disabled).")

                # Send notification to timeout channel if configured
                try:
                    timeout_channel_id = await timeout_manager.get_timeout_channel()
                    if timeout_channel_id:
                        channel = interaction.guild.get_channel(int(timeout_channel_id))
                        if channel:
                            # Create public embed for timeout channel
                            public_embed = discord.Embed(
                                title=f"{Config.ICONS['clock_lightgreen']} Player Timeout Cleared",
                                description=f"**Player:** {user.display_name}\n"
                                f"**Action:** Timeout cleared\n"
                                f"**By:** {interaction.user.mention}",
                                color=Config.EMBED_COLORS["light_green"],
                            )
                            public_embed.set_thumbnail(url=user.display_avatar.url)
                            await channel.send(embed=public_embed)
                except Exception as e:
                    logger.error(
                        f"Failed to send timeout clear notification to channel: {e}"
                    )

                logger.info(f"Timeout cleared for {user.id} by {interaction.user.id}")
            else:
                embed = discord.Embed(
                    title=f"{Config.ICONS['unsuccess']} Failed to Clear Timeout",
                    description=f"Failed to clear timeout for {user.display_name}. Please try again.",
                    color=Config.EMBED_COLORS["red"],
                )

            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as e:
            logger.error(f"Failed to clear timeout for user {user.id}: {e}")
            embed = discord.Embed(
                title=f"{Config.ICONS['unsuccess']} Error",
                description="Failed to clear timeout. Please try again.",
                color=Config.EMBED_COLORS["red"],
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(
        name="clear_all_timeouts",
        description="Admin command to clear all active timeouts.",
    )
    @app_commands.default_permissions(administrator=True)
    async def clear_all_timeouts(self, interaction: discord.Interaction):
        """Clear all active timeouts"""
        # Check if user has admin permissions
        if not interaction.user.guild_permissions.administrator:
            embed = discord.Embed(
                title=f"{Config.ICONS['unsuccess']} Permission Denied",
                description="You need administrator permissions to use this command.",
                color=Config.EMBED_COLORS["red"],
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        try:
            # Get current timeouts count first
            timeouts = await timeout_manager.get_all_timeouts()
            timeout_count = len(timeouts)

            if timeout_count == 0:
                embed = discord.Embed(
                    title=f"{Config.ICONS['clock_yellow']} No Timeouts",
                    description="There are no active timeouts to clear.",
                    color=Config.EMBED_COLORS["yellow"],
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
                return

            # Clear all timeouts
            success = await timeout_manager.clear_all_timeouts()

            if success:
                embed = discord.Embed(
                    title=f"{Config.ICONS['success']} All Timeouts Cleared",
                    description=f"Successfully cleared {timeout_count} timeout(s).",
                    color=Config.EMBED_COLORS["light_green"],
                )
                embed.set_footer(text="All users can now join lobbies again.")

                # Send notification to timeout channel if configured
                try:
                    timeout_channel_id = await timeout_manager.get_timeout_channel()
                    if timeout_channel_id:
                        channel = interaction.guild.get_channel(int(timeout_channel_id))
                        if channel:
                            # Create public embed for timeout channel
                            public_embed = discord.Embed(
                                title=f"{Config.ICONS['clock_lightgreen']} All Timeouts Cleared",
                                description=f"**Action:** All timeouts cleared '{timeout_count} timeout(s)'\n"
                                f"**By:** {interaction.user.mention}\n"
                                f"**Status:** All users can now join lobbies again",
                                color=Config.EMBED_COLORS["light_green"],
                            )
                            await channel.send(embed=public_embed)
                except Exception as e:
                    logger.error(
                        f"Failed to send clear all timeouts notification to channel: {e}"
                    )

                logger.info(
                    f"All timeouts cleared ({timeout_count}) by {interaction.user.id}"
                )
            else:
                embed = discord.Embed(
                    title=f"{Config.ICONS['unsuccess']} Failed to Clear Timeouts",
                    description="Failed to clear all timeouts. Please try again.",
                    color=Config.EMBED_COLORS["red"],
                )

            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as e:
            logger.error(f"Failed to clear all timeouts: {e}")
            embed = discord.Embed(
                title=f"{Config.ICONS['unsuccess']} Error",
                description="Failed to clear all timeouts. Please try again.",
                color=Config.EMBED_COLORS["red"],
            )
            await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(TimeoutSystem(bot))
