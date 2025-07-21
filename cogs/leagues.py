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

    async def add_league_to_database(
        self, league_name: str, original_league_name: str, league_emoji: str = None
    ) -> dict:
        """
        Add league to the leagues table with both sanitized and original names, and optional emoji
        """
        loop = asyncio.get_running_loop()

        def db_insert():
            try:
                insert_data = {
                    "league_name": league_name,
                    "original_league_name": original_league_name,
                    "created_at": "now()",
                }

                # Add emoji if provided
                if league_emoji:
                    insert_data["league_emoji"] = league_emoji

                response = (
                    Config.supabase().table("leagues").insert(insert_data).execute()
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
        league_name="The name of the league to create (e.g., 'PRO League')",
        league_emoji="Optional emoji for the league (e.g., '🏆', '⚡', '🔥')",
    )
    @app_commands.default_permissions(administrator=True)
    async def create_league(
        self,
        interaction: discord.Interaction,
        league_name: str,
        league_emoji: str = None,
    ):
        """
        Create a new league by:
        1. Adding a sanitized name to the leagues table
        2. Creating a dedicated table for the league
        3. Creating a Discord role with the original league name
        """
        # Verifică autorizația utilizatorului
        if not await Config.check_authorization(interaction):
            return
            
        # Check if user has admin permissions
        if not interaction.user.guild_permissions.administrator:
            embed = discord.Embed(
                title=f"{Config.ICONS['unsuccess']} Access Denied",
                description="You need administrator permissions to create leagues.",
                color=Config.EMBED_COLORS["red"],
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # Defer the interaction
        await interaction.response.defer(ephemeral=True)

        message = None  # Initialize message variable

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

            # Clean up league_emoji if provided (no validation as requested)
            if league_emoji:
                league_emoji = league_emoji.strip()
                if not league_emoji:  # If it becomes empty after stripping, set to None
                    league_emoji = None

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

            league_data = await self.add_league_to_database(
                sanitized_name, league_name, league_emoji
            )
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
            # Prepare league details text
            league_details = f"**Original Name:** {league_name}\n**Database Name:** `{sanitized_name}`\n**League ID:** #{league_data['league_id']}"
            if league_emoji:
                league_details += f"\n**League Emoji:** {league_emoji}"

            success_embed.add_field(
                name="League Details",
                value=league_details,
                inline=False,
            )

            success_embed.add_field(
                name="Components Created",
                value=f"{Config.ICONS['success']} Database entry in `leagues` table\n{Config.ICONS['success']} Dedicated table `{sanitized_name}`\n{Config.ICONS['success']} Discord role {discord_role.mention}",
                inline=False,
            )

            success_embed.add_field(
                name="Table Structure",
                value="• `discord_id` (TEXT, references users)\n• `points` (INT4)\n• `kills` (INT4)\n• `deaths` (INT4)\n• `wins` (INT4)\n• `loses` (INT4)",
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
                # Only try to edit message if it exists
                if message:
                    try:
                        await message.edit(embed=embed)
                    except:
                        # If editing message also fails, log the error
                        logger.error(
                            "Failed to send error message via both followup and edit"
                        )

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
                    value=f"**Database:** `{league_name}`\n**Discord Role:** {role_status}\n**Created:** <t:{self._parse_timestamp(created_at)}:F>",
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
                        .select("league_name, original_league_name")
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

            # Use original league names from database and filter based on current input
            choices = []
            search_term = (current or "").lower()  # Handle None case

            for league in leagues:
                league_name = league["league_name"]  # sanitized name (table name)
                original_name = league.get("original_league_name")

                # Fallback for old entries that might not have original_league_name
                if not original_name:
                    original_name = league_name.replace("_", " ").title()

                # Filter based on current input (show all if no search term)
                if (
                    not search_term
                    or search_term in original_name.lower()
                    or search_term in league_name.lower()
                ):
                    choices.append(
                        app_commands.Choice(name=original_name, value=league_name)
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
        # Verifică autorizația utilizatorului
        if not await Config.check_authorization(interaction):
            return
            
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

    @app_commands.command(
        name="league_promote",
        description="Promote a user to a specific league by giving them the league role",
    )
    @app_commands.describe(
        league_name="The league to promote the user to",
        user="The user to promote to the league",
    )
    @app_commands.autocomplete(league_name=league_name_autocomplete)
    @app_commands.default_permissions(administrator=True)
    async def league_promote(
        self, interaction: discord.Interaction, league_name: str, user: discord.Member
    ):
        """
        Promote a user to a specific league by giving them the league's Discord role
        """
        # Check if user has admin permissions
        if not interaction.user.guild_permissions.administrator:
            embed = discord.Embed(
                title=f"{Config.ICONS['unsuccess']} Access Denied",
                description="You need administrator permissions to assign users to leagues.",
                color=Config.EMBED_COLORS["red"],
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        # Initialize variables to avoid linter warnings
        league_data = None
        league_role = None

        try:
            # Validate league name is provided
            if not league_name or len(league_name.strip()) == 0:
                embed = discord.Embed(
                    title=f"{Config.ICONS['unsuccess']} League Required",
                    description="Please specify a league to assign the user to.",
                    color=Config.EMBED_COLORS["red"],
                )
                embed.add_field(
                    name=f"{Config.ICONS['info']} Tip",
                    value="Use the autocomplete to select from available leagues.",
                    inline=False,
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
                return

            league_name = league_name.strip()

            # Get league data from database
            league_data = await self.get_league_by_name(league_name)
            if not league_data:
                embed = discord.Embed(
                    title=f"{Config.ICONS['unsuccess']} League Not Found",
                    description=f"The league **'{league_name}'** does not exist in the database.",
                    color=Config.EMBED_COLORS["red"],
                )
                embed.add_field(
                    name=f"{Config.ICONS['info']} Suggestions",
                    value="• Use the autocomplete to select from available leagues\n• Check the spelling of the league name\n• Use `/list_leagues` to see all available leagues",
                    inline=False,
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
                return

            # Find the associated Discord role
            league_role = await self.find_discord_role_for_league(
                interaction.guild, league_data["league_name"]
            )
            if not league_role:
                embed = discord.Embed(
                    title=f"{Config.ICONS['unsuccess']} Discord Role Not Found",
                    description=f"The Discord role for league **'{league_name}'** could not be found.",
                    color=Config.EMBED_COLORS["red"],
                )
                embed.add_field(
                    name=f"{Config.ICONS['fix']} What might be wrong",
                    value="• The Discord role was deleted after the league was created\n• The role name was changed manually\n• League setup is incomplete",
                    inline=False,
                )
                embed.add_field(
                    name=f"{Config.ICONS['info']} Solutions",
                    value="• Contact an administrator to recreate the role\n• Use `/list_leagues` to check league status\n• Try recreating the league if necessary",
                    inline=False,
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
                return

            # Check if user already has the role
            if league_role in user.roles:
                embed = discord.Embed(
                    title=f"{Config.ICONS['info']} Already Promoted",
                    description=f"**{user.display_name}** is already promoted to the **{league_role.name}** league.",
                    color=Config.EMBED_COLORS["yellow"],
                )
                embed.add_field(
                    name="Current Status",
                    value=f"{Config.ICONS['success']} User already has the {league_role.mention} role",
                    inline=False,
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
                return

            # Check if user is already in the league database table
            try:
                is_in_table = await self.check_user_in_league_table(
                    league_name, user.id
                )
                if is_in_table:
                    # User is in database but doesn't have role - this is an inconsistent state
                    # Add the role and show appropriate message
                    try:
                        await user.add_roles(
                            league_role,
                            reason=f"Syncing inconsistent state: user already in {league_role.name} database",
                        )

                        embed = discord.Embed(
                            title=f"{Config.ICONS['info']} Role Added (Sync Fix)",
                            description=f"**{user.display_name}** was already in the **{league_role.name}** league database but missing the Discord role.",
                            color=Config.EMBED_COLORS["yellow"],
                        )
                        embed.add_field(
                            name=f"{Config.ICONS['info']} Inconsistent State Fixed",
                            value="User was in league database but didn't have Discord role. The role has been added to sync with the database state.",
                            inline=False,
                        )
                        embed.add_field(
                            name=f"{Config.ICONS['fix']} Action Taken",
                            value=f"• Added {league_role.mention} to user\n• No database changes needed (user already in league table)\n• Discord and database are now synchronized",
                            inline=False,
                        )
                        embed.add_field(
                            name=f"{Config.ICONS['info']} Note",
                            value="User retains their existing league stats and progress.",
                            inline=False,
                        )
                        embed.set_thumbnail(url=user.display_avatar.url)
                        embed.set_footer(
                            text=f"Sync fix by {interaction.user.display_name}"
                        )

                        await interaction.followup.send(embed=embed, ephemeral=True)
                        logger.info(
                            f"Sync fix: Added role {league_role.name} to {user} who was already in {league_role.name} database"
                        )
                        return

                    except discord.Forbidden:
                        embed = discord.Embed(
                            title=f"{Config.ICONS['unsuccess']} Permission Denied",
                            description="Cannot add the Discord role due to insufficient permissions.",
                            color=Config.EMBED_COLORS["red"],
                        )
                        embed.add_field(
                            name=f"{Config.ICONS['info']} Issue",
                            value="User is in league database but doesn't have Discord role, but I cannot fix this automatically.",
                            inline=False,
                        )
                        embed.add_field(
                            name=f"{Config.ICONS['fix']} Manual Fix Required",
                            value="An administrator with higher permissions needs to add the role manually.",
                            inline=False,
                        )
                        await interaction.followup.send(embed=embed, ephemeral=True)
                        return

                    except Exception as e:
                        logger.error(f"Failed to add missing role: {e}")
                        embed = discord.Embed(
                            title=f"{Config.ICONS['unsuccess']} Sync Fix Failed",
                            description="Failed to add the missing Discord role.",
                            color=Config.EMBED_COLORS["red"],
                        )
                        embed.add_field(
                            name=f"{Config.ICONS['info']} Issue",
                            value="User is in league database but doesn't have Discord role, and automatic fix failed.",
                            inline=False,
                        )
                        embed.add_field(
                            name=f"{Config.ICONS['fix']} Error",
                            value=f"```{str(e)[:500]}```",
                            inline=False,
                        )
                        await interaction.followup.send(embed=embed, ephemeral=True)
                        return

                skip_db_insert = False  # User not in database, proceed normally
            except Exception as e:
                logger.error(f"Failed to check user in league table: {e}")
                skip_db_insert = False  # Default to attempting database insert

            # Check bot permissions
            if not interaction.guild.me.guild_permissions.manage_roles:
                embed = discord.Embed(
                    title=f"{Config.ICONS['unsuccess']} Missing Permissions",
                    description="I don't have permission to manage roles in this server.",
                    color=Config.EMBED_COLORS["red"],
                )
                embed.add_field(
                    name=f"{Config.ICONS['fix']} Required Permission",
                    value="`Manage Roles` - Needed to assign league roles to users",
                    inline=False,
                )
                embed.add_field(
                    name=f"{Config.ICONS['info']} How to fix",
                    value="• Go to Server Settings → Roles\n• Find my role and enable `Manage Roles`\n• Or contact an administrator",
                    inline=False,
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
                return

            # Check if bot's role is high enough to assign the league role
            if league_role.position >= interaction.guild.me.top_role.position:
                embed = discord.Embed(
                    title=f"{Config.ICONS['unsuccess']} Role Hierarchy Issue",
                    description="I cannot assign this league role because it's higher than or equal to my highest role.",
                    color=Config.EMBED_COLORS["red"],
                )
                embed.add_field(
                    name=f"{Config.ICONS['fix']} What's wrong",
                    value=f"The {league_role.mention} role is positioned too high in the role hierarchy.",
                    inline=False,
                )
                embed.add_field(
                    name=f"{Config.ICONS['info']} How to fix",
                    value="• Move my bot role higher in the server role hierarchy\n• Or move the league role lower\n• Contact an administrator for help",
                    inline=False,
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
                return

            # Assign the role to the user
            await user.add_roles(
                league_role,
                reason=f"Assigned to {league_role.name} league by {interaction.user}",
            )

            # Add user to league database table
            user_data = None
            if not skip_db_insert:
                try:
                    # Add user to league-specific table
                    db_success = await self.add_user_to_league_table(
                        league_name, user.id
                    )
                    if not db_success:
                        logger.warning(
                            f"Failed to add user {user.id} to league table {league_name}"
                        )
                except Exception as e:
                    logger.error(
                        f"Database operation failed for user promotion: {e}"
                    )  # Create success embed
            embed = discord.Embed(
                title=f"{Config.ICONS['success']} League Promotion Successful",
                description=f"**{user.display_name}** has been successfully promoted to the **{league_role.name}** league!",
                color=Config.EMBED_COLORS["light_green"],
            )
            embed.add_field(
                name="User", value=f"{user.mention} ({user.display_name})", inline=True
            )
            embed.add_field(name="League", value=f"{league_role.name}", inline=True)
            embed.add_field(
                name="Role Assigned", value=f"{league_role.mention}", inline=True
            )
            embed.add_field(
                name=f"{Config.ICONS['success']} What this means",
                value=f"• User can now join {league_name} lobbies\n• User has access to league-specific features\n• Role will be visible in their profile",
                inline=False,
            )
            embed.set_thumbnail(url=user.display_avatar.url)
            embed.set_footer(
                text=f"Assigned by {interaction.user.display_name} • League ID: #{league_data['league_id']}"
            )

            await interaction.followup.send(embed=embed, ephemeral=True)

        except discord.Forbidden:
            logger.error(f"Forbidden error assigning league role: Missing permissions")
            embed = discord.Embed(
                title=f"{Config.ICONS['unsuccess']} Permission Denied",
                description="I don't have sufficient permissions to assign roles.",
                color=Config.EMBED_COLORS["red"],
            )
            embed.add_field(
                name=f"{Config.ICONS['fix']} Required Permissions",
                value="• `Manage Roles` - Assign league roles to users\n• Role hierarchy must be properly configured",
                inline=False,
            )
            embed.add_field(
                name=f"{Config.ICONS['info']} Contact",
                value="Please contact a server administrator to fix permissions.",
                inline=False,
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
        except discord.HTTPException as he:
            logger.error(f"Discord HTTP error assigning league role: {he}")
            embed = discord.Embed(
                title=f"{Config.ICONS['unsuccess']} Discord API Error",
                description="Failed to assign league role due to a Discord API issue.",
                color=Config.EMBED_COLORS["red"],
            )
            embed.add_field(
                name=f"{Config.ICONS['fix']} Possible Causes",
                value="• Role hierarchy issues\n• Rate limited by Discord\n• Temporary Discord outage",
                inline=False,
            )
            embed.add_field(
                name=f"{Config.ICONS['info']} Solutions",
                value="• Check role hierarchy settings\n• Wait a few minutes and try again\n• Contact support if issue persists",
                inline=False,
            )
            embed.set_footer(text=f"Error Code: {he.status}")
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            logger.error(f"Unexpected error assigning league role: {e}")
            embed = discord.Embed(
                title=f"{Config.ICONS['unsuccess']} Unexpected Error",
                description="An unexpected error occurred while assigning the league role.",
                color=Config.EMBED_COLORS["red"],
            )
            embed.add_field(
                name=f"{Config.ICONS['fix']} Error Information",
                value=f"```{str(e)[:1000]}```",
                inline=False,
            )
            embed.add_field(
                name=f"{Config.ICONS['info']} Next Steps",
                value="• Try again in a few minutes\n• Contact an administrator if the issue persists\n• Check that all required systems are working",
                inline=False,
            )
            embed.set_footer(text="Error logged for administrator review")
            await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(
        name="league_demote",
        description="Remove a user from a specific league by removing their league role",
    )
    @app_commands.describe(
        league_name="The league to remove the user from",
        user="The user to remove from the league",
    )
    @app_commands.autocomplete(league_name=league_name_autocomplete)
    @app_commands.default_permissions(administrator=True)
    async def league_demote(
        self, interaction: discord.Interaction, league_name: str, user: discord.Member
    ):
        """
        Remove a user from a specific league by removing their league's Discord role
        """
        # Check if user has admin permissions
        if not interaction.user.guild_permissions.administrator:
            embed = discord.Embed(
                title=f"{Config.ICONS['unsuccess']} Access Denied",
                description="You need administrator permissions to remove users from leagues.",
                color=Config.EMBED_COLORS["red"],
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        # Initialize variables to avoid linter warnings
        league_data = None
        league_role = None

        try:
            # Validate league name is provided
            if not league_name or len(league_name.strip()) == 0:
                embed = discord.Embed(
                    title=f"{Config.ICONS['unsuccess']} League Required",
                    description="Please specify a league to remove the user from.",
                    color=Config.EMBED_COLORS["red"],
                )
                embed.add_field(
                    name=f"{Config.ICONS['info']} Tip",
                    value="Use the autocomplete to select from available leagues.",
                    inline=False,
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
                return

            league_name = league_name.strip()

            # Get league data from database
            league_data = await self.get_league_by_name(league_name)
            if not league_data:
                embed = discord.Embed(
                    title=f"{Config.ICONS['unsuccess']} League Not Found",
                    description=f"The league **'{league_name}'** does not exist in the database.",
                    color=Config.EMBED_COLORS["red"],
                )
                embed.add_field(
                    name=f"{Config.ICONS['info']} Suggestions",
                    value="• Use the autocomplete to select from available leagues\n• Check the spelling of the league name\n• Use `/list_leagues` to see all available leagues",
                    inline=False,
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
                return

            # Find the associated Discord role
            league_role = await self.find_discord_role_for_league(
                interaction.guild, league_data["league_name"]
            )
            if not league_role:
                embed = discord.Embed(
                    title=f"{Config.ICONS['unsuccess']} Discord Role Not Found",
                    description=f"The Discord role for league **'{league_name}'** could not be found.",
                    color=Config.EMBED_COLORS["red"],
                )
                embed.add_field(
                    name=f"{Config.ICONS['fix']} What might be wrong",
                    value="• The Discord role was deleted after the league was created\n• The role name was changed manually\n• League setup is incomplete",
                    inline=False,
                )
                embed.add_field(
                    name=f"{Config.ICONS['info']} Solutions",
                    value="• Contact an administrator to recreate the role\n• Use `/list_leagues` to check league status\n• Try recreating the league if necessary",
                    inline=False,
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
                return

            # Check if user doesn't have the role
            if league_role not in user.roles:
                embed = discord.Embed(
                    title=f"{Config.ICONS['info']} Not in League",
                    description=f"**{user.display_name}** is not currently in the **{league_role.name}** league.",
                    color=Config.EMBED_COLORS["yellow"],
                )
                embed.add_field(
                    name="Current Status",
                    value=f"{Config.ICONS['unsuccess']} User doesn't have the {league_role.mention} role",
                    inline=False,
                )
                embed.add_field(
                    name=f"{Config.ICONS['info']} Did you mean to",
                    value=f"• Use `/league_promote` to add them to the league?\n• Check a different league?\n• Verify the correct user?",
                    inline=False,
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
                return

            # Check if user is in the league database table
            try:
                is_in_table = await self.check_user_in_league_table(
                    league_name, user.id
                )
                if not is_in_table:
                    # User has role but not in database - this is an inconsistent state
                    # Remove the role and show appropriate message
                    try:
                        await user.remove_roles(
                            league_role,
                            reason=f"Syncing inconsistent state: user not in {league_role.name} database",
                        )

                        embed = discord.Embed(
                            title=f"{Config.ICONS['info']} Role Removed (Sync Fix)",
                            description=f"**{user.display_name}** had the **{league_role.name}** Discord role but was not in the league database.",
                            color=Config.EMBED_COLORS["yellow"],
                        )
                        embed.add_field(
                            name=f"{Config.ICONS['info']} Inconsistent State Fixed",
                            value="User had Discord role but wasn't in league database. The role has been removed to sync with the database state.",
                            inline=False,
                        )
                        embed.add_field(
                            name=f"{Config.ICONS['fix']} Action Taken",
                            value=f"• Removed {league_role.mention} from user\n• No database changes needed (user wasn't in league table)\n• Discord and database are now synchronized",
                            inline=False,
                        )
                        embed.add_field(
                            name=f"{Config.ICONS['info']} Note",
                            value="If the user should be in this league, use `/league_promote` to properly add them.",
                            inline=False,
                        )
                        embed.set_thumbnail(url=user.display_avatar.url)
                        embed.set_footer(
                            text=f"Sync fix by {interaction.user.display_name}"
                        )

                        await interaction.followup.send(embed=embed, ephemeral=True)
                        logger.info(
                            f"Sync fix: Removed role {league_role.name} from {user} who wasn't in {league_role.name} database"
                        )
                        return

                    except discord.Forbidden:
                        embed = discord.Embed(
                            title=f"{Config.ICONS['unsuccess']} Permission Denied",
                            description="Cannot remove the Discord role due to insufficient permissions.",
                            color=Config.EMBED_COLORS["red"],
                        )
                        embed.add_field(
                            name=f"{Config.ICONS['info']} Issue",
                            value="User has Discord role but isn't in league database, but I cannot fix this automatically.",
                            inline=False,
                        )
                        embed.add_field(
                            name=f"{Config.ICONS['fix']} Manual Fix Required",
                            value="An administrator with higher permissions needs to remove the role manually.",
                            inline=False,
                        )
                        await interaction.followup.send(embed=embed, ephemeral=True)
                        return

                    except Exception as e:
                        logger.error(f"Failed to remove inconsistent role: {e}")
                        embed = discord.Embed(
                            title=f"{Config.ICONS['unsuccess']} Sync Fix Failed",
                            description="Failed to remove the inconsistent Discord role.",
                            color=Config.EMBED_COLORS["red"],
                        )
                        embed.add_field(
                            name=f"{Config.ICONS['info']} Issue",
                            value="User has Discord role but isn't in league database, and automatic fix failed.",
                            inline=False,
                        )
                        embed.add_field(
                            name=f"{Config.ICONS['fix']} Error",
                            value=f"```{str(e)[:500]}```",
                            inline=False,
                        )
                        await interaction.followup.send(embed=embed, ephemeral=True)
                        return

                skip_db_delete = False  # User is in database, proceed normally
            except Exception as e:
                logger.error(f"Failed to check user in league table: {e}")
                skip_db_delete = False  # Default to attempting database deletion

            # Check bot permissions
            if not interaction.guild.me.guild_permissions.manage_roles:
                embed = discord.Embed(
                    title=f"{Config.ICONS['unsuccess']} Missing Permissions",
                    description="I don't have permission to manage roles in this server.",
                    color=Config.EMBED_COLORS["red"],
                )
                embed.add_field(
                    name=f"{Config.ICONS['fix']} Required Permission",
                    value="`Manage Roles` - Needed to remove league roles from users",
                    inline=False,
                )
                embed.add_field(
                    name=f"{Config.ICONS['info']} How to fix",
                    value="• Go to Server Settings → Roles\n• Find my role and enable `Manage Roles`\n• Or contact an administrator",
                    inline=False,
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
                return

            # Check if bot's role is high enough to remove the league role
            if league_role.position >= interaction.guild.me.top_role.position:
                embed = discord.Embed(
                    title=f"{Config.ICONS['unsuccess']} Role Hierarchy Issue",
                    description="I cannot remove this league role because it's higher than or equal to my highest role.",
                    color=Config.EMBED_COLORS["red"],
                )
                embed.add_field(
                    name=f"{Config.ICONS['fix']} What's wrong",
                    value=f"The {league_role.mention} role is positioned too high in the role hierarchy.",
                    inline=False,
                )
                embed.add_field(
                    name=f"{Config.ICONS['info']} How to fix",
                    value="• Move my bot role higher in the server role hierarchy\n• Or move the league role lower\n• Contact an administrator for help",
                    inline=False,
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
                return  # Remove the role from the user
            await user.remove_roles(
                league_role,
                reason=f"Removed from {league_role.name} league by {interaction.user}",
            )  # Remove user from league database table
            if not skip_db_delete:
                try:
                    # Remove user from league-specific table
                    db_success = await self.remove_user_from_league_table(
                        league_name, user.id
                    )
                    if not db_success:
                        logger.warning(
                            f"Failed to remove user {user.id} from league table {league_name}"
                        )
                except Exception as e:
                    logger.error(f"Database operation failed for user demotion: {e}")

            # Create success embed
            embed = discord.Embed(
                title=f"{Config.ICONS['success']} League Demotion Successful",
                description=f"**{user.display_name}** has been successfully demoted from the **{league_role.name}** league!",
                color=Config.EMBED_COLORS["light_green"],
            )
            embed.add_field(
                name="User", value=f"{user.mention} ({user.display_name})", inline=True
            )
            embed.add_field(name="League", value=f"{league_role.name}", inline=True)
            embed.add_field(
                name="Role Removed", value=f"{league_role.mention}", inline=True
            )
            embed.add_field(
                name=f"{Config.ICONS['info']} What this means",
                value=f"• User can no longer join {league_name} lobbies\n• User lost access to league-specific features\n• Role is no longer visible in their profile\n• User removed from league database (stats preserved)",
                inline=False,
            )
            embed.set_thumbnail(url=user.display_avatar.url)
            embed.set_footer(
                text=f"Demoted by {interaction.user.display_name} • League ID: #{league_data['league_id']}"
            )

            await interaction.followup.send(embed=embed, ephemeral=True)

        except discord.Forbidden:
            logger.error(f"Forbidden error removing league role: Missing permissions")
            embed = discord.Embed(
                title=f"{Config.ICONS['unsuccess']} Permission Denied",
                description="I don't have sufficient permissions to remove roles.",
                color=Config.EMBED_COLORS["red"],
            )
            embed.add_field(
                name=f"{Config.ICONS['fix']} Required Permissions",
                value="• `Manage Roles` - Remove league roles from users\n• Role hierarchy must be properly configured",
                inline=False,
            )
            embed.add_field(
                name=f"{Config.ICONS['info']} Contact",
                value="Please contact a server administrator to fix permissions.",
                inline=False,
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
        except discord.HTTPException as he:
            logger.error(f"Discord HTTP error removing league role: {he}")
            embed = discord.Embed(
                title=f"{Config.ICONS['unsuccess']} Discord API Error",
                description="Failed to remove league role due to a Discord API issue.",
                color=Config.EMBED_COLORS["red"],
            )
            embed.add_field(
                name=f"{Config.ICONS['fix']} Possible Causes",
                value="• Role hierarchy issues\n• Rate limited by Discord\n• Temporary Discord outage",
                inline=False,
            )
            embed.add_field(
                name=f"{Config.ICONS['info']} Solutions",
                value="• Check role hierarchy settings\n• Wait a few minutes and try again\n• Contact support if issue persists",
                inline=False,
            )
            embed.set_footer(text=f"Error Code: {he.status}")
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            logger.error(f"Unexpected error removing league role: {e}")
            embed = discord.Embed(
                title=f"{Config.ICONS['unsuccess']} Unexpected Error",
                description="An unexpected error occurred while removing the league role.",
                color=Config.EMBED_COLORS["red"],
            )
            embed.add_field(
                name=f"{Config.ICONS['fix']} Error Information",
                value=f"```{str(e)[:1000]}```",
                inline=False,
            )
            embed.add_field(
                name=f"{Config.ICONS['info']} Next Steps",
                value="• Try again in a few minutes\n• Contact an administrator if the issue persists\n• Check that all required systems are working",
                inline=False,
            )
            embed.set_footer(text="Error logged for administrator review")
            await interaction.followup.send(embed=embed, ephemeral=True)

    async def add_user_to_league_table(
        self, league_name: str, discord_id: int
    ) -> bool:
        """
        Add a user to a specific league table
        """
        loop = asyncio.get_running_loop()

        def db_insert():
            try:
                # Sanitize the league name to get the table name
                table_name = self.sanitize_league_name(league_name)

                # Insert user with initial stats
                response = (
                    Config.supabase()
                    .table(table_name)
                    .insert(
                        {
                            "discord_id": str(discord_id),
                            "points": 200,  # Starting points
                            "kills": 0,
                            "deaths": 0,
                            "wins": 0,
                            "loses": 0,
                        }
                    )
                    .execute()
                )
                return True
            except Exception as e:
                logger.error(f"Failed to add user to league table '{table_name}': {e}")
                return False

        return await loop.run_in_executor(None, db_insert)

    async def remove_user_from_league_table(
        self, league_name: str, discord_id: int
    ) -> bool:
        """
        Remove a user from a specific league table
        """
        loop = asyncio.get_running_loop()

        def db_delete():
            try:
                # Sanitize the league name to get the table name
                table_name = self.sanitize_league_name(league_name)

                # Remove user from league table
                response = (
                    Config.supabase()
                    .table(table_name)
                    .delete()
                    .eq("discord_id", str(discord_id))
                    .execute()
                )
                return True
            except Exception as e:
                logger.error(
                    f"Failed to remove user from league table '{table_name}': {e}"
                )
                return False

        return await loop.run_in_executor(None, db_delete)

    async def check_user_in_league_table(
        self, league_name: str, discord_id: int
    ) -> bool:
        """
        Check if a user exists in a specific league table
        """
        loop = asyncio.get_running_loop()

        def db_check():
            try:
                # Sanitize the league name to get the table name
                table_name = self.sanitize_league_name(league_name)

                # Check if user exists in league table
                response = (
                    Config.supabase()
                    .table(table_name)
                    .select("discord_id")
                    .eq("discord_id", str(discord_id))
                    .execute()
                )
                return len(response.data) > 0
            except Exception as e:
                logger.error(
                    f"Failed to check user in league table '{table_name}': {e}"
                )
                return False

        return await loop.run_in_executor(None, db_check)

    def _parse_timestamp(self, timestamp_str):
        """Parse timestamp string and return Unix timestamp for Discord formatting"""
        if not timestamp_str or timestamp_str == "Unknown":
            return "Unknown"

        try:
            from datetime import datetime

            # Handle string timestamps from database
            if isinstance(timestamp_str, str):
                # Replace Z with +00:00 for proper ISO parsing
                if timestamp_str.endswith("Z"):
                    timestamp_str = timestamp_str[:-1] + "+00:00"

                dt = datetime.fromisoformat(timestamp_str)
                return int(dt.timestamp())
            # Handle datetime objects
            elif hasattr(timestamp_str, "timestamp"):
                return int(timestamp_str.timestamp())
            else:
                return "Unknown"
        except Exception as e:
            logger.warning(f"Failed to parse timestamp {timestamp_str}: {e}")
            return "Unknown"


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
