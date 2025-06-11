import discord
from discord.ext import commands
from discord import app_commands
from config import Config
import asyncio
import logging
from models.user import User

logger = logging.getLogger(__name__)


class LevelsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot  # Level configuration with point ranges and colors

    LEVEL_CONFIG = {
        1: {
            "min_points": 0,
            "max_points": 500,
            "color": 0xBBBBBB,
            "win_points": 70,
            "loss_points": -5,
            "emoji": "<:level1:1380672303360118804>",
        },
        2: {
            "min_points": 501,
            "max_points": 751,
            "color": 0x4ADE80,
            "win_points": 70,
            "loss_points": -10,
            "emoji": "<:level2:1380672304928919583>",
        },
        3: {
            "min_points": 751,
            "max_points": 900,
            "color": 0x4ADE80,
            "win_points": 55,
            "loss_points": -10,
            "emoji": "<:level3:1380672307034460371>",
        },
        4: {
            "min_points": 901,
            "max_points": 1050,
            "color": 0xF8C627,
            "win_points": 55,
            "loss_points": -13,
            "emoji": "<:level4:1380672309148516524>",
        },
        5: {
            "min_points": 1051,
            "max_points": 1200,
            "color": 0xF8C627,
            "win_points": 55,
            "loss_points": -20,
            "emoji": "<:level5:1380672311249600615>",
        },
        6: {
            "min_points": 1201,
            "max_points": 1350,
            "color": 0xF8C627,
            "win_points": 40,
            "loss_points": -24,
            "emoji": "<:level6:1380672313401540689>",
        },
        7: {
            "min_points": 1351,
            "max_points": 1530,
            "color": 0xF8C627,
            "win_points": 40,
            "loss_points": -28,
            "emoji": "<:level7:1380672315410350291>",
        },
        8: {
            "min_points": 1531,
            "max_points": 1750,
            "color": 0xFA6E22,
            "win_points": 40,
            "loss_points": -34,
            "emoji": "<:level8:1380672318195499039>",
        },
        9: {
            "min_points": 1751,
            "max_points": 2000,
            "color": 0xFA6E22,
            "win_points": 30,
            "loss_points": -40,
            "emoji": "<:level9:1380672319898255450>",
        },
        10: {
            "min_points": 2001,
            "max_points": float("inf"),
            "color": 0xDD0226,
            "win_points": 20,
            "loss_points": -45,
            "emoji": "<:level10:1380672321525776485>",
        },
    }

    CHALLENGER_CONFIG = {
        "min_points": 2300,
        "color": 0xDD0226,
        "win_points": 15,
        "loss_points": -48,
        "emoji": "<:challenger:1380672301129011222>",
    }

    @staticmethod
    def get_level_from_points(points):
        """Get the level number based on points"""
        for level, config in LevelsCog.LEVEL_CONFIG.items():
            if config["min_points"] <= points <= config["max_points"]:
                return level
        return 10  # Default to level 10 for points above 2001

    @staticmethod
    def get_level_role_name(level):
        """Get the role name for a level"""
        return f"Level {level}"

    @staticmethod
    def get_point_delta(points, is_win):
        """Get the point change based on current points and win/loss"""
        level = LevelsCog.get_level_from_points(points)
        config = LevelsCog.LEVEL_CONFIG[level]

        if is_win:
            return config["win_points"]
        else:
            return config["loss_points"]

    @staticmethod
    def get_challenger_point_delta(is_win):
        """Get point change for Challenger players"""
        if is_win:
            return LevelsCog.CHALLENGER_CONFIG["win_points"]
        else:
            return LevelsCog.CHALLENGER_CONFIG["loss_points"]

    @staticmethod
    def get_level_emoji(points):
        """Get the level emoji based on points"""
        # Check if player is Challenger level
        if points >= LevelsCog.CHALLENGER_CONFIG["min_points"]:
            return LevelsCog.CHALLENGER_CONFIG["emoji"]

        # Otherwise, get the regular level emoji
        for level, config in LevelsCog.LEVEL_CONFIG.items():
            if config["min_points"] <= points <= config["max_points"]:
                return config["emoji"]

        # Default to level 10 emoji for points above 2001 but below challenger
        return LevelsCog.LEVEL_CONFIG[10]["emoji"]

    async def get_top_10_players(self):
        """Get the top 10 players by points from database"""
        loop = asyncio.get_running_loop()

        def db_query():
            response = (
                Config.supabase()
                .table("users")
                .select("discord_id, points")
                .order("points", desc=True)
                .limit(10)
                .execute()
            )
            return response.data

        return await loop.run_in_executor(None, db_query)

    async def is_challenger_eligible(self, discord_id, points):
        """Check if a player is eligible for Challenger role"""
        if points < self.CHALLENGER_CONFIG["min_points"]:
            return False

        top_10 = await self.get_top_10_players()
        top_10_ids = [int(player["discord_id"]) for player in top_10]

        return discord_id in top_10_ids

    async def update_player_roles(self, guild, discord_id, new_points):
        """Update a player's level roles based on their new points"""
        try:
            member = guild.get_member(discord_id)
            if not member:
                logger.warning(f"Member {discord_id} not found in guild")
                return False

            # Calculate new level
            new_level = self.get_level_from_points(new_points)
            new_level_role_name = self.get_level_role_name(new_level)

            # Check if eligible for Challenger
            is_challenger = await self.is_challenger_eligible(discord_id, new_points)

            # Get all level roles and challenger role
            level_roles = []
            challenger_role = None

            for role in guild.roles:
                if role.name.startswith("Level ") and role.name != new_level_role_name:
                    level_roles.append(role)
                elif role.name == "Challenger" and not is_challenger:
                    challenger_role = role

            # Find the correct new level role
            new_level_role = discord.utils.get(guild.roles, name=new_level_role_name)
            challenger_role_obj = discord.utils.get(guild.roles, name="Challenger")

            # Remove old level roles
            roles_to_remove = [role for role in level_roles if role in member.roles]
            if challenger_role and challenger_role in member.roles:
                roles_to_remove.append(challenger_role)

            if roles_to_remove:
                await member.remove_roles(*roles_to_remove, reason="Level update")

            # Add new roles
            roles_to_add = []
            if new_level_role and new_level_role not in member.roles:
                roles_to_add.append(new_level_role)

            if (
                is_challenger
                and challenger_role_obj
                and challenger_role_obj not in member.roles
            ):
                roles_to_add.append(challenger_role_obj)

            if roles_to_add:
                await member.add_roles(*roles_to_add, reason="Level update")

            logger.info(
                f"Updated roles for {member.display_name}: Level {new_level}"
                + (" + Challenger" if is_challenger else "")
            )
            return True

        except Exception as e:
            logger.error(f"Error updating roles for {discord_id}: {e}")
            return False

    @app_commands.command(
        name="setup_levels", description="Create level roles in the server"
    )
    @app_commands.describe()
    @app_commands.default_permissions(administrator=True)
    async def setup_levels(self, interaction: discord.Interaction):
        """Create all level roles with correct names and colors"""
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "❌ You need administrator permissions to use this command.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            guild = interaction.guild
            created_roles = []
            existing_roles = []

            # Create level roles
            for level, config in self.LEVEL_CONFIG.items():
                role_name = self.get_level_role_name(level)
                existing_role = discord.utils.get(guild.roles, name=role_name)

                if existing_role:
                    existing_roles.append(role_name)
                    # Update color if different
                    if existing_role.color.value != config["color"]:
                        await existing_role.edit(color=discord.Color(config["color"]))
                        logger.info(f"Updated color for existing role: {role_name}")
                else:
                    new_role = await guild.create_role(
                        name=role_name,
                        color=discord.Color(config["color"]),
                        reason="Level system setup",
                    )
                    created_roles.append(role_name)
                    logger.info(f"Created role: {role_name}")

            # Create Challenger role
            challenger_role = discord.utils.get(guild.roles, name="Challenger")
            if challenger_role:
                existing_roles.append("Challenger")
                # Update color if different
                if challenger_role.color.value != self.CHALLENGER_CONFIG["color"]:
                    await challenger_role.edit(
                        color=discord.Color(self.CHALLENGER_CONFIG["color"])
                    )
                    logger.info("Updated color for existing Challenger role")
            else:
                new_role = await guild.create_role(
                    name="Challenger",
                    color=discord.Color(self.CHALLENGER_CONFIG["color"]),
                    reason="Level system setup",
                )
                created_roles.append("Challenger")
                logger.info("Created Challenger role")

            # Create response embed
            embed = discord.Embed(
                title="✅ Level System Setup Complete",
                description="All level roles have been created successfully!",
                color=discord.Color.green(),
            )

            if created_roles:
                embed.add_field(
                    name="🆕 Created Roles",
                    value="\n".join(f"• {role}" for role in created_roles),
                    inline=False,
                )

            if existing_roles:
                embed.add_field(
                    name="✅ Existing Roles (Updated if needed)",
                    value="\n".join(f"• {role}" for role in existing_roles),
                    inline=False,
                )

            embed.add_field(
                name="📊 Role Colors",
                value="• Level 1: Gray\n• Level 2-3: Green\n• Level 4-7: Gold\n• Level 8-9: Orange\n• Level 10 & Challenger: Red",
                inline=False,
            )

            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as e:
            logger.error(f"Error in setup_levels: {e}")
            embed = discord.Embed(
                title="❌ Setup Failed",
                description=f"An error occurred while setting up level roles:\n```{str(e)}```",
                color=0xFF0000,
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(
        name="update_players_levels",
        description="Update all players' level roles to match their current points",
    )
    @app_commands.default_permissions(administrator=True)
    async def update_players_levels(self, interaction: discord.Interaction):
        """Update all players' roles to match their current points"""
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "❌ You need administrator permissions to use this command.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            # Get all users from database
            loop = asyncio.get_running_loop()

            def db_query():
                response = (
                    Config.supabase()
                    .table("users")
                    .select("discord_id, points")
                    .execute()
                )
                return response.data

            all_users = await loop.run_in_executor(None, db_query)

            if not all_users:
                await interaction.followup.send(
                    "❌ No users found in database.", ephemeral=True
                )
                return

            guild = interaction.guild
            updated_count = 0
            error_count = 0

            # Create processing embed
            processing_embed = discord.Embed(
                title="🔄 Updating Player Levels",
                description=f"Processing {len(all_users)} players...",
                color=0xFFAA00,
            )
            message = await interaction.followup.send(
                embed=processing_embed, ephemeral=True
            )

            # Update each player's roles
            for i, user_data in enumerate(all_users):
                discord_id = int(user_data["discord_id"])
                points = user_data["points"]

                success = await self.update_player_roles(guild, discord_id, points)
                if success:
                    updated_count += 1
                else:
                    error_count += 1

                # Update progress every 10 players
                if (i + 1) % 10 == 0:
                    progress_embed = discord.Embed(
                        title="🔄 Updating Player Levels",
                        description=f"Progress: {i + 1}/{len(all_users)} players processed",
                        color=0xFFAA00,
                    )
                    await message.edit(embed=progress_embed)

            # Create final results embed
            final_embed = discord.Embed(
                title="✅ Level Update Complete",
                description="All player levels have been updated!",
                color=discord.Color.green(),
            )

            final_embed.add_field(
                name="📊 Results",
                value=f"• Successfully updated: {updated_count}\n• Errors: {error_count}\n• Total processed: {len(all_users)}",
                inline=False,
            )

            if error_count > 0:
                final_embed.add_field(
                    name="⚠️ Note",
                    value="Some players may not be in the server or roles may not exist. Check server logs for details.",
                    inline=False,
                )

            await message.edit(embed=final_embed)

        except Exception as e:
            logger.error(f"Error in update_players_levels: {e}")
            embed = discord.Embed(
                title="❌ Update Failed",
                description=f"An error occurred while updating player levels:\n```{str(e)}```",
                color=0xFF0000,
            )
            await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(LevelsCog(bot))
