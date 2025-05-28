import discord
from discord.ext import commands, tasks
from discord import app_commands
from config import Config
from models.match import Match
from models.lobby_state import LobbyState
import random
import asyncio
import logging
from collections import defaultdict

logger = logging.getLogger(__name__)

LOBBY_CATEGORY_PREFIX = "PlayHub | Lobby #"
LOBBY_VOICE_NAME = "Lobby"
LOBBY_TEXT_NAME = "Lobby"
CT_VOICE_NAME = "CT Voice"
T_VOICE_NAME = "T Voice"

EMBED_COLORS = {
    "waiting": 0xFFFF00,  # Yellow
    "draft": 0x3498DB,  # Blue
    "veto": 0x9B59B6,  # Purple
    "final": 0xB2FA19,  # Green
}


class Matchmaking(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.lobby_counter = 1
        self.active_lobbies = {}
        self.cleanup_voice_channels.start()
        self.lobby_locks = defaultdict(asyncio.Lock)  # Prevent race conditions
        # Restore lobbies on startup
        asyncio.create_task(self.restore_lobbies())

    @app_commands.command(
        name="create_lobby", description="Create a new matchmaking lobby."
    )
    async def create_lobby(self, interaction: discord.Interaction):
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

                lobby_number = self.lobby_counter
                self.lobby_counter += 1
                category_name = (
                    f"{LOBBY_CATEGORY_PREFIX}{lobby_number}"  # Check bot permissions
                )
                if not guild.me.guild_permissions.manage_channels:
                    raise ValueError(
                        "Bot doesn't have permission to manage channels in this server."
                    )

                # Get the Registered role
                registered_role = discord.utils.get(
                    guild.roles, name=Config.REGISTERED_ROLE
                )
                if not registered_role:
                    # Create the role if it doesn't exist
                    registered_role = await guild.create_role(
                        name=Config.REGISTERED_ROLE,
                        color=0xB2FA19,  # Vibrant green color
                        reason="Auto-created for registered players",
                    )

                # Set up permission overwrites
                overwrites = {
                    guild.default_role: discord.PermissionOverwrite(
                        view_channel=False, connect=False
                    ),  # Deny @everyone
                    registered_role: discord.PermissionOverwrite(
                        view_channel=True,
                        connect=True,
                        send_messages=True,
                        read_messages=True,
                    ),  # Allow registered players
                    guild.me: discord.PermissionOverwrite(
                        view_channel=True,
                        connect=True,
                        manage_channels=True,
                        send_messages=True,
                        read_messages=True,
                    ),  # Allow bot
                }

                # Allow administrators to access lobbies
                for role in guild.roles:
                    if role.permissions.administrator:
                        overwrites[role] = discord.PermissionOverwrite(
                            view_channel=True,
                            connect=True,
                            send_messages=True,
                            read_messages=True,
                        )

                category = await guild.create_category(
                    category_name, overwrites=overwrites
                )
                voice = await category.create_voice_channel(
                    LOBBY_VOICE_NAME, user_limit=4
                )
                text = await category.create_text_channel(LOBBY_TEXT_NAME)
                embed = discord.Embed(
                    title="LOBBY STATUS",
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
                }  # Save lobby state to database
                await self.save_lobby_state(category.id)  # Create success embed
                embed = discord.Embed(
                    title="✅ Lobby Created Successfully",
                    description="A new lobby has been created and is ready for registered players!",
                    color=EMBED_COLORS["waiting"],
                )
                embed.add_field(
                    name="Lobby Number", value=f"#{lobby_number}", inline=True
                )
                embed.add_field(name="Players", value="0/4", inline=True)
                embed.add_field(name="Status", value="Waiting for players", inline=True)
                embed.add_field(
                    name="Access", value="🔒 Registered players only", inline=False
                )
                embed.set_footer(
                    text="Only registered players can see and join this lobby!"
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
        def __init__(self, lobby, cog):
            options = [
                discord.SelectOption(label=member.display_name, value=str(member.id))
                for member in lobby["available_players"]
            ]
            super().__init__(
                placeholder="Select a player to draft...",
                min_values=1,
                max_values=1,
                options=options,
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
        def __init__(self, lobby, cog):
            super().__init__(timeout=120)
            self.lobby = lobby
            self.cog = cog
            self.add_item(Matchmaking.DraftDropdown(lobby, cog))

        async def on_timeout(self):
            # Only trigger draft timeout if phase is 2 (drafting)
            if self.lobby["phase"] == 2:
                await self.cog.handle_timeout(self.lobby, reason="Draft pick timeout")

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
            # Only trigger veto timeout if phase is 3 (veto)
            if self.lobby["phase"] == 3:
                await self.cog.handle_timeout(self.lobby, reason="Map veto timeout")

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

    def get_draft_view(self, lobby):
        return Matchmaking.DraftView(lobby, self)

    def get_veto_view(self, lobby):
        return Matchmaking.VetoView(lobby, self)

    # --- TIMEOUT & ERROR HANDLING ---
    async def handle_timeout(self, lobby, reason="Timeout"):
        # Only remove users if not in waiting phase
        if lobby["phase"] == 1:
            return  # Ignore timeouts in waiting phase
        for member in lobby["players"]:
            try:
                await member.send(
                    f"You have been removed from the lobby due to: {reason}. 2-minute timeout applied."
                )
            except Exception:
                logger.error(f"Failed to send timeout message to {member}: {reason}")
                pass
        await self.reset_lobby(lobby)

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
        for lobby in self.active_lobbies.values():
            if after.channel and after.channel.id == lobby["voice"].id:
                if member not in lobby["players"]:
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
                    try:
                        await member.send(
                            "You have been removed from the lobby due to leaving mid-game. 2-minute timeout applied."
                        )
                    except Exception as e:
                        logger.error(
                            f"Failed to send mid-game leave message to {member}: {e}"
                        )
                    await self.reset_lobby(lobby, keep_voice_members=True)

    async def update_lobby_status(self, lobby):
        """Update the lobby status embed"""
        # Only update the embed if the lobby is in phase 1 (waiting for players)
        if lobby["phase"] == 1:
            try:
                embed = discord.Embed(
                    title="LOBBY STATUS",
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
        ct_lines = "\n".join([m.mention for m in lobby["ct_team"]]) or "-"
        t_lines = "\n".join([m.mention for m in lobby["t_team"]]) or "-"
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
        view = self.get_draft_view(lobby)
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
            # --- New Draft UI ---
            ct_lines = "\n".join([m.mention for m in lobby["ct_team"]]) or "-"
            t_lines = "\n".join([m.mention for m in lobby["t_team"]]) or "-"
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
            view = self.get_draft_view(lobby) if lobby["available_players"] else None
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
        match_id = f"Match #{random.randint(1000, 9999)}"
        lobby["match_id"] = match_id
        ct_team = [m.display_name for m in lobby["ct_team"]]
        t_team = [m.display_name for m in lobby["t_team"]]
        final_map = lobby["remaining_maps"][0]
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        # Render HTML with Jinja2
        env = Environment(loader=FileSystemLoader(os.path.dirname(__file__) + "/../"))
        template = env.get_template("final_match_template.html")
        html_content = template.render(
            match_id=match_id, ct_team=ct_team, t_team=t_team, map_name=final_map
        )
        # Render HTML to image
        hti = Html2Image(output_path=tempfile.gettempdir())
        image_path = os.path.join(
            tempfile.gettempdir(), f"final_match_{match_id.replace('#','')}.png"
        )
        hti.screenshot(
            html_str=html_content, save_as=os.path.basename(image_path), size=(740, 711)
        )  # Upload image to Discord CDN by sending it as a file
        file = discord.File(image_path, filename="final_match.png")
        embed = discord.Embed(
            title=f"PlayHub | {match_id}", color=EMBED_COLORS["final"]
        )
        embed.set_image(url=f"attachment://final_match.png")
        embed.set_footer(text=f"Match Created: {now}")

        # Create the Get ID button view
        get_id_view = self.GetIdView(lobby, self)

        await lobby["veto_message"].edit(
            embed=embed, view=get_id_view, attachments=[file]
        )
        await self.add_match_to_supabase(match_id, lobby, final_map)
        await self.create_team_voice_channels(lobby)
        lobby["just_finalized"] = True
        await self.reset_lobby(lobby, keep_voice_members=False)

        # Save lobby state after finalization
        await self.save_lobby_state(lobby["category"].id)

    async def add_match_to_supabase(self, match_id, lobby, final_map):
        try:
            await Match.create(match_id, lobby["ct_team"], lobby["t_team"], final_map)
        except Exception as e:
            logger.error(f"Failed to insert match into supabase: {e}")

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
                title="LOBBY STATUS",
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
            embed = lobby["message"].embeds[0]
            embed.description = f"Players in Queue: {len(lobby['players'])}/4\nPhase: Waiting for players..."
            await lobby["message"].edit(embed=embed, view=None)
        if hasattr(lobby, "_just_finalized"):
            del lobby._just_finalized

        # Save lobby state after reset
        await self.save_lobby_state(lobby["category"].id)

    # --- LOBBY PERSISTENCE METHODS ---

    async def save_lobby_state(self, category_id: int):
        """Save lobby state to database"""
        try:
            lobby = self.active_lobbies.get(category_id)
            if lobby:
                await LobbyState.save_lobby(
                    lobby["category"].guild.id, category_id, lobby
                )
                logger.info(f"Saved lobby state for category {category_id}")
        except Exception as e:
            logger.error(f"Failed to save lobby state for category {category_id}: {e}")

    async def restore_lobbies(self):
        """Restore lobbies from database on bot startup"""
        try:
            # Wait for bot to be ready
            await self.bot.wait_until_ready()

            # Get all guilds the bot is in
            for guild in self.bot.guilds:
                try:
                    lobby_states = await LobbyState.load_lobbies(guild.id)

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
            current_players = set(voice_channel.members) if voice_channel else set()

            # Restore lobby data
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

    # Add error handling, timeouts, and cleanup logic as next steps


async def setup(bot):
    await bot.add_cog(Matchmaking(bot))
