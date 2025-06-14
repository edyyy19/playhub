import discord
from discord.ext import commands
from discord import app_commands
from config import Config
import logging
import re
import asyncio

logger = logging.getLogger(__name__)

EMBED_COLOR = 0xB2FA19  # Vibrant green color


class Leagues(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def sanitize_league_name(self, league_name: str) -> str:
        """
        Sanitize league name for database usage:
        - Convert to lowercase
        - Replace spaces and symbols with underscores
        """
        # Convert to lowercase
        sanitized = league_name.lower()

        # Replace spaces and non-alphanumeric characters with underscores
        sanitized = re.sub(r"[^a-z0-9]", "_", sanitized)

        # Remove multiple consecutive underscores
        sanitized = re.sub(r"_+", "_", sanitized)

        # Remove leading/trailing underscores
        sanitized = sanitized.strip("_")

        return sanitized

    async def create_league_table_rpc(self, table_name: str) -> bool:
        """
        Create a league table using Supabase RPC function
        """
        loop = asyncio.get_running_loop()

        def call_rpc():
            try:
                # Call the Supabase RPC function to create league table
                response = (
                    Config.supabase()
                    .rpc("create_league_table", {"table_name": table_name})
                    .execute()
                )
                return True
            except Exception as e:
                logger.error(f"Failed to create league table via RPC: {e}")
                return False

        return await loop.run_in_executor(None, call_rpc)

    async def add_league_to_database(self, league_name: str) -> dict:
        """
        Add league to the leagues table
        """
        loop = asyncio.get_running_loop()

        def db_insert():
            try:
                response = (
                    Config.supabase()
                    .table("leagues")
                    .insert({"league_name": league_name, "created_at": "now()"})
                    .execute()
                )
                return response.data[0] if response.data else None
            except Exception as e:
                logger.error(f"Failed to add league to database: {e}")
                return None

        return await loop.run_in_executor(None, db_insert)

    async def league_exists(self, league_name: str) -> bool:
        """
        Check if a league already exists in the database
        """
        loop = asyncio.get_running_loop()

        def db_check():
            try:
                response = (
                    Config.supabase()
                    .table("leagues")
                    .select("league_name")
                    .eq("league_name", league_name)
                    .execute()
                )
                return len(response.data) > 0
            except Exception as e:
                logger.error(f"Failed to check league existence: {e}")
                return False

        return await loop.run_in_executor(None, db_check)

    @app_commands.command(
        name="create_league",
        description="Create a new league with dedicated table and Discord role",
    )
    @app_commands.describe(
        league_name="The name of the league to create (e.g., 'PRO League')"
    )
    @app_commands.default_permissions(administrator=True)
    async def create_league(self, interaction: discord.Interaction, league_name: str):
        """
        Create a new league by:
        1. Adding a sanitized name to the leagues table
        2. Creating a dedicated table for the league
        3. Creating a Discord role with the original league name
        """
        # Check if user has admin permissions
        if not interaction.user.guild_permissions.administrator:
            embed = discord.Embed(
                title=f"{Config.ICONS['unsuccess']} Access Denied",
                description="You need administrator permissions to create leagues.",
                color=Config.EMBED_COLORS["red"],
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        try:
            # Validate league name
            if not league_name or len(league_name.strip()) == 0:
                embed = discord.Embed(
                    title=f"{Config.ICONS['unsuccess']} Invalid League Name",
                    description="League name cannot be empty.",
                    color=Config.EMBED_COLORS["red"],
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
                return

            league_name = league_name.strip()

            # Sanitize the league name for database
            sanitized_name = self.sanitize_league_name(league_name)

            if not sanitized_name:
                embed = discord.Embed(
                    title=f"{Config.ICONS['unsuccess']} Invalid League Name",
                    description="League name contains no valid characters.",
                    color=Config.EMBED_COLORS["red"],
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
                return

            # Check if league already exists
            if await self.league_exists(sanitized_name):
                embed = discord.Embed(
                    title=f"{Config.ICONS['unsuccess']} League Already Exists",
                    description=f"A league with the name '{league_name}' already exists.",
                    color=Config.EMBED_COLORS["red"],
                )
                embed.add_field(
                    name="Sanitized Name", value=f"`{sanitized_name}`", inline=False
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
                return

            # Check if Discord role already exists
            existing_role = discord.utils.get(interaction.guild.roles, name=league_name)
            if existing_role:
                embed = discord.Embed(
                    title=f"{Config.ICONS['unsuccess']} Role Already Exists",
                    description=f"A Discord role named '{league_name}' already exists.",
                    color=Config.EMBED_COLORS["red"],
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
                return

            # Create processing embed
            processing_embed = discord.Embed(
                title=f"{Config.ICONS['updating']} Creating League",
                description=f"Setting up '{league_name}'...",
                color=Config.EMBED_COLORS["yellow"],
            )
            processing_embed.add_field(
                name="Progress",
                value=f"{Config.ICONS['updating']} Starting setup...",
                inline=False,
            )
            message = await interaction.followup.send(
                embed=processing_embed, ephemeral=True
            )

            # Step 1: Add league to database
            processing_embed.set_field_at(
                0,
                name="Progress",
                value=f"{Config.ICONS['stats']} Adding league to database...",
                inline=False,
            )
            await message.edit(embed=processing_embed)

            league_data = await self.add_league_to_database(sanitized_name)
            if not league_data:
                embed = discord.Embed(
                    title=f"{Config.ICONS['unsuccess']} Database Error",
                    description="Failed to add league to database.",
                    color=Config.EMBED_COLORS["red"],
                )
                await message.edit(embed=embed)
                return

            # Step 2: Create league table
            processing_embed.set_field_at(
                0,
                name="Progress",
                value=f"{Config.ICONS['sql']} Creating league table...",
                inline=False,
            )
            await message.edit(embed=processing_embed)

            table_created = await self.create_league_table_rpc(sanitized_name)
            if not table_created:
                embed = discord.Embed(
                    title=f"{Config.ICONS['unsuccess']} Table Creation Failed",
                    description="Failed to create league table. The league entry will be removed from database.",
                    color=Config.EMBED_COLORS["red"],
                )

                # Cleanup: Remove league from database
                await self.cleanup_league(sanitized_name)
                await message.edit(embed=embed)
                return

            # Step 3: Create Discord role
            processing_embed.set_field_at(
                0,
                name="Progress",
                value=f"{Config.ICONS['tag']} Creating Discord role...",
                inline=False,
            )
            await message.edit(embed=processing_embed)

            try:
                discord_role = await interaction.guild.create_role(
                    name=league_name,
                    color=EMBED_COLOR,
                    hoist=True,  # Display role separately in member list
                    mentionable=True,  # Allow mention
                    reason=f"League role created for '{league_name}'",
                )
                logger.info(
                    f"Created Discord role '{league_name}' with ID: {discord_role.id}"
                )
            except discord.Forbidden:
                embed = discord.Embed(
                    title=f"{Config.ICONS['unsuccess']} Role Creation Failed",
                    description="Failed to create Discord role. Missing permissions.",
                    color=Config.EMBED_COLORS["red"],
                )

                # Cleanup
                await self.cleanup_league(sanitized_name)
                await message.edit(embed=embed)
                return
            except Exception as e:
                logger.error(f"Failed to create Discord role: {e}")
                embed = discord.Embed(
                    title=f"{Config.ICONS['unsuccess']} Role Creation Failed",
                    description=f"Failed to create Discord role: {str(e)}",
                    color=Config.EMBED_COLORS["red"],
                )

                # Cleanup
                await self.cleanup_league(sanitized_name)
                await message.edit(embed=embed)
                return

            # Success!
            success_embed = discord.Embed(
                title=f"{Config.ICONS['success']} League Created Successfully",
                description=f"League '{league_name}' has been created and is ready for use!",
                color=Config.EMBED_COLORS["light_green"],
            )

            success_embed.add_field(
                name="League Details",
                value=f"**Original Name:** {league_name}\n**Database Name:** `{sanitized_name}`\n**League ID:** #{league_data['league_id']}",
                inline=False,
            )

            success_embed.add_field(
                name="Components Created",
                value=f"{Config.ICONS['success']} Database entry in `leagues` table\n{Config.ICONS['success']} Dedicated table `{sanitized_name}`\n{Config.ICONS['success']} Discord role {discord_role.mention}",
                inline=False,
            )

            success_embed.add_field(
                name="Table Structure",
                value="• `discord_id` (TEXT, references users)\n• `nickname` (TEXT)\n• `points` (INT4)\n• `kills` (INT4)\n• `deaths` (INT4)\n• `wins` (INT4)\n• `loses` (INT4)",
                inline=False,
            )

            success_embed.set_footer(
                text=f"Created by {interaction.user.display_name} • League system ready"
            )

            await message.edit(embed=success_embed)
            logger.info(
                f"Successfully created league '{league_name}' (sanitized: '{sanitized_name}') by {interaction.user}"
            )

        except Exception as e:
            logger.error(f"Unexpected error creating league: {e}")
            embed = discord.Embed(
                title=f"{Config.ICONS['unsuccess']} Unexpected Error",
                description="An unexpected error occurred while creating the league.",
                color=Config.EMBED_COLORS["red"],
            )
            embed.add_field(name="Error Details", value=f"```{str(e)}```", inline=False)

            try:
                await interaction.followup.send(embed=embed, ephemeral=True)
            except:
                await message.edit(embed=embed)

    async def cleanup_league(self, sanitized_name: str):
        """
        Cleanup league entries if creation fails
        """
        loop = asyncio.get_running_loop()

        def db_cleanup():
            try:
                # Remove from leagues table
                Config.supabase().table("leagues").delete().eq(
                    "league_name", sanitized_name
                ).execute()
                logger.info(f"Cleaned up league entry: {sanitized_name}")
            except Exception as e:
                logger.error(f"Failed to cleanup league entry: {e}")

        await loop.run_in_executor(None, db_cleanup)

    async def get_league_by_name(self, league_name: str) -> dict:
        """
        Get league data by name (supports both original and sanitized names)
        """
        loop = asyncio.get_running_loop()

        def db_get():
            try:
                # First try exact match
                response = (
                    Config.supabase()
                    .table("leagues")
                    .select("*")
                    .eq("league_name", league_name)
                    .execute()
                )

                if response.data:
                    return response.data[0]

                # Try sanitized version if exact match fails
                sanitized = self.sanitize_league_name(league_name)
                response = (
                    Config.supabase()
                    .table("leagues")
                    .select("*")
                    .eq("league_name", sanitized)
                    .execute()
                )

                return response.data[0] if response.data else None
            except Exception as e:
                logger.error(f"Failed to get league by name: {e}")
                return None

        return await loop.run_in_executor(None, db_get)

    async def drop_league_table_rpc(self, table_name: str) -> bool:
        """
        Drop a league table using Supabase RPC function
        """
        loop = asyncio.get_running_loop()

        def call_rpc():
            try:
                # Call the Supabase RPC function to drop league table
                response = (
                    Config.supabase()
                    .rpc("drop_league_table", {"table_name": table_name})
                    .execute()
                )
                return True
            except Exception as e:
                logger.error(f"Failed to drop league table via RPC: {e}")
                return False

        return await loop.run_in_executor(None, call_rpc)

    async def remove_league_from_database(self, league_name: str) -> bool:
        """
        Remove league from the leagues table
        """
        loop = asyncio.get_running_loop()

        def db_delete():
            try:
                response = (
                    Config.supabase()
                    .table("leagues")
                    .delete()
                    .eq("league_name", league_name)
                    .execute()
                )
                return True
            except Exception as e:
                logger.error(f"Failed to remove league from database: {e}")
                return False

        return await loop.run_in_executor(None, db_delete)

    async def get_league_table_stats(self, table_name: str) -> dict:
        """
        Get statistics about a league table (player count, etc.)
        """
        loop = asyncio.get_running_loop()

        def get_stats():
            try:
                # Get player count in league
                response = (
                    Config.supabase()
                    .table(table_name)
                    .select("discord_id", count="exact")
                    .execute()
                )
                player_count = response.count if hasattr(response, "count") else 0

                # Get some basic stats
                return {"player_count": player_count, "exists": True}
            except Exception as e:
                logger.error(f"Failed to get league table stats: {e}")
                return {"player_count": 0, "exists": False}

        return await loop.run_in_executor(None, get_stats)

    async def find_discord_role_for_league(
        self, guild: discord.Guild, league_name: str
    ) -> discord.Role:
        """
        Find the Discord role associated with a league
        """
        # Try to find role by reverse-sanitizing the league name
        original_name_variations = [
            league_name.replace("_", " ").title(),
            league_name.replace("_", " ").upper(),
            league_name.replace("_", " ").lower(),
            league_name.replace("_", " "),
            league_name.replace("_", "-"),
            league_name.upper(),
            league_name.title(),
        ]

        for role in guild.roles:
            # Check if the role's sanitized name matches our league name
            if self.sanitize_league_name(role.name) == league_name:
                return role

            # Also check some common variations
            if role.name in original_name_variations:
                return role

        return None

    @app_commands.command(name="list_leagues", description="List all existing leagues")
    @app_commands.default_permissions(administrator=True)
    async def list_leagues(self, interaction: discord.Interaction):
        """
        List all existing leagues from the database
        """
        await interaction.response.defer(ephemeral=True)

        try:
            loop = asyncio.get_running_loop()

            def get_leagues():
                try:
                    response = (
                        Config.supabase()
                        .table("leagues")
                        .select("*")
                        .order("created_at", desc=True)
                        .execute()
                    )
                    return response.data
                except Exception as e:
                    logger.error(f"Failed to fetch leagues: {e}")
                    return None

            leagues = await loop.run_in_executor(None, get_leagues)

            if leagues is None:
                embed = discord.Embed(
                    title=f"{Config.ICONS['unsuccess']} Database Error",
                    description="Failed to fetch leagues from database.",
                    color=Config.EMBED_COLORS["red"],
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
                return

            if not leagues:
                embed = discord.Embed(
                    title=f"{Config.ICONS['info']} No Leagues Found",
                    description="No leagues have been created yet.",
                    color=Config.EMBED_COLORS["yellow"],
                )
                embed.add_field(
                    name="Get Started",
                    value="Use `/create_league` to create your first league!",
                    inline=False,
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
                return

            embed = discord.Embed(
                title=f"{Config.ICONS['league']} League Directory",
                description=f"Found {len(leagues)} league(s)",
                color=EMBED_COLOR,
            )

            for i, league in enumerate(
                leagues[:10]
            ):  # Limit to 10 leagues to avoid embed limits
                league_id = league.get("league_id", "N/A")
                league_name = league.get("league_name", "Unknown")
                created_at = league.get("created_at", "Unknown")

                # Try to find corresponding Discord role
                discord_role = None
                for role in interaction.guild.roles:
                    if self.sanitize_league_name(role.name) == league_name:
                        discord_role = role
                        break

                role_status = (
                    f"{Config.ICONS['success']} {discord_role.mention}"
                    if discord_role
                    else f"{Config.ICONS['unsuccess']} Role not found"
                )

                embed.add_field(
                    name=f"#{league_id} • {league_name.replace('_', ' ').title()}",
                    value=f"**Database:** `{league_name}`\n**Discord Role:** {role_status}\n**Created:** <t:{int(created_at.timestamp()) if hasattr(created_at, 'timestamp') else 'Unknown'}:R>",
                    inline=False,
                )

            if len(leagues) > 10:
                embed.set_footer(text=f"Showing first 10 of {len(leagues)} leagues")

            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as e:
            logger.error(f"Error listing leagues: {e}")
            embed = discord.Embed(
                title=f"{Config.ICONS['unsuccess']} Error",
                description="An error occurred while fetching leagues.",
                color=Config.EMBED_COLORS["red"],
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

    async def league_name_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """
        Autocomplete function for league names in delete command
        """
        try:
            loop = asyncio.get_running_loop()

            def get_leagues():
                try:
                    response = (
                        Config.supabase()
                        .table("leagues")
                        .select("league_name")
                        .order("created_at", desc=True)
                        .execute()
                    )
                    return response.data
                except Exception as e:
                    logger.error(f"Failed to fetch leagues for autocomplete: {e}")
                    return []

            leagues = await loop.run_in_executor(None, get_leagues)

            if not leagues:
                return []

            # Convert sanitized names to display names and filter based on current input
            choices = []
            for league in leagues:
                league_name = league["league_name"]
                # Convert sanitized name to display name
                display_name = league_name.replace("_", " ").title()

                # Filter based on current input
                if (
                    current.lower() in display_name.lower()
                    or current.lower() in league_name.lower()
                ):
                    choices.append(
                        app_commands.Choice(name=display_name, value=league_name)
                    )
                # Limit to 25 choices (Discord limit)
                if len(choices) >= 25:
                    break

            return choices

        except Exception as e:
            logger.error(f"Error in league autocomplete: {e}")
            return []

    @app_commands.command(
        name="delete_league",
        description="Delete a league and all its associated data (IRREVERSIBLE!)",
    )
    @app_commands.describe(league_name="The name of the league to delete")
    @app_commands.autocomplete(league_name=league_name_autocomplete)
    @app_commands.default_permissions(administrator=True)
    async def delete_league(self, interaction: discord.Interaction, league_name: str):
        """
        Delete a league by:
        1. Removing the league table and all player data
        2. Removing the league entry from the leagues table
        3. Deleting the associated Discord role
        """
        # Check if user has admin permissions
        if not interaction.user.guild_permissions.administrator:
            embed = discord.Embed(
                title=f"{Config.ICONS['unsuccess']} Access Denied",
                description="You need administrator permissions to delete leagues.",
                color=Config.EMBED_COLORS["red"],
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        try:
            # Validate league name
            if not league_name or len(league_name.strip()) == 0:
                embed = discord.Embed(
                    title=f"{Config.ICONS['unsuccess']} Invalid League Name",
                    description="League name cannot be empty.",
                    color=Config.EMBED_COLORS["red"],
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
                return

            league_name = league_name.strip()

            # Get league data from database
            league_data = await self.get_league_by_name(league_name)
            if not league_data:
                embed = discord.Embed(
                    title=f"{Config.ICONS['unsuccess']} League Not Found",
                    description=f"No league found with the name '{league_name}'.",
                    color=Config.EMBED_COLORS["red"],
                )
                embed.add_field(
                    name="Available Leagues",
                    value="Use `/list_leagues` to see all available leagues.",
                    inline=False,
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
                return

            sanitized_name = league_data["league_name"]
            league_id = league_data["league_id"]

            # Get league table statistics
            table_stats = await self.get_league_table_stats(sanitized_name)

            # Find associated Discord role
            discord_role = await self.find_discord_role_for_league(
                interaction.guild, sanitized_name
            )

            # Create confirmation embed with detailed information
            confirmation_embed = discord.Embed(
                title=f"{Config.ICONS['info']} Delete League Confirmation",
                description=f"You are about to **permanently delete** the league:\n**`{league_name}`**",
                color=Config.EMBED_COLORS["yellow"],
            )

            confirmation_embed.add_field(
                name="League Information",
                value=f"**League ID:** #{league_id}\n**Database Name:** `{sanitized_name}`\n**Players in League:** {table_stats['player_count']}",
                inline=False,
            )

            components_to_delete = []
            if table_stats["exists"]:
                components_to_delete.append(
                    f"{Config.ICONS['sql']} League table `{sanitized_name}` (with {table_stats['player_count']} player records)"
                )
            components_to_delete.append(
                f"{Config.ICONS['stats']} League entry from database"
            )
            if discord_role:
                components_to_delete.append(
                    f"{Config.ICONS['tag']} Discord role {discord_role.mention}"
                )
            else:
                components_to_delete.append(
                    f"{Config.ICONS['tag']} Discord role (if found)"
                )

            confirmation_embed.add_field(
                name="Components to Delete",
                value="\n".join(components_to_delete),
                inline=False,
            )

            confirmation_embed.add_field(
                name=f"{Config.ICONS['info']} WARNING",
                value="**This action is IRREVERSIBLE!**\nAll player data, statistics, and league history will be permanently lost.",
                inline=False,
            )

            confirmation_embed.set_footer(
                text="Click 'Confirm Delete' to proceed or 'Cancel' to abort."
            )

            # Create confirmation view with buttons
            view = DeleteLeagueConfirmationView(
                league_data=league_data,
                sanitized_name=sanitized_name,
                discord_role=discord_role,
                cog_instance=self,
            )

            await interaction.followup.send(
                embed=confirmation_embed, view=view, ephemeral=True
            )

        except Exception as e:
            logger.error(f"Unexpected error in delete_league: {e}")
            embed = discord.Embed(
                title=f"{Config.ICONS['unsuccess']} Unexpected Error",
                description="An unexpected error occurred while preparing to delete the league.",
                color=Config.EMBED_COLORS["red"],
            )
            embed.add_field(name="Error Details", value=f"```{str(e)}```", inline=False)
            await interaction.followup.send(embed=embed, ephemeral=True)

    async def perform_league_deletion(
        self,
        interaction: discord.Interaction,
        league_data: dict,
        sanitized_name: str,
        discord_role: discord.Role = None,
    ):
        """
        Perform the actual league deletion process
        """

        async def safe_edit_response(embed, view=None):
            """Safely edit the interaction response with error handling"""
            try:
                await interaction.edit_original_response(embed=embed, view=view)
            except discord.NotFound:
                # Webhook expired or not found, try followup instead
                try:
                    await interaction.followup.send(embed=embed, ephemeral=True)
                except Exception as e:
                    logger.error(f"Failed to send followup message: {e}")
            except Exception as e:
                logger.error(f"Failed to edit original response: {e}")

        try:
            # Create processing embed
            processing_embed = discord.Embed(
                title=f"{Config.ICONS['updating']} Deleting League",
                description=f"Removing '{league_data['league_name']}'...",
                color=Config.EMBED_COLORS["yellow"],
            )
            processing_embed.add_field(
                name="Progress",
                value=f"{Config.ICONS['updating']} Starting deletion process...",
                inline=False,
            )
            # Edit the original message to show progress
            await safe_edit_response(processing_embed)

            deletion_results = {
                "table_deleted": False,
                "database_entry_deleted": False,
                "discord_role_deleted": False,
                "errors": [],
            }

            # Step 1: Drop league table
            processing_embed.set_field_at(
                0,
                name="Progress",
                value=f"{Config.ICONS['sql']} Dropping league table...",
                inline=False,
            )
            await safe_edit_response(processing_embed)

            table_dropped = await self.drop_league_table_rpc(sanitized_name)
            deletion_results["table_deleted"] = table_dropped
            if not table_dropped:
                deletion_results["errors"].append("Failed to drop league table")

            # Step 2: Remove from leagues table
            processing_embed.set_field_at(
                0,
                name="Progress",
                value=f"{Config.ICONS['stats']} Removing from database...",
                inline=False,
            )
            await safe_edit_response(processing_embed)

            database_removed = await self.remove_league_from_database(sanitized_name)
            deletion_results["database_entry_deleted"] = database_removed
            if not database_removed:
                deletion_results["errors"].append(
                    "Failed to remove league from database"
                )

            # Step 3: Delete Discord role
            if discord_role:
                processing_embed.set_field_at(
                    0,
                    name="Progress",
                    value=f"{Config.ICONS['tag']} Deleting Discord role...",
                    inline=False,
                )
                await safe_edit_response(processing_embed)

                try:
                    await discord_role.delete(
                        reason=f"League '{league_data['league_name']}' deleted"
                    )
                    deletion_results["discord_role_deleted"] = True
                    logger.info(
                        f"Deleted Discord role '{discord_role.name}' for league '{sanitized_name}'"
                    )
                except discord.Forbidden:
                    deletion_results["errors"].append(
                        "Missing permissions to delete Discord role"
                    )
                except discord.NotFound:
                    deletion_results["errors"].append(
                        "Discord role was already deleted"
                    )
                except Exception as e:
                    deletion_results["errors"].append(
                        f"Failed to delete Discord role: {str(e)}"
                    )
            else:
                deletion_results["discord_role_deleted"] = True  # No role to delete

            # Create final result embed
            if len(deletion_results["errors"]) == 0:
                # Complete success
                final_embed = discord.Embed(
                    title=f"{Config.ICONS['success']} League Deleted Successfully",
                    description=f"League '{league_data['league_name']}' has been completely removed.",
                    color=Config.EMBED_COLORS["light_green"],
                )

                success_components = []
                if deletion_results["table_deleted"]:
                    success_components.append(
                        f"{Config.ICONS['success']} League table and all player data"
                    )
                if deletion_results["database_entry_deleted"]:
                    success_components.append(
                        f"{Config.ICONS['success']} Database entry"
                    )
                if deletion_results["discord_role_deleted"]:
                    success_components.append(f"{Config.ICONS['success']} Discord role")

                final_embed.add_field(
                    name="Components Removed",
                    value="\n".join(success_components),
                    inline=False,
                )

            else:
                # Partial success or failure
                final_embed = discord.Embed(
                    title=f"{Config.ICONS['unsuccess']} League Deletion Incomplete",
                    description=f"Some components of league '{league_data['league_name']}' could not be deleted.",
                    color=Config.EMBED_COLORS["red"],
                )

                success_components = []
                if deletion_results["table_deleted"]:
                    success_components.append(
                        f"{Config.ICONS['success']} League table and player data"
                    )
                if deletion_results["database_entry_deleted"]:
                    success_components.append(
                        f"{Config.ICONS['success']} Database entry"
                    )
                if deletion_results["discord_role_deleted"] and discord_role:
                    success_components.append(f"{Config.ICONS['success']} Discord role")

                if success_components:
                    final_embed.add_field(
                        name="Successfully Removed",
                        value="\n".join(success_components),
                        inline=False,
                    )

                if deletion_results["errors"]:
                    final_embed.add_field(
                        name="Errors Encountered",
                        value="\n".join(
                            [
                                f"{Config.ICONS['unsuccess']} {error}"
                                for error in deletion_results["errors"]
                            ]
                        ),
                        inline=False,
                    )

            final_embed.set_footer(
                text=f"Deleted by {interaction.user.display_name} • League ID: #{league_data['league_id']}"
            )

            await safe_edit_response(final_embed)
            logger.info(
                f"League deletion completed for '{sanitized_name}' by {interaction.user}"
            )

        except Exception as e:
            logger.error(f"Unexpected error during league deletion: {e}")
            error_embed = discord.Embed(
                title=f"{Config.ICONS['unsuccess']} Deletion Failed",
                description="An unexpected error occurred during the deletion process.",
                color=Config.EMBED_COLORS["red"],
            )
            error_embed.add_field(
                name="Error Details", value=f"```{str(e)}```", inline=False
            )
            await safe_edit_response(error_embed)


class DeleteLeagueConfirmationView(discord.ui.View):
    def __init__(
        self,
        league_data: dict,
        sanitized_name: str,
        discord_role: discord.Role,
        cog_instance,
    ):
        super().__init__(timeout=60.0)
        self.league_data = league_data
        self.sanitized_name = sanitized_name
        self.discord_role = discord_role
        self.cog_instance = cog_instance

    @discord.ui.button(label="Confirm Delete", style=discord.ButtonStyle.danger)
    async def confirm_delete(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        # Acknowledge the button interaction first
        await interaction.response.defer()

        # Perform the deletion
        await self.cog_instance.perform_league_deletion(
            interaction, self.league_data, self.sanitized_name, self.discord_role
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_delete(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        cancel_embed = discord.Embed(
            title=f"{Config.ICONS['success']} Deletion Cancelled",
            description=f"League '{self.league_data['league_name']}' was not deleted.",
            color=Config.EMBED_COLORS["light_green"],
        )
        cancel_embed.add_field(
            name="League Safe",
            value="No changes were made to the league or its data.",
            inline=False,
        )
        await interaction.response.edit_message(embed=cancel_embed, view=None)

    async def on_timeout(self):
        # Disable all buttons when timeout occurs
        for item in self.children:
            item.disabled = True


async def setup(bot):
    await bot.add_cog(Leagues(bot))
