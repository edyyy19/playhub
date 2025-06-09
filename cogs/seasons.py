import discord
from discord.ext import commands
from discord import app_commands
from config import Config
from models.season import Season
from models.match import Match
from models.user import User
import logging
import asyncio
from datetime import datetime
import re

logger = logging.getLogger(__name__)

EMBED_COLOR = 0xB2FA19  # Vibrant green color


class Seasons(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def _parse_timestamp(self, timestamp_str: str) -> int:
        """Parse timestamp string and return Unix timestamp for Discord formatting"""
        try:
            # Handle various timestamp formats from Supabase
            if timestamp_str.endswith("Z"):
                # Remove Z and treat as UTC
                timestamp_str = timestamp_str[:-1] + "+00:00"
            elif "+" not in timestamp_str and "T" in timestamp_str:
                # Add UTC timezone if none specified
                timestamp_str = timestamp_str + "+00:00"

            dt = datetime.fromisoformat(timestamp_str)
            return int(dt.timestamp())
        except Exception as e:
            logger.error(f"Error parsing timestamp {timestamp_str}: {e}")
            # Fallback to current time
            return int(datetime.now().timestamp())

    def has_founder_role(self, interaction: discord.Interaction) -> bool:
        """Check if user has the Founder role"""
        if not interaction.user.roles:
            return False
        return any(role.name == "Founder" for role in interaction.user.roles)

    @app_commands.command(
        name="start_season", description="Start a new season (Founder only)"
    )
    async def start_season(self, interaction: discord.Interaction):
        """Start a new season - Founder role only"""
        # Check if user has Founder role
        if not self.has_founder_role(interaction):
            embed = discord.Embed(
                title="❌ Permission Denied",
                description="This command can only be used by users with the **Founder** role.",
                color=0xFF4444,
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await interaction.response.defer()

        try:
            # Check if there's already an ongoing season
            ongoing_season = await Season.get_ongoing_season()
            if ongoing_season:
                embed = discord.Embed(
                    title="❌ Season Already Active",
                    description=f"Season {ongoing_season['season_id']} is already active and started on <t:{self._parse_timestamp(ongoing_season['started_at'])}:F>.",
                    color=0xFF4444,
                )
                embed.add_field(
                    name="💡 Tip",
                    value="Use `/end_season` to end the current season before starting a new one.",
                    inline=False,
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
                return

            # Start database transaction-like operations
            try:
                # 1. Create new season
                season_result = await Season.create_new_season()
                new_season = season_result.data[0]

                # 2. Clear matches table
                await Season.clear_matches_table()

                # Success embed
                embed = discord.Embed(
                    title="🎉 Season Started!",
                    description=f"**Season {new_season['season_id']}** has begun!",
                    color=EMBED_COLOR,
                )
                embed.add_field(
                    name="📅 Started",
                    value=f"<t:{self._parse_timestamp(new_season['started_at'])}:F>",
                    inline=True,
                )
                embed.add_field(
                    name="🎮 Changes Made",
                    value="• New season created\n• Match history cleared\n• Match IDs reset (start from #1)\n• Submit results enabled\n• Leaderboard enabled",
                    inline=False,
                )
                embed.set_footer(
                    text=f"Good luck to all players in Season {new_season['season_id']}!"
                )

                await interaction.followup.send(embed=embed)

                # Log the action
                logger.info(
                    f"Season {new_season['season_id']} started by {interaction.user.display_name} ({interaction.user.id})"
                )

            except Exception as e:
                logger.error(f"Error during season start transaction: {e}")
                embed = discord.Embed(
                    title="❌ Season Start Failed",
                    description="An error occurred while starting the new season. Please try again.",
                    color=0xFF4444,
                )
                await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as e:
            logger.error(f"Error in start_season command: {e}")
            embed = discord.Embed(
                title="❌ Command Error",
                description="An unexpected error occurred. Please try again.",
                color=0xFF4444,
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(
        name="end_season", description="End the current season (Founder only)"
    )
    async def end_season(self, interaction: discord.Interaction):
        """End the current season - Founder role only"""
        # Check if user has Founder role
        if not self.has_founder_role(interaction):
            embed = discord.Embed(
                title="❌ Permission Denied",
                description="This command can only be used by users with the **Founder** role.",
                color=0xFF4444,
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await interaction.response.defer()

        try:
            # Check if there's an ongoing season
            ongoing_season = await Season.get_ongoing_season()
            if not ongoing_season:
                embed = discord.Embed(
                    title="❌ No Active Season",
                    description="There is no ongoing season to end.",
                    color=0xFF4444,
                )
                embed.add_field(
                    name="💡 Tip",
                    value="Use `/start_season` to start a new season.",
                    inline=False,
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
                return

            # Start archiving process
            processing_embed = discord.Embed(
                title="🔄 Ending Season...",
                description=f"Ending Season {ongoing_season['season_id']} and archiving data...",
                color=0xFFAA00,
            )
            processing_embed.add_field(
                name="📊 Progress",
                value="• Retrieving matches...\n• Collecting player stats...\n• Resetting player statistics...\n• Updating season record...",
                inline=False,
            )
            processing_message = await interaction.followup.send(embed=processing_embed)

            try:
                # 1. Get all matches for archiving
                all_matches = await Season.get_all_matches()

                # 2. Get all users stats for archiving
                all_users_stats = await Season.get_all_users_stats()

                # 3. End the season with archived data
                await Season.end_season(
                    ongoing_season["season_id"], all_matches, all_users_stats
                )

                # 4. Reset all player statistics
                await Season.reset_all_players_stats()

                # 5. Clear matches table and reset sequence
                await Season.clear_matches_table()

                # Success embed
                embed = discord.Embed(
                    title="🏁 Season Ended!",
                    description=f"**Season {ongoing_season['season_id']}** has been completed and archived.",
                    color=EMBED_COLOR,
                )
                embed.add_field(
                    name="📊 Season Statistics",
                    value=f"• **Matches Archived:** {len(all_matches)}\n• **Players Archived:** {len(all_users_stats)}",
                    inline=False,
                )
                embed.add_field(
                    name="📅 Started",
                    value=f"<t:{self._parse_timestamp(ongoing_season['started_at'])}:F>",
                    inline=True,
                )
                embed.add_field(
                    name="📅 Ended",
                    value=f"<t:{int(datetime.now().timestamp())}:F>",
                    inline=True,
                )
                embed.add_field(
                    name="🎮 Changes Made",
                    value="• Season marked as completed\n• Match/player data archived\n• Player stats reset (200 pts, 0 K/D/W/L)\n• Match history cleared\n• Match IDs reset (start from #1)\n• Submit results disabled\n• Leaderboard disabled",
                    inline=False,
                )
                embed.set_footer(
                    text=f"Thank you to all players who participated in Season {ongoing_season['season_id']}!"
                )

                await processing_message.edit(embed=embed)

                # Log the action
                logger.info(
                    f"Season {ongoing_season['season_id']} ended by {interaction.user.display_name} ({interaction.user.id}). Archived {len(all_matches)} matches and {len(all_users_stats)} players."
                )

            except Exception as e:
                logger.error(f"Error during season end transaction: {e}")
                embed = discord.Embed(
                    title="❌ Season End Failed",
                    description="An error occurred while ending the season. Please try again.",
                    color=0xFF4444,
                )
                await processing_message.edit(embed=embed)

        except Exception as e:
            logger.error(f"Error in end_season command: {e}")
            embed = discord.Embed(
                title="❌ Command Error",
                description="An unexpected error occurred. Please try again.",
                color=0xFF4444,
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(
        name="season_status", description="Check the current season status"
    )
    async def season_status(self, interaction: discord.Interaction):
        """Check current season status - available to all users"""
        try:
            ongoing_season = await Season.get_ongoing_season()

            if ongoing_season:
                embed = discord.Embed(
                    title="📅 Current Season Status",
                    description=f"**Season {ongoing_season['season_id']} is currently ACTIVE**",
                    color=EMBED_COLOR,
                )
                embed.add_field(
                    name="📅 Started",
                    value=f"<t:{self._parse_timestamp(ongoing_season['started_at'])}:F>\n(<t:{self._parse_timestamp(ongoing_season['started_at'])}:R>)",
                    inline=True,
                )
                embed.add_field(
                    name="🎮 Features",
                    value="✅ Submit Results\n✅ Leaderboard\n✅ Matchmaking",
                    inline=True,
                )
            else:
                # Get the next season number
                last_season_id = await Season.get_last_season_id()
                next_season_number = last_season_id + 1

                embed = discord.Embed(
                    title="📅 Current Season Status",
                    description="**No active season**",
                    color=0xFF4444,
                )
                embed.add_field(
                    name="🎮 Features",
                    value="❌ Submit Results\n❌ Leaderboard\n✅ Matchmaking",
                    inline=True,
                )
                embed.add_field(
                    name="💡 Note",
                    value=f"Please wait for Season {next_season_number} to start.",
                    inline=False,
                )

            await interaction.response.send_message(embed=embed, ephemeral=True)

        except Exception as e:
            logger.error(f"Error in season_status command: {e}")
            embed = discord.Embed(
                title="❌ Command Error",
                description="An unexpected error occurred while checking season status.",
                color=0xFF4444,
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Seasons(bot))
