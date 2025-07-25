import discord
from discord.ext import commands, tasks
from discord import app_commands
from config import Config
from models.match import Match
from models.lobby_state import LobbyState
from models.timeout import timeout_manager
from models.user import User
from models.stats import Stats
from models.party import Party
from cogs.levels import LevelsCog
import random
import asyncio
import logging
import time
from collections import defaultdict
from typing import List

logger = logging.getLogger(__name__)

LOBBY_VOICE_NAME = "Lobby"
LOBBY_TEXT_NAME = "Lobby"
CT_VOICE_NAME = "CT Voice"
T_VOICE_NAME = "T Voice"


class Matchmaking(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.lobby_counter = 1
        self.active_lobbies = {}
        self.lobbies_locked = False  # Track if lobbies are locked globally
        self.cleanup_voice_channels.start()
        self.lobby_locks = defaultdict(asyncio.Lock)  # Prevent race conditions
        # Restore lobbies on startup
        asyncio.create_task(self.restore_lobbies())
        # Initialize timeout manager
        asyncio.create_task(self.initialize_timeout_manager())

    async def initialize_timeout_manager(self):
        """Initialize timeout manager and load active timeouts"""
        try:
            await self.bot.wait_until_ready()
            await timeout_manager.load_active_timeouts()
            logger.info("Timeout manager initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize timeout manager: {e}")

    def get_leagues_cog(self):
        """Get the leagues cog for accessing league methods"""
        return self.bot.get_cog("Leagues")

    async def league_name_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        """
        Autocomplete function for league names - delegates to leagues cog
        """
        leagues_cog = self.get_leagues_cog()
        if leagues_cog:
            return await leagues_cog.league_name_autocomplete(interaction, current)
        return []

    @app_commands.command(
        name="create_lobby", description="Create a new matchmaking lobby."
    )
    @app_commands.describe(league_name="The league to create the lobby for (required)")
    @app_commands.autocomplete(league_name=league_name_autocomplete)
    @app_commands.default_permissions(administrator=True)
    async def create_lobby(self, interaction: discord.Interaction, league_name: str):
        # Defer the response immediately to prevent timeout
        await interaction.response.defer(ephemeral=True)
        
        # Verifică autorizația utilizatorului
        if not await Config.check_authorization(interaction):
            return
            
        # Check if interaction has a valid guild
        if not interaction.guild:
            await interaction.followup.send(
                "This command can only be used in a server.", ephemeral=True
            )
            return

        async with self.lobby_locks[interaction.guild.id]:
            try:
                guild = interaction.guild

                if not guild:
                    raise ValueError("Could not access the server.")
                # Determine which role to use for the lobby
                # Validate league name is provided
                if not league_name or len(league_name.strip()) == 0:
                    await interaction.followup.send(
                        f"{Config.ICONS['unsuccess']} **League selection is required**\n"
                        "Please specify a league to create the lobby for. "
                        "Use the autocomplete to select from available leagues.",
                        ephemeral=True,
                    )
                    return

                league_name = league_name.strip()
                # Get league data from database using leagues cog
                leagues_cog = self.get_leagues_cog()
                if not leagues_cog:
                    embed = discord.Embed(
                        title=f"{Config.ICONS['unsuccess']} Leagues System Unavailable",
                        description="The leagues system is currently not available. Please contact an administrator.",
                        color=Config.EMBED_COLORS["red"],
                    )
                    await interaction.followup.send(embed=embed, ephemeral=True)
                    return

                league_data = await leagues_cog.get_league_by_name(league_name)
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
                lobby_role = await leagues_cog.find_discord_role_for_league(
                    guild, league_data["league_name"]
                )
                if not lobby_role:
                    embed = discord.Embed(
                        title=f"{Config.ICONS['unsuccess']} Discord Role Not Found",
                        description=f"The Discord role for league **'{league_name}'** could not be found.",
                        color=Config.EMBED_COLORS["red"],
                    )
                    embed.add_field(
                        name=f"{Config.ICONS['info']} What might be wrong",
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

                lobby_role_name = lobby_role.name

                lobby_number = self.lobby_counter
                self.lobby_counter += 1
                category_name = f"[5v5] | {lobby_role_name}"
                # Check bot permissions
                if not guild.me.guild_permissions.manage_channels:
                    embed = discord.Embed(
                        title=f"{Config.ICONS['unsuccess']} Missing Permissions",
                        description="I don't have permission to manage channels in this server.",
                        color=Config.EMBED_COLORS["red"],
                    )
                    embed.add_field(
                        name=f"{Config.ICONS['info']} Required Permission",
                        value="`Manage Channels` - Needed to create lobby categories and channels",
                        inline=False,
                    )
                    embed.add_field(
                        name=f"{Config.ICONS['info']} How to fix",
                        value="• Go to Server Settings → Roles\n• Find my role and enable `Manage Channels`\n• Or contact an administrator",
                        inline=False,
                    )
                    await interaction.followup.send(embed=embed, ephemeral=True)
                    return
                # Set up permission overwrites - everyone can see, only specific role can join
                overwrites = {
                    guild.default_role: discord.PermissionOverwrite(
                        view_channel=True,  # Allow everyone to see
                        connect=False,  # Deny @everyone from connecting
                        send_messages=False,  # Prevent everyone from sending messages
                        read_messages=True,
                        create_public_threads=False,  # Prevent creating public threads
                        create_private_threads=False,  # Prevent creating private threads
                        send_messages_in_threads=False,  # Prevent sending messages in threads
                    ),
                    guild.me: discord.PermissionOverwrite(
                        view_channel=True,
                        connect=True,
                        manage_channels=True,
                        send_messages=True,
                        read_messages=True,
                        create_public_threads=True,  # Allow bot to create threads
                        create_private_threads=True,  # Allow bot to create private threads
                        send_messages_in_threads=True,  # Allow bot to send messages in threads
                        manage_threads=True,  # Allow bot to manage threads
                    ),  # Allow bot full access
                }

                # Set lobby role permissions based on lock status
                if self.lobbies_locked:
                    overwrites[lobby_role] = discord.PermissionOverwrite(
                        view_channel=True,
                        connect=False,  # Deny connection when locked (even for role holders)
                        send_messages=False,  # Prevent role holders from sending messages
                        read_messages=True,
                        create_public_threads=False,  # Prevent creating public threads
                        create_private_threads=False,  # Prevent creating private threads
                        send_messages_in_threads=False,  # Prevent sending messages in threads
                    )  # Locked: Can see but cannot connect or send messages
                else:
                    overwrites[lobby_role] = discord.PermissionOverwrite(
                        view_channel=True,
                        connect=True,  # Only this role can connect when unlocked
                        send_messages=False,  # Prevent role holders from sending messages
                        read_messages=True,
                        create_public_threads=False,  # Prevent creating public threads
                        create_private_threads=False,  # Prevent creating private threads
                        send_messages_in_threads=False,  # Prevent sending messages in threads
                    )  # Unlocked: Role holders can connect but not send messages

                # Allow administrators to access lobbies
                for role in guild.roles:
                    if role.permissions.administrator:
                        overwrites[role] = discord.PermissionOverwrite(
                            view_channel=True,
                            connect=True,
                            send_messages=True,  # Administrators can send messages
                            read_messages=True,
                            create_public_threads=True,  # Administrators can create public threads
                            create_private_threads=True,  # Administrators can create private threads
                            send_messages_in_threads=True,  # Administrators can send messages in threads
                            manage_threads=True,  # Administrators can manage threads
                        )

                category = await guild.create_category(
                    category_name, overwrites=overwrites
                )
                voice = await category.create_voice_channel(
                    f"Lobby #{lobby_number}", user_limit=Config.PLAYERS_REQUIRED
                )
                text = await category.create_text_channel(LOBBY_TEXT_NAME)
                embed = discord.Embed(
                    title=f"LOBBY #{lobby_number} - WAITING FOR PLAYERS",
                    description="Waiting for the lobby to fill...\n"
                               "Connect to the **Lobby** voice channel to join the queue!\n\n",
                    color=Config.EMBED_COLORS["waiting"],
                )
                if Config.LOBBIES_WAITING_BANNER:
                    embed.set_image(url=Config.LOBBIES_WAITING_BANNER)
                msg = await text.send(embed=embed)

                self.active_lobbies[category.id] = {
                    "category": category,
                    "voice": voice,
                    "text": text,
                    "phase": 1,
                    "players": set(),
                    "message": msg,
                    "team1": [],
                    "team2": [],
                    "map_pool": [map_info["name"] for map_info in Config.MAP_POOL],
                    "match_id": None,
                    "team1_voice": None,
                    "team2_voice": None,
                    "lobby_number": lobby_number,
                    "required_role_id": lobby_role.id,
                    "required_role_name": lobby_role_name,
                    "league_table": league_data["league_name"],  # Add league table name
                }
                # Save lobby state to database
                await self.save_lobby_state(category.id)
                # Create success embed
                embed = discord.Embed(
                    title=f"{Config.ICONS['success']} Lobby Created Successfully",
                    description=f"A new lobby has been created and is ready for {lobby_role_name} players!",
                    color=Config.EMBED_COLORS["light_green"],
                )
                embed.add_field(
                    name="Lobby Number", value=f"#{lobby_number}", inline=True
                )
                embed.add_field(
                    name="Status", value="Waiting for players", inline=True
                )
                embed.add_field(
                    name="Phase", value="Gathering players", inline=True
                )
                embed.add_field(
                    name="Access",
                    value=f"{Config.ICONS['lock']} {lobby_role_name} players only",
                    inline=False,
                )
                embed.set_footer(
                    text=f"Lobby is visible to everyone, but only {lobby_role_name} players can join voice!"
                )

                await interaction.followup.send(embed=embed, ephemeral=True)
            except discord.Forbidden:
                logger.error(f"Forbidden error creating lobby: Missing permissions")
                embed = discord.Embed(
                    title=f"{Config.ICONS['unsuccess']} Permission Denied",
                    description="I don't have sufficient permissions to create the lobby.",
                    color=Config.EMBED_COLORS["red"],
                )
                embed.add_field(
                    name=f"{Config.ICONS['info']} Required Permissions",
                    value="• `Manage Channels` - Create categories and channels\n• `Manage Roles` - Set up channel permissions\n• `Send Messages` - Send lobby information",
                    inline=False,
                )
                embed.add_field(
                    name=f"{Config.ICONS['info']} Contact",
                    value="Please contact a server administrator to fix permissions.",
                    inline=False,
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
            except discord.HTTPException as he:
                logger.error(f"Discord HTTP error creating lobby: {he}")
                embed = discord.Embed(
                    title=f"{Config.ICONS['unsuccess']} Discord API Error",
                    description="Failed to create lobby due to a Discord API issue.",
                    color=Config.EMBED_COLORS["red"],
                )
                embed.add_field(
                    name=f"{Config.ICONS['info']} Possible Causes",
                    value="• Server has reached channel limits\n• Rate limited by Discord\n• Temporary Discord outage",
                    inline=False,
                )
                embed.add_field(
                    name=f"{Config.ICONS['info']} Solutions",
                    value="• Wait a few minutes and try again\n• Check if server has too many channels\n• Contact support if issue persists",
                    inline=False,
                )
                embed.set_footer(text=f"Error Code: {he.status}")
                await interaction.followup.send(embed=embed, ephemeral=True)
            except ValueError as ve:
                logger.error(f"Validation error creating lobby: {ve}")
                embed = discord.Embed(
                    title=f"{Config.ICONS['unsuccess']} Configuration Error",
                    description=f"There's an issue with the server or league configuration.",
                    color=Config.EMBED_COLORS["red"],
                )
                embed.add_field(
                    name=f"{Config.ICONS['info']} Error Details",
                    value=f"```{str(ve)}```",
                    inline=False,
                )
                embed.add_field(
                    name=f"{Config.ICONS['info']} What to do",
                    value="• Contact an administrator\n• Check server configuration\n• Verify league setup is complete",
                    inline=False,
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
            except Exception as e:
                logger.error(f"Unexpected error creating lobby: {e}")
                embed = discord.Embed(
                    title=f"{Config.ICONS['unsuccess']} Unexpected Error",
                    description="An unexpected error occurred while creating the lobby.",
                    color=Config.EMBED_COLORS["red"],
                )
                embed.add_field(
                    name=f"{Config.ICONS['info']} Error Information",
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

    # --- DRAFT PICK UI ---
    class DraftDropdown(discord.ui.Select):
        def __init__(self, lobby, cog, options_with_stats, picks_needed=1):
            # Calculate how many players this captain can pick
            max_picks = min(
                picks_needed, len(options_with_stats), 25
            )  # Discord limit is 25

            # Set placeholder based on number of picks needed
            if picks_needed == 1:
                placeholder = "Select a player to draft..."
            else:
                placeholder = f"Select {picks_needed} players to draft..."

            super().__init__(
                placeholder=placeholder,
                min_values=min(picks_needed, len(options_with_stats)),
                max_values=max_picks,
                options=options_with_stats,
            )
            self.lobby = lobby
            self.cog = cog
            self.picks_needed = picks_needed

        async def callback(self, interaction: discord.Interaction):
            # Critical validation: Check if lobby is still in draft phase
            if self.lobby["phase"] != 2:
                await interaction.response.send_message(
                    "This lobby is no longer in draft phase. Please check the current status.",
                    ephemeral=True,
                )
                return

            # Only allow the captain whose turn it is
            if interaction.user != self.lobby["next_pick"]:
                await interaction.response.send_message(
                    "You are not the captain or it's not your turn to pick.",
                    ephemeral=True,
                )
                return

            # Get all selected players and validate they still exist
            picked_members = []
            for value in self.values:
                picked_id = int(value)
                picked_member = discord.utils.get(
                    self.lobby["available_players"], id=picked_id
                )
                if picked_member:
                    picked_members.append(picked_member)
                else:
                    # Player no longer available
                    await interaction.response.send_message(
                        "One or more selected players are no longer available for draft.",
                        ephemeral=True,
                    )
                    return

            # Validate we have valid picks
            if not picked_members:
                await interaction.response.send_message(
                    "No valid players selected for draft.",
                    ephemeral=True,
                )
                return

            # Acknowledge the interaction
            await interaction.response.defer(ephemeral=True)

            # Handle multiple picks
            await self.cog.handle_draft_picks(self.lobby, picked_members)

    class DraftView(discord.ui.View):
        def __init__(self, lobby, cog, draft_dropdown):
            super().__init__(timeout=120)
            self.lobby = lobby
            self.cog = cog
            self.add_item(draft_dropdown)

        async def on_timeout(self):
            # Enhanced timeout validation with safety checks
            try:
                # Only trigger draft timeout if phase is 2 (drafting) and captain hasn't changed
                # Also check if this view is still the current active one
                if (self.lobby["phase"] == 2 and 
                    self.lobby.get("next_pick") and 
                    self.lobby.get("current_view") == self and
                    self.lobby["category"].id in self.cog.active_lobbies):
                    
                    logger.info(f"Draft timeout triggered for lobby #{self.lobby.get('lobby_number', 'unknown')}, captain: {self.lobby['next_pick'].display_name}")
                    
                    # Double-check that this view is still the active one
                    await self.cog.handle_timeout(
                        self.lobby, reason="Draft pick timeout"
                    )
                else:
                    logger.debug(f"Draft timeout ignored for lobby #{self.lobby.get('lobby_number', 'unknown')} - no longer active or phase changed")
            except Exception as e:
                # If handle_timeout fails, it might be because it's already been called
                logger.warning(
                    f"Draft timeout failed for lobby #{self.lobby.get('lobby_number', 'unknown')}, possibly due to race condition: {e}"
                )

    # --- MAP VETO UI ---
    class VetoDropdown(discord.ui.Select):
        def __init__(self, lobby, cog):
            options = [
                discord.SelectOption(
                    label=map_name, value=map_name, emoji=cog.get_map_emoji(map_name)
                )
                for map_name in lobby["remaining_maps"]
            ]
            super().__init__(
                placeholder="Ban a map...", min_values=1, max_values=1, options=options
            )
            self.lobby = lobby
            self.cog = cog

        async def callback(self, interaction: discord.Interaction):
            # Only allow the captain whose turn it is
            if interaction.user != self.lobby["veto_turn"]:
                await interaction.response.send_message(
                    "You are not the captain or it's not your turn to ban.",
                    ephemeral=True,
                )
                return

            banned_map = self.values[0]

            # Acknowledge the interaction
            await interaction.response.defer(ephemeral=True)

            await self.cog.handle_map_ban(self.lobby, banned_map)

    class VetoView(discord.ui.View):
        def __init__(self, lobby, cog):
            super().__init__(timeout=120)
            self.lobby = lobby
            self.cog = cog
            self.add_item(Matchmaking.VetoDropdown(lobby, cog))

        async def on_timeout(self):
            # Only trigger veto timeout if phase is 3 (veto) and captain hasn't changed
            # Also check if this view is still the current active one
            if (self.lobby["phase"] == 3 and self.lobby.get("veto_turn") and 
                self.lobby.get("current_view") == self):
                # Double-check that this view is still the active one
                try:
                    await self.cog.handle_timeout(self.lobby, reason="Map veto timeout")
                except Exception as e:
                    # If handle_timeout fails, it might be because it's already been called
                    logger.warning(
                        f"Veto timeout failed, possibly due to race condition: {e}"
                    )

    async def get_draft_view(self, lobby):
        options_with_stats = await self.create_draft_options_with_stats(
            lobby["available_players"], lobby.get("league_table")
        )
        picks_needed = await self.calculate_picks_needed(lobby)
        draft_dropdown = Matchmaking.DraftDropdown(
            lobby, self, options_with_stats, picks_needed
        )
        return Matchmaking.DraftView(lobby, self, draft_dropdown)

    def get_veto_view(self, lobby):
        return Matchmaking.VetoView(lobby, self)

    def stop_current_view(self, lobby):
        """Stop the current active view to prevent timeout issues"""
        current_view = lobby.get("current_view")
        if current_view and not current_view.is_finished():
            current_view.stop()
        lobby["current_view"] = None

    def set_current_view(self, lobby, view):
        """Set the current active view and store it in lobby"""
        # Stop any existing view first
        self.stop_current_view(lobby)
        lobby["current_view"] = view
        return view

    # --- TIMEOUT & ERROR HANDLING ---

    async def handle_timeout(self, lobby, reason="Timeout"):
        # Use lobby-specific lock to prevent race conditions
        async with self.lobby_locks[lobby["category"].guild.id]:
            # Only remove users if not in waiting phase
            if lobby["phase"] == 1:
                return  # Ignore timeouts in waiting phase

            # Determine which captain timed out based on the current phase and turn
            captain_to_timeout = None

            if lobby["phase"] == 2:  # Draft phase
                captain_to_timeout = lobby.get("next_pick")
            elif lobby["phase"] == 3:  # Veto phase
                captain_to_timeout = lobby.get("veto_turn")

            if not captain_to_timeout:
                # Fallback: reset entire lobby if we can't determine the captain
                await self.reset_lobby(lobby)
                return

            # Check if captain is already timeouted to prevent duplicates
            is_already_timeouted, _ = await timeout_manager.is_user_timeouted(
                captain_to_timeout.id
            )
            if is_already_timeouted:
                logger.warning(
                    f"Captain {captain_to_timeout.id} is already timeouted, skipping timeout application"
                )
                # Still need to reset lobby even if already timeouted
                await self._reset_lobby_after_captain_timeout(lobby, captain_to_timeout)
                return

            # Add timeout to database for ONLY the captain who failed to act
            success = await timeout_manager.add_timeout(
                captain_to_timeout.id, timeout_minutes=2, reason=reason
            )

        # Send timeout notification to the captain via DM
        try:
            if success:
                # Create timeout notification embed for the captain
                embed = discord.Embed(
                    title=f"{Config.ICONS['clock']} Captain Timeout Applied",
                    description="You have been removed from the lobby and timeouted for failing to act within the time limit.",
                    color=Config.EMBED_COLORS["red"],
                )
                embed.add_field(
                    name="Reason",
                    value=reason,
                    inline=False,
                )
                embed.add_field(
                    name="Timeout Duration",
                    value="2 minutes",
                    inline=True,
                )
                embed.add_field(
                    name="Status",
                    value=f"{Config.ICONS['not_allowed']} Cannot join any lobbies during timeout",
                    inline=True,
                )
                embed.set_footer(
                    text="You will be able to join lobbies again once the timeout expires."
                )

                await captain_to_timeout.send(embed=embed)
            else:
                # Fallback embed for database failure
                embed = discord.Embed(
                    title=f"{Config.ICONS['clock']} Captain Timeout Applied",
                    description="You have been removed from the lobby and timeouted for failing to act within the time limit.",
                    color=Config.EMBED_COLORS["red"],
                )
                embed.add_field(
                    name="Reason",
                    value=reason,
                    inline=False,
                )
                embed.add_field(
                    name="Timeout Duration",
                    value="2 minutes",
                    inline=True,
                )
                embed.set_footer(
                    text="Note: Timeout may not be properly tracked due to database issues."
                )

                await captain_to_timeout.send(embed=embed)
        except Exception as e:
            logger.error(f"Failed to send timeout message to {captain_to_timeout}: {e}")

        # Reset lobby and handle captain removal
        await self._reset_lobby_after_captain_timeout(lobby, captain_to_timeout)

    async def _reset_lobby_after_captain_timeout(self, lobby, captain_to_timeout):
        """Helper method to reset lobby after captain timeout"""
        # Remove the timeouted captain from the lobby
        lobby["players"].discard(captain_to_timeout)

        # Disconnect captain from voice channel
        try:
            if captain_to_timeout.voice and captain_to_timeout.voice.channel:
                await captain_to_timeout.move_to(None)
                logger.info(
                    f"Disconnected timeouted captain {captain_to_timeout} from voice channel"
                )
        except Exception as e:
            logger.error(
                f"Failed to disconnect captain {captain_to_timeout} from voice: {e}"
            )

        # Send private DM notifications to remaining players
        remaining_players = list(lobby["players"])
        for player in remaining_players:
            try:
                notification_embed = discord.Embed(
                    title=f"{Config.ICONS['info']} Captain Timeout",
                    description=f"Captain {captain_to_timeout.display_name} has been timeouted for not acting within the time limit.\n\n**The lobby has been reset to waiting phase.** You can stay and wait for new players to join, or leave to find another lobby.",
                    color=Config.EMBED_COLORS["yellow"],
                )
                notification_embed.add_field(
                    name="Phase Reset",
                    value="🔄 Lobby is now waiting for players",
                    inline=True,
                )
                notification_embed.add_field(
                    name="Your Options",
                    value="• Stay and wait for new players\n• Leave to join another lobby",
                    inline=True,
                )
                await player.send(embed=notification_embed)
            except Exception as e:
                logger.error(f"Failed to send timeout notification to {player}: {e}")

        # Reset lobby to phase 1 (waiting for players) instead of completely resetting
        # Check if we're resetting from map veto phase and need to clean up veto message
        was_in_veto_phase = lobby["phase"] == 3
        veto_message_to_delete = lobby.get("veto_message") if was_in_veto_phase else None
        
        lobby["phase"] = 1
        lobby["team1"] = []
        lobby["team2"] = []
        lobby["available_players"] = []
        lobby["next_pick"] = None
        lobby["veto_turn"] = None
        lobby["remaining_maps"] = []
        lobby["last_ban"] = None

        # Clean up veto message if we were in veto phase
        if veto_message_to_delete:
            try:
                await veto_message_to_delete.delete()
                logger.info(f"Deleted veto message for lobby #{lobby.get('lobby_number', 'X')} after captain timeout")
            except discord.NotFound:
                logger.info("Veto message was already deleted")
            except discord.Forbidden:
                logger.warning("No permission to delete veto message")
            except Exception as e:
                logger.warning(f"Could not delete veto message after captain timeout: {e}")
            # Clear the veto message reference
            lobby["veto_message"] = None

        # Update lobby status to show waiting for players again
        await self.update_lobby_status(lobby)

    @tasks.loop(seconds=30)
    async def cleanup_voice_channels(self):
        now = discord.utils.utcnow()
        if not hasattr(self, "_empty_since"):
            self._empty_since = {}
        for channel_id, empty_time in list(self._empty_since.items()):
            channel = self.bot.get_channel(int(channel_id))
            if not channel:
                del self._empty_since[channel_id]
                continue
            if len(channel.members) > 0:
                del self._empty_since[channel_id]
            elif (now - empty_time).total_seconds() > 300:
                if channel.name in [CT_VOICE_NAME, T_VOICE_NAME]:
                    try:
                        await channel.delete()
                    except Exception as e:
                        logger.error(f"Failed to delete channel {channel.name}: {e}")
                del self._empty_since[channel_id]
        for lobby in self.active_lobbies.values():
            for team in ["team1_voice", "team2_voice"]:
                channel = lobby.get(team)
                if (
                    channel
                    and channel.id not in self._empty_since
                    and len(channel.members) == 0
                ):
                    self._empty_since[channel.id] = now

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        for lobby in list(self.active_lobbies.values()):
            # CRITICAL FIX: Use the existing lobby lock to prevent race conditions during critical transitions
            # This prevents the issue where multiple players joining simultaneously could cause one player
            # to be incorrectly disconnected when the lobby transitions from phase 1 to phase 2
            async with self.lobby_locks[lobby["category"].guild.id]:
                if after.channel and after.channel.id == lobby["voice"].id:
                    if (
                        member not in lobby["players"]
                    ):  # Check if user is timeouted before allowing them to join
                        (
                            is_timeouted,
                            timeout_until,
                        ) = await timeout_manager.is_user_timeouted(member.id)
                        if is_timeouted:
                            try:
                                remaining_minutes = timeout_manager.get_remaining_time(
                                    member.id
                                )

                                # Create timeout notification embed
                                embed = discord.Embed(
                                    title=f"{Config.ICONS['clock']} Lobby Access Denied",
                                    description="You are currently timeouted and cannot join lobbies.",
                                    color=Config.EMBED_COLORS["red"],
                                )
                                embed.add_field(
                                    name="Time Remaining",
                                    value=f"{remaining_minutes} minute(s)",
                                    inline=True,
                                )
                                embed.add_field(
                                    name="Status",
                                    value=f"{Config.ICONS['not_allowed']} Lobby access blocked",
                                    inline=True,
                                )
                                embed.set_footer(
                                    text="You will be able to join lobbies again once the timeout expires."
                                )

                                await member.send(embed=embed)
                                # Disconnect the user from the voice channel
                                await member.move_to(None)
                            except Exception as e:
                                logger.error(
                                    f"Failed to handle timeouted user {member}: {e}"
                                )
                            continue  # Check if lobbies are locked and user has the required role for this lobby
                        if self.lobbies_locked:
                            # Get the required role for this specific lobby
                            required_role_id = lobby.get("required_role_id")
                            if required_role_id:
                                required_role = member.guild.get_role(int(required_role_id))
                            else:
                                # Fallback to Registered role for old lobbies
                                required_role = discord.utils.get(
                                    member.guild.roles, name=Config.REGISTERED_ROLE
                                )

                            if required_role and required_role in member.roles:
                                try:
                                    lobby_role_name = lobby.get(
                                        "required_role_name", Config.REGISTERED_ROLE
                                    )
                                    # Create lobby locked notification embed
                                    embed = discord.Embed(
                                        title=f"{Config.ICONS['lock']} Lobbies Currently Locked",
                                        description=f"All lobbies are currently locked by administrators. {lobby_role_name} players cannot connect to lobby voice channels at this time.",
                                        color=Config.EMBED_COLORS["red"],
                                    )
                                    embed.add_field(
                                        name="Status",
                                        value=f"{Config.ICONS['not_allowed']} Voice access temporarily restricted",
                                        inline=True,
                                    )
                                    embed.add_field(
                                        name="Access Level",
                                        value="👀 You can still view text channels",
                                        inline=True,
                                    )
                                    embed.set_footer(
                                        text="Please wait for lobbies to be unlocked by administrators."
                                    )

                                    await member.send(embed=embed)
                                    # Disconnect the user from the voice channel
                                    await member.move_to(None)
                                except Exception as e:
                                    logger.error(
                                        f"Failed to handle locked lobby access for {member}: {e}"
                                    )
                                continue

                        # Double-check lobby phase before allowing player addition (prevents race conditions)
                        # This ensures that if the lobby has already transitioned to draft/veto phase,
                        # new players are not added and are properly notified instead of being kicked
                        if lobby["phase"] != 1:
                            # Lobby has already started, don't allow new players
                            try:
                                embed = discord.Embed(
                                    title=f"{Config.ICONS['info']} Lobby Already Started",
                                    description="This lobby has already started the drafting phase. You cannot join at this time.",
                                    color=Config.EMBED_COLORS["red"],
                                )
                                embed.add_field(
                                    name="Current Phase",
                                    value=f"Phase {lobby['phase']} ({'Draft' if lobby['phase'] == 2 else 'Map Veto' if lobby['phase'] == 3 else 'In Progress'})",
                                    inline=True,
                                )
                                embed.set_footer(
                                    text="Please wait for the next lobby or create a new one."
                                )
                                await member.send(embed=embed)
                                # Disconnect the user from the voice channel
                                await member.move_to(None)
                            except Exception as e:
                                logger.error(
                                    f"Failed to notify user about lobby phase: {e}"
                                )
                            continue

                        lobby["players"].add(member)
                    # No longer calling update_lobby_status here to prevent frequent embed updates
                    # The embed is now static and only created once when the lobby is initialized
                    
                    # Atomic check and transition to captain selection - prevents multiple threads
                    # from simultaneously detecting 10/10 players and causing race conditions
                    if (
                        len(lobby["players"]) == Config.PLAYERS_REQUIRED
                        and lobby["phase"] == 1
                    ):
                        await self.start_captain_selection(lobby)
                if (
                    before.channel
                    and before.channel.id == lobby["voice"].id
                    and (not after.channel or after.channel.id != lobby["voice"].id)
                ):
                    if member in lobby["players"]:
                        lobby["players"].remove(member)
                    # No longer calling update_lobby_status here to prevent frequent embed updates
                    # Players leaving in phase 1 don't need to trigger embed updates
                    
                    if lobby["phase"] in [2, 3]:
                        # Check if user is already timeouted to prevent double timeout
                        is_already_timeouted, _ = await timeout_manager.is_user_timeouted(
                            member.id
                        )
                        if is_already_timeouted:
                            logger.info(
                                f"User {member.id} already timeouted, skipping mid-game leave timeout"
                            )
                            # Still reset lobby if needed, but don't apply another timeout
                            await self.reset_lobby(lobby, keep_voice_members=True)
                            continue

                        # Add timeout to database
                        success = await timeout_manager.add_timeout(
                            member.id,
                            timeout_minutes=2,
                            reason=f"Left during Phase {lobby['phase']} ({'Draft' if lobby['phase'] == 2 else 'Veto'})",
                        )

                        try:
                            if success:
                                # Create mid-game leave timeout embed
                                embed = discord.Embed(
                                    title=f"{Config.ICONS['clock']} Mid-Game Leave Penalty",
                                    description="You have been removed from the lobby due to leaving mid-game.",
                                    color=Config.EMBED_COLORS["red"],
                                )
                                embed.add_field(
                                    name="Phase Left During",
                                    value=f"Phase {lobby['phase']} ({'Draft' if lobby['phase'] == 2 else 'Veto'})",
                                    inline=False,
                                )
                                embed.add_field(
                                    name="Timeout Duration",
                                    value="2 minutes",
                                    inline=True,
                                )
                                embed.add_field(
                                    name="Status",
                                    value=f"{Config.ICONS['not_allowed']} Cannot join any lobbies during timeout",
                                    inline=True,
                                )
                                embed.set_footer(
                                    text="You will be able to join lobbies again once the timeout expires."
                                )

                                await member.send(embed=embed)
                            else:
                                # Fallback embed for database failure
                                embed = discord.Embed(
                                    title=f"{Config.ICONS['clock']} Mid-Game Leave Penalty",
                                    description="You have been removed from the lobby due to leaving mid-game.",
                                    color=Config.EMBED_COLORS["red"],
                                )
                                embed.add_field(
                                    name="Timeout Duration",
                                    value="2 minutes",
                                    inline=True,
                                )
                                embed.set_footer(
                                    text="Note: Timeout may not be properly tracked due to database issues."
                                )

                                await member.send(embed=embed)
                        except Exception as e:
                            logger.error(
                                f"Failed to send mid-game leave message to {member}: {e}"
                            )
                        await self.reset_lobby(lobby, keep_voice_members=True)

    async def update_lobby_status(self, lobby):
        """Update the lobby status embed - now only for initial setup and phase transitions"""
        # Check if lobby still exists before updating
        if lobby["category"].id not in self.active_lobbies:
            return

        # Additional safety check - ensure lobby components still exist
        try:
            if not lobby.get("category") or not lobby.get("text"):
                return
        except Exception:
            return

        # Only create initial embed if the lobby is in phase 1 and no message exists yet
        if lobby["phase"] == 1 and not lobby.get("message"):
            try:
                # Static embed without real-time player count to prevent frequent updates
                embed = discord.Embed(
                    title=f"LOBBY #{lobby.get('lobby_number', 'X')} - WAITING FOR PLAYERS",
                    description="Waiting for the lobby to fill...\n"
                               "Connect to the **Lobby** voice channel to join the queue!\n\n",
                    color=Config.EMBED_COLORS["waiting"],
                )
                if Config.LOBBIES_WAITING_BANNER:
                    embed.set_image(url=Config.LOBBIES_WAITING_BANNER)

                # Check if text channel still exists and we have permissions
                if (
                    not lobby["text"]
                    or not lobby["text"]
                    .permissions_for(lobby["text"].guild.me)
                    .send_messages
                ):
                    logger.warning(
                        f"Missing permissions or text channel for lobby #{lobby.get('lobby_number', 'unknown')}"
                    )
                    return

                try:
                    # Create new message only if it doesn't exist
                    lobby["message"] = await lobby["text"].send(embed=embed)
                except discord.Forbidden:
                    logger.warning(
                        f"Missing permissions to send message in lobby #{lobby.get('lobby_number', 'unknown')}"
                    )
                except Exception as e:
                    logger.error(f"Unexpected error creating initial lobby message: {e}")
                
                # Save lobby state when initially created
                await self.save_lobby_state(lobby["category"].id)

            except Exception as e:
                logger.error(
                    f"Error in update_lobby_status for lobby #{lobby.get('lobby_number', 'unknown')}: {e}"
                )

    async def format_team_list(self, team_members, league_name=None):
        """Format team list with level emojis for default league or league emojis for non-default leagues"""
        if not team_members:
            return "-"

        # Get the default league from config to determine if we should show level emojis
        default_league = await self.get_default_league_from_config()
        is_default_league = league_name == default_league if default_league else True

        # Get league emoji for non-default leagues
        league_emoji = None
        if not is_default_league and league_name:
            leagues_cog = self.get_leagues_cog()
            if leagues_cog:
                league_data = await leagues_cog.get_league_by_name(league_name)
                if league_data:
                    league_emoji = league_data.get("league_emoji")

        formatted_players = []
        for member in team_members:
            try:
                if is_default_league:
                    # Get user stats to determine points for level emoji
                    stats_result = await Stats.get_user_stats(member.id, league_name)
                    if stats_result and stats_result.get("success"):
                        stats_data = stats_result.get("stats", {})
                        points = stats_data.get("points", 0)
                        level_emoji = LevelsCog.get_level_emoji(points)
                        formatted_players.append(f"{level_emoji} {member.mention}")
                    else:
                        # Default emoji if user not found in league tables
                        default_emoji = LevelsCog.get_level_emoji(0)
                        formatted_players.append(f"{default_emoji} {member.mention}")
                else:
                    # Non-default league: show league emoji if it exists
                    if league_emoji:
                        formatted_players.append(f"{league_emoji} {member.mention}")
                    else:
                        # No emoji if league doesn't have one
                        formatted_players.append(member.mention)
            except Exception as e:
                logger.error(f"Error getting emoji for {member.display_name}: {e}")
                # Fallback - show player mention without emoji
                formatted_players.append(member.mention)

        return "\n".join(formatted_players)

    async def start_captain_selection(self, lobby):
        lobby["phase"] = 2
        players = list(lobby["players"])

        # Use party-based captain selection
        captains, party_info, party_groups = await self.select_party_based_captains(
            lobby
        )

        team1_captain, team2_captain = captains[0], captains[1]

        lobby["team1"] = [team1_captain]
        lobby["team2"] = [team2_captain]
        lobby["available_players"] = [p for p in players if p not in captains]

        # Auto-assign party members to their leader's teams
        await self.assign_party_members_to_teams(lobby, party_info, party_groups)

        # Only auto-assign the last remaining player if there's exactly 1 left
        # Let the draft process handle team balancing through multiple picks
        if len(lobby["available_players"]) == 1:
            last_player = lobby["available_players"][0]
            team1_size = len(lobby["team1"])
            team2_size = len(lobby["team2"])

            if team1_size <= team2_size:
                lobby["team1"].append(last_player)
            else:
                lobby["team2"].append(last_player)
            lobby["available_players"].remove(last_player)

        # Determine next pick based on team balance after party assignments
        # Set flag to indicate this is the start of draft (Team1 should go first if balanced)
        lobby["draft_start"] = True
        lobby["next_pick"] = await self.determine_next_pick_balanced(lobby)

        # --- New Draft UI --- 
        # Optimize: Get both team formats in parallel and cache league_table
        league_table = lobby.get("league_table")
        team1_lines, team2_lines = await asyncio.gather(
            self.format_team_list(lobby["team1"], league_table),
            self.format_team_list(lobby["team2"], league_table)
        )
        
        desc = (
            f"**Team 1**\n{team1_lines}\n\n" f"**Team 2**\n{team2_lines}\n\n"
        )

        if lobby["available_players"]:
            picks_needed = await self.calculate_picks_needed(lobby)
            if picks_needed > 1:
                desc += f"Captain {lobby['next_pick'].mention} needs to pick {picks_needed} players…"
            else:
                desc += f"Captain {lobby['next_pick'].mention} is currently picking…"
        else:
            desc += "All players have been assigned to teams."

        embed = discord.Embed(
            title="Drafting Players Phase",
            description=desc,
            color=Config.EMBED_COLORS["draft"],
        )
        embed.set_footer(text="Next Phase: Map Veto")

        if lobby["available_players"]:
            view = await self.get_draft_view(lobby)
            view = self.set_current_view(lobby, view)
        else:
            self.stop_current_view(lobby)
            view = None

        try:
            await lobby["message"].edit(embed=embed, view=view)
        except Exception as e:
            logger.error(f"Failed to update lobby message in start_captain_selection: {e}")
            return
            
        # If no players left to draft, immediately proceed to map veto
        if not lobby["available_players"]:
            await self.start_map_veto(lobby)

    async def start_map_veto(self, lobby):
        lobby["phase"] = 3
        lobby["veto_turn"] = lobby["team1"][0]  # Team1 starts
        lobby["remaining_maps"] = [map_info["name"] for map_info in Config.MAP_POOL]
        lobby["last_ban"] = None  # Track previous ban
        text = lobby["text"]
        # --- New Map Veto UI ---

        desc = ""
        if lobby["last_ban"]:
            banned_map_emoji = self.get_map_emoji(lobby["last_ban"]["map"])
            emoji_part = f"{banned_map_emoji} " if banned_map_emoji else ""
            desc += f"Captain {lobby['last_ban']['captain'].mention} has banned: {emoji_part}**{lobby['last_ban']['map']}**\n\n"

        # Create maps list with emojis
        maps_list = []
        for map_name in lobby["remaining_maps"]:
            map_emoji = self.get_map_emoji(map_name)
            if map_emoji:
                maps_list.append(f"{map_emoji} | **{map_name}**")
            else:
                maps_list.append(f"**{map_name}**")
        maps_str = "\n".join(maps_list)
        desc += f"{maps_str}\n\n"
        desc += f"Captain {lobby['veto_turn'].mention} is currently banning…"
        embed = discord.Embed(
            title="Map Veto Phase", description=desc, color=Config.EMBED_COLORS["veto"]
        )
        embed.set_footer(text="Next Phase: Final Match Data")
        view = self.get_veto_view(lobby)
        view = self.set_current_view(lobby, view)
        lobby["veto_message"] = await text.send(embed=embed, view=view)

    async def handle_map_ban(self, lobby, banned_map):
        async with self.lobby_locks[lobby["category"].guild.id]:
            # Track last ban
            lobby["last_ban"] = {"captain": lobby["veto_turn"], "map": banned_map}
            lobby["remaining_maps"].remove(banned_map)
            # Switch turn
            if lobby["veto_turn"] == lobby["team1"][0]:
                lobby["veto_turn"] = lobby["team2"][0]
            else:
                lobby["veto_turn"] = lobby["team1"][0]

            # --- New Map Veto UI ---
            banned_map_emoji = self.get_map_emoji(lobby["last_ban"]["map"])
            emoji_part = f"{banned_map_emoji} " if banned_map_emoji else ""
            desc = f"Captain {lobby['last_ban']['captain'].mention} has banned: {emoji_part}**{lobby['last_ban']['map']}**\n\n"

            # Create maps list with emojis
            maps_list = []
            for map_name in lobby["remaining_maps"]:
                map_emoji = self.get_map_emoji(map_name)
                if map_emoji:
                    maps_list.append(f"{map_emoji} | **{map_name}**")
                else:
                    maps_list.append(f"**{map_name}**")
            maps_str = "\n".join(maps_list)
            desc += f"{maps_str}\n\n"
            if len(lobby["remaining_maps"]) > 1:
                desc += f"Captain {lobby['veto_turn'].mention} is currently banning…"
            else:
                desc += "Map veto complete."
            embed = discord.Embed(
                title="Map Veto Phase",
                description=desc,
                color=Config.EMBED_COLORS["veto"],
            )
            embed.set_footer(text="Next Phase: Final Match Data")
            if len(lobby["remaining_maps"]) > 1:
                view = self.get_veto_view(lobby)
                view = self.set_current_view(lobby, view)
            else:
                self.stop_current_view(lobby)
                view = None
            await lobby["veto_message"].edit(embed=embed, view=view)
            if len(lobby["remaining_maps"]) == 1:
                await self.finalize_match(lobby)

    async def finalize_match(self, lobby):
        # Stop any active view to prevent timeout issues  
        self.stop_current_view(lobby)
        
        import os
        import tempfile
        import datetime
        from assets.template_generators.final_match_generator import FinalMatchTemplateGenerator

        lobby["phase"] = 4
        team1_players = [m.display_name for m in lobby["team1"]]
        team2_players = [m.display_name for m in lobby["team2"]]
        final_map = lobby["remaining_maps"][0]
        now = datetime.datetime.now().strftime("%H:%M | %d/%m/%Y")

        # Create match in Supabase first to get the auto-generated ID
        match_result = await self.add_match_to_supabase(lobby, final_map)
        match_id = f"Match #{match_result['match_id']}"
        lobby["match_id"] = match_id

        # Generate image using Pillow
        generator = FinalMatchTemplateGenerator()
        image_path = os.path.join(
            tempfile.gettempdir(), f"final_match_{match_result['match_id']}.webp"
        )
        
        success = await generator.generate_final_match_image(
            match_id=match_id,
            team1_players=lobby["team1"],  # Pass Discord Members directly
            team2_players=lobby["team2"],  # Pass Discord Members directly
            team1_captain=lobby["team1"][0],
            team2_captain=lobby["team2"][0],
            map_name=final_map,
            output_path=image_path
        )
        
        if not success:
            logger.error("Failed to generate final match image")
            # Fallback: create a simple text embed without image
            embed = discord.Embed(
                title=f"{Config.APP_NAME} | {match_id}",
                description=f"**Map:** {final_map}\n**Team 1 Captain:** {lobby['team1'][0].display_name}\n**Team 2 Captain:** {lobby['team2'][0].display_name}",
                color=Config.EMBED_COLORS["final"]
            )
            embed.set_footer(text=f"Match Created: {now}")
            
            logger.info("🟣 Sending fallback embed without view...")
            await lobby["veto_message"].edit(embed=embed, view=None)
        else:
            logger.info("🟣 Creating Discord.File object...")
            # Upload image to Discord CDN by sending it as a file
            file = discord.File(image_path, filename="final_match.webp")
            
            logger.info("🟣 Creating embed...")
            embed = discord.Embed(
                title=f"{Config.APP_NAME} | {match_id}", color=Config.EMBED_COLORS["final"]
            )
            embed.set_image(url=f"attachment://final_match.webp")
            embed.set_footer(text=f"Match Created: {now}")

            logger.info("🟣 Sending embed with attachment to Discord...")
            await lobby["veto_message"].edit(
                embed=embed, view=None, attachments=[file]
            )
        
        # Mark as finalized immediately after sending embed
        lobby["just_finalized"] = True
        
        # Log timing pentru debugging
        logger.info("🟣 Embed sent successfully, sending host ID via DM...")
        
        # Trimite host ID prin DM în background (non-blocking)
        asyncio.create_task(self.send_host_id_to_players(lobby))
        
        logger.info("🟣 Starting background operations...")
        
        # IMPORTANT: Trebuie să facem voice channel operations ÎNAINTE de reset
        # pentru că reset-ul șterge echipele
        start_time = time.time()
        
        try:
            # Primul pas: creăm voice channels și mutăm jucătorii
            await self.create_team_voice_channels(lobby)
            voice_time = time.time() - start_time
            logger.info(f"🟣 Voice channels created and players moved in {voice_time:.2f}s")
            
            # Al doilea pas: facem reset și save în paralel
            reset_start = time.time()
            await asyncio.gather(
                self.reset_lobby(lobby, keep_voice_members=False),
                self.save_lobby_state(lobby["category"].id),
                return_exceptions=True
            )
            reset_time = time.time() - reset_start
            logger.info(f"🟣 Reset and save completed in {reset_time:.2f}s")
            
        except Exception as e:
            logger.error(f"🟣 Error in background operations: {e}")
            # Fallback: do operations sequentially if parallel fails
            try:
                await self.reset_lobby(lobby, keep_voice_members=False)
                await self.save_lobby_state(lobby["category"].id)
                logger.info("🟣 Fallback sequential operations completed")
            except Exception as fallback_error:
                logger.error(f"🟣 Even fallback operations failed: {fallback_error}")
        
        total_time = time.time() - start_time
        logger.info(f"🟣 All background operations completed in {total_time:.2f}s")

    async def send_host_id_to_players(self, lobby):
        """Trimite ID-ul host-ului prin DM la toți jucătorii"""
        try:
            all_players = lobby["team1"] + lobby["team2"]
            if not all_players:
                logger.warning("No players found to send host ID")
                return
                
            # Selectează random un host
            import random
            host_player = random.choice(all_players)
            
            logger.info(f"🟣 Selected host: {host_player.display_name}")
            
            # Obține datele host-ului din baza de date
            from models.user import User
            host_data = await User.find(host_player.id)
            
            if not host_data:
                logger.warning(f"Host {host_player.display_name} not found in database")
                return
                
            so2_id = host_data.get('so2_id')
            if not so2_id:
                logger.warning(f"Host {host_player.display_name} has no SO2 ID")
                return
            
            logger.info(f"🟣 Sending host ID ({so2_id}) to {len(all_players)} players via DM...")
            
            # Trimite DM la toți jucătorii
            successful_dms = 0
            for player in all_players:
                try:
                    embed = discord.Embed(
                        title=f"{Config.ICONS['crosshair']} Match Host Information",
                        description=f"**{Config.ICONS['game']} Lobby Host:** {host_player.display_name}\n**{Config.ICONS["id"]} SO2 ID:** `{so2_id}`\n\nYou can copy the ID above to invite the host\nOn mobile? Tap the ID to copy it",
                        color=Config.MAIN_COLOR
                    )
                    embed.set_footer(text="Good luck in your match!")
                    
                    await player.send(embed=embed)
                    successful_dms += 1
                except Exception as e:
                    logger.error(f"Failed to send host ID DM to {player.display_name}: {e}")
            
            logger.info(f"🟣 Successfully sent host ID to {successful_dms}/{len(all_players)} players")
            
        except Exception as e:
            logger.error(f"Error in send_host_id_to_players: {e}")

    async def add_match_to_supabase(self, lobby, final_map):
        try:
            # Get the league table name from the lobby
            league_table = lobby.get("league_table")
            result = await Match.create(
                lobby["team1"], lobby["team2"], lobby["team1"][0], lobby["team2"][0], final_map, league_table
            )
            return result.data[0]  # Return the first (and only) inserted record
        except Exception as e:
            logger.error(f"Failed to insert match into supabase: {e}")
            raise

    async def create_team_voice_channels(self, lobby):
        category = lobby["category"]
        guild = category.guild
        # Set permissions so only team members, the bot, and optionally admins can see/connect
        overwrites_ct = {
            guild.default_role: discord.PermissionOverwrite(
                view_channel=False, connect=False
            ),
            guild.me: discord.PermissionOverwrite(
                view_channel=True, connect=True, speak=True
            ),
        }
        overwrites_t = {
            guild.default_role: discord.PermissionOverwrite(
                view_channel=False, connect=False
            ),
            guild.me: discord.PermissionOverwrite(
                view_channel=True, connect=True, speak=True
            ),
        }
        # Optionally allow admins to join for moderation
        for role in guild.roles:
            if role.permissions.administrator:
                overwrites_ct[role] = discord.PermissionOverwrite(
                    view_channel=True, connect=True, speak=True
                )
                overwrites_t[role] = discord.PermissionOverwrite(
                    view_channel=True, connect=True, speak=True
                )
        for member in lobby["team1"]:
            overwrites_ct[member] = discord.PermissionOverwrite(
                view_channel=True, connect=True, speak=True
            )
        for member in lobby["team2"]:
            overwrites_t[member] = discord.PermissionOverwrite(
                view_channel=True, connect=True, speak=True
            )
        # Create channels with correct permissions
        ct_voice = await category.create_voice_channel(
            CT_VOICE_NAME, overwrites=overwrites_ct
        )
        t_voice = await category.create_voice_channel(
            T_VOICE_NAME, overwrites=overwrites_t
        )
        lobby["team1_voice"] = ct_voice
        lobby["team2_voice"] = t_voice
        # Move players to their team channels
        for member in lobby["team1"]:
            try:
                await member.move_to(ct_voice)
            except Exception as e:
                logger.error(f"Failed to move {member} to Team1 Voice: {e}")
        for member in lobby["team2"]:
            try:
                await member.move_to(t_voice)
            except Exception as e:
                logger.error(f"Failed to move {member} to Team2 Voice: {e}")
        # Start cleanup timer (placeholder)
        # ...

    async def reset_lobby(self, lobby, keep_voice_members=False):
        # Stop any active view to prevent timeout issues
        self.stop_current_view(lobby)
        
        # Check if we're resetting from map veto phase and need to clean up veto message
        was_in_veto_phase = lobby["phase"] == 3
        veto_message_to_delete = lobby.get("veto_message") if was_in_veto_phase else None
        
        lobby["phase"] = 1
        if keep_voice_members:
            voice_members = set(lobby["voice"].members)
            lobby["players"] = voice_members
        else:
            lobby["players"] = set()
        lobby["team1"] = []
        lobby["team2"] = []
        lobby["map_pool"] = [map_info["name"] for map_info in Config.MAP_POOL]
        lobby["match_id"] = None
        
        # Clean up veto message if we were in veto phase and it hasn't been finalized yet
        if veto_message_to_delete and not lobby.get("just_finalized"):
            try:
                await veto_message_to_delete.delete()
                logger.info(f"Deleted veto message for lobby #{lobby.get('lobby_number', 'X')} during reset")
            except discord.NotFound:
                logger.info("Veto message was already deleted")
            except discord.Forbidden:
                logger.warning("No permission to delete veto message")
            except Exception as e:
                logger.warning(f"Could not delete veto message during reset: {e}")
            # Clear the veto message reference
            lobby["veto_message"] = None
        
        text = lobby["text"]
        # Only create a new waiting embed if this is the first time or after a match (not on every reset)
        if not lobby.get("message") or lobby.pop("just_finalized", False):
            embed = discord.Embed(
                title=f"LOBBY #{lobby.get('lobby_number', 'X')} - WAITING FOR PLAYERS",
                description="Waiting for the lobby to fill...\n"
                           "Connect to the **Lobby** voice channel to join the queue!\n\n",
                color=Config.EMBED_COLORS["waiting"],
            )
            if Config.LOBBIES_WAITING_BANNER:
                embed.set_image(url=Config.LOBBIES_WAITING_BANNER)
            if lobby.get("message"):
                try:
                    await lobby["message"].delete()
                except discord.NotFound:
                    logger.info("Lobby message was already deleted")
                except discord.Forbidden:
                    logger.warning("No permission to delete lobby message")
                except Exception as e:
                    logger.warning(f"Could not delete old lobby message: {e}")
            lobby["message"] = await text.send(embed=embed)
        else:
            # Update existing message with proper waiting phase embed properties
            embed = discord.Embed(
                title=f"LOBBY #{lobby.get('lobby_number', 'X')} - WAITING FOR PLAYERS",
                description="Waiting for the lobby to fill...\n"
                           "Connect to the **Lobby** voice channel to join the queue!\n\n",
                color=Config.EMBED_COLORS["waiting"],
            )
            if Config.LOBBIES_WAITING_BANNER:
                embed.set_image(url=Config.LOBBIES_WAITING_BANNER)
            await lobby["message"].edit(embed=embed, view=None)
        if hasattr(lobby, "_just_finalized"):
            del lobby._just_finalized  # Save lobby state after reset
        await self.save_lobby_state(lobby["category"].id)

    # --- LOBBY PERSISTENCE METHODS ---

    async def save_lobby_state(self, category_id: int):
        """Save lobby state to database"""
        try:
            logger.info(f"Attempting to save lobby state for category {category_id}")
            lobby = self.active_lobbies.get(category_id)
            if lobby:
                logger.info(
                    f"Found lobby data for category {category_id}, saving to database..."
                )
                await LobbyState.save_lobby(
                    lobby["category"].guild.id, category_id, lobby, self.lobbies_locked
                )
                logger.info(
                    f"Successfully saved lobby state for category {category_id}"
                )
            else:
                logger.warning(
                    f"No lobby found in active_lobbies for category {category_id}"
                )
        except Exception as e:
            logger.error(f"Failed to save lobby state for category {category_id}: {e}")
            raise  # Re-raise to see the full traceback

    async def restore_lobbies(self):
        """Restore lobbies from database on bot startup"""
        try:
            # Wait for bot to be ready
            await self.bot.wait_until_ready()

            # Get all guilds the bot is in
            for guild in self.bot.guilds:
                try:
                    lobby_states = await LobbyState.load_lobbies(guild.id)

                    # Load lobby lock status from database
                    self.lobbies_locked = await LobbyState.get_lobby_lock_status(
                        guild.id
                    )
                    logger.info(
                        f"Loaded lobby lock status for {guild.name}: {'Locked' if self.lobbies_locked else 'Unlocked'}"
                    )

                    for state in lobby_states:
                        try:
                            await self.restore_single_lobby(guild, state)
                        except Exception as e:
                            logger.error(
                                f"Failed to restore lobby {state.get('category_id')}: {e}"
                            )
                            # Clean up failed lobby from database
                            try:
                                await LobbyState.delete_lobby(int(state["category_id"]))
                            except:
                                pass

                    logger.info(
                        f"Restored {len(lobby_states)} lobbies for guild {guild.name}"
                    )

                except Exception as e:
                    logger.error(f"Failed to restore lobbies for guild {guild.id}: {e}")

                    # Update lobby counter to avoid conflicts
            if self.active_lobbies:
                max_lobby_num = max(
                    lobby.get("lobby_number", 0)
                    for lobby in self.active_lobbies.values()
                )
                self.lobby_counter = max_lobby_num + 1

            logger.info(
                f"Lobby restoration complete. Active lobbies: {len(self.active_lobbies)}"
            )

            # Clean up any orphaned team voice channels
            await self.cleanup_orphaned_team_channels()

        except Exception as e:
            logger.error(f"Failed to restore lobbies: {e}")

    async def restore_single_lobby(self, guild: discord.Guild, state: dict):
        """Restore a single lobby from saved state"""
        try:
            import json

            # Get Discord objects
            category = guild.get_channel(int(state["category_id"]))
            if not category:
                logger.warning(
                    f"Category {state['category_id']} not found, skipping lobby restore"
                )
                await LobbyState.delete_lobby(int(state["category_id"]))
                return

            voice_channel = (
                guild.get_channel(int(state["voice_channel_id"]))
                if state["voice_channel_id"]
                else None
            )
            text_channel = (
                guild.get_channel(int(state["text_channel_id"]))
                if state["text_channel_id"]
                else None
            )

            if not voice_channel or not text_channel:
                logger.warning(
                    f"Voice or text channel missing for lobby {state['category_id']}, skipping restore"
                )
                await LobbyState.delete_lobby(int(state["category_id"]))
                return

            # Restore message
            message = None
            if state.get("message_id"):
                try:
                    message = await text_channel.fetch_message(int(state["message_id"]))
                except:
                    # Create new message if old one doesn't exist
                    embed = discord.Embed(
                        title="LOBBY - WAITING FOR PLAYERS",
                        description="Waiting for the lobby to fill...\n"
                                   "Connect to the **Lobby** voice channel to join the queue!\n\n",
                        color=Config.EMBED_COLORS["waiting"],
                    )
                    if Config.LOBBIES_WAITING_BANNER:
                        embed.set_image(url=Config.LOBBIES_WAITING_BANNER)
                    message = await text_channel.send(embed=embed)

            # Get player objects (only for players currently in voice)
            current_players = (
                set(voice_channel.members) if voice_channel else set()
            )  # Restore lobby data
            restored_lobby = {
                "category": category,
                "voice": voice_channel,
                "text": text_channel,
                "phase": 1,  # Always reset to waiting phase on restore
                "players": current_players,
                "message": message,
                "team1": [],
                "team2": [],
                "map_pool": json.loads(state.get("map_pool", "[]"))
                or [map_info["name"] for map_info in Config.MAP_POOL],
                "match_id": None,  # Clear match ID on restore
                "team1_voice": None,
                "team2_voice": None,
                "lobby_number": state.get("lobby_number", 1),
                "required_role_id": state.get("required_role_id"),
                "required_role_name": state.get(
                    "required_role_name", Config.REGISTERED_ROLE
                ),
                "league_table": state.get("league"),  # Restore league table name
            }

            # Clean up team voice channels if they exist
            if state.get("ct_voice_id"):
                ct_voice = guild.get_channel(int(state["ct_voice_id"]))
                if ct_voice:
                    try:
                        await ct_voice.delete()
                    except:
                        pass

            if state.get("t_voice_id"):
                t_voice = guild.get_channel(int(state["t_voice_id"]))
                if t_voice:
                    try:
                        await t_voice.delete()
                    except:
                        pass

            # Add to active lobbies
            self.active_lobbies[category.id] = restored_lobby

            # Update status message
            await self.update_lobby_status(restored_lobby)
            logger.info(
                f"Successfully restored lobby {state['category_id']} with {len(current_players)} players"
            )

        except Exception as e:
            logger.error(f"Failed to restore lobby {state.get('category_id')}: {e}")
            raise

    async def delete_lobby_state(self, category_id: int):
        """Delete lobby state from database"""
        try:
            await LobbyState.delete_lobby(category_id)
            logger.info(f"Deleted lobby state for category {category_id}")
        except Exception as e:
            # Log as warning instead of error since this might be normal (lobby already deleted)
            logger.warning(
                f"Could not delete lobby state for category {category_id}: {e}"
            )

    @app_commands.command(
        name="cleanup_lobbies",
        description="Admin command to clean up old lobby states.",
    )
    @app_commands.default_permissions(administrator=True)
    async def cleanup_lobbies(self, interaction: discord.Interaction, days: int = 7):
        """Clean up lobby states older than specified days"""
        try:
            result = await LobbyState.cleanup_old_lobbies(days)

            # Create success embed
            embed = discord.Embed(
                title=f"{Config.ICONS['success']} Lobby Cleanup Completed",
                description=f"Successfully cleaned up old lobby states from the database.",
                color=Config.EMBED_COLORS["light_green"],
            )
            embed.add_field(name="Cleanup Period", value=f"{days} days", inline=True)
            embed.add_field(name="Status", value="All old states removed", inline=True)
            embed.set_footer(text="Database has been optimized and cleaned up.")

            await interaction.response.send_message(embed=embed, ephemeral=True)
        except Exception as e:
            logger.error(f"Failed to cleanup old lobbies: {e}")

            # Create error embed
            embed = discord.Embed(
                title=f"{Config.ICONS['unsuccess']} Cleanup Failed",
                description="An error occurred while cleaning up old lobby states.",
                color=Config.EMBED_COLORS["red"],
            )
            embed.add_field(
                name="Error", value="Database cleanup operation failed", inline=False
            )
            embed.set_footer(text="Please try again or contact an administrator.")

            await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name="delete_lobby",
        description="Admin command to delete a lobby and its channels.",
    )
    @app_commands.default_permissions(administrator=True)
    async def delete_lobby(self, interaction: discord.Interaction, lobby_number: int):
        """Delete a lobby and clean up its state"""
        # Verifică autorizația utilizatorului
        if not await Config.check_authorization(interaction):
            return
            
        # Defer the interaction immediately to prevent timeout
        await interaction.response.defer(ephemeral=True)

        try:
            # Find the lobby by number
            target_lobby = None
            target_category_id = None

            for category_id, lobby in self.active_lobbies.items():
                if lobby.get("lobby_number") == lobby_number:
                    target_lobby = lobby
                    target_category_id = category_id
                    break

            if not target_lobby:
                # Create not found embed
                embed = discord.Embed(
                    title=f"{Config.ICONS['info']} Lobby Not Found",
                    description=f"**Lobby #{lobby_number}** could not be found.",
                    color=Config.EMBED_COLORS["yellow"],
                )
                embed.add_field(
                    name="Suggestion",
                    value="Check the lobby number and try again",
                    inline=False,
                )
                embed.set_footer(
                    text="Use /cleanup_lobbies to remove old lobby states."
                )

                await interaction.followup.send(embed=embed, ephemeral=True)
                return

            # Notify players
            for member in target_lobby["players"]:
                try:
                    await member.send(
                        f"Lobby #{lobby_number} has been deleted by an administrator."
                    )
                except Exception:
                    pass

            # Delete all channels within the lobby's category
            category = target_lobby["category"]

            # Get all channels in the category (voice and text channels)
            channels_to_delete = []
            for channel in category.channels:
                channels_to_delete.append(channel)

            # Delete all channels within the category
            for channel in channels_to_delete:
                try:
                    await channel.delete()
                    logger.info(f"Deleted channel: {channel.name}")
                    await asyncio.sleep(0.5)  # Small delay between deletions
                except discord.NotFound:
                    logger.info(f"Channel {channel.name} was already deleted")
                except discord.Forbidden:
                    logger.warning(f"No permission to delete channel {channel.name}")
                except Exception as e:
                    logger.error(
                        f"Failed to delete channel {channel.name}: {e}"
                    )  # Finally delete the category
            try:
                await target_lobby["category"].delete()
                logger.info(f"Deleted category for lobby #{lobby_number}")
            except discord.NotFound:
                logger.info(f"Category for lobby #{lobby_number} was already deleted")
            except discord.Forbidden:
                logger.warning(f"No permission to delete category for lobby #{lobby_number}")
            except Exception as e:
                logger.error(f"Failed to delete category for lobby #{lobby_number}: {e}")
                # Don't raise here, continue with cleanup

            # Remove from active lobbies first to prevent other operations
            if target_category_id in self.active_lobbies:
                del self.active_lobbies[target_category_id]

            # Delete from database (do this early to avoid state issues)
            await self.delete_lobby_state(target_category_id)

            # Create success embed
            embed = discord.Embed(
                title=f"{Config.ICONS['success']} Lobby Deleted Successfully",
                description=f"**Lobby #{lobby_number}** has been permanently deleted.",
                color=Config.EMBED_COLORS["light_green"],
            )
            embed.add_field(
                name="Channels Deleted",
                value=f"{len(channels_to_delete)} channels",
                inline=True,
            )
            embed.set_footer(
                text="All lobby data has been cleaned up from the database."
            )

            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as e:
            logger.error(f"Failed to delete lobby {lobby_number}: {e}")
            try:
                # Create error embed
                embed = discord.Embed(
                    title=f"{Config.ICONS['unsuccess']} Failed to Delete Lobby",
                    description=f"An error occurred while deleting **Lobby #{lobby_number}**.",
                    color=Config.EMBED_COLORS["red"],
                )
                embed.add_field(
                    name="Error", value="Lobby deletion operation failed", inline=False
                )
                embed.set_footer(text="Please try again or contact an administrator.")

                await interaction.followup.send(embed=embed, ephemeral=True)
            except Exception:
                # If followup also fails, just log it
                logger.error(f"Failed to send error message for lobby {lobby_number}")

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel):
        """Handle when channels are deleted externally"""
        # Check if this was a lobby category
        if channel.type == discord.ChannelType.category:
            if channel.id in self.active_lobbies:
                logger.info(f"Lobby category {channel.id} was deleted externally")
                del self.active_lobbies[channel.id]
                await self.delete_lobby_state(channel.id)

    @app_commands.command(
        name="cleanup_team_channels",
        description="Admin command to clean up orphaned team voice channels.",
    )
    @app_commands.default_permissions(administrator=True)
    async def cleanup_team_channels_command(self, interaction: discord.Interaction):
        """Manually trigger cleanup of orphaned team voice channels"""
        await interaction.response.defer(ephemeral=True)

        try:
            # Get all currently tracked team voice channel IDs
            tracked_team_channels = set()
            for lobby in self.active_lobbies.values():
                if lobby.get("ct_voice"):
                    tracked_team_channels.add(lobby["ct_voice"].id)
                if lobby.get("t_voice"):
                    tracked_team_channels.add(lobby["t_voice"].id)

            total_deleted = 0
            guild_summary = []

            # Check all guilds
            for guild in self.bot.guilds:
                guild_deleted = 0
                try:
                    # Find all CT/T voice channels in the guild
                    team_channels = []
                    for channel in guild.voice_channels:
                        if channel.name in [CT_VOICE_NAME, T_VOICE_NAME]:
                            team_channels.append(channel)

                    # Delete orphaned team channels
                    for channel in team_channels:
                        if channel.id not in tracked_team_channels:
                            try:
                                await channel.delete()
                                total_deleted += 1
                                guild_deleted += 1
                                # Small delay to prevent rate limiting
                                await asyncio.sleep(0.5)
                            except discord.NotFound:
                                # Channel was already deleted
                                pass
                            except Exception as e:
                                logger.error(
                                    f"Failed to delete orphaned team channel {channel.name}: {e}"
                                )

                    if guild_deleted > 0:
                        guild_summary.append(
                            f"**{guild.name}**: {guild_deleted} channels"
                        )

                except Exception as e:
                    logger.error(
                        f"Failed to cleanup orphaned channels in guild {guild.id}: {e}"
                    )

            # Create response embed
            if total_deleted > 0:
                embed = discord.Embed(
                    title=f"{Config.ICONS['success']} Team Channels Cleaned Up",
                    description=f"Successfully deleted **{total_deleted}** orphaned team voice channels.",
                    color=Config.EMBED_COLORS["light_green"],
                )

                if guild_summary:
                    embed.add_field(
                        name="Cleanup Summary",
                        value="\n".join(
                            guild_summary[:10]
                        ),  # Limit to 10 guilds to avoid embed limits
                        inline=False,
                    )

                embed.set_footer(
                    text="All orphaned CT Voice and T Voice channels have been removed."
                )
            else:
                embed = discord.Embed(
                    title=f"{Config.ICONS['success']} No Cleanup Needed",
                    description="No orphaned team voice channels were found.",
                    color=Config.EMBED_COLORS["light_green"],
                )
                embed.set_footer(text="All team voice channels are properly tracked.")

            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as e:
            logger.error(f"Failed to cleanup team channels: {e}")
            embed = discord.Embed(
                title=f"{Config.ICONS['unsuccess']} Cleanup Failed",
                description="An error occurred while cleaning up team voice channels.",
                color=Config.EMBED_COLORS["red"],
            )
            embed.add_field(
                name="Error",
                value="Team channel cleanup operation failed",
                inline=False,
            )
            embed.set_footer(text="Please try again or contact an administrator.")
            await interaction.followup.send(embed=embed, ephemeral=True)

    async def cleanup_orphaned_team_channels(self):
        """Clean up orphaned CT/T voice channels that don't belong to any active lobby"""
        try:
            logger.info("Starting cleanup of orphaned team voice channels...")

            # Get all currently tracked team voice channel IDs
            tracked_team_channels = set()
            for lobby in self.active_lobbies.values():
                if lobby.get("ct_voice"):
                    tracked_team_channels.add(lobby["ct_voice"].id)
                if lobby.get("t_voice"):
                    tracked_team_channels.add(lobby["t_voice"].id)

            total_deleted = 0

            # Check all guilds
            for guild in self.bot.guilds:
                try:
                    # Find all CT/T voice channels in the guild
                    team_channels = []
                    for channel in guild.voice_channels:
                        if channel.name in [CT_VOICE_NAME, T_VOICE_NAME]:
                            team_channels.append(channel)

                    # Delete orphaned team channels
                    for channel in team_channels:
                        if channel.id not in tracked_team_channels:
                            try:
                                logger.info(
                                    f"Deleting orphaned team channel: {channel.name} ({channel.id}) in {guild.name}"
                                )
                                await channel.delete()
                                total_deleted += 1
                                # Small delay to prevent rate limiting
                                await asyncio.sleep(0.5)
                            except discord.NotFound:
                                # Channel was already deleted
                                pass
                            except Exception as e:
                                logger.error(
                                    f"Failed to delete orphaned team channel {channel.name}: {e}"
                                )

                except Exception as e:
                    logger.error(
                        f"Failed to cleanup orphaned channels in guild {guild.id}: {e}"
                    )

            if total_deleted > 0:
                logger.info(
                    f"Cleanup complete: Deleted {total_deleted} orphaned team voice channels"
                )
            else:
                logger.info("Cleanup complete: No orphaned team voice channels found")

        except Exception as e:
            logger.error(f"Failed to cleanup orphaned team channels: {e}")

    async def get_default_league_from_config(self, max_retries=3):
        """Get the default league from the config table with retry logic"""
        import asyncio

        loop = asyncio.get_running_loop()

        def db_get():
            for attempt in range(max_retries):
                try:
                    response = (
                        Config.supabase().table("config").select("default_league").execute()
                    )
                    if response.data and len(response.data) > 0:
                        return response.data[0].get("default_league")
                    return None
                except Exception as e:
                    if attempt < max_retries - 1:
                        # Wait before retry with exponential backoff
                        import time
                        wait_time = (2 ** attempt) * 0.5  # 0.5s, 1s, 2s
                        logger.warning(f"Database connection failed (attempt {attempt + 1}/{max_retries}), retrying in {wait_time}s: {e}")
                        time.sleep(wait_time)
                    else:
                        logger.error(f"Failed to get default league from config after {max_retries} attempts: {e}")
                        return None
            return None

        return await loop.run_in_executor(None, db_get)

    async def create_draft_options_with_stats(
        self, available_players, league_name=None
    ):
        """Create dropdown options with player stats as descriptions filtered by league"""
        options = []

        # Get the default league from config to determine if we should show level emojis
        default_league = await self.get_default_league_from_config()
        is_default_league = league_name == default_league if default_league else True

        # Get league emoji for non-default leagues
        league_emoji = None
        if not is_default_league and league_name:
            leagues_cog = self.get_leagues_cog()
            if leagues_cog:
                league_data = await leagues_cog.get_league_by_name(league_name)
                if league_data:
                    league_emoji = league_data.get("league_emoji")

        for member in available_players:
            try:
                # Get user stats from the specific league table
                stats_result = await Stats.get_user_stats(member.id, league_name)
                if stats_result and stats_result.get("success"):
                    stats_data = stats_result.get("stats", {})
                    points = stats_data.get("points", 0)
                    kills = stats_data.get("kills", 0)
                    deaths = stats_data.get("deaths", 0)
                    wins = stats_data.get("wins", 0)
                    losses = stats_data.get("loses", 0)

                    # Calculate K/D ratio with proper handling for zero deaths
                    if deaths == 0:
                        kd_ratio = kills if kills > 0 else 0.0
                    else:
                        kd_ratio = kills / deaths

                    # Format K/D ratio to 2 decimal places
                    kd_formatted = f"{kd_ratio:.2f}"

                    # Calculate average kills per match
                    total_matches = wins + losses
                    if total_matches == 0:
                        avg_kills = 0.0
                    else:
                        avg_kills = kills / total_matches

                    # Format average kills to 1 decimal place
                    avg_formatted = f"{avg_kills:.1f}"

                    description = (
                        f"Points: {points} | K/D: {kd_formatted} | AVG {avg_formatted}"
                    )

                    # Determine which emoji to show
                    if is_default_league:
                        # Show level emoji for default league
                        emoji_to_show = LevelsCog.get_level_emoji(points)
                    else:
                        # Show league emoji for non-default leagues (if it exists)
                        emoji_to_show = league_emoji
                else:
                    # Fallback if no stats found in league tables
                    description = "No stats available"
                    if is_default_league:
                        emoji_to_show = LevelsCog.get_level_emoji(0)
                    else:
                        emoji_to_show = league_emoji

                options.append(
                    discord.SelectOption(
                        label=member.display_name,
                        value=str(member.id),
                        description=description,
                        emoji=emoji_to_show,
                    )
                )
            except Exception as e:
                # Fallback in case of error
                logger.error(f"Error fetching stats for {member.display_name}: {e}")

                # Determine fallback emoji
                if is_default_league:
                    fallback_emoji = LevelsCog.get_level_emoji(0)
                else:
                    fallback_emoji = league_emoji

                options.append(
                    discord.SelectOption(
                        label=member.display_name,
                        value=str(member.id),
                        description="Stats unavailable",
                        emoji=fallback_emoji,
                    )
                )
        return options

    # --- LOBBY LOCKING COMMANDS ---

    @app_commands.command(
        name="lock_lobbies",
        description="Admin command to lock all lobbies - prevents Registered role from connecting to voice.",
    )
    @app_commands.default_permissions(administrator=True)
    async def lock_lobbies(self, interaction: discord.Interaction):
        """Lock all lobbies to prevent Registered role from connecting"""
        # Verifică autorizația utilizatorului
        if not await Config.check_authorization(interaction):
            return
            
        await interaction.response.defer(ephemeral=True)

        try:
            if self.lobbies_locked:
                embed = discord.Embed(
                    title=f"{Config.ICONS['info']} Lobbies Already Locked",
                    description="Lobbies are already locked. Registered players cannot connect to lobby voice channels.",
                    color=Config.EMBED_COLORS["yellow"],
                )
                embed.set_footer(text="Use /unlock_lobbies to restore access.")
                await interaction.followup.send(embed=embed, ephemeral=True)
                return  # Set locked status
            self.lobbies_locked = True

            # Persist lock status to database
            await LobbyState.update_lobby_lock_status(
                interaction.guild.id, True
            )  # Update permissions for all active lobbies
            lobbies_updated = 0
            for lobby in self.active_lobbies.values():
                try:
                    # Get the required role for this specific lobby
                    required_role_id = lobby.get("required_role_id")
                    if required_role_id:
                        lobby_role = interaction.guild.get_role(int(required_role_id))
                    else:
                        # Fallback to Registered role for old lobbies
                        lobby_role = discord.utils.get(
                            interaction.guild.roles, name=Config.REGISTERED_ROLE
                        )

                    if not lobby_role:
                        logger.warning(
                            f"Could not find required role for lobby {lobby.get('lobby_number')}"
                        )
                        continue

                    # Update voice and text channel permissions
                    voice_channel = lobby.get("voice")
                    text_channel = lobby.get("text")
                    if voice_channel:
                        await voice_channel.set_permissions(
                            lobby_role,
                            view_channel=True,
                            connect=False,  # Remove connect permission
                            send_messages=False,  # Prevent sending messages in text
                            read_messages=True,
                            reason="Lobbies locked by administrator",
                        )
                    if text_channel:
                        await text_channel.set_permissions(
                            lobby_role,
                            view_channel=True,
                            send_messages=False,  # Prevent sending messages in text
                            read_messages=True,
                            reason="Lobbies locked by administrator",
                        )
                        lobbies_updated += 1
                except Exception as e:
                    logger.error(f"Failed to update permissions for lobby: {e}")

            # Create success embed
            embed = discord.Embed(
                title=f"{Config.ICONS['lock']} Lobbies Locked Successfully",
                description="All lobbies have been locked. Registered players can see but cannot connect to lobby voice channels.",
                color=Config.EMBED_COLORS["red"],
            )
            embed.add_field(
                name="Lobbies Updated",
                value=f"{lobbies_updated} active lobbies",
                inline=True,
            )
            embed.add_field(
                name="Effect",
                value=f"{Config.ICONS['not_allowed']} Registered role cannot connect to voice",
                inline=True,
            )
            embed.set_footer(
                text="Use /unlock_lobbies to restore normal access. New lobbies will also be locked."
            )

            await interaction.followup.send(embed=embed, ephemeral=True)
            logger.info(
                f"Lobbies locked by {interaction.user} - {lobbies_updated} lobbies updated"
            )

        except Exception as e:
            logger.error(f"Failed to lock lobbies: {e}")
            embed = discord.Embed(
                title=f"{Config.ICONS['unsuccess']} Failed to Lock Lobbies",
                description="An error occurred while locking lobbies.",
                color=Config.EMBED_COLORS["red"],
            )
            embed.add_field(
                name="Error",
                value="Lobby locking operation failed",
                inline=False,
            )
            embed.set_footer(text="Please try again or contact an administrator.")
            await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(
        name="unlock_lobbies",
        description="Admin command to unlock all lobbies - restores Registered role voice access.",
    )
    @app_commands.default_permissions(administrator=True)
    async def unlock_lobbies(self, interaction: discord.Interaction):
        """Unlock all lobbies to restore Registered role voice access"""
        # Verifică autorizația utilizatorului
        if not await Config.check_authorization(interaction):
            return
            
        await interaction.response.defer(ephemeral=True)

        try:
            if not self.lobbies_locked:
                embed = discord.Embed(
                    title=f"{Config.ICONS['info']} Lobbies Already Unlocked",
                    description="Lobbies are already unlocked. Registered players have full access to lobby voice channels.",
                    color=Config.EMBED_COLORS["yellow"],
                )
                embed.set_footer(text="Use /lock_lobbies to restrict access.")
                await interaction.followup.send(embed=embed, ephemeral=True)
                return  # Set unlocked status
            self.lobbies_locked = False

            # Persist unlock status to database
            await LobbyState.update_lobby_lock_status(
                interaction.guild.id, False
            )  # Update permissions for all active lobbies
            lobbies_updated = 0
            for lobby in self.active_lobbies.values():
                try:
                    # Get the required role for this specific lobby
                    required_role_id = lobby.get("required_role_id")
                    if required_role_id:
                        lobby_role = interaction.guild.get_role(int(required_role_id))
                    else:
                        # Fallback to Registered role for old lobbies
                        lobby_role = discord.utils.get(
                            interaction.guild.roles, name=Config.REGISTERED_ROLE
                        )

                    if not lobby_role:
                        logger.warning(
                            f"Could not find required role for lobby {lobby.get('lobby_number')}"
                        )
                        continue

                    # Update voice and text channel permissions
                    voice_channel = lobby.get("voice")
                    text_channel = lobby.get("text")
                    if voice_channel:
                        await voice_channel.set_permissions(
                            lobby_role,
                            view_channel=True,
                            connect=True,  # Restore connect permission
                            send_messages=False,  # Still prevent sending messages in text
                            read_messages=True,
                            reason="Lobbies unlocked by administrator",
                        )
                    if text_channel:
                        await text_channel.set_permissions(
                            lobby_role,
                            view_channel=True,
                            send_messages=False,  # Still prevent sending messages in text
                            read_messages=True,
                            reason="Lobbies unlocked by administrator",
                        )
                        lobbies_updated += 1
                except Exception as e:
                    logger.error(f"Failed to update permissions for lobby: {e}")

            # Create success embed
            embed = discord.Embed(
                title=f"{Config.ICONS['unlock']} Lobbies Unlocked Successfully",
                description="All lobbies have been unlocked. Registered players now have full voice access to lobby channels.",
                color=Config.EMBED_COLORS["light_green"],
            )
            embed.add_field(
                name="Lobbies Updated",
                value=f"{lobbies_updated} active lobbies",
                inline=True,
            )
            embed.add_field(
                name="Effect",
                value=f"{Config.ICONS['success']} Registered role can connect to voice",
                inline=True,
            )
            embed.set_footer(
                text="Use /lock_lobbies to restrict access if needed. New lobbies will be unlocked."
            )

            await interaction.followup.send(embed=embed, ephemeral=True)
            logger.info(
                f"Lobbies unlocked by {interaction.user} - {lobbies_updated} lobbies updated"
            )

        except Exception as e:
            logger.error(f"Failed to unlock lobbies: {e}")
            embed = discord.Embed(
                title=f"{Config.ICONS['unsuccess']} Failed to Unlock Lobbies",
                description="An error occurred while unlocking lobbies.",
                color=Config.EMBED_COLORS["red"],
            )
            embed.add_field(
                name="Error",
                value="Lobby unlocking operation failed",
                inline=False,
            )
            embed.set_footer(text="Please try again or contact an administrator.")
            await interaction.followup.send(embed=embed, ephemeral=True)

    # --- PARTY-BASED MATCHMAKING HELPERS ---

    async def get_party_info_for_players(self, players):
        """Get party information for all players in the lobby"""
        party_info = {}
        party_leaders = []
        party_groups = {}

        for player in players:
            party = await Party.find_user_party(str(player.id))
            if party:
                party_id = party["party_id"]
                leader_id = party["leader_id"]

                # Store party info for this player
                party_info[player.id] = {
                    "party_id": party_id,
                    "leader_id": leader_id,
                    "is_leader": str(player.id) == leader_id,
                    "party": party,
                }

                # Group players by party
                if party_id not in party_groups:
                    party_groups[party_id] = []
                party_groups[party_id].append(player)

        # Filter out "parties" that have only 1 member in the lobby
        # This prevents the issue where a player is in a party but their party members aren't in lobby
        valid_party_groups = {}
        valid_party_info = {}
        
        for party_id, members in party_groups.items():
            if len(members) >= 2:  # Only consider parties with 2+ members in lobby
                valid_party_groups[party_id] = members
                # Keep party info only for members of valid parties
                for member in members:
                    valid_party_info[member.id] = party_info[member.id]
                    # Track party leaders only from valid parties
                    if party_info[member.id]["is_leader"]:
                        party_leaders.append(member)

        return valid_party_info, party_leaders, valid_party_groups

    async def select_party_based_captains(self, lobby):
        """Select captains prioritizing party leaders when parties are present"""
        players = list(lobby["players"])
        party_info, party_leaders, party_groups = await self.get_party_info_for_players(
            players
        )

        # If no valid parties present (parties with 2+ members in lobby), use original random selection
        if not party_leaders:
            return random.sample(players, 2), party_info, party_groups

        # If only one valid party leader, select them and one random player from non-party members
        if len(party_leaders) == 1:
            captain1 = party_leaders[0]
            # Get all players not in any valid party
            non_party_players = [p for p in players if p.id not in party_info]
            if non_party_players:
                captain2 = random.choice(non_party_players)
            else:
                # Fallback to random selection if all players are in valid parties
                remaining_players = [p for p in players if p != captain1]
                captain2 = random.choice(remaining_players)
            return [captain1, captain2], party_info, party_groups

        # If two or more valid party leaders, select the first two as captains
        return party_leaders[:2], party_info, party_groups

    async def assign_party_members_to_teams(self, lobby, party_info, party_groups):
        """Automatically assign party members to their leader's team"""
        team1_captain = lobby["team1"][0]
        team2_captain = lobby["team2"][0]

        # Assign party members to their captain's team
        # Note: party_groups only contains valid parties (2+ members in lobby)
        for party_id, party_members in party_groups.items():
            # Find which captain is the leader of this party
            party_leader_id = None
            for member in party_members:
                if party_info[member.id]["is_leader"]:
                    party_leader_id = member.id
                    break

            if party_leader_id:
                # Assign all party members to the same team as their leader
                if party_leader_id == team1_captain.id:
                    # Leader is Team1 captain, assign all members to Team1 team
                    for member in party_members:
                        if (
                            member != team1_captain
                            and member in lobby["available_players"]
                        ):
                            lobby["team1"].append(member)
                            lobby["available_players"].remove(member)
                elif party_leader_id == team2_captain.id:
                    # Leader is Team2 captain, assign all members to Team2 team
                    for member in party_members:
                        if member != team2_captain and member in lobby["available_players"]:
                            lobby["team2"].append(member)
                            lobby["available_players"].remove(member)
                else:
                    # Party leader is not a captain, assign the whole party to the smaller team
                    team1_size = len(lobby["team1"])
                    team2_size = len(lobby["team2"])

                    if team1_size <= team2_size:  # Assign to Team1 team
                        for member in party_members:
                            if member in lobby["available_players"]:
                                lobby["team1"].append(member)
                                lobby["available_players"].remove(member)
                    else:
                        # Assign to Team2 team
                        for member in party_members:
                            if member in lobby["available_players"]:
                                lobby["team2"].append(member)
                                lobby["available_players"].remove(member)

    async def determine_next_pick_balanced(self, lobby):
        """Determine which captain should pick next following proper draft flow"""
        team1_size = len(lobby["team1"])
        team2_size = len(lobby["team2"])
        team1_captain = lobby["team1"][0]
        team2_captain = lobby["team2"][0]
        current_pick = lobby.get("next_pick")
        is_draft_start = lobby.get("draft_start", False)

        # If teams are unbalanced, the smaller team ALWAYS picks to balance
        if team1_size != team2_size:
            # Clear any balancing flags since we're in imbalanced state
            lobby.pop("just_balanced", None)
            lobby.pop("draft_start", None)
            # Return the captain of the smaller team
            if team1_size < team2_size:
                logger.info(f"Lobby #{lobby.get('lobby_number', 'unknown')}: Team1 ({team1_size}) < Team2 ({team2_size}), Team1 captain picks to balance")
                return team1_captain
            else:
                logger.info(f"Lobby #{lobby.get('lobby_number', 'unknown')}: Team2 ({team2_size}) < Team1 ({team1_size}), Team2 captain picks to balance")
                return team2_captain

        # Teams are balanced - handle various scenarios
        logger.info(f"Lobby #{lobby.get('lobby_number', 'unknown')}: Teams balanced ({team1_size} vs {team2_size})")

        # Special case: Draft just started after party assignments
        if is_draft_start:
            lobby.pop("draft_start", None)
            # If teams are balanced after party assignments, Team1 starts
            # If teams are imbalanced, the smaller team should start to balance
            if team1_size == team2_size:
                logger.info(f"Lobby #{lobby.get('lobby_number', 'unknown')}: Draft start with balanced teams - Team1 captain goes first")
                return team1_captain
            else:
                # Teams are imbalanced after party assignments, smaller team picks first
                if team1_size < team2_size:
                    logger.info(f"Lobby #{lobby.get('lobby_number', 'unknown')}: Draft start with imbalanced teams - Team1 (smaller) captain goes first")
                    return team1_captain
                else:
                    logger.info(f"Lobby #{lobby.get('lobby_number', 'unknown')}: Draft start with imbalanced teams - Team2 (smaller) captain goes first")
                    return team2_captain

        # Check if we just finished balancing (teams just became equal)
        if lobby.get("just_balanced"):
            # Clear the flag and switch to the other captain for normal alternation
            lobby.pop("just_balanced", None)
            # After balancing, switch to the other captain
            if current_pick == team1_captain:
                logger.info(f"Lobby #{lobby.get('lobby_number', 'unknown')}: Just balanced - switching to Team2 captain")
                return team2_captain
            else:
                logger.info(f"Lobby #{lobby.get('lobby_number', 'unknown')}: Just balanced - switching to Team1 captain")
                return team1_captain

        # Standard case: If no current pick set, Team1 captain starts
        if not current_pick:
            logger.info(f"Lobby #{lobby.get('lobby_number', 'unknown')}: No current pick - Team1 captain starts")
            return team1_captain

        # Standard alternating: switch to the other captain
        if current_pick == team1_captain:
            logger.info(f"Lobby #{lobby.get('lobby_number', 'unknown')}: Alternating from Team1 to Team2 captain")
            return team2_captain
        else:
            logger.info(f"Lobby #{lobby.get('lobby_number', 'unknown')}: Alternating from Team2 to Team1 captain")
            return team1_captain

    async def calculate_picks_needed(self, lobby):
        """Calculate how many players the current captain needs to pick based on proper draft flow"""
        if not lobby["available_players"]:
            return 0

        team1_size = len(lobby["team1"])
        team2_size = len(lobby["team2"])
        current_captain = lobby["next_pick"]
        available_count = len(lobby["available_players"])

        # Determine which team the current captain belongs to
        is_team1_captain = current_captain == lobby["team1"][0]

        if is_team1_captain:
            current_team_size = team1_size
            other_team_size = team2_size
        else:
            current_team_size = team2_size
            other_team_size = team1_size

        # Calculate the team size difference
        team_diff = other_team_size - current_team_size

        # If the current captain's team is smaller, they need to pick multiple players to balance
        if team_diff > 0:
            # Pick enough players to balance the teams, but don't exceed available players
            picks_needed = min(team_diff, available_count)
            logger.info(f"Lobby #{lobby.get('lobby_number', 'unknown')}: Captain needs {picks_needed} picks to balance teams (diff: {team_diff}, available: {available_count})")
        else:
            # Standard draft: pick 1 player (teams are equal or current team is larger)
            picks_needed = min(1, available_count)
            logger.info(f"Lobby #{lobby.get('lobby_number', 'unknown')}: Standard 1 pick (teams balanced or current team larger)")

        # Don't pick more players than available
        picks_needed = min(picks_needed, len(lobby["available_players"]))

        # If only 1 player left, they'll be auto-assigned anyway
        if len(lobby["available_players"]) == 1:
            return 1

        return picks_needed

    async def handle_draft_picks(self, lobby, picked_members):
        """Handle multiple draft picks at once"""
        async with self.lobby_locks[lobby["category"].guild.id]:
            # Critical validation: Ensure lobby is still in correct phase
            if lobby["phase"] != 2:
                logger.warning(f"Draft picks attempted on lobby #{lobby.get('lobby_number', 'unknown')} in phase {lobby['phase']}")
                return

            # Validate lobby still exists
            if lobby["category"].id not in self.active_lobbies:
                logger.warning(f"Draft picks attempted on non-existent lobby #{lobby.get('lobby_number', 'unknown')}")
                return

            # Validate current captain still exists and is correct
            current_captain = lobby.get("next_pick")
            if not current_captain:
                logger.warning(f"Draft picks attempted with no current captain in lobby #{lobby.get('lobby_number', 'unknown')}")
                return

            # Validate all picked members are still available
            for picked_member in picked_members:
                if picked_member not in lobby["available_players"]:
                    logger.warning(f"Attempted to pick unavailable player: {picked_member} in lobby #{lobby.get('lobby_number', 'unknown')}")
                    return

            logger.info(f"Processing draft picks in lobby #{lobby.get('lobby_number', 'unknown')}: {[p.display_name for p in picked_members]} by captain {current_captain.display_name}")

            # Check if teams were imbalanced before this pick
            team1_size_before = len(lobby["team1"])
            team2_size_before = len(lobby["team2"])
            was_imbalanced = team1_size_before != team2_size_before

            # Add all picked players to the correct team
            for picked_member in picked_members:
                if current_captain == lobby["team1"][0]:
                    lobby["team1"].append(picked_member)
                else:
                    lobby["team2"].append(picked_member)
                
                # Safe removal with error handling
                try:
                    lobby["available_players"].remove(picked_member)
                except ValueError:
                    logger.warning(f"Player {picked_member} was not in available_players list")

            # Check if teams are now balanced after the pick
            team1_size_after = len(lobby["team1"])
            team2_size_after = len(lobby["team2"])
            is_now_balanced = team1_size_after == team2_size_after

            # If we just transitioned from imbalanced to balanced, set flag
            if was_imbalanced and is_now_balanced:
                lobby["just_balanced"] = True
                logger.info(f"Lobby #{lobby.get('lobby_number', 'unknown')}: Teams just became balanced ({team1_size_after} vs {team2_size_after})")

            # Handle automatic assignment of last player if only one remains
            if len(lobby["available_players"]) == 1:
                last_player = lobby["available_players"][0]
                team1_size = len(lobby["team1"])
                team2_size = len(lobby["team2"])

                # Assign to the smaller team
                if team1_size <= team2_size:
                    lobby["team1"].append(last_player)
                    logger.info(f"Lobby #{lobby.get('lobby_number', 'unknown')}: Auto-assigned last player {last_player.display_name} to Team1")
                else:
                    lobby["team2"].append(last_player)
                    logger.info(f"Lobby #{lobby.get('lobby_number', 'unknown')}: Auto-assigned last player {last_player.display_name} to Team2")
                lobby["available_players"].remove(last_player)

            # Update next pick based on balanced team logic
            if lobby["available_players"]:
                lobby["next_pick"] = await self.determine_next_pick_balanced(lobby)
                logger.info(f"Lobby #{lobby.get('lobby_number', 'unknown')}: Next pick determined: {lobby['next_pick'].display_name}")

            # --- New Draft UI ---
            team1_lines = await self.format_team_list(
                lobby["team1"], lobby.get("league_table")
            )
            team2_lines = await self.format_team_list(
                lobby["team2"], lobby.get("league_table")
            )
            desc = (
                f"**Team 1**\n{team1_lines}\n\n"
                f"**Team 2**\n{team2_lines}\n\n"
            )
            if lobby["available_players"]:
                picks_needed = await self.calculate_picks_needed(lobby)
                if picks_needed > 1:
                    desc += f"Captain {lobby['next_pick'].mention} needs to pick {picks_needed} players…"
                else:
                    desc += (
                        f"Captain {lobby['next_pick'].mention} is currently picking…"
                    )
            else:
                desc += "All players have been drafted."

            embed = discord.Embed(
                title="Drafting Players Phase",
                description=desc,
                color=Config.EMBED_COLORS["draft"],
            )
            embed.set_footer(text="Next Phase: Map Veto")

            if lobby["available_players"]:
                view = await self.get_draft_view(lobby)
                view = self.set_current_view(lobby, view)
            else:
                self.stop_current_view(lobby)
                view = None

            try:
                await lobby["message"].edit(embed=embed, view=view)
            except discord.NotFound:
                logger.error(f"Lobby message not found during draft update for lobby #{lobby.get('lobby_number', 'unknown')}")
                return
            except discord.Forbidden:
                logger.error(f"Missing permissions to edit lobby message for lobby #{lobby.get('lobby_number', 'unknown')}")
                return
            except Exception as e:
                logger.error(f"Unexpected error updating draft message: {e}")
                return

            if not lobby["available_players"]:
                try:
                    await self.start_map_veto(lobby)
                except Exception as e:
                    logger.error(f"Failed to start map veto after draft completion: {e}")
                    # Reset lobby to prevent stuck state
                    await self.reset_lobby(lobby)

    @staticmethod
    def get_map_emoji(map_name):
        """Get the emoji for a map from the config"""
        for map_info in Config.MAP_POOL:
            if map_info["name"] == map_name:
                return map_info["emoji"]
        return None


async def setup(bot):
    await bot.add_cog(Matchmaking(bot))
