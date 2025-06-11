import discord
from discord.ext import commands
from discord import app_commands
from config import Config
import asyncio
import logging
from models.party import Party
from models.user import User
from cogs.levels import LevelsCog
from datetime import datetime

logger = logging.getLogger(__name__)

EMBED_COLOR = 0xB2FA19  # Vibrant green color


class PartyInviteView(discord.ui.View):
    def __init__(
        self, inviter_id: str, invited_user: discord.User, party_id: int = None
    ):
        super().__init__(timeout=60)
        self.inviter_id = inviter_id
        self.invited_user = invited_user
        self.party_id = party_id

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.green)
    async def accept_invite(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if interaction.user.id != self.invited_user.id:
            await interaction.response.send_message(
                "This invitation is not for you!", ephemeral=True
            )
            return

        try:
            # Check if the invited user is already in a party
            existing_party = await Party.find_user_party(str(interaction.user.id))
            if existing_party:
                embed = discord.Embed(
                    title="❌ Already in Party",
                    description="You are already in a party! Leave your current party first.",
                    color=0xFF0000,
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return

            # If no party_id, the inviter needs to create a party first
            if not self.party_id:
                inviter = interaction.guild.get_member(int(self.inviter_id))
                if not inviter:
                    await interaction.response.send_message(
                        "Inviter not found!", ephemeral=True
                    )
                    return

                # Create party with inviter as leader
                result = await Party.create_party(self.inviter_id, inviter.display_name)
                if not result.data:
                    await interaction.response.send_message(
                        "Failed to create party!", ephemeral=True
                    )
                    return

                self.party_id = result.data[0]["party_id"]

            # Check party size
            member_count = await Party.get_member_count(self.party_id)
            if member_count >= Config.MAX_PARTY_MEMBERS:
                embed = discord.Embed(
                    title="❌ Party Full",
                    description=f"The party is already full! Maximum {Config.MAX_PARTY_MEMBERS} members allowed.",
                    color=0xFF0000,
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return  # Add user to party
            result = await Party.add_member(
                self.party_id, str(interaction.user.id), interaction.user.display_name
            )
            if result:
                embed = discord.Embed(
                    title="✅ Joined Party",
                    description=f"{interaction.user.mention} has joined the party!",
                    color=discord.Color.green(),
                )
                # Replace the original message with the response
                await interaction.response.edit_message(embed=embed, view=None)
            else:
                await interaction.response.send_message(
                    "Failed to join party!", ephemeral=True
                )

        except Exception as e:
            logger.error(f"Error accepting party invite: {e}")
            await interaction.response.send_message(
                "An error occurred while joining the party.", ephemeral=True
            )

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.red)
    async def deny_invite(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if interaction.user.id != self.invited_user.id:
            await interaction.response.send_message(
                "This invitation is not for you!", ephemeral=True
            )
            return

        embed = discord.Embed(
            title="❌ Invitation Declined",
            description=f"{interaction.user.mention} has declined the party invitation.",
            color=0xFF0000,
        )
        # Replace the original message with the response
        await interaction.response.edit_message(embed=embed, view=None)

    async def on_timeout(self):
        # Disable all buttons when the view times out
        for item in self.children:
            item.disabled = True


class Parties(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="party_info", description="Display information about your current party"
    )
    async def party_info(self, interaction: discord.Interaction):
        """Display party information"""
        try:
            # Find user's party
            party = await Party.find_user_party(str(interaction.user.id))

            if not party:
                embed = discord.Embed(
                    title="❌ No Party",
                    description="You are not currently in a party.",
                    color=0xFF0000,
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return

            # Get leader info
            leader_id = party["leader_id"]
            leader_member = interaction.guild.get_member(int(leader_id))
            leader_name = leader_member.display_name if leader_member else "Unknown"

            # Create embed
            embed = discord.Embed(title="👥 Party Information", color=EMBED_COLOR)

            embed.add_field(name="Party Leader", value=leader_name, inline=False)

            # Format members list with level emojis
            members_list = []
            for member_data in party["members"]:
                discord_id = member_data["discord_id"]
                nickname = member_data["nickname"]
                role = member_data["role"]

                # Get user points for level emoji
                try:
                    user_data = await User.find(int(discord_id))
                    points = user_data.get("points", 0) if user_data else 0
                    level_emoji = LevelsCog.get_level_emoji(points)
                except Exception as e:
                    logger.error(f"Error getting user data for {discord_id}: {e}")
                    level_emoji = LevelsCog.get_level_emoji(0)  # Default emoji

                # Add crown for leader
                if role == "leader":
                    members_list.append(f"{level_emoji} {nickname}")
                else:
                    members_list.append(f"{level_emoji} {nickname}")

            embed.add_field(
                name=f"Members ({len(party['members'])}/{Config.MAX_PARTY_MEMBERS})",
                value="\n".join(members_list) if members_list else "No members",
                inline=False,
            )

            embed.add_field(
                name="📅 Created",
                value=f"<t:{int(datetime.now().timestamp())}:F>",
                inline=True,
            )

            await interaction.response.send_message(embed=embed, ephemeral=True)

        except Exception as e:
            logger.error(f"Error in party_info: {e}")
            embed = discord.Embed(
                title="❌ Error",
                description="An error occurred while fetching party information.",
                color=0xFF0000,
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name="party_invite", description="Invite a user to your party"
    )
    @app_commands.describe(user="The user to invite to your party")
    async def party_invite(self, interaction: discord.Interaction, user: discord.User):
        """Invite a user to party"""
        try:
            # Check if inviter is a bot
            if interaction.user.bot:
                await interaction.response.send_message(
                    "Bots cannot create or join parties!", ephemeral=True
                )
                return

            # Check if invited user is a bot
            if user.bot:
                embed = discord.Embed(
                    title="❌ Cannot Invite Bot",
                    description="You cannot invite bots to parties!",
                    color=0xFF0000,
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return

            # Check if trying to invite themselves
            if interaction.user.id == user.id:
                embed = discord.Embed(
                    title="❌ Cannot Invite Yourself",
                    description="You cannot invite yourself to a party!",
                    color=0xFF0000,
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return

            # Check if invited user is already in a party
            invited_party = await Party.find_user_party(str(user.id))
            if invited_party:
                embed = discord.Embed(
                    title="❌ User Already in Party",
                    description=f"{user.mention} is already in a party!",
                    color=0xFF0000,
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return

            # Check if inviter is in a party
            inviter_party = await Party.find_user_party(str(interaction.user.id))
            party_id = None

            if inviter_party:
                party_id = inviter_party["party_id"]
                # Check if party is full
                if len(inviter_party["members"]) >= Config.MAX_PARTY_MEMBERS:
                    embed = discord.Embed(
                        title="❌ Party Full",
                        description=f"Your party is already full! Maximum {Config.MAX_PARTY_MEMBERS} members allowed.",
                        color=0xFF0000,
                    )
                    await interaction.response.send_message(embed=embed, ephemeral=True)
                    return

            # Create invitation embed
            embed = discord.Embed(
                title="👥 Party Invitation",
                description=f"{user.mention}, you have been invited to join {interaction.user.mention}'s party!",
                color=EMBED_COLOR,
            )

            if not inviter_party:
                embed.add_field(
                    name="Info",
                    value="A new party will be created when you accept this invitation.",
                    inline=False,
                )

            # Create view with buttons
            view = PartyInviteView(str(interaction.user.id), user, party_id)

            await interaction.response.send_message(embed=embed, view=view)

        except Exception as e:
            logger.error(f"Error in party_invite: {e}")
            embed = discord.Embed(
                title="❌ Error",
                description="An error occurred while sending the party invitation.",
                color=0xFF0000,
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="party_leave", description="Leave your current party")
    async def party_leave(self, interaction: discord.Interaction):
        """Leave current party"""
        try:
            # Find user's party
            party = await Party.find_user_party(str(interaction.user.id))

            if not party:
                embed = discord.Embed(
                    title="❌ No Party",
                    description="You are not currently in a party.",
                    color=0xFF0000,
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return

            party_id = party["party_id"]
            is_leader = party["leader_id"] == str(interaction.user.id)

            if is_leader:
                # If leader leaves, disband the party
                await Party.delete_party(party_id)
                embed = discord.Embed(
                    title="👥 Party Disbanded",
                    description="You were the party leader, so the party has been disbanded.",
                    color=EMBED_COLOR,
                )
            else:
                # Remove member from party
                await Party.remove_member(party_id, str(interaction.user.id))
                embed = discord.Embed(
                    title="👋 Left Party",
                    description="You have successfully left the party.",
                    color=EMBED_COLOR,
                )

            await interaction.response.send_message(embed=embed, ephemeral=True)

        except Exception as e:
            logger.error(f"Error in party_leave: {e}")
            embed = discord.Embed(
                title="❌ Error",
                description="An error occurred while leaving the party.",
                color=0xFF0000,
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name="party_kick", description="Kick a member from your party (leader only)"
    )
    @app_commands.describe(user="The user to kick from the party")
    async def party_kick(self, interaction: discord.Interaction, user: discord.User):
        """Kick a member from party (leader only)"""
        try:
            # Find user's party
            party = await Party.find_user_party(str(interaction.user.id))

            if not party:
                embed = discord.Embed(
                    title="❌ No Party",
                    description="You are not currently in a party.",
                    color=0xFF0000,
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return

            # Check if user is the party leader
            if party["leader_id"] != str(interaction.user.id):
                embed = discord.Embed(
                    title="❌ Not Party Leader",
                    description="Only the party leader can kick members!",
                    color=0xFF0000,
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return

            # Check if trying to kick themselves
            if interaction.user.id == user.id:
                embed = discord.Embed(
                    title="❌ Cannot Kick Yourself",
                    description="You cannot kick yourself! Use `/party_leave` to leave the party.",
                    color=0xFF0000,
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return

            # Check if user is in the party
            user_in_party = False
            for member in party["members"]:
                if member["discord_id"] == str(user.id):
                    user_in_party = True
                    break

            if not user_in_party:
                embed = discord.Embed(
                    title="❌ User Not in Party",
                    description=f"{user.mention} is not in your party!",
                    color=0xFF0000,
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return

            # Remove the member
            party_id = party["party_id"]
            await Party.remove_member(party_id, str(user.id))

            # Check if party should be disbanded (only leader left)
            updated_party = await Party.get_party(party_id)
            if updated_party and len(updated_party["members"]) == 1:
                # Only leader left, but we don't auto-disband in this case
                pass

            embed = discord.Embed(
                title="👢 Member Kicked",
                description=f"{user.mention} has been kicked from the party.",
                color=EMBED_COLOR,
            )
            await interaction.response.send_message(embed=embed)

        except Exception as e:
            logger.error(f"Error in party_kick: {e}")
            embed = discord.Embed(
                title="❌ Error",
                description="An error occurred while kicking the member.",
                color=0xFF0000,
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Parties(bot))
