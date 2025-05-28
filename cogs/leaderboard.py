import discord
from discord.ext import commands, tasks
from discord import app_commands
from config import Config
from models.user import User
import logging
from datetime import datetime
import asyncio

logger = logging.getLogger(__name__)

EMBED_COLOR = 0xB2FA19  # Vibrant green color


class Leaderboard(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.leaderboard_messages = {}  # Store message references for auto-updates
        self.update_leaderboards.start()  # Start the auto-update task

    def cog_unload(self):
        """Clean up when the cog is unloaded"""
        self.update_leaderboards.cancel()

    @app_commands.command(
        name="leaderboard",
        description="Display the leaderboard in the specified channel.",
    )
    @app_commands.describe(channel="The channel where the leaderboard should be posted")
    async def leaderboard(
        self, interaction: discord.Interaction, channel: discord.TextChannel = None
    ):
        """Create or update a leaderboard embed in the specified channel"""
        # Use current channel if none specified
        target_channel = channel or interaction.channel

        # Check if bot has permissions in the target channel
        if not target_channel.permissions_for(interaction.guild.me).send_messages:
            await interaction.response.send_message(
                f"❌ I don't have permission to send messages in {target_channel.mention}",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            # Create the leaderboard embed
            embed, error_msg = await self.create_leaderboard_embed()

            if error_msg:
                await interaction.followup.send(f"❌ {error_msg}", ephemeral=True)
                return

            # Send the leaderboard to the target channel
            message = await target_channel.send(embed=embed)

            # Store the message reference for auto-updates
            self.leaderboard_messages[target_channel.id] = {
                "message": message,
                "channel": target_channel,
            }

            # Success response
            embed_response = discord.Embed(
                title="✅ Leaderboard Posted",
                description=f"Leaderboard has been posted in {target_channel.mention}",
                color=EMBED_COLOR,
            )
            embed_response.add_field(
                name="Auto-Update",
                value="The leaderboard will automatically update every hour",
                inline=False,
            )
            await interaction.followup.send(embed=embed_response, ephemeral=True)

        except Exception as e:
            logger.error(f"Failed to create leaderboard: {e}")
            await interaction.followup.send(
                "❌ An error occurred while creating the leaderboard. Please try again.",
                ephemeral=True,
            )

    async def create_leaderboard_embed(self):
        """Create the leaderboard embed with player data"""
        try:
            # Fetch all users from database
            users_data = await self.get_all_users()

            if not users_data:
                return None, "No registered users found in the database."

            # Process and sort users by points
            processed_users = []
            for user in users_data:
                try:
                    # Calculate win rate
                    wins = user.get("wins", 0)
                    losses = user.get(
                        "loses", 0
                    )  # Note: database uses 'loses' not 'losses'
                    total_games = wins + losses
                    win_rate = (wins / total_games * 100) if total_games > 0 else 0.0

                    processed_users.append(
                        {
                            "nickname": user.get("nickname", "Unknown"),
                            "points": user.get("points", 0),
                            "wins": wins,
                            "losses": losses,
                            "win_rate": round(win_rate, 2),
                        }
                    )
                except Exception as e:
                    logger.warning(f"Error processing user data: {user}, error: {e}")
                    continue

            # Sort by points (descending)
            processed_users.sort(key=lambda x: x["points"], reverse=True)

            # Create embed
            embed = discord.Embed(
                title="🏆 Leaderboard",
                description="Top players ranked by points",
                color=EMBED_COLOR,
                timestamp=datetime.now(),
            )

            # Add players to embed (top 15 to avoid Discord limits)
            leaderboard_text = ""
            for i, user in enumerate(processed_users[:15], 1):
                # Add medal emojis for top 3
                medal = ""
                if i == 1:
                    medal = "🥇 "
                elif i == 2:
                    medal = "🥈 "
                elif i == 3:
                    medal = "🥉 "

                leaderboard_text += (
                    f"{medal}**{i}.** {user['nickname']}\n"
                    f"   📊 **{user['points']}** pts | "
                    f"🏆 {user['wins']}W | "
                    f"💀 {user['losses']}L | "
                    f"📈 {user['win_rate']}%\n\n"
                )

            if leaderboard_text:
                embed.description = leaderboard_text
            else:
                embed.description = "No player data available."

            embed.set_footer(
                text=f"Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} UTC"
            )

            return embed, None

        except Exception as e:
            logger.error(f"Failed to create leaderboard embed: {e}")
            return None, f"Database error: {str(e)}"

    async def get_all_users(self):
        """Fetch all users from the database"""
        loop = asyncio.get_running_loop()

        def db_select():
            try:
                response = (
                    Config.supabase()
                    .table(User.TABLE)
                    .select("nickname, points, wins, loses")
                    .order("points", desc=True)
                    .execute()
                )
                return response.data if response.data else []
            except Exception as e:
                logger.error(f"Failed to fetch users from database: {e}")
                raise

        return await loop.run_in_executor(None, db_select)

    @tasks.loop(hours=1)
    async def update_leaderboards(self):
        """Automatically update all active leaderboard messages every hour"""
        if not self.leaderboard_messages:
            return

        logger.info(f"Updating {len(self.leaderboard_messages)} leaderboard messages")

        # Create new embed
        embed, error_msg = await self.create_leaderboard_embed()

        if error_msg:
            logger.error(f"Failed to create leaderboard for auto-update: {error_msg}")
            return

        # Update each active leaderboard message
        messages_to_remove = []
        for channel_id, data in self.leaderboard_messages.items():
            try:
                message = data["message"]
                channel = data["channel"]

                # Check if message and channel still exist
                if not channel or not message:
                    messages_to_remove.append(channel_id)
                    continue

                # Try to edit the message
                await message.edit(embed=embed)
                logger.info(f"Updated leaderboard in channel {channel.name}")

            except discord.NotFound:
                # Message was deleted
                logger.info(f"Leaderboard message in channel {channel_id} was deleted")
                messages_to_remove.append(channel_id)
            except discord.Forbidden:
                # No permission to edit
                logger.warning(
                    f"No permission to edit leaderboard in channel {channel_id}"
                )
                messages_to_remove.append(channel_id)
            except Exception as e:
                logger.error(
                    f"Failed to update leaderboard in channel {channel_id}: {e}"
                )

        # Remove invalid message references
        for channel_id in messages_to_remove:
            del self.leaderboard_messages[channel_id]

    @update_leaderboards.before_loop
    async def before_update_leaderboards(self):
        """Wait for the bot to be ready before starting the loop"""
        await self.bot.wait_until_ready()

    @app_commands.command(
        name="leaderboard_stop",
        description="Stop auto-updating leaderboards in this channel.",
    )
    @app_commands.checks.has_permissions(manage_messages=True)
    async def stop_leaderboard(self, interaction: discord.Interaction):
        """Stop auto-updating leaderboards in the current channel"""
        channel_id = interaction.channel.id

        if channel_id in self.leaderboard_messages:
            del self.leaderboard_messages[channel_id]
            embed = discord.Embed(
                title="✅ Leaderboard Auto-Update Stopped",
                description="The leaderboard in this channel will no longer auto-update.",
                color=EMBED_COLOR,
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            embed = discord.Embed(
                title="❌ No Active Leaderboard",
                description="There is no active auto-updating leaderboard in this channel.",
                color=discord.Color.red(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name="leaderboard_status",
        description="Check the status of active leaderboards.",
    )
    @app_commands.checks.has_permissions(manage_messages=True)
    async def leaderboard_status(self, interaction: discord.Interaction):
        """Show status of all active leaderboards"""
        if not self.leaderboard_messages:
            embed = discord.Embed(
                title="📊 Leaderboard Status",
                description="No active auto-updating leaderboards found.",
                color=discord.Color.orange(),
            )
        else:
            embed = discord.Embed(
                title="📊 Leaderboard Status",
                description=f"**{len(self.leaderboard_messages)}** active auto-updating leaderboard(s)",
                color=EMBED_COLOR,
            )

            channels_list = []
            for channel_id, data in self.leaderboard_messages.items():
                channel = data["channel"]
                if channel:
                    channels_list.append(f"• {channel.mention}")
                else:
                    channels_list.append(f"• Channel ID: {channel_id} (deleted)")

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


async def setup(bot):
    await bot.add_cog(Leaderboard(bot))
