import discord
from discord.ext import commands, tasks
from discord import app_commands
from config import Config
from models.match import Match
from models.lobby_state import LobbyState
from models.timeout import timeout_manager
from models.user import User
from cogs.levels import LevelsCog
import random
import asyncio
import logging
from collections import defaultdict

logger = logging.getLogger(__name__)

LOBBY_VOICE_NAME = "Lobby"
LOBBY_TEXT_NAME = "Lobby"
CT_VOICE_NAME = "CT Voice"
T_VOICE_NAME = "T Voice"

EMBED_COLORS = {
    "waiting": 0xFFFF00,  # Yellow
    "draft": 0x6969E7,  # Blue
    "veto": 0xFB5B29,  # Orange
    "final": 0xB2FA19,  # Green
}


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
            await timeout_manager.create_table_if_not_exists()
            await timeout_manager.load_active_timeouts()
            logger.info("Timeout manager initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize timeout manager: {e}")

    @app_commands.command(
        name="create_lobby", description="Create a new matchmaking lobby."
    )
    async def create_lobby(
        self, interaction: discord.Interaction, required_role: discord.Role = None
    ):
        # Check if interaction has a valid guild
        if not interaction.guild:
            await interaction.response.send_message(
                "This command can only be used in a server.", ephemeral=True
            )
            return

        async with self.lobby_locks[interaction.guild.id]:
            try:
                guild = interaction.guild

                if not guild:
                    raise ValueError("Could not access the server.")

                # Determine which role to use for the lobby
                if required_role:
                    lobby_role = required_role
                    lobby_role_name = required_role.name
                else:
                    # Get the default Registered role
                    lobby_role = discord.utils.get(
                        guild.roles, name=Config.REGISTERED_ROLE
                    )
                    if not lobby_role:
                        # Create the role if it doesn't exist
                        lobby_role = await guild.create_role(
                            name=Config.REGISTERED_ROLE,
                            color=0xB2FA19,  # Vibrant green color
                            reason="Auto-created for registered players",
                        )
                    lobby_role_name = Config.REGISTERED_ROLE

                lobby_number = self.lobby_counter
                self.lobby_counter += 1
                category_name = f"[5v5] | {lobby_role_name}"

                # Check bot permissions
                if not guild.me.guild_permissions.manage_channels:
                    raise ValueError(
                        "Bot doesn't have permission to manage channels in this server."
                    )
                # Set up permission overwrites - everyone can see, only specific role can join
                overwrites = {
                    guild.default_role: discord.PermissionOverwrite(
                        view_channel=True,  # Allow everyone to see
                        connect=False,  # Deny @everyone from connecting
                        send_messages=False,  # Prevent everyone from sending messages
                        read_messages=True,
                    ),
                    guild.me: discord.PermissionOverwrite(
                        view_channel=True,
                        connect=True,
                        manage_channels=True,
                        send_messages=True,
                        read_messages=True,
                    ),  # Allow bot full access
                }

                # Set lobby role permissions based on lock status
                if self.lobbies_locked:
                    overwrites[lobby_role] = discord.PermissionOverwrite(
                        view_channel=True,
                        connect=False,  # Deny connection when locked (even for role holders)
                        send_messages=False,  # Prevent role holders from sending messages
                        read_messages=True,
                    )  # Locked: Can see but cannot connect or send messages
                else:
                    overwrites[lobby_role] = discord.PermissionOverwrite(
                        view_channel=True,
                        connect=True,  # Only this role can connect when unlocked
                        send_messages=False,  # Prevent role holders from sending messages
                        read_messages=True,
                    )  # Unlocked: Role holders can connect but not send messages

                # Allow administrators to access lobbies
                for role in guild.roles:
                    if role.permissions.administrator:
                        overwrites[role] = discord.PermissionOverwrite(
                            view_channel=True,
                            connect=True,
                            send_messages=True,  # Administrators can send messages
                            read_messages=True,
                        )

                category = await guild.create_category(
                    category_name, overwrites=overwrites
                )
                voice = await category.create_voice_channel(
                    f"Lobby #{lobby_number}", user_limit=4
                )
                text = await category.create_text_channel(LOBBY_TEXT_NAME)
                embed = discord.Embed(
                    title=f"LOBBY #{lobby_number} STATUS",
                    description="Players in Queue: 0/4\nPhase: Waiting for players...",
                    color=EMBED_COLORS["waiting"],
                )
                msg = await text.send(embed=embed)

                self.active_lobbies[category.id] = {
                    "category": category,
                    "voice": voice,
                    "text": text,
                    "phase": 1,
                    "players": set(),
                    "message": msg,
                    "ct_team": [],
                    "t_team": [],
                    "map_pool": Config.MAP_POOL.copy(),
                    "match_id": None,
                    "ct_voice": None,
                    "t_voice": None,
                    "lobby_number": lobby_number,
                    "required_role_id": lobby_role.id,
                    "required_role_name": lobby_role_name,
                }
                # Save lobby state to database
                await self.save_lobby_state(category.id)
                # Create success embed
                embed = discord.Embed(
                    title="✅ Lobby Created Successfully",
                    description=f"A new lobby has been created and is ready for {lobby_role_name} players!",
                    color=EMBED_COLORS["waiting"],
                )
                embed.add_field(
                    name="Lobby Number", value=f"#{lobby_number}", inline=True
                )
                embed.add_field(name="Players", value="0/4", inline=True)
                embed.add_field(name="Status", value="Waiting for players", inline=True)
                embed.add_field(
                    name="Access",
                    value=f"🔒 {lobby_role_name} players only",
                    inline=False,
                )
                embed.set_footer(
                    text=f"Lobby is visible to everyone, but only {lobby_role_name} players can join voice!"
                )

                await interaction.response.send_message(embed=embed, ephemeral=True)
            except ValueError as ve:
                logger.error(f"Error creating lobby - validation error: {ve}")
                await interaction.response.send_message(
                    f"Failed to create lobby: {ve}", ephemeral=True
                )
            except Exception as e:
                logger.error(f"Error creating lobby - unexpected error: {e}")
                await interaction.response.send_message(
                    "Failed to create lobby due to an unexpected error. Please try again later.",
                    ephemeral=True,
                )

    # --- DRAFT PICK UI ---

    class DraftDropdown(discord.ui.Select):
        def __init__(self, lobby, cog, options_with_stats):
            super().__init__(
                placeholder="Select a player to draft...",
                min_values=1,
                max_values=1,
                options=options_with_stats,
            )
            self.lobby = lobby
            self.cog = cog

        async def callback(self, interaction: discord.Interaction):
            # Only allow the captain whose turn it is
            if interaction.user != self.lobby["next_pick"]:
                await interaction.response.send_message(
                    "You are not the captain or it's not your turn to pick.",
                    ephemeral=True,
                )
                return

            picked_id = int(self.values[0])
            picked_member = discord.utils.get(
                self.lobby["available_players"], id=picked_id
            )

            # Acknowledge the interaction
            await interaction.response.defer(ephemeral=True)

            await self.cog.handle_draft_pick(self.lobby, picked_member)

    class DraftView(discord.ui.View):
        def __init__(self, lobby, cog, draft_dropdown):
            super().__init__(timeout=120)
            self.lobby = lobby
            self.cog = cog
            self.add_item(draft_dropdown)

        async def on_timeout(self):
            # Only trigger draft timeout if phase is 2 (drafting) and captain hasn't changed
            if self.lobby["phase"] == 2 and self.lobby.get("next_pick"):
                # Double-check that this view is still the active one
                try:
                    await self.cog.handle_timeout(
                        self.lobby, reason="Draft pick timeout"
                    )
                except Exception as e:
                    # If handle_timeout fails, it might be because it's already been called
                    logger.warning(
                        f"Draft timeout failed, possibly due to race condition: {e}"
                    )

    # --- MAP VETO UI ---
    class VetoDropdown(discord.ui.Select):
        def __init__(self, lobby, cog):
            options = [
                discord.SelectOption(label=map_name, value=map_name)
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
            if self.lobby["phase"] == 3 and self.lobby.get("veto_turn"):
                # Double-check that this view is still the active one
                try:
                    await self.cog.handle_timeout(self.lobby, reason="Map veto timeout")
                except Exception as e:
                    # If handle_timeout fails, it might be because it's already been called
                    logger.warning(
                        f"Veto timeout failed, possibly due to race condition: {e}"
                    )

    # --- GET ID BUTTON UI ---
    class GetIdView(discord.ui.View):
        def __init__(self, lobby, cog):
            super().__init__(timeout=3600)  # 1 hour timeout
            self.lobby = lobby
            self.cog = cog
            # Store team data at creation time to prevent data loss after lobby reset
            self.ct_team = lobby["ct_team"].copy()
            self.t_team = lobby["t_team"].copy()
            # Randomly select the host when the view is created
            all_players = self.ct_team + self.t_team
            self.host_player = random.choice(all_players) if all_players else None
            self.host_data = None  # Will be populated when first accessed

        @discord.ui.button(label="Get Host ID", style=discord.ButtonStyle.primary)
        async def get_id_button(
            self, interaction: discord.Interaction, button: discord.ui.Button
        ):
            await interaction.response.defer(ephemeral=True)

            try:
                # Check if the user who clicked is in one of the teams
                all_players = self.ct_team + self.t_team

                if interaction.user not in all_players:
                    await interaction.followup.send(
                        "❌ You are not part of this match! Only match participants can view the host ID.",
                        ephemeral=True,
                    )
                    return

                # Check if we have a host selected
                if not self.host_player:
                    await interaction.followup.send(
                        "❌ No host could be selected for this match. Please contact an administrator.",
                        ephemeral=True,
                    )
                    return

                # Fetch the host's data from database (cache it for efficiency)
                if not self.host_data:
                    from models.user import User

                    self.host_data = await User.find(self.host_player.id)

                if not self.host_data:
                    await interaction.followup.send(
                        f"❌ The selected host ({self.host_player.display_name}) is not registered in the database.",
                        ephemeral=True,
                    )
                    return

                # Create a simple message with the host's ID (easy to copy)
                message = (
                    f"🎯 **Lobby Host:** {self.host_player.display_name}\n"
                    f"**Host's SO2 ID:** `{self.host_data['so2_id']}`\n\n"
                    f"*Click to copy the ID above*"
                )

                await interaction.followup.send(message, ephemeral=True)

            except Exception as e:
                logger.error(f"Failed to get host ID for {interaction.user}: {e}")
                await interaction.followup.send(
                    "❌ An error occurred while retrieving the host ID. Please try again.",
                    ephemeral=True,
                )

        async def on_timeout(self):
            # Disable the button when timeout occurs
            for item in self.children:
                item.disabled = True

    async def get_draft_view(self, lobby):
        options_with_stats = await self.create_draft_options_with_stats(
            lobby["available_players"]
        )
        draft_dropdown = Matchmaking.DraftDropdown(lobby, self, options_with_stats)
        return Matchmaking.DraftView(lobby, self, draft_dropdown)

    def get_veto_view(self, lobby):
        return Matchmaking.VetoView(lobby, self)

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
                    title="⏰ Captain Timeout Applied",
                    description="You have been removed from the lobby and timeouted for failing to act within the time limit.",
                    color=discord.Color.red(),
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
                    value="🚫 Cannot join any lobbies during timeout",
                    inline=True,
                )
                embed.set_footer(
                    text="You will be able to join lobbies again once the timeout expires."
                )

                await captain_to_timeout.send(embed=embed)
            else:
                # Fallback embed for database failure
                embed = discord.Embed(
                    title="⏰ Captain Timeout Applied",
                    description="You have been removed from the lobby and timeouted for failing to act within the time limit.",
                    color=discord.Color.red(),
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
                    title="⚠️ Captain Timeout",
                    description=f"Captain {captain_to_timeout.display_name} has been timeouted for not acting within the time limit.\n\n**The lobby has been reset to waiting phase.** You can stay and wait for new players to join, or leave to find another lobby.",
                    color=discord.Color.orange(),
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
        lobby["phase"] = 1
        lobby["ct_team"] = []
        lobby["t_team"] = []
        lobby["available_players"] = []
        lobby["next_pick"] = None
        lobby["veto_turn"] = None
        lobby["remaining_maps"] = []
        lobby["last_ban"] = None

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
            for team in ["ct_voice", "t_voice"]:
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
                                title="⏰ Lobby Access Denied",
                                description="You are currently timeouted and cannot join lobbies.",
                                color=discord.Color.red(),
                            )
                            embed.add_field(
                                name="Time Remaining",
                                value=f"{remaining_minutes} minute(s)",
                                inline=True,
                            )
                            embed.add_field(
                                name="Status",
                                value="🚫 Lobby access blocked",
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
                                    title="🔒 Lobbies Currently Locked",
                                    description=f"All lobbies are currently locked by administrators. {lobby_role_name} players cannot connect to lobby voice channels at this time.",
                                    color=discord.Color.red(),
                                )
                                embed.add_field(
                                    name="Status",
                                    value="🚫 Voice access temporarily restricted",
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

                    lobby["players"].add(member)
                await self.update_lobby_status(lobby)
                if len(lobby["players"]) == 4 and lobby["phase"] == 1:
                    await self.start_captain_selection(lobby)
            if (
                before.channel
                and before.channel.id == lobby["voice"].id
                and (not after.channel or after.channel.id != lobby["voice"].id)
            ):
                if member in lobby["players"]:
                    lobby["players"].remove(member)
                await self.update_lobby_status(lobby)
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
                                title="⏰ Mid-Game Leave Penalty",
                                description="You have been removed from the lobby due to leaving mid-game.",
                                color=discord.Color.red(),
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
                                value="🚫 Cannot join any lobbies during timeout",
                                inline=True,
                            )
                            embed.set_footer(
                                text="You will be able to join lobbies again once the timeout expires."
                            )

                            await member.send(embed=embed)
                        else:
                            # Fallback embed for database failure
                            embed = discord.Embed(
                                title="⏰ Mid-Game Leave Penalty",
                                description="You have been removed from the lobby due to leaving mid-game.",
                                color=discord.Color.red(),
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
        """Update the lobby status embed"""
        # Check if lobby still exists before updating
        if lobby["category"].id not in self.active_lobbies:
            return

        # Additional safety check - ensure lobby components still exist
        try:
            if not lobby.get("category") or not lobby.get("text"):
                return
        except Exception:
            return

        # Only update the embed if the lobby is in phase 1 (waiting for players)
        if lobby["phase"] == 1:
            try:
                embed = discord.Embed(
                    title=f"LOBBY #{lobby.get('lobby_number', 'X')} STATUS",
                    description=f"Players in Queue: {len(lobby['players'])}/4\nPhase: Waiting for players...",
                    color=EMBED_COLORS["waiting"],
                )

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
                    # If message exists, try to edit it
                    if lobby["message"]:
                        await lobby["message"].edit(embed=embed, view=None)
                    else:
                        # Create new message if it doesn't exist
                        lobby["message"] = await lobby["text"].send(embed=embed)
                except discord.NotFound:
                    # Message was deleted, create a new one
                    lobby["message"] = await lobby["text"].send(embed=embed)
                except discord.Forbidden:
                    logger.warning(
                        f"Missing permissions to edit message in lobby #{lobby.get('lobby_number', 'unknown')}"
                    )
                except Exception as e:
                    logger.error(f"Unexpected error updating lobby message: {e}")

                # Save lobby state when players change
                await self.save_lobby_state(lobby["category"].id)

            except Exception as e:
                logger.error(
                    f"Error in update_lobby_status for lobby #{lobby.get('lobby_number', 'unknown')}: {e}"
                )

    async def format_team_list(self, team_members):
        """Format team list with level emojis for each player"""
        if not team_members:
            return "-"

        formatted_players = []
        for member in team_members:
            try:
                # Get user data to determine points
                user_data = await User.find(member.id)
                if user_data:
                    points = user_data.get("points", 0)
                    level_emoji = LevelsCog.get_level_emoji(points)
                    formatted_players.append(f"{level_emoji} {member.mention}")
                else:
                    # Default emoji if user not found in database
                    default_emoji = LevelsCog.get_level_emoji(0)
                    formatted_players.append(f"{default_emoji} {member.mention}")
            except Exception as e:
                logger.error(
                    f"Error getting level emoji for {member.display_name}: {e}"
                )
                # Fallback without emoji
                formatted_players.append(member.mention)

        return "\n".join(formatted_players)

    async def start_captain_selection(self, lobby):
        lobby["phase"] = 2
        players = list(lobby["players"])
        captains = random.sample(players, 2)
        ct_captain, t_captain = captains[0], captains[1]
        lobby["ct_team"] = [ct_captain]
        lobby["t_team"] = [t_captain]
        lobby["available_players"] = [p for p in players if p not in captains]
        lobby["next_pick"] = ct_captain
        # --- New Draft UI ---
        ct_lines = await self.format_team_list(lobby["ct_team"])
        t_lines = await self.format_team_list(lobby["t_team"])
        desc = (
            f"**Counter-Terrorists**\n{ct_lines}\n\n"
            f"**Terrorists**\n{t_lines}\n\n"
            f"Captain {lobby['next_pick'].mention} is currently picking…"
        )
        embed = discord.Embed(
            title="Drafting Players Phase",
            description=desc,
            color=EMBED_COLORS["draft"],
        )
        embed.set_footer(text="Next Phase: Map Veto")
        view = await self.get_draft_view(lobby)
        await lobby["message"].edit(embed=embed, view=view)

    async def handle_draft_pick(self, lobby, picked_member):
        async with self.lobby_locks[lobby["category"].guild.id]:
            # Add picked player to the correct team
            if lobby["next_pick"] == lobby["ct_team"][0]:
                lobby["ct_team"].append(picked_member)
                lobby["next_pick"] = lobby["t_team"][0]
            else:
                lobby["t_team"].append(picked_member)
                lobby["next_pick"] = lobby["ct_team"][0]
            lobby["available_players"].remove(picked_member)

            # If there's only 1 player left, automatically assign them to the other team
            if len(lobby["available_players"]) == 1:
                last_player = lobby["available_players"][0]
                if lobby["next_pick"] == lobby["ct_team"][0]:
                    lobby["ct_team"].append(last_player)
                else:
                    lobby["t_team"].append(last_player)
                lobby["available_players"].remove(last_player)

            # --- New Draft UI ---
            ct_lines = await self.format_team_list(lobby["ct_team"])
            t_lines = await self.format_team_list(lobby["t_team"])
            desc = (
                f"**Counter-Terrorists**\n{ct_lines}\n\n"
                f"**Terrorists**\n{t_lines}\n\n"
            )
            if lobby["available_players"]:
                desc += f"Captain {lobby['next_pick'].mention} is currently picking…"
            else:
                desc += "All players have been drafted."
            embed = discord.Embed(
                title="Drafting Players Phase",
                description=desc,
                color=EMBED_COLORS["draft"],
            )
            embed.set_footer(text="Next Phase: Map Veto")
            if lobby["available_players"]:
                view = await self.get_draft_view(lobby)
            else:
                view = None
            await lobby["message"].edit(embed=embed, view=view)
            if not lobby["available_players"]:
                await self.start_map_veto(lobby)

    async def start_map_veto(self, lobby):
        lobby["phase"] = 3
        lobby["veto_turn"] = lobby["ct_team"][0]  # CT starts
        lobby["remaining_maps"] = Config.MAP_POOL.copy()
        lobby["last_ban"] = None  # Track previous ban
        text = lobby["text"]
        # --- New Map Veto UI ---
        desc = ""
        if lobby["last_ban"]:
            desc += f"Captain {lobby['last_ban']['captain'].mention} has banned: **{lobby['last_ban']['map']}**\n\n"
        maps_str = "\n".join([f"| **{m}**" for m in lobby["remaining_maps"]])
        desc += f"{maps_str}\n\n"
        desc += f"Captain {lobby['veto_turn'].mention} is currently banning…"
        embed = discord.Embed(
            title="Map Veto Phase", description=desc, color=EMBED_COLORS["veto"]
        )
        embed.set_footer(text="Next Phase: Final Match Data")
        view = self.get_veto_view(lobby)
        lobby["veto_message"] = await text.send(embed=embed, view=view)

    async def handle_map_ban(self, lobby, banned_map):
        async with self.lobby_locks[lobby["category"].guild.id]:
            # Track last ban
            lobby["last_ban"] = {"captain": lobby["veto_turn"], "map": banned_map}
            lobby["remaining_maps"].remove(banned_map)
            # Switch turn
            if lobby["veto_turn"] == lobby["ct_team"][0]:
                lobby["veto_turn"] = lobby["t_team"][0]
            else:
                lobby["veto_turn"] = lobby["ct_team"][0]
            # --- New Map Veto UI ---
            desc = f"Captain {lobby['last_ban']['captain'].mention} has banned: **{lobby['last_ban']['map']}**\n\n"
            maps_str = "\n".join([f"| **{m}**" for m in lobby["remaining_maps"]])
            desc += f"{maps_str}\n\n"
            if len(lobby["remaining_maps"]) > 1:
                desc += f"Captain {lobby['veto_turn'].mention} is currently banning…"
            else:
                desc += "Map veto complete."
            embed = discord.Embed(
                title="Map Veto Phase", description=desc, color=EMBED_COLORS["veto"]
            )
            embed.set_footer(text="Next Phase: Final Match Data")
            view = (
                self.get_veto_view(lobby) if len(lobby["remaining_maps"]) > 1 else None
            )
            await lobby["veto_message"].edit(embed=embed, view=view)
            if len(lobby["remaining_maps"]) == 1:
                await self.finalize_match(lobby)

    async def finalize_match(self, lobby):
        import os
        from jinja2 import Environment, FileSystemLoader
        from html2image import Html2Image
        import tempfile
        import datetime

        lobby["phase"] = 4
        ct_team = [m.display_name for m in lobby["ct_team"]]
        t_team = [m.display_name for m in lobby["t_team"]]
        final_map = lobby["remaining_maps"][0]
        now = datetime.datetime.now().strftime("%H:%M | %d/%m/%Y")

        # Create match in Supabase first to get the auto-generated ID
        match_result = await self.add_match_to_supabase(lobby, final_map)
        match_id = f"Match #{match_result['match_id']}"
        lobby["match_id"] = match_id

        # Render HTML with Jinja2
        env = Environment(loader=FileSystemLoader(os.path.dirname(__file__) + "/../"))
        template = env.get_template("final_match_template.html")
        html_content = template.render(
            app_name=Config.APP_NAME,
            match_id=match_id,
            ct_team=ct_team,
            t_team=t_team,
            map_name=final_map,
        )
        # Render HTML to image
        hti = Html2Image(output_path=tempfile.gettempdir())
        image_path = os.path.join(
            tempfile.gettempdir(), f"final_match_{match_result['match_id']}.png"
        )
        hti.screenshot(
            html_str=html_content, save_as=os.path.basename(image_path), size=(740, 711)
        )
        # Upload image to Discord CDN by sending it as a file
        file = discord.File(image_path, filename="final_match.png")
        embed = discord.Embed(
            title=f"{Config.APP_NAME} | {match_id}", color=EMBED_COLORS["final"]
        )
        embed.set_image(url=f"attachment://final_match.png")
        embed.set_footer(text=f"Match Created: {now}")

        # Create the Get ID button view
        get_id_view = self.GetIdView(lobby, self)

        await lobby["veto_message"].edit(
            embed=embed, view=get_id_view, attachments=[file]
        )
        await self.create_team_voice_channels(lobby)
        lobby["just_finalized"] = True
        await self.reset_lobby(lobby, keep_voice_members=False)
        # Save lobby state after finalization
        await self.save_lobby_state(lobby["category"].id)

    async def add_match_to_supabase(self, lobby, final_map):
        try:
            result = await Match.create(lobby["ct_team"], lobby["t_team"], final_map)
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
        for member in lobby["ct_team"]:
            overwrites_ct[member] = discord.PermissionOverwrite(
                view_channel=True, connect=True, speak=True
            )
        for member in lobby["t_team"]:
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
        lobby["ct_voice"] = ct_voice
        lobby["t_voice"] = t_voice
        # Move players to their team channels
        for member in lobby["ct_team"]:
            try:
                await member.move_to(ct_voice)
            except Exception as e:
                logger.error(f"Failed to move {member} to CT Voice: {e}")
        for member in lobby["t_team"]:
            try:
                await member.move_to(t_voice)
            except Exception as e:
                logger.error(f"Failed to move {member} to T Voice: {e}")
        # Start cleanup timer (placeholder)
        # ...

    async def reset_lobby(self, lobby, keep_voice_members=False):
        lobby["phase"] = 1
        if keep_voice_members:
            voice_members = set(lobby["voice"].members)
            lobby["players"] = voice_members
        else:
            lobby["players"] = set()
        lobby["ct_team"] = []
        lobby["t_team"] = []
        lobby["map_pool"] = Config.MAP_POOL.copy()
        lobby["match_id"] = None
        text = lobby["text"]
        # Only create a new waiting embed if this is the first time or after a match (not on every reset)
        if not lobby.get("message") or lobby.pop("just_finalized", False):
            embed = discord.Embed(
                title=f"LOBBY #{lobby.get('lobby_number', 'X')} STATUS",
                description=f"Players in Queue: {len(lobby['players'])}/4\nPhase: Waiting for players...",
                color=EMBED_COLORS["waiting"],
            )
            if lobby.get("message"):
                try:
                    await lobby["message"].delete()
                except Exception as e:
                    logger.error(f"Failed to delete old lobby message: {e}")
            lobby["message"] = await text.send(embed=embed)
        else:
            # Update existing message with proper waiting phase embed properties
            embed = discord.Embed(
                title=f"LOBBY #{lobby.get('lobby_number', 'X')} STATUS",
                description=f"Players in Queue: {len(lobby['players'])}/4\nPhase: Waiting for players...",
                color=EMBED_COLORS["waiting"],
            )
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
                        title="LOBBY STATUS",
                        description="Players in Queue: 0/4\nPhase: Waiting for players...",
                        color=EMBED_COLORS["waiting"],
                    )
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
                "ct_team": [],
                "t_team": [],
                "map_pool": json.loads(state.get("map_pool", "[]"))
                or Config.MAP_POOL.copy(),
                "match_id": None,  # Clear match ID on restore
                "ct_voice": None,
                "t_voice": None,
                "lobby_number": state.get("lobby_number", 1),
                "required_role_id": state.get("required_role_id"),
                "required_role_name": state.get(
                    "required_role_name", Config.REGISTERED_ROLE
                ),
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
            logger.error(
                f"Failed to delete lobby state for category {category_id}: {e}"
            )

    @app_commands.command(
        name="cleanup_lobbies",
        description="Admin command to clean up old lobby states.",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def cleanup_lobbies(self, interaction: discord.Interaction, days: int = 7):
        """Clean up lobby states older than specified days"""
        try:
            result = await LobbyState.cleanup_old_lobbies(days)

            # Create success embed
            embed = discord.Embed(
                title="✅ Lobby Cleanup Completed",
                description=f"Successfully cleaned up old lobby states from the database.",
                color=discord.Color.green(),
            )
            embed.add_field(name="Cleanup Period", value=f"{days} days", inline=True)
            embed.add_field(name="Status", value="All old states removed", inline=True)
            embed.set_footer(text="Database has been optimized and cleaned up.")

            await interaction.response.send_message(embed=embed, ephemeral=True)
        except Exception as e:
            logger.error(f"Failed to cleanup old lobbies: {e}")

            # Create error embed
            embed = discord.Embed(
                title="❌ Cleanup Failed",
                description="An error occurred while cleaning up old lobby states.",
                color=discord.Color.red(),
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
    @app_commands.checks.has_permissions(administrator=True)
    async def delete_lobby(self, interaction: discord.Interaction, lobby_number: int):
        """Delete a lobby and clean up its state"""
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
                    title="❌ Lobby Not Found",
                    description=f"**Lobby #{lobby_number}** could not be found.",
                    color=discord.Color.orange(),
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
                    pass  # Channel was already deleted
                except Exception as e:
                    logger.error(
                        f"Failed to delete channel {channel.name}: {e}"
                    )  # Finally delete the category
            try:
                await target_lobby["category"].delete()
                logger.info(f"Deleted category for lobby #{lobby_number}")
            except discord.NotFound:
                pass  # Category was already deleted
            except Exception as e:
                logger.error(f"Failed to delete category: {e}")
                raise

            # Remove from active lobbies
            if target_category_id in self.active_lobbies:
                del self.active_lobbies[target_category_id]

            # Delete from database
            await self.delete_lobby_state(target_category_id)

            # Create success embed
            embed = discord.Embed(
                title="✅ Lobby Deleted Successfully",
                description=f"**Lobby #{lobby_number}** has been permanently deleted.",
                color=discord.Color.green(),
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
                    title="❌ Failed to Delete Lobby",
                    description=f"An error occurred while deleting **Lobby #{lobby_number}**.",
                    color=discord.Color.red(),
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
                    "❌ You don't have permission to check other users' timeout status.",
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
                        title="⏰ Timeout Status",
                        description="No users are currently timeouted.",
                        color=discord.Color.green(),
                    )
                else:
                    embed = discord.Embed(
                        title="⏰ Active Timeouts",
                        description=f"Found {len(timeouts)} active timeout(s):",
                        color=discord.Color.orange(),
                    )

                    for timeout in timeouts[:10]:  # Limit to 10 to avoid embed limits
                        try:
                            user_obj = await self.bot.fetch_user(timeout["user_id"])
                            user_name = (
                                user_obj.display_name
                                if user_obj
                                else f"User {timeout['user_id']}"
                            )
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
                        title="⏰ Timeout Status",
                        description=f"{'You are' if target_user == interaction.user else f'{target_user.display_name} is'} currently timeouted.",
                        color=discord.Color.red(),
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
                        title="⏰ Timeout Status",
                        description=f"{'You are' if target_user == interaction.user else f'{target_user.display_name} is'} not currently timeouted.",
                        color=discord.Color.green(),
                    )

            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as e:
            logger.error(f"Failed to check timeout status: {e}")
            embed = discord.Embed(
                title="❌ Error",
                description="Failed to check timeout status. Please try again.",
                color=discord.Color.red(),
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(
        name="clear_timeout", description="Admin command to clear a user's timeout."
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def clear_timeout(
        self, interaction: discord.Interaction, user: discord.Member
    ):
        """Clear a specific user's timeout"""
        await interaction.response.defer(ephemeral=True)

        try:
            # Check if user is actually timeouted
            is_timeouted, timeout_until = await timeout_manager.is_user_timeouted(
                user.id
            )

            if not is_timeouted:
                embed = discord.Embed(
                    title="⏰ Timeout Status",
                    description=f"{user.display_name} is not currently timeouted.",
                    color=discord.Color.yellow(),
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
                return

            # Remove the timeout
            success = await timeout_manager.remove_timeout(user.id)

            if success:
                embed = discord.Embed(
                    title="✅ Timeout Cleared",
                    description=f"Successfully cleared timeout for {user.display_name}.",
                    color=discord.Color.green(),
                )

                # Try to notify the user
                try:
                    # Create timeout cleared notification embed
                    user_embed = discord.Embed(
                        title="✅ Timeout Cleared",
                        description="Your timeout has been cleared by an administrator.",
                        color=discord.Color.green(),
                    )
                    user_embed.add_field(
                        name="Status",
                        value="🟢 You can now join lobbies again",
                        inline=False,
                    )
                    user_embed.set_footer(
                        text=f"Welcome back to {Config.APP_NAME} matchmaking!"
                    )

                    await user.send(embed=user_embed)
                    embed.set_footer(text="User has been notified.")
                except:
                    embed.set_footer(text="User could not be notified (DMs disabled).")
            else:
                embed = discord.Embed(
                    title="❌ Failed to Clear Timeout",
                    description=f"Failed to clear timeout for {user.display_name}. Please try again.",
                    color=discord.Color.red(),
                )

            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as e:
            logger.error(f"Failed to clear timeout for user {user.id}: {e}")
            embed = discord.Embed(
                title="❌ Error",
                description="Failed to clear timeout. Please try again.",
                color=discord.Color.red(),
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(
        name="clear_all_timeouts",
        description="Admin command to clear all active timeouts.",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def clear_all_timeouts(self, interaction: discord.Interaction):
        """Clear all active timeouts"""
        await interaction.response.defer(ephemeral=True)

        try:
            # Get current timeouts count first
            timeouts = await timeout_manager.get_all_timeouts()
            timeout_count = len(timeouts)

            if timeout_count == 0:
                embed = discord.Embed(
                    title="⏰ No Timeouts",
                    description="There are no active timeouts to clear.",
                    color=discord.Color.yellow(),
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
                return

            # Clear all timeouts
            success = await timeout_manager.clear_all_timeouts()

            if success:
                embed = discord.Embed(
                    title="✅ All Timeouts Cleared",
                    description=f"Successfully cleared {timeout_count} timeout(s).",
                    color=discord.Color.green(),
                )
                embed.set_footer(text="All users can now join lobbies again.")
            else:
                embed = discord.Embed(
                    title="❌ Failed to Clear Timeouts",
                    description="Failed to clear all timeouts. Please try again.",
                    color=discord.Color.red(),
                )

            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as e:
            logger.error(f"Failed to clear all timeouts: {e}")
            embed = discord.Embed(
                title="❌ Error",
                description="Failed to clear all timeouts. Please try again.",
                color=discord.Color.red(),
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(
        name="cleanup_team_channels",
        description="Admin command to clean up orphaned team voice channels.",
    )
    @app_commands.checks.has_permissions(administrator=True)
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
                    title="✅ Team Channels Cleaned Up",
                    description=f"Successfully deleted **{total_deleted}** orphaned team voice channels.",
                    color=discord.Color.green(),
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
                    title="✅ No Cleanup Needed",
                    description="No orphaned team voice channels were found.",
                    color=discord.Color.green(),
                )
                embed.set_footer(text="All team voice channels are properly tracked.")

            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as e:
            logger.error(f"Failed to cleanup team channels: {e}")
            embed = discord.Embed(
                title="❌ Cleanup Failed",
                description="An error occurred while cleaning up team voice channels.",
                color=discord.Color.red(),
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

    async def create_draft_options_with_stats(self, available_players):
        """Create dropdown options with player stats as descriptions"""
        options = []
        for member in available_players:
            try:
                user_data = await User.find(member.id)
                if user_data:
                    points = user_data.get("points", 0)
                    kills = user_data.get("kills", 0)
                    deaths = user_data.get("deaths", 0)
                    wins = user_data.get("wins", 0)
                    losses = user_data.get("loses", 0)

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

                    # Get level emoji based on points
                    level_emoji = LevelsCog.get_level_emoji(points)
                else:
                    # Fallback if no data found
                    description = "No stats available"
                    level_emoji = LevelsCog.get_level_emoji(
                        0
                    )  # Default to level 1 emoji

                options.append(
                    discord.SelectOption(
                        label=member.display_name,
                        value=str(member.id),
                        description=description,
                        emoji=level_emoji,
                    )
                )
            except Exception as e:
                # Fallback in case of error
                logger.error(f"Error fetching stats for {member.display_name}: {e}")
                options.append(
                    discord.SelectOption(
                        label=member.display_name,
                        value=str(member.id),
                        description="Stats unavailable",
                        emoji=LevelsCog.get_level_emoji(0),  # Default to level 1 emoji
                    )
                )
        return options

    # --- LOBBY LOCKING COMMANDS ---

    @app_commands.command(
        name="lock_lobbies",
        description="Admin command to lock all lobbies - prevents Registered role from connecting to voice.",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def lock_lobbies(self, interaction: discord.Interaction):
        """Lock all lobbies to prevent Registered role from connecting"""
        await interaction.response.defer(ephemeral=True)

        try:
            if self.lobbies_locked:
                embed = discord.Embed(
                    title="⚠️ Lobbies Already Locked",
                    description="Lobbies are already locked. Registered players cannot connect to lobby voice channels.",
                    color=discord.Color.orange(),
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
                title="🔒 Lobbies Locked Successfully",
                description="All lobbies have been locked. Registered players can see but cannot connect to lobby voice channels.",
                color=discord.Color.red(),
            )
            embed.add_field(
                name="Lobbies Updated",
                value=f"{lobbies_updated} active lobbies",
                inline=True,
            )
            embed.add_field(
                name="Effect",
                value="🚫 Registered role cannot connect to voice",
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
                title="❌ Failed to Lock Lobbies",
                description="An error occurred while locking lobbies.",
                color=discord.Color.red(),
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
    @app_commands.checks.has_permissions(administrator=True)
    async def unlock_lobbies(self, interaction: discord.Interaction):
        """Unlock all lobbies to restore Registered role voice access"""
        await interaction.response.defer(ephemeral=True)

        try:
            if not self.lobbies_locked:
                embed = discord.Embed(
                    title="⚠️ Lobbies Already Unlocked",
                    description="Lobbies are already unlocked. Registered players have full access to lobby voice channels.",
                    color=discord.Color.orange(),
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
                title="🔓 Lobbies Unlocked Successfully",
                description="All lobbies have been unlocked. Registered players now have full voice access to lobby channels.",
                color=discord.Color.green(),
            )
            embed.add_field(
                name="Lobbies Updated",
                value=f"{lobbies_updated} active lobbies",
                inline=True,
            )
            embed.add_field(
                name="Effect",
                value="✅ Registered role can connect to voice",
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
                title="❌ Failed to Unlock Lobbies",
                description="An error occurred while unlocking lobbies.",
                color=discord.Color.red(),
            )
            embed.add_field(
                name="Error",
                value="Lobby unlocking operation failed",
                inline=False,
            )
            embed.set_footer(text="Please try again or contact an administrator.")
            await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Matchmaking(bot))
