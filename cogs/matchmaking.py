import discord
from discord.ext import commands, tasks
from discord import app_commands
from config import Config
from models.match import Match
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
    'waiting': 0xFFFF00,  # Yellow
    'draft': 0x3498DB,   # Blue
    'veto': 0x9B59B6,    # Purple
    'final': 0xB2FA19    # Green
}

class Matchmaking(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.lobby_counter = 1
        self.active_lobbies = {}
        self.cleanup_voice_channels.start()
        self.lobby_locks = defaultdict(asyncio.Lock)  # Prevent race conditions

    @app_commands.command(name="create_lobby", description="Create a new matchmaking lobby.")
    async def create_lobby(self, interaction: discord.Interaction):
        # Check if interaction has a valid guild
        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        async with self.lobby_locks[interaction.guild.id]:
            try:
                guild = interaction.guild
                if not guild:
                    raise ValueError("Could not access the server.")

                lobby_number = self.lobby_counter
                self.lobby_counter += 1
                category_name = f"{LOBBY_CATEGORY_PREFIX}{lobby_number}"
                
                # Check bot permissions
                if not guild.me.guild_permissions.manage_channels:
                    raise ValueError("Bot doesn't have permission to manage channels in this server.")

                category = await guild.create_category(category_name)
                voice = await category.create_voice_channel(LOBBY_VOICE_NAME, user_limit=4)
                text = await category.create_text_channel(LOBBY_TEXT_NAME)
                embed = discord.Embed(title="LOBBY STATUS", description="Players in Queue: 0/4\nPhase: Waiting for players...", color=EMBED_COLORS['waiting'])
                msg = await text.send(embed=embed)
                
                self.active_lobbies[category.id] = {
                    'category': category,
                    'voice': voice,
                    'text': text,
                    'phase': 1,
                    'players': set(),
                    'message': msg,
                    'ct_team': [],
                    't_team': [],
                    'map_pool': Config.MAP_POOL.copy(),
                    'match_id': None,
                    'ct_voice': None,
                    't_voice': None
                }
                await interaction.response.send_message(f"Lobby created: {category_name}", ephemeral=True)
            except ValueError as ve:
                logger.error(f"Error creating lobby - validation error: {ve}")
                await interaction.response.send_message(f"Failed to create lobby: {ve}", ephemeral=True)
            except Exception as e:
                logger.error(f"Error creating lobby - unexpected error: {e}")
                await interaction.response.send_message("Failed to create lobby due to an unexpected error. Please try again later.", ephemeral=True)

    # --- DRAFT PICK UI ---
    class DraftDropdown(discord.ui.Select):
        def __init__(self, lobby, cog):
            options = [discord.SelectOption(label=member.display_name, value=str(member.id)) for member in lobby['available_players']]
            super().__init__(placeholder="Select a player to draft...", min_values=1, max_values=1, options=options)
            self.lobby = lobby
            self.cog = cog

        async def callback(self, interaction: discord.Interaction):
            # Only allow the captain whose turn it is
            if interaction.user != self.lobby['next_pick']:
                await interaction.response.send_message("You are not the captain or it's not your turn to pick.", ephemeral=True)
                return

            picked_id = int(self.values[0])
            picked_member = discord.utils.get(self.lobby['available_players'], id=picked_id)
            
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
            if self.lobby['phase'] == 2:
                await self.cog.handle_timeout(self.lobby, reason="Draft pick timeout")

    # --- MAP VETO UI ---
    class VetoDropdown(discord.ui.Select):
        def __init__(self, lobby, cog):
            options = [discord.SelectOption(label=map_name, value=map_name) for map_name in lobby['remaining_maps']]
            super().__init__(placeholder="Ban a map...", min_values=1, max_values=1, options=options)
            self.lobby = lobby
            self.cog = cog

        async def callback(self, interaction: discord.Interaction):
            # Only allow the captain whose turn it is
            if interaction.user != self.lobby['veto_turn']:
                await interaction.response.send_message("You are not the captain or it's not your turn to ban.", ephemeral=True)
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
            if self.lobby['phase'] == 3:
                await self.cog.handle_timeout(self.lobby, reason="Map veto timeout")

    def get_draft_view(self, lobby):
        return Matchmaking.DraftView(lobby, self)

    def get_veto_view(self, lobby):
        return Matchmaking.VetoView(lobby, self)

    # --- TIMEOUT & ERROR HANDLING ---
    async def handle_timeout(self, lobby, reason="Timeout"):
        # Only remove users if not in waiting phase
        if lobby['phase'] == 1:
            return  # Ignore timeouts in waiting phase
        for member in lobby['players']:
            try:
                await member.send(f"You have been removed from the lobby due to: {reason}. 2-minute timeout applied.")
            except Exception:
                logger.error(f"Failed to send timeout message to {member}: {reason}")
                pass
        await self.reset_lobby(lobby)

    @tasks.loop(seconds=30)
    async def cleanup_voice_channels(self):
        now = discord.utils.utcnow()
        if not hasattr(self, '_empty_since'):
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
            for team in ['ct_voice', 't_voice']:
                channel = lobby.get(team)
                if channel and channel.id not in self._empty_since and len(channel.members) == 0:
                    self._empty_since[channel.id] = now

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        for lobby in self.active_lobbies.values():
            if after.channel and after.channel.id == lobby['voice'].id:
                if member not in lobby['players']:
                    lobby['players'].add(member)
                await self.update_lobby_status(lobby)
                if len(lobby['players']) == 4 and lobby['phase'] == 1:
                    await self.start_captain_selection(lobby)
            if before.channel and before.channel.id == lobby['voice'].id and (not after.channel or after.channel.id != lobby['voice'].id):
                if member in lobby['players']:
                    lobby['players'].remove(member)
                await self.update_lobby_status(lobby)
                if lobby['phase'] in [2, 3]:
                    try:
                        await member.send("You have been removed from the lobby due to leaving mid-game. 2-minute timeout applied.")
                    except Exception as e:
                        logger.error(f"Failed to send mid-game leave message to {member}: {e}")
                    await self.reset_lobby(lobby, keep_voice_members=True)

    async def update_lobby_status(self, lobby):
        # Only update the embed if the lobby is in phase 1 (waiting for players)
        if lobby['phase'] == 1:
            embed = discord.Embed(
                title="LOBBY STATUS",
                description=f"Players in Queue: {len(lobby['players'])}/4\nPhase: Waiting for players...",
                color=EMBED_COLORS['waiting']
            )
            text = lobby['text']
            if lobby['message']:
                await lobby['message'].edit(embed=embed, view=None)
            else:
                lobby['message'] = await text.send(embed=embed)

    async def start_captain_selection(self, lobby):
        lobby['phase'] = 2
        players = list(lobby['players'])
        captains = random.sample(players, 2)
        ct_captain, t_captain = captains[0], captains[1]
        lobby['ct_team'] = [ct_captain]
        lobby['t_team'] = [t_captain]
        lobby['available_players'] = [p for p in players if p not in captains]
        lobby['next_pick'] = ct_captain
        # --- New Draft UI ---
        ct_lines = '\n'.join([m.mention for m in lobby['ct_team']]) or '-'
        t_lines = '\n'.join([m.mention for m in lobby['t_team']]) or '-'
        desc = (
            f"**Counter-Terrorists**\n{ct_lines}\n\n"
            f"**Terrorists**\n{t_lines}\n\n"
            f"Captain {lobby['next_pick'].mention} is currently picking…"
        )
        embed = discord.Embed(
            title="Drafting Players Phase",
            description=desc,
            color=EMBED_COLORS['draft']
        )
        embed.set_footer(text="Next Phase: Map Veto")
        view = self.get_draft_view(lobby)
        await lobby['message'].edit(embed=embed, view=view)

    async def handle_draft_pick(self, lobby, picked_member):
        async with self.lobby_locks[lobby['category'].guild.id]:
            # Add picked player to the correct team
            if lobby['next_pick'] == lobby['ct_team'][0]:
                lobby['ct_team'].append(picked_member)
                lobby['next_pick'] = lobby['t_team'][0]
            else:
                lobby['t_team'].append(picked_member)
                lobby['next_pick'] = lobby['ct_team'][0]
            lobby['available_players'].remove(picked_member)
            # --- New Draft UI ---
            ct_lines = '\n'.join([m.mention for m in lobby['ct_team']]) or '-'
            t_lines = '\n'.join([m.mention for m in lobby['t_team']]) or '-'
            desc = (
                f"**Counter-Terrorists**\n{ct_lines}\n\n"
                f"**Terrorists**\n{t_lines}\n\n"
            )
            if lobby['available_players']:
                desc += f"Captain {lobby['next_pick'].mention} is currently picking…"
            else:
                desc += "All players have been drafted."
            embed = discord.Embed(
                title="Drafting Players Phase",
                description=desc,
                color=EMBED_COLORS['draft']
            )
            embed.set_footer(text="Next Phase: Map Veto")
            view = self.get_draft_view(lobby) if lobby['available_players'] else None
            await lobby['message'].edit(embed=embed, view=view)
            if not lobby['available_players']:
                await self.start_map_veto(lobby)

    async def start_map_veto(self, lobby):
        lobby['phase'] = 3
        lobby['veto_turn'] = lobby['ct_team'][0]  # CT starts
        lobby['remaining_maps'] = Config.MAP_POOL.copy()
        lobby['last_ban'] = None  # Track previous ban
        text = lobby['text']
        # --- New Map Veto UI ---
        desc = ""
        if lobby['last_ban']:
            desc += f"Captain {lobby['last_ban']['captain'].mention} has banned: **{lobby['last_ban']['map']}**\n\n"
        maps_str = '\n'.join([f"| **{m}**" for m in lobby['remaining_maps']])
        desc += f"{maps_str}\n\n"
        desc += f"Captain {lobby['veto_turn'].mention} is currently banning…"
        embed = discord.Embed(
            title="Map Veto Phase",
            description=desc,
            color=EMBED_COLORS['veto']
        )
        embed.set_footer(text="Next Phase: Final Match Data")
        view = self.get_veto_view(lobby)
        lobby['veto_message'] = await text.send(embed=embed, view=view)

    async def handle_map_ban(self, lobby, banned_map):
        async with self.lobby_locks[lobby['category'].guild.id]:
            # Track last ban
            lobby['last_ban'] = {'captain': lobby['veto_turn'], 'map': banned_map}
            lobby['remaining_maps'].remove(banned_map)
            # Switch turn
            if lobby['veto_turn'] == lobby['ct_team'][0]:
                lobby['veto_turn'] = lobby['t_team'][0]
            else:
                lobby['veto_turn'] = lobby['ct_team'][0]
            # --- New Map Veto UI ---
            desc = f"Captain {lobby['last_ban']['captain'].mention} has banned: **{lobby['last_ban']['map']}**\n\n"
            maps_str = '\n'.join([f"| **{m}**" for m in lobby['remaining_maps']])
            desc += f"{maps_str}\n\n"
            if len(lobby['remaining_maps']) > 1:
                desc += f"Captain {lobby['veto_turn'].mention} is currently banning…"
            else:
                desc += "Map veto complete."
            embed = discord.Embed(
                title="Map Veto Phase",
                description=desc,
                color=EMBED_COLORS['veto']
            )
            embed.set_footer(text="Next Phase: Final Match Data")
            view = self.get_veto_view(lobby) if len(lobby['remaining_maps']) > 1 else None
            await lobby['veto_message'].edit(embed=embed, view=view)
            if len(lobby['remaining_maps']) == 1:
                await self.finalize_match(lobby)

    async def finalize_match(self, lobby):
        import os
        from jinja2 import Environment, FileSystemLoader
        from html2image import Html2Image
        import tempfile
        import datetime
        lobby['phase'] = 4
        match_id = f"Match #{random.randint(1000, 9999)}"
        lobby['match_id'] = match_id
        ct_team = [m.display_name for m in lobby['ct_team']]
        t_team = [m.display_name for m in lobby['t_team']]
        final_map = lobby['remaining_maps'][0]
        now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
        # Render HTML with Jinja2
        env = Environment(loader=FileSystemLoader(os.path.dirname(__file__) + '/../'))
        template = env.get_template('final_match_template.html')
        html_content = template.render(match_id=match_id, ct_team=ct_team, t_team=t_team, map_name=final_map)
        # Render HTML to image
        hti = Html2Image(output_path=tempfile.gettempdir())
        image_path = os.path.join(tempfile.gettempdir(), f"final_match_{match_id.replace('#','')}.png")
        hti.screenshot(html_str=html_content, save_as=os.path.basename(image_path), size=(740, 711))
        # Upload image to Discord CDN by sending it as a file
        file = discord.File(image_path, filename="final_match.png")
        embed = discord.Embed(
            title=f"PlayHub | {match_id}",
            color=EMBED_COLORS['final']
        )
        embed.set_image(url=f"attachment://final_match.png")
        embed.set_footer(text=f"Match Created: {now}")
        await lobby['veto_message'].edit(embed=embed, view=None, attachments=[file])
        await self.add_match_to_supabase(match_id, lobby, final_map)
        await self.create_team_voice_channels(lobby)
        lobby['just_finalized'] = True
        await self.reset_lobby(lobby, keep_voice_members=False)

    async def add_match_to_supabase(self, match_id, lobby, final_map):
        try:
            await Match.create(match_id, lobby['ct_team'], lobby['t_team'], final_map)
        except Exception as e:
            logger.error(f"Failed to insert match into supabase: {e}")

    async def create_team_voice_channels(self, lobby):
        category = lobby['category']
        guild = category.guild
        # Set permissions so only team members, the bot, and optionally admins can see/connect
        overwrites_ct = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False, connect=False),
            guild.me: discord.PermissionOverwrite(view_channel=True, connect=True, speak=True)
        }
        overwrites_t = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False, connect=False),
            guild.me: discord.PermissionOverwrite(view_channel=True, connect=True, speak=True)
        }
        # Optionally allow admins to join for moderation
        for role in guild.roles:
            if role.permissions.administrator:
                overwrites_ct[role] = discord.PermissionOverwrite(view_channel=True, connect=True, speak=True)
                overwrites_t[role] = discord.PermissionOverwrite(view_channel=True, connect=True, speak=True)
        for member in lobby['ct_team']:
            overwrites_ct[member] = discord.PermissionOverwrite(view_channel=True, connect=True, speak=True)
        for member in lobby['t_team']:
            overwrites_t[member] = discord.PermissionOverwrite(view_channel=True, connect=True, speak=True)
        # Create channels with correct permissions
        ct_voice = await category.create_voice_channel(CT_VOICE_NAME, overwrites=overwrites_ct)
        t_voice = await category.create_voice_channel(T_VOICE_NAME, overwrites=overwrites_t)
        lobby['ct_voice'] = ct_voice
        lobby['t_voice'] = t_voice
        # Move players to their team channels
        for member in lobby['ct_team']:
            try:
                await member.move_to(ct_voice)
            except Exception as e:
                logger.error(f"Failed to move {member} to CT Voice: {e}")
        for member in lobby['t_team']:
            try:
                await member.move_to(t_voice)
            except Exception as e:
                logger.error(f"Failed to move {member} to T Voice: {e}")
        # Start cleanup timer (placeholder)
        # ...

    async def reset_lobby(self, lobby, keep_voice_members=False):
        lobby['phase'] = 1
        if keep_voice_members:
            voice_members = set(lobby['voice'].members)
            lobby['players'] = voice_members
        else:
            lobby['players'] = set()
        lobby['ct_team'] = []
        lobby['t_team'] = []
        lobby['map_pool'] = Config.MAP_POOL.copy()
        lobby['match_id'] = None
        text = lobby['text']
        # Only create a new waiting embed if this is the first time or after a match (not on every reset)
        if not lobby.get('message') or lobby.pop('just_finalized', False):
            embed = discord.Embed(
                title="LOBBY STATUS",
                description=f"Players in Queue: {len(lobby['players'])}/4\nPhase: Waiting for players...",
                color=EMBED_COLORS['waiting']
            )
            if lobby.get('message'):
                try:
                    await lobby['message'].delete()
                except Exception as e:
                    logger.error(f"Failed to delete old lobby message: {e}")
            lobby['message'] = await text.send(embed=embed)
        else:
            embed = lobby['message'].embeds[0]
            embed.description = f"Players in Queue: {len(lobby['players'])}/4\nPhase: Waiting for players..."
            await lobby['message'].edit(embed=embed, view=None)
        if hasattr(lobby, '_just_finalized'):
            del lobby._just_finalized

    # Add error handling, timeouts, and cleanup logic as next steps
async def setup(bot):
    await bot.add_cog(Matchmaking(bot))
