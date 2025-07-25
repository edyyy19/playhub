"""
Leaderboard Template Generator using Pillow
Generates leaderboard images with player avatars and statistics
"""

import os
import tempfile
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont
import aiohttp
import io
import asyncio
import logging
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)


class LeaderboardTemplateGenerator:
    """Generator pentru template-ul de leaderboard folosind Pillow"""
    
    def __init__(self, background_image_path: str = "template_images/arena_faceit_leaderboard.webp"):
        self.background_image_path = background_image_path
        self.default_font_path = "fonts/font-black.ttf"

    async def download_and_create_round_avatar(self, bot, discord_user_id: int, size: int = 80) -> Optional[Image.Image]:
        """Download user avatar and create a round avatar image"""
        try:
            # Try to get the user from bot cache first
            user = bot.get_user(int(discord_user_id))
            
            if not user:
                # Try to fetch the user if not in cache
                try:
                    user = await bot.fetch_user(int(discord_user_id))
                except Exception as e:
                    logger.warning(f"Error fetching user {discord_user_id}: {e}")
                    return None
            
            # Get avatar URL (use default avatar if no custom avatar)
            avatar_url = user.avatar.url if user.avatar else user.default_avatar.url
            
            # Download the avatar
            async with aiohttp.ClientSession() as session:
                async with session.get(avatar_url) as response:
                    if response.status == 200:
                        avatar_data = await response.read()
                    else:
                        logger.warning(f"Failed to download avatar for user {discord_user_id}: HTTP {response.status}")
                        return None
            
            # Process the image to make it round
            avatar_image = Image.open(io.BytesIO(avatar_data))
            
            # Resize to the desired size
            avatar_image = avatar_image.resize((size, size), Image.LANCZOS)
            
            # Create a round mask
            mask = Image.new("L", (size, size), 0)
            draw = ImageDraw.Draw(mask)
            draw.ellipse((0, 0, size, size), fill=255)
            
            # Apply the mask to create a round avatar
            avatar_image = avatar_image.convert("RGBA")
            avatar_image.putalpha(mask)
            
            # Create a new image with transparent background
            round_avatar = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            round_avatar.paste(avatar_image, (0, 0), avatar_image)
            
            return round_avatar
            
        except Exception as e:
            logger.error(f"Error creating round avatar for user {discord_user_id}: {e}")
            return None

    async def generate_leaderboard_image(
        self, 
        bot, 
        players: List[Dict], 
        display_name: str, 
        league_table_name: str
    ) -> str:
        """Generate leaderboard image using Pillow"""
        try:
            # Get the project root directory
            project_root = os.path.dirname(os.path.dirname(__file__))
            
            # Load the background image
            background_path = os.path.join(project_root, self.background_image_path)
            if not os.path.exists(background_path):
                logger.error(f"Background image not found: {background_path}")
                return await self._create_fallback_image(league_table_name)
                
            image = Image.open(background_path).copy()
            
            # Create a drawing context
            draw = ImageDraw.Draw(image)
            
            # Load the custom font
            font_path = os.path.join(project_root, self.default_font_path)
            try:
                font = ImageFont.truetype(font_path, 72)  # Font size for leaderboard
                logger.info(f"Loaded custom font: {font_path}")
            except (IOError, OSError) as e:
                logger.warning(f"Could not load font from {font_path}, using default font: {e}")
                font = ImageFont.load_default()
            
            # Define text color (white)
            text_color = (255, 255, 255)
            
            # Layout configuration
            avatar_size = 80  # Size of round avatars
            
            # Y positions for each row (top 10 players)
            row_y_positions = [
                198,   # Row 1
                322,   # Row 2
                446,   # Row 3
                577,   # Row 4
                709,   # Row 5
                840,   # Row 6
                963,   # Row 7
                1096,  # Row 8
                1227,  # Row 9
                1351   # Row 10
        ]
            
            # Coordinates for each column
            avatar_x = 626  # Position for avatar (before name)
            name_x = 720
            points_x = 1208
            games_x = 1345
            wins_x = 1487
            losses_x = 1635
            kills_x = 1784
            deaths_x = 1928
            
            # Download all avatars concurrently
            avatar_tasks = []
            for player in players:
                discord_id = player.get('discord_id')
                if discord_id:
                    task = self.download_and_create_round_avatar(bot, discord_id, avatar_size)
                    avatar_tasks.append(task)
                else:
                    avatar_tasks.append(None)
            
            # Wait for all avatar downloads to complete
            avatars = await asyncio.gather(*[task for task in avatar_tasks if task is not None], return_exceptions=True)
            
            # Create avatar lookup by player index
            avatar_lookup = {}
            task_index = 0
            for i, player in enumerate(players):
                if player.get('discord_id') and task_index < len(avatars):
                    avatar_result = avatars[task_index]
                    if not isinstance(avatar_result, Exception) and avatar_result:
                        avatar_lookup[i] = avatar_result
                    task_index += 1
            
            # Add each player's data
            for i, player in enumerate(players):
                # Skip if we exceed the maximum number of rows
                if i >= len(row_y_positions):
                    logger.warning(f"Player {i+1} exceeds maximum leaderboard positions, skipping")
                    break
                    
                y_position = row_y_positions[i]
                
                # Add round avatar if available
                if i in avatar_lookup:
                    avatar = avatar_lookup[i]
                    avatar_y = y_position - (avatar_size // 2)  # Center vertically
                    image.paste(avatar, (avatar_x, avatar_y), avatar)
                
                # Player name
                draw.text((name_x, y_position), str(player['nickname']), font=font, fill=text_color, anchor="lm")
                
                # Points
                draw.text((points_x, y_position), str(player['points']), font=font, fill=text_color, anchor="mm")
                
                # Total games
                draw.text((games_x, y_position), str(player['total_games']), font=font, fill=text_color, anchor="mm")
                
                # Wins
                draw.text((wins_x, y_position), str(player['wins']), font=font, fill=text_color, anchor="mm")
                
                # Losses
                draw.text((losses_x, y_position), str(player['losses']), font=font, fill=text_color, anchor="mm")
                
                # Kills
                draw.text((kills_x, y_position), str(player['kills']), font=font, fill=text_color, anchor="mm")
                
                # Deaths
                draw.text((deaths_x, y_position), str(player['deaths']), font=font, fill=text_color, anchor="mm")
            
            # Save the image to a temporary file
            temp_path = os.path.join(
                tempfile.gettempdir(),
                f"leaderboard_{league_table_name}_{int(datetime.now().timestamp())}.webp",
            )
            image.save(temp_path, "WebP", quality=85)
            
            logger.info(f"Leaderboard image generated successfully: {temp_path}")
            return temp_path
            
        except Exception as e:
            logger.error(f"Error generating leaderboard image with Pillow: {e}")
            return await self._create_fallback_image(league_table_name)

    async def _create_fallback_image(self, league_table_name: str) -> str:
        """Create a simple fallback image in case of errors"""
        try:
            # Create a simple colored image with error text
            fallback_image = Image.new("RGB", (1920, 1080), (255, 0, 0))  # Red background
            draw = ImageDraw.Draw(fallback_image)
            
            try:
                font = ImageFont.load_default()
                draw.text(
                    (960, 540), 
                    "Error generating leaderboard image", 
                    font=font, 
                    fill=(255, 255, 255), 
                    anchor="mm"
                )
            except Exception:
                # If even default font fails, just create red image
                pass
                
            temp_path = os.path.join(
                tempfile.gettempdir(), 
                f"leaderboard_error_{league_table_name}_{int(datetime.now().timestamp())}.webp"
            )
            fallback_image.save(temp_path, "WebP", quality=85)
            
            logger.info(f"Created fallback leaderboard image: {temp_path}")
            return temp_path
            
        except Exception as e:
            logger.error(f"Failed to create fallback image: {e}")
            # Return a dummy path - calling code should handle this gracefully
            return ""

    def get_image_dimensions(self) -> tuple:
        """Get the dimensions of the background image"""
        try:
            project_root = os.path.dirname(os.path.dirname(__file__))
            background_path = os.path.join(project_root, self.background_image_path)
            
            if os.path.exists(background_path):
                with Image.open(background_path) as img:
                    return img.size
            return (1920, 1080)  # Default dimensions
        except Exception:
            return (1920, 1080)  # Default dimensions
