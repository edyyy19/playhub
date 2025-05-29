import discord
from discord.ext import commands
from discord import app_commands
from models.user import User
from config import Config
import logging

logger = logging.getLogger(__name__)

# Constants
REGISTRATION_BANNER = "https://res.cloudinary.com/dzhebhupq/image/upload/v1748000675/playhub_banner_mvqfjj.png"
EMBED_COLOR = 0xB2FA19  # Vibrant green color


class RegisterModal(discord.ui.Modal, title="PlayHub Registration"):
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
            if await User.find(interaction.user.id):
                embed = discord.Embed(
                    title="❌ Already Registered",
                    description="You're already registered in our system!",
                    color=discord.Color.red(),
                )
                return await interaction.response.send_message(
                    embed=embed, ephemeral=True
                )

            await User.register(
                interaction.user.id, self.nickname.value, self.so2_id.value
            )

            role = discord.utils.get(
                interaction.guild.roles, name=Config.REGISTERED_ROLE
            )
            if not role:
                role = await interaction.guild.create_role(
                    name=Config.REGISTERED_ROLE,
                    color=EMBED_COLOR,
                    reason="Auto-created for registered players",
                )

            await interaction.user.add_roles(role)

            # Create success embed
            embed = discord.Embed(title="✅ Registration Successful", color=EMBED_COLOR)
            embed.add_field(name="Nickname", value=self.nickname.value, inline=True)
            embed.add_field(name="Player ID", value=self.so2_id.value, inline=True)

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
            embed.set_footer(text="Welcome to PlayHub competitive!")

            await interaction.response.send_message(embed=embed, ephemeral=True)

        except Exception as e:
            logger.error(f"Registration error: {e}")
            embed = discord.Embed(
                title="❌ Registration Failed",
                description="An error occurred during registration. Please contact an admin.",
                color=discord.Color.red(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)


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

    @app_commands.command(name="setup", description="Initialize registration system")
    @app_commands.checks.has_permissions(administrator=True)
    async def setup(self, interaction: discord.Interaction):
        try:
            # Create or get the "Registered" role with proper permissions
            registered_role = discord.utils.get(
                interaction.guild.roles, name=Config.REGISTERED_ROLE
            )

            if not registered_role:
                # Create the role with display role and mention permissions
                registered_role = await interaction.guild.create_role(
                    name=Config.REGISTERED_ROLE,
                    color=EMBED_COLOR,
                    hoist=True,  # Display role separately in member list
                    mentionable=True,  # Allow mention
                    reason="Auto-created for registered players during setup",
                )
                logger.info(f"Created 'Registered' role with ID: {registered_role.id}")
            else:
                # Update existing role to ensure it has the correct permissions
                await registered_role.edit(
                    hoist=True,  # Display role separately in member list
                    mentionable=True,  # Allow mention
                    reason="Updated role permissions during setup",
                )
                logger.info(f"Updated existing 'Registered' role permissions")

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
                logger.info(f"Created registration channel with proper permissions")
            else:
                # Update existing channel permissions to ensure registered users can't view it
                await channel.set_permissions(
                    registered_role,
                    read_messages=False,
                    send_messages=False,
                    reason="Updated during setup to hide from registered users",
                )
                logger.info(f"Updated permissions for existing registration channel")

            embed = discord.Embed(
                title="PlayHub Registration",
                description="Click below to register for PlayHub — your gateway to 5v5 matchmaking!",
                color=EMBED_COLOR,
            )
            embed.set_image(url=REGISTRATION_BANNER)
            embed.set_footer(text="PlayHub Matchmaking")

            await channel.send(embed=embed, view=RegisterView())

            # Create success response with details
            embed = discord.Embed(
                title="✅ Setup Completed Successfully",
                description=f"Registration system initialized in {channel.mention}",
                color=EMBED_COLOR,
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

            embed.set_footer(
                text="Registered users will be automatically hidden from the registration channel"
            )

            await interaction.response.send_message(embed=embed, ephemeral=True)

        except Exception as e:
            logger.error(f"Setup error: {e}")
            await interaction.response.send_message(
                f"❌ Setup failed: {e}", ephemeral=True
            )

    @commands.command(name="checkperms")
    @commands.has_permissions(administrator=True)
    async def check_perms(self, ctx):
        """Check if bot has nickname permissions"""
        perms = ctx.guild.me.guild_permissions
        role_pos = ctx.guild.me.top_role.position
        target_pos = ctx.author.top_role.position

        embed = discord.Embed(title="Permission Check", color=EMBED_COLOR)
        embed.add_field(
            name="Nickname Permissions",
            value=f"• `Manage Nicknames`: {perms.manage_nicknames}\n"
            f"• `Bot Role Position`: {role_pos}\n"
            f"• `Your Role Position`: {target_pos}",
            inline=False,
        )

        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(Registration(bot))
