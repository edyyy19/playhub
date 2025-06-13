import discord
from discord.ext import commands
from discord import app_commands
from models.user import User
from config import Config
import logging

logger = logging.getLogger(__name__)

# Constants
REGISTRATION_BANNER = "https://res.cloudinary.com/dzhebhupq/image/upload/v1748788836/playhub_3d_banner_u93fy0.png"
EMBED_COLOR = 0xB2FA19  # Vibrant green color


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
                return await interaction.response.send_message(
                    embed=embed, ephemeral=True
                )

            # Validate that SO2 ID contains only numbers
            if not self.so2_id.value.isdigit():
                embed = discord.Embed(
                    title=f"{Config.ICONS['unsuccess']} Invalid Player ID",
                    description="Standoff 2 Player ID must contain only numbers.",
                    color=Config.EMBED_COLORS["red"],
                )
                return await interaction.response.send_message(
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

            # Assign Level 1 role to new users (they start with 200 points = Level 1)
            try:
                from cogs.levels import LevelsCog

                levels_cog = LevelsCog(None)
                await levels_cog.update_player_roles(
                    interaction.guild, interaction.user.id, 200
                )
            except Exception as e:
                logger.error(f"Error assigning level role during registration: {e}")

            # Create success embed
            embed = discord.Embed(
                title=f"{Config.ICONS['success']} Registration Successful",
                color=Config.EMBED_COLORS["light_green"],
            )
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
            embed.set_footer(text=f"Welcome to {Config.APP_NAME} competitive!")

            await interaction.response.send_message(embed=embed, ephemeral=True)

        except Exception as e:
            logger.error(f"Registration error: {e}")
            embed = discord.Embed(
                title=f"{Config.ICONS['unsuccess']} Registration Failed",
                description="An error occurred during registration. Please contact an admin.",
                color=Config.EMBED_COLORS["red"],
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
    @app_commands.default_permissions(administrator=True)
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
                title=f"{Config.APP_NAME} Registration",
                description=f"Click below to register for {Config.APP_NAME} — your gateway to 5v5 matchmaking!",
                color=EMBED_COLOR,
            )
            embed.set_image(url=REGISTRATION_BANNER)
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

            embed.set_footer(
                text="Registered users will be automatically hidden from the registration channel"
            )

            await interaction.response.send_message(embed=embed, ephemeral=True)

        except Exception as e:
            logger.error(f"Setup error: {e}")
            await interaction.response.send_message(
                f"{Config.ICONS['unsuccess']} Setup failed: {e}", ephemeral=True
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

    @app_commands.command(
        name="restore_user", description="Manually restore a user's registration data"
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

            # Get or create the registered role
            role = discord.utils.get(
                interaction.guild.roles, name=Config.REGISTERED_ROLE
            )
            if not role:
                role = await interaction.guild.create_role(
                    name=Config.REGISTERED_ROLE,
                    color=EMBED_COLOR,
                    hoist=True,
                    mentionable=True,
                    reason="Auto-created for registered players",
                )  # Add the registered role if they don't have it
            if role not in member.roles:
                await member.add_roles(role, reason="Manual restoration by admin")
                role_added = True
            else:
                role_added = False

            # Assign the appropriate level role based on their current points
            level_role_updated = False
            try:
                from cogs.levels import LevelsCog

                levels_cog = LevelsCog(None)
                current_points = user_data.get(
                    "points", 200
                )  # Default to 200 if no points
                await levels_cog.update_player_roles(
                    interaction.guild, member.id, current_points
                )
                level_role_updated = True
            except Exception as e:
                logger.error(f"Error assigning level role during restore_user: {e}")

            # Update their nickname if it doesn't match
            nickname_updated = False
            if member.nick != user_data["nickname"]:
                try:
                    await member.edit(
                        nick=user_data["nickname"], reason="Manual restoration by admin"
                    )
                    nickname_updated = True
                except discord.Forbidden:
                    pass
                except discord.HTTPException:
                    pass

            embed = discord.Embed(
                title=f"{Config.ICONS['success']} User Restoration Complete",
                description=f"Restored registration data for {member.mention}",
                color=Config.EMBED_COLORS["light_green"],
            )
            embed.add_field(name="Nickname", value=user_data["nickname"], inline=True)
            embed.add_field(name="Player ID", value=user_data["so2_id"], inline=True)
            embed.add_field(
                name="Points", value=user_data.get("points", 0), inline=True
            )

            status_text = []
            if role_added:
                status_text.append(f"{Config.ICONS['success']} Role added")
            else:
                status_text.append("✓ Role already present")

            if level_role_updated:
                status_text.append(f"{Config.ICONS['success']} Level role updated")
            else:
                status_text.append(f"{Config.ICONS['info']} Level role update failed")

            if nickname_updated:
                status_text.append(f"{Config.ICONS['success']} Nickname updated")
            else:
                status_text.append("✓ Nickname already correct")

            embed.add_field(
                name="Actions Taken", value="\n".join(status_text), inline=False
            )

            await interaction.followup.send(embed=embed)

        except Exception as e:
            logger.error(f"Error in restore_user command: {e}")
            embed = discord.Embed(
                title=f"{Config.ICONS['unsuccess']} Restoration Failed",
                description=f"An error occurred: {e}",
                color=Config.EMBED_COLORS["red"],
            )
            await interaction.followup.send(embed=embed)

    @commands.Cog.listener()
    async def on_member_join(self, member):
        """Check if the user is already registered and restore their nickname and role"""
        try:
            # Check if user exists in database
            user_data = await User.find(member.id)

            if user_data:
                # User is already registered, restore their data
                logger.info(
                    f"Restoring registered user: {member} (Discord ID: {member.id})"
                )

                # Get or create the registered role
                role = discord.utils.get(
                    member.guild.roles, name=Config.REGISTERED_ROLE
                )
                if not role:
                    role = await member.guild.create_role(
                        name=Config.REGISTERED_ROLE,
                        color=EMBED_COLOR,
                        hoist=True,
                        mentionable=True,
                        reason="Auto-created for registered players",
                    )
                    logger.info(f"Created missing 'Registered' role")

                # Add the registered role
                await member.add_roles(
                    role, reason="Restoring role for returning registered user"
                )

                # Assign the appropriate level role based on their current points
                try:
                    from cogs.levels import LevelsCog

                    levels_cog = LevelsCog(None)
                    current_points = user_data.get(
                        "points", 200
                    )  # Default to 200 if no points
                    await levels_cog.update_player_roles(
                        member.guild, member.id, current_points
                    )
                except Exception as e:
                    logger.error(f"Error assigning level role during member join: {e}")

                # Update their nickname to match the database
                try:
                    await member.edit(
                        nick=user_data["nickname"],
                        reason="Restoring nickname for returning registered user",
                    )
                    logger.info(
                        f"Restored nickname '{user_data['nickname']}' for user {member}"
                    )
                except discord.Forbidden:
                    logger.warning(
                        f"Could not update nickname for {member} (missing permissions)"
                    )
                except discord.HTTPException as e:
                    logger.error(f"Failed to update nickname for {member}: {e}")

                # Send a welcome back message to the user via DM
                try:
                    embed = discord.Embed(
                        title=f"🎉 Welcome Back to {Config.APP_NAME}!",
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
                        name="Points", value=user_data.get("points", 0), inline=True
                    )
                    embed.set_footer(text="Ready to play competitive matches!")

                    await member.send(embed=embed)
                    logger.info(f"Sent welcome back message to {member}")
                except discord.Forbidden:
                    logger.info(f"Could not send DM to {member} (DMs disabled)")
                except Exception as e:
                    logger.error(
                        f"Failed to send welcome back message to {member}: {e}"
                    )

        except Exception as e:
            logger.error(f"Error in on_member_join for {member}: {e}")


async def setup(bot):
    await bot.add_cog(Registration(bot))
