import discord
from discord.ext import commands
from discord import app_commands
from models.user import User
from models.stats import Stats
from config import Config
import logging
import asyncio
import time

logger = logging.getLogger(__name__)

# Constants
EMBED_COLOR = Config.MAIN_COLOR  # Main brand color


class RegisterModal(discord.ui.Modal, title=f"{Config.APP_NAME} Registration"):
    nickname = discord.ui.TextInput(
        label="In-Game Nickname",
        placeholder="Enter your in game nickname",
        max_length=32,
    )
    so2_id = discord.ui.TextInput(
        label="Standoff 2 Player ID", placeholder="Enter your Player ID"
    )

    async def on_submit(self, interaction: discord.Interaction):
        try:
            # Defer the response to prevent timeout during database operations
            await interaction.response.defer(ephemeral=True)
            
            existing_user = await User.find(interaction.user.id)
            if existing_user:
                embed = discord.Embed(
                    title=f"{Config.ICONS['unsuccess']} Already Registered",
                    description="You're already registered in our system!",
                    color=Config.EMBED_COLORS["red"],
                )
                embed.add_field(
                    name="Your Current Registration",
                    value=f"**Nickname:** {existing_user['nickname']}\n**Player ID:** {existing_user['so2_id']}",
                    inline=False,
                )
                embed.add_field(
                    name="Missing Role or Nickname?",
                    value="If you don't have the Registered role or your nickname isn't set, try leaving and rejoining the server. If the issue persists, contact an administrator.",
                    inline=False,
                )
                return await interaction.followup.send(
                    embed=embed, ephemeral=True
                )

            # Validate that SO2 ID contains only numbers
            if not self.so2_id.value.isdigit():
                embed = discord.Embed(
                    title=f"{Config.ICONS['unsuccess']} Invalid Player ID",
                    description="Standoff 2 Player ID must contain only numbers.",
                    color=Config.EMBED_COLORS["red"],
                )
                return await interaction.followup.send(
                    embed=embed, ephemeral=True
                )
            # Check for duplicates
            duplicate_issues = []
            existing_nickname_user = await User.find_by_nickname(self.nickname.value)
            existing_so2_user = await User.find_by_so2_id(self.so2_id.value)

            if existing_nickname_user:
                duplicate_issues.append("nickname")
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
                return await interaction.followup.send(
                    embed=embed, ephemeral=True
                )

            # Check if default league is configured and still exists
            try:
                bot = interaction.client
                registration_cog = bot.get_cog("Registration")
                leagues_cog = bot.get_cog("Leagues")

                if registration_cog and leagues_cog:
                    config = await registration_cog.get_config()

                    if config and config.get("default_league"):
                        default_league = config["default_league"]

                        # Check if the default league still exists
                        league_exists = await leagues_cog.league_exists(default_league)

                        if not league_exists:
                            embed = discord.Embed(
                                title=f"{Config.ICONS['unsuccess']} Registration Temporarily Unavailable",
                                description="Registration is currently unavailable because the default league has been deleted.",
                                color=Config.EMBED_COLORS["red"],
                            )
                            embed.add_field(
                                name="Administrator Action Required",
                                value="An administrator needs to either:\n"
                                "• Recreate the deleted league, or\n"
                                "• Reconfigure the default league using `/setup`",
                                inline=False,
                            )
                            embed.set_footer(
                                text="Please contact an administrator for assistance."
                            )
                            return await interaction.followup.send(
                                embed=embed, ephemeral=True
                            )
                    else:
                        # No default league configured
                        embed = discord.Embed(
                            title=f"{Config.ICONS['unsuccess']} Registration Not Set Up",
                            description="Registration is not properly configured. No default league has been set.",
                            color=Config.EMBED_COLORS["red"],
                        )
                        embed.add_field(
                            name="Administrator Action Required",
                            value="An administrator needs to configure the default league using `/setup`",
                            inline=False,
                        )
                        embed.set_footer(
                            text="Please contact an administrator for assistance."
                        )
                        return await interaction.followup.send(
                            embed=embed, ephemeral=True
                        )
                else:
                    # System components not available
                    embed = discord.Embed(
                        title=f"{Config.ICONS['unsuccess']} System Error",
                        description="Registration system components are not available.",
                        color=Config.EMBED_COLORS["red"],
                    )
                    embed.set_footer(text="Please contact an administrator.")
                    return await interaction.followup.send(
                        embed=embed, ephemeral=True
                    )

            except Exception as e:
                logger.error(
                    f"🟡 Error validating default league during registration: {e}"
                )
                embed = discord.Embed(
                    title=f"{Config.ICONS['unsuccess']} Registration Error",
                    description="An error occurred while validating registration settings.",
                    color=Config.EMBED_COLORS["red"],
                )
                embed.set_footer(text="Please contact an administrator.")
                return await interaction.followup.send(
                    embed=embed, ephemeral=True
                )

            await User.register(
                interaction.user.id, self.nickname.value, self.so2_id.value
            )

            # Get or create the Registered role using helper function
            role = await Registration.get_or_create_registered_role_static(interaction.guild)

            # Add Registered role using helper function
            registered_role_assigned = await Registration.assign_role_safely_static(
                interaction.user, role, "User registration"
            )

            # Assign Level 1 role to new users (they start with 200 points = Level 1)
            level_role_assigned = False
            try:
                # Fix: Use correct cog name "LevelsCog" instead of "Levels"
                levels_cog = interaction.client.get_cog("LevelsCog")
                if levels_cog:
                    await levels_cog.update_player_roles(
                        interaction.guild, interaction.user.id, 200
                    )
                    level_role_assigned = True
                    logger.info(f"🟡 Assigned level role to {interaction.user}")
                else:
                    logger.warning("🟡 LevelsCog not found - skipping level role assignment")
            except Exception as e:
                logger.error(f"🟡 Error assigning level role during registration: {e}")
                level_role_assigned = False

            # Add user to default league and assign league role
            league_added = False
            league_role_assigned = False
            default_league_display = "No League"

            try:
                # Get the bot instance to access other cogs
                bot = interaction.client
                registration_cog = bot.get_cog("Registration")

                if registration_cog:
                    # Get config to find default league
                    config = await registration_cog.get_config()

                    if config and config.get("default_league"):
                        default_league = config["default_league"]
                        default_league_display = config["default_league_display"]
                        # Add user to the default league table
                        league_result = await registration_cog.add_user_to_league(
                            interaction.user.id, default_league
                        )

                        if league_result:
                            league_added = True

                            # Try to assign the league role using helper function
                            league_role = discord.utils.get(
                                interaction.guild.roles, name=default_league_display
                            )

                            if league_role:
                                league_role_assigned = await Registration.assign_role_safely_static(
                                    interaction.user, league_role, "Default league assignment"
                                )
                            else:
                                logger.warning(
                                    f"🟡 League role '{default_league_display}' not found for user {interaction.user.id}"
                                )
                        else:
                            logger.error(
                                f"🟡 Failed to add user {interaction.user.id} to league {default_league}"
                            )
                    else:
                        logger.warning("🟡 No default league configured")
                else:
                    logger.error("🟡 Registration cog not found")

            except Exception as e:
                logger.error(f"🟡 Error adding user to default league: {e}")

            # Create success embed with detailed role status
            embed = discord.Embed(
                title=f"{Config.ICONS['success']} Registration Successful",
                color=Config.EMBED_COLORS["light_green"],
            )
            embed.add_field(name="Nickname", value=self.nickname.value, inline=True)
            embed.add_field(name="Player ID", value=self.so2_id.value, inline=True)

            # Add role assignment status
            role_status = ""
            if registered_role_assigned:
                role_status += f"{Config.ICONS['success']} Registered role assigned\n"
            else:
                role_status += f"{Config.ICONS['unsuccess']} Registered role failed\n"
            
            if level_role_assigned:
                role_status += f"{Config.ICONS['success']} Level role assigned\n"
            else:
                role_status += f"{Config.ICONS['info']} Level role skipped\n"

            embed.add_field(name="Roles", value=role_status.strip(), inline=False)

            # Add league information
            if league_added:
                league_status = (
                    f"{Config.ICONS['success']} Added to {default_league_display}"
                )
                if league_role_assigned:
                    league_status += f"\n{Config.ICONS['success']} {default_league_display} role assigned"
                else:
                    league_status += f"\n{Config.ICONS['unsuccess']} {default_league_display} role failed"
            else:
                league_status = f"{Config.ICONS['unsuccess']} League assignment failed"

            embed.add_field(name="League", value=league_status, inline=False)

            try:
                await interaction.user.edit(nick=self.nickname.value)
                embed.add_field(
                    name="Status", value="Nickname updated successfully", inline=False
                )
            except discord.Forbidden:
                embed.add_field(
                    name="Status",
                    value="Couldn't update nickname (missing permissions)",
                    inline=False,
                )

            embed.set_thumbnail(url=interaction.user.display_avatar.url)
            embed.set_footer(text=f"Welcome to {Config.APP_NAME} competitive!")

            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as e:
            logger.error(f"🟡 Registration error: {e}")
            embed = discord.Embed(
                title=f"{Config.ICONS['unsuccess']} Registration Failed",
                description="An error occurred during registration. Please contact an admin.",
                color=Config.EMBED_COLORS["red"],
            )
            
            # Since we deferred the response, always use followup
            await interaction.followup.send(embed=embed, ephemeral=True)


class RegisterView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(
            discord.ui.Button(
                label="Register",
                style=discord.ButtonStyle.primary,
                custom_id="persistent_register_btn",
            )
        )

    async def interaction_check(self, interaction: discord.Interaction):
        await interaction.response.send_modal(RegisterModal())


class Registration(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.bot.add_view(RegisterView())
        
        # CACHE SYSTEM pentru optimizare
        self._config_cache = None
        self._config_cache_time = 0
        self._config_cache_ttl = 300  # 5 minute TTL
        
        self._leagues_cache = None
        self._leagues_cache_time = 0
        self._leagues_cache_ttl = 300  # 5 minute TTL

    @staticmethod
    async def get_or_create_registered_role_static(guild: discord.Guild) -> discord.Role:
        """Static helper function for getting/creating registered role - accessible from RegisterModal"""
        role = discord.utils.get(guild.roles, name=Config.REGISTERED_ROLE)
        if not role:
            try:
                role = await guild.create_role(
                    name=Config.REGISTERED_ROLE,
                    color=EMBED_COLOR,
                    hoist=True,
                    mentionable=True,
                    reason="Auto-created for registered players",
                )
                logger.info(f"🟡 Created Registered role in {guild.name}")
            except discord.Forbidden:
                logger.error(f"🟡 No permission to create Registered role in {guild.name}")
                raise Exception("Bot lacks permission to create roles")
            except Exception as e:
                logger.error(f"🟡 Failed to create Registered role in {guild.name}: {e}")
                raise Exception(f"Role creation failed: {e}")
        return role

    @staticmethod
    async def assign_role_safely_static(member: discord.Member, role: discord.Role, reason: str) -> bool:
        """Static helper function for safely assigning roles - accessible from RegisterModal"""
        if role not in member.roles:
            try:
                await member.add_roles(role, reason=reason)
                logger.info(f"🟡 Assigned role '{role.name}' to {member}")
                return True
            except discord.Forbidden:
                logger.error(f"🟡 No permission to assign role '{role.name}' to {member}")
                return False
            except Exception as e:
                logger.error(f"🟡 Failed to assign role '{role.name}' to {member}: {e}")
                return False
        return True  # Already has role

    async def league_name_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """
        Autocomplete function for league names - delegates to leagues cog
        """
        # Get the leagues cog and use its autocomplete method
        leagues_cog = self.bot.get_cog("Leagues")
        if leagues_cog:
            return await leagues_cog.league_name_autocomplete(interaction, current)
        return []

    async def get_config(self):
        """Get current config from database with caching"""
        current_time = time.time()
        
        # Check if cache is still valid
        if (self._config_cache and 
            current_time - self._config_cache_time < self._config_cache_ttl):
            return self._config_cache
        
        # Cache miss or expired, fetch from database
        loop = asyncio.get_running_loop()

        def db_select():
            try:
                response = Config.supabase().table("config").select("*").execute()
                return response.data[0] if response.data else None
            except Exception as e:
                logger.error(f"🟡 Failed to get config: {e}")
                return None

        result = await loop.run_in_executor(None, db_select)
        
        # Update cache
        if result:
            self._config_cache = result
            self._config_cache_time = current_time
            logger.debug("🟡 Config cache updated")
        
        return result

    async def get_leagues_cached(self):
        """Get all leagues with caching"""
        current_time = time.time()
        
        # Check if cache is still valid
        if (self._leagues_cache and 
            current_time - self._leagues_cache_time < self._leagues_cache_ttl):
            return self._leagues_cache
        
        # Cache miss or expired, fetch from database
        loop = asyncio.get_running_loop()

        def get_all_leagues():
            try:
                response = (
                    Config.supabase()
                    .table("leagues")
                    .select("league_name, original_league_name")
                    .execute()
                )
                return response.data
            except Exception as e:
                logger.error(f"Failed to fetch leagues: {e}")
                return []

        result = await loop.run_in_executor(None, get_all_leagues)
        
        # Update cache
        if result:
            self._leagues_cache = result
            self._leagues_cache_time = current_time
            logger.debug("Leagues cache updated")
        
        return result

    async def invalidate_cache(self):
        """Invalidate all caches (useful after config/league changes)"""
        self._config_cache = None
        self._config_cache_time = 0
        self._leagues_cache = None
        self._leagues_cache_time = 0
        logger.info("🟡 Registration caches invalidated")

    async def update_config(self, default_league: str, default_league_display: str):
        """Update or insert config with default league settings"""
        loop = asyncio.get_running_loop()

        def db_upsert():
            try:
                # Try to get existing config
                response = Config.supabase().table("config").select("*").execute()

                data = {
                    "default_league": default_league,
                    "default_league_display": default_league_display,
                }

                if response.data:
                    # Update existing config
                    result = (
                        Config.supabase()
                        .table("config")
                        .update(data)
                        .eq("id", response.data[0]["id"])
                        .execute()
                    )
                else:
                    # Insert new config
                    result = Config.supabase().table("config").insert(data).execute()

                return result.data[0] if result.data else None
            except Exception as e:
                logger.error(f"🟡 Failed to update config: {e}")
                return None

        result = await loop.run_in_executor(None, db_upsert)
        
        # 🚀 OPTIMIZARE: Invalidează cache după update
        if result:
            await self.invalidate_cache()
        
        return result

    async def add_user_to_league(self, discord_id: int, league_table: str):
        """Add a user to a specific league table using the Stats model"""
        # Use the Stats model to create user stats (league_table is used directly)
        result = await Stats.create_user_stats(discord_id, league_table)

        if result and result.get("success"):
            return {"discord_id": str(discord_id)}
        else:
            logger.error(f"🟡 Failed to add user {discord_id} to league {league_table}")
            return None

    @app_commands.command(
        name="setup", description="Initialize registration system with default league"
    )
    @app_commands.describe(
        default_league="The default league that new users will be added to"
    )
    @app_commands.autocomplete(default_league=league_name_autocomplete)
    @app_commands.default_permissions(administrator=True)
    async def setup(self, interaction: discord.Interaction, default_league: str):
        # Verifică autorizația utilizatorului
        if not await Config.check_authorization(interaction):
            return
            
        await interaction.response.defer(ephemeral=True)
            
        try:
            # Get the leagues cog to use its utilities
            leagues_cog = self.bot.get_cog("Leagues")
            if not leagues_cog:
                embed = discord.Embed(
                    title=f"{Config.ICONS['unsuccess']} Setup Failed",
                    description="Leagues system is not available. Please ensure the leagues cog is loaded.",
                    color=Config.EMBED_COLORS["red"],
                )
                return await interaction.followup.send(
                    embed=embed, ephemeral=True
                )
            # Validate that the selected league exists using leagues cog
            league_exists = await leagues_cog.league_exists(default_league)

            if not league_exists:
                embed = discord.Embed(
                    title=f"{Config.ICONS['unsuccess']} League Not Found",
                    description=f"The league '{default_league}' does not exist. Please create it first using the leagues commands.",
                    color=Config.EMBED_COLORS["red"],
                )
                return await interaction.followup.send(
                    embed=embed, ephemeral=True
                )

            # Get league data to retrieve the original display name
            league_data = await leagues_cog.get_league_by_name(default_league)
            if not league_data:
                embed = discord.Embed(
                    title=f"{Config.ICONS['unsuccess']} League Data Error",
                    description="Failed to retrieve league information.",
                    color=Config.EMBED_COLORS["red"],
                )
                return await interaction.followup.send(
                    embed=embed, ephemeral=True
                )

            # Use the original league name for display
            default_league_display = league_data.get(
                "original_league_name", default_league
            )

            # Update config with selected league
            config_result = await self.update_config(
                default_league, default_league_display
            )

            if not config_result:
                embed = discord.Embed(
                    title=f"{Config.ICONS['unsuccess']} Config Update Failed",
                    description="Failed to save the default league configuration.",
                    color=Config.EMBED_COLORS["red"],
                )
                return await interaction.followup.send(
                    embed=embed, ephemeral=True
                )
            # Create or get the "Registered" role using helper function
            try:
                registered_role = await self.get_or_create_registered_role_static(interaction.guild)
            except Exception as e:
                embed = discord.Embed(
                    title=f"{Config.ICONS['unsuccess']} Role Creation Failed",
                    description=f"Failed to create or get Registered role: {e}",
                    color=Config.EMBED_COLORS["red"],
                )
                return await interaction.followup.send(embed=embed, ephemeral=True)
            
            # Update existing role to ensure it has the correct permissions
            try:
                await registered_role.edit(
                    hoist=True,  # Display role separately in member list
                    mentionable=True,  # Allow mention
                    reason="Updated role permissions during setup",
                )
                logger.info(f"🟡 Updated 'Registered' role permissions")
            except Exception as e:
                logger.warning(f"🟡 Failed to update role permissions: {e}")

            channel = discord.utils.get(
                interaction.guild.text_channels, name=Config.REGISTRATION_CHANNEL
            )

            if not channel:
                # Set up permission overwrites for the registration channel
                overwrites = {
                    interaction.guild.default_role: discord.PermissionOverwrite(
                        read_messages=True, send_messages=False
                    ),
                    registered_role: discord.PermissionOverwrite(
                        read_messages=False, send_messages=False
                    ),  # Registered users cannot view the registration channel
                    interaction.guild.me: discord.PermissionOverwrite(
                        read_messages=True, send_messages=True, manage_messages=True
                    ),
                }

                # Allow administrators to view the registration channel
                for role in interaction.guild.roles:
                    if role.permissions.administrator:
                        overwrites[role] = discord.PermissionOverwrite(
                            read_messages=True, send_messages=True, manage_messages=True
                        )

                channel = await interaction.guild.create_text_channel(
                    Config.REGISTRATION_CHANNEL, overwrites=overwrites, position=0
                )
                logger.info(f"🟡 Created registration channel with proper permissions")
            else:
                # Update existing channel permissions to ensure registered users can't view it
                await channel.set_permissions(
                    registered_role,
                    read_messages=False,
                    send_messages=False,
                    reason="Updated during setup to hide from registered users",
                )
                logger.info(f"🟡 Updated permissions for existing registration channel")

            embed = discord.Embed(
                title=f"{Config.APP_NAME} Registration",
                description=f"Click below to register for {Config.APP_NAME} — your gateway to 5v5 matchmaking!",
                color=EMBED_COLOR,
            )
            if Config.REGISTRATION_BANNER:
                embed.set_image(url=Config.REGISTRATION_BANNER)
            embed.set_footer(text=f"{Config.APP_NAME} Matchmaking")

            await channel.send(embed=embed, view=RegisterView())

            # Create success response with details
            embed = discord.Embed(
                title=f"{Config.ICONS['success']} Setup Completed Successfully",
                description=f"Registration system initialized in {channel.mention}",
                color=Config.EMBED_COLORS["light_green"],
            )

            embed.add_field(
                name="Role Created/Updated",
                value=f"**{registered_role.name}** role is now configured with:\n"
                f"• Display role\n"
                f"• Allow mention\n"
                f"• Cannot view registration channel",
                inline=False,
            )

            embed.add_field(
                name="Channel Permissions",
                value=f"• **Everyone**: Can view, cannot send messages\n"
                f"• **{registered_role.name}**: Cannot view channel\n"
                f"• **Administrators**: Full access",
                inline=False,
            )

            embed.add_field(
                name="Default League Configuration",
                value=f"• **Default League**: {default_league_display}\n"
                f"• **New users will be automatically added to this league**",
                inline=False,
            )

            embed.set_footer(
                text="New registrations will be added to the configured default league"
            )

            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as e:
            logger.error(f"🟡 Setup error: {e}")
            await interaction.followup.send(
                f"{Config.ICONS['unsuccess']} Setup failed: {e}", ephemeral=True
            )

    @app_commands.command(
        name="restore_user",
        description="Manually restore a user's registration data",
    )
    @app_commands.default_permissions(administrator=True)
    async def restore_user(
        self, interaction: discord.Interaction, member: discord.Member
    ):
        """Manually restore a user's registration data"""
        await interaction.response.defer(ephemeral=True)

        try:
            # Check if user exists in database
            user_data = await User.find(member.id)

            if not user_data:
                embed = discord.Embed(
                    title=f"{Config.ICONS['unsuccess']} User Not Found",
                    description=f"{member.mention} is not registered in the database.",
                    color=Config.EMBED_COLORS["red"],
                )
                return await interaction.followup.send(embed=embed)

            # Use the same robust restoration logic as restore_all_users
            success = await self._restore_user_logic(member, user_data)

            if success:
                embed = discord.Embed(
                    title=f"{Config.ICONS['success']} User Restoration Complete",
                    description=f"Successfully restored registration data for {member.mention}",
                    color=Config.EMBED_COLORS["light_green"],
                )
                embed.add_field(
                    name="Nickname", value=user_data["nickname"], inline=True
                )
                embed.add_field(
                    name="Player ID", value=user_data["so2_id"], inline=True
                )
                embed.add_field(
                    name="Status",
                    value=f"{Config.ICONS['success']} All roles and league memberships restored",
                    inline=False,
                )
                embed.set_thumbnail(url=member.display_avatar.url)
                embed.set_footer(text="Restoration completed successfully")
            else:
                embed = discord.Embed(
                    title=f"{Config.ICONS['unsuccess']} Restoration Failed",
                    description=f"Failed to restore data for {member.mention}. Check bot logs for details.",
                    color=Config.EMBED_COLORS["red"],
                )

            await interaction.followup.send(embed=embed)

        except Exception as e:
            logger.error(f"🟡 Error in restore_user command: {e}")
            embed = discord.Embed(
                title=f"{Config.ICONS['unsuccess']} Restoration Failed",
                description=f"An error occurred: {e}",
                color=Config.EMBED_COLORS["red"],
            )
            await interaction.followup.send(embed=embed)

    @app_commands.command(
        name="restore_all_users",
        description="Restore registration data for all registered users",
    )
    @app_commands.default_permissions(administrator=True)
    async def restore_all_users(self, interaction: discord.Interaction):
        """OPTIMIZED: Restore registration data for all registered users in the server using batch operations"""
        await interaction.response.defer(ephemeral=True)

        try:
            # Get all non-bot members in the guild
            all_members = [member for member in interaction.guild.members if not member.bot]
            
            if not all_members:
                embed = discord.Embed(
                    title=f"{Config.ICONS['info']} No Members Found",
                    description="No non-bot members found in this server.",
                    color=Config.EMBED_COLORS["yellow"],
                )
                return await interaction.followup.send(embed=embed)

            # BATCH OPTIMIZATION: Fetch ALL user data in one query
            all_member_ids = [member.id for member in all_members]
            users_batch = await User.find_multiple(all_member_ids)
            logger.info(f"🟡 Fetched user data in batch for {len(all_member_ids)} members")

            # Filter to only registered members using batch result
            registered_members = []
            for member in all_members:
                user_data = users_batch.get(member.id)
                if user_data:
                    registered_members.append((member, user_data))

            if not registered_members:
                embed = discord.Embed(
                    title=f"{Config.ICONS['info']} No Registered Users Found",
                    description="No registered users found in this server.",
                    color=Config.EMBED_COLORS["yellow"],
                )
                return await interaction.followup.send(embed=embed)

            # BATCH OPTIMIZATION: Get leagues data once with cache
            leagues = await self.get_leagues_cached()
            
            # Process in chunks to avoid overwhelming Discord API
            restored_count = 0
            failed_count = 0
            chunk_size = 20  # Process 20 users at a time
            
            for chunk_start in range(0, len(registered_members), chunk_size):
                chunk_end = min(chunk_start + chunk_size, len(registered_members))
                chunk = registered_members[chunk_start:chunk_end]
                
                # Process chunk with batch league lookup
                chunk_results = await self._restore_users_chunk_optimized(chunk, leagues)
                
                for success in chunk_results:
                    if success:
                        restored_count += 1
                    else:
                        failed_count += 1
                
                # Small delay to prevent rate limiting
                if chunk_end < len(registered_members):
                    await asyncio.sleep(0.5)

            # Send final results
            embed = discord.Embed(
                title=f"{Config.ICONS['success']} Restoration Complete",
                description=f"Finished processing {len(registered_members)} registered users.",
                color=Config.EMBED_COLORS["light_green"],
            )
            embed.add_field(
                name="Results",
                value=f"{Config.ICONS['success']} Successfully restored: {restored_count}\n"
                f"{Config.ICONS['unsuccess']} Failed to restore: {failed_count}",
                inline=False,
            )
            
            embed.add_field(
                name="Performance",
                value=f"Used batch optimization\nProcessed {len(all_member_ids)} members with 1 batch query",
                inline=False,
            )

            if failed_count > 0:
                embed.add_field(
                    name="Note",
                    value="Check the bot logs for details about failed restorations.",
                    inline=False,
                )

            await interaction.followup.send(embed=embed)

        except Exception as e:
            logger.error(f"🟡 Error in restore_all_users command: {e}")
            embed = discord.Embed(
                title=f"{Config.ICONS['unsuccess']} Restoration Failed",
                description=f"An error occurred during bulk restoration: {e}",
                color=Config.EMBED_COLORS["red"],
            )
            await interaction.followup.send(embed=embed)

    async def _restore_user_logic(self, member: discord.Member, user_data: dict) -> bool:
        """
        Core restoration logic - wrapper for optimized version that fetches league data
        """
        try:
            # Get user's league data for optimized restoration
            leagues_data = await self.get_leagues_cached()
            user_leagues = await self._get_user_leagues_batch([member.id], leagues_data)
            
            # Use optimized version with fetched league data
            return await self._restore_user_logic_optimized(
                member, user_data, user_leagues.get(member.id, [])
            )
        except Exception as e:
            logger.error(f"🟡 Error in _restore_user_logic for {member}: {e}")
            return False

    @commands.Cog.listener()
    async def on_member_join(self, member):
        """Check if the user is already registered and restore their nickname and role"""
        try:
            # Check if user exists in database
            user_data = await User.find(member.id)

            if user_data:
                # User is already registered, restore their data
                logger.info(
                    f"🟡 Restoring registered user: {member} (Discord ID: {member.id})"
                )

                # Use the same robust restoration logic as restore_all_users and restore_user
                success = await self._restore_user_logic(member, user_data)

                if success:
                    logger.info(f"🟡 Successfully restored user {member} on member join")

                    # Send a welcome back message to the user via DM
                    try:
                        embed = discord.Embed(
                            title=f"{Config.ICONS['wave']} Welcome Back to {Config.APP_NAME}!",
                            description=f"Your registration has been automatically restored.",
                            color=EMBED_COLOR,
                        )
                        embed.add_field(
                            name="Nickname", value=user_data["nickname"], inline=True
                        )
                        embed.add_field(
                            name="Player ID", value=user_data["so2_id"], inline=True
                        )
                        embed.add_field(
                            name="Status",
                            value=f"{Config.ICONS['success_playcolor']} All roles and league memberships restored",
                            inline=False,
                        )
                        embed.set_footer(text="Ready to play competitive matches!")

                        await member.send(embed=embed)
                        logger.info(f"🟡 Sent welcome back message to {member}")
                    except discord.Forbidden:
                        logger.info(f"🟡 Could not send DM to {member} (DMs disabled)")
                    except Exception as e:
                        logger.error(
                            f"🟡 Failed to send welcome back message to {member}: {e}"
                        )
                else:
                    logger.error(f"🟡 Failed to restore user {member} on member join")

        except Exception as e:
            logger.error(f"🟡 Error in on_member_join for {member}: {e}")

    async def _restore_users_chunk_optimized(self, chunk_members, leagues_data):
        """OPTIMIZED: Process a chunk of users with batch league lookups"""
        results = []
        
        # Get all member IDs for batch league lookup
        member_ids = [member.id for member, _ in chunk_members]
        
        # BATCH LEAGUE LOOKUP: Check all users' leagues in batch
        user_leagues_batch = await self._get_user_leagues_batch(member_ids, leagues_data)
        
        # Process each member in the chunk
        for member, user_data in chunk_members:
            try:
                # Use optimized restoration with batch league data
                success = await self._restore_user_logic_optimized(
                    member, user_data, user_leagues_batch.get(member.id, [])
                )
                results.append(success)
                
            except Exception as e:
                logger.error(f"🟡 Error restoring user {member} (ID: {member.id}): {e}")
                results.append(False)
        
        return results

    async def _get_user_leagues_batch(self, user_ids, leagues_data):
        """BATCH OPTIMIZATION: Get league memberships for multiple users"""
        user_leagues = {}
        
        # Initialize empty lists for all users
        for user_id in user_ids:
            user_leagues[user_id] = []
        
        # Check each league table for all users in batch
        for league in leagues_data:
            league_table = league["league_name"]
            original_name = league.get("original_league_name", league_table.replace("_", " ").title())
            
            try:
                loop = asyncio.get_running_loop()
                
                def batch_league_check():
                    try:
                        # Query all users at once with IN clause - include points
                        user_ids_str = [str(uid) for uid in user_ids]
                        response = (
                            Config.supabase()
                            .table(league_table)
                            .select("discord_id, points")
                            .in_("discord_id", user_ids_str)
                            .execute()
                        )
                        return response.data
                    except Exception as e:
                        logger.error(f"🟡 Failed batch check for league {league_table}: {e}")
                        return []
                
                # Get users who are in this league with their points
                league_users_data = await loop.run_in_executor(None, batch_league_check)
                
                # Add league info for users who are members
                for user_data in league_users_data:
                    user_id = int(user_data["discord_id"])
                    if user_id in user_leagues:
                        user_leagues[user_id].append({
                            "table": league_table,
                            "original_name": original_name,
                            "points": user_data.get("points", 200)  # Include points
                        })
                        
            except Exception as e:
                logger.error(f"🟡 Error in batch league check for {league_table}: {e}")
        
        logger.info(f"🟡 Batch league lookup completed for {len(user_ids)} users across {len(leagues_data)} leagues")
        return user_leagues

    async def _restore_user_logic_optimized(self, member: discord.Member, user_data: dict, user_leagues: list) -> bool:
        """OPTIMIZED: Core restoration logic with pre-fetched league data"""
        try:
            # Get or create the registered role using static function
            role = await self.get_or_create_registered_role_static(member.guild)
            
            # Assign the registered role using static function
            success = await self.assign_role_safely_static(
                member, role, f"Restored registration for {user_data['nickname']}"
            )
            if not success:
                return False

            # Assign level role based on user's actual points from pre-fetched league data
            try:
                levels_cog = self.bot.get_cog("LevelsCog")
                if levels_cog and user_leagues:
                    # Use points from pre-fetched league data
                    user_points = user_leagues[0].get("points", 200) if user_leagues else 200
                    await levels_cog.update_player_roles(member.guild, member.id, user_points)
                else:
                    if not levels_cog:
                        logger.warning("🟡 LevelsCog not found during optimized restore")
                    else:
                        # No leagues, use default starting points
                        await levels_cog.update_player_roles(member.guild, member.id, 200)
            except Exception as e:
                logger.error(f"🟡 Error assigning level role during optimized restore for {member}: {e}")

            # Restore league roles using pre-fetched data
            league_roles_added = []
            for league_info in user_leagues:
                league_role_name = league_info["original_name"]
                league_role = discord.utils.get(member.guild.roles, name=league_role_name)
                
                if league_role and league_role not in member.roles:
                    try:
                        await member.add_roles(
                            league_role, 
                            reason=f"Restored league membership for {user_data['nickname']}"
                        )
                        league_roles_added.append(league_role_name)
                    except discord.Forbidden:
                        logger.error(f"🟡 No permission to add league role {league_role_name} to {member}")
                    except Exception as e:
                        logger.error(f"🟡 Failed to add league role {league_role_name} to {member}: {e}")

            # Restore nickname from database
            try:
                if member.display_name != user_data['nickname']:
                    await member.edit(nick=user_data['nickname'])
                    logger.info(f"🟡 Updated nickname for {member} to '{user_data['nickname']}'")
            except discord.Forbidden:
                logger.warning(f"🟡 No permission to change nickname for {member}")
            except Exception as e:
                logger.error(f"🟡 Failed to update nickname for {member}: {e}")

            logger.info(f"🟡 Successfully restored {member} with {len(league_roles_added)} league roles")
            return True

        except Exception as e:
            logger.error(f"🟡 Error in optimized restore logic for {member}: {e}")
            return False


async def setup(bot):
    await bot.add_cog(Registration(bot))
