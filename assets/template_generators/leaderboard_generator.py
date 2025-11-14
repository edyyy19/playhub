"""
Leaderboard Template Generator using Pillow
Generates leaderboard images with player avatars and statistics
"""

import os
import sys
import tempfile
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont
import aiohttp
import io
import asyncio
import logging
from typing import List, Dict, Optional

# Add parent directories to path to import from cogs
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from cogs.levels import LevelsCog

logger = logging.getLogger(__name__)


class LeaderboardTemplateGenerator:
    """Generator pentru template-ul de leaderboard folosind Pillow"""
    
    def __init__(self, background_image_path: str = "template_images/leaderboard.png"):
        self.background_image_path = background_image_path
        self.default_font_path = "fonts/fira_code_semibold.ttf"

    def _get_project_root(self) -> str:
        """Get the project root directory"""
        return os.path.dirname(os.path.dirname(__file__))

    def _ensure_rgba(self, image: Image.Image) -> Image.Image:
        """Ensure image is in RGBA format"""
        if image.mode != 'RGBA':
            return image.convert('RGBA')
        return image

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
        """Generate leaderboard image using Pillow with new coordinates"""
        try:
            # Get the project root directory
            project_root = self._get_project_root()
            
            # Load the background image
            background_path = os.path.join(project_root, self.background_image_path)
            if not os.path.exists(background_path):
                logger.error(f"Background image not found: {background_path}")
                return await self._create_fallback_image(league_table_name)
                
            image = Image.open(background_path).copy()
            
            # Create a drawing context
            draw = ImageDraw.Draw(image)
            
            # Load fonts with different sizes
            font_path = os.path.join(project_root, self.default_font_path)
            fonts = {}
            font_sizes = [24, 32, 54]  # Font sizes needed for leaderboard
            
            for size in font_sizes:
                try:
                    fonts[size] = ImageFont.truetype(font_path, size)
                except (IOError, OSError) as e:
                    logger.warning(f"Could not load font size {size} from {font_path}, using default font: {e}")
                    fonts[size] = ImageFont.load_default()
            
            # Define colors
            text_color = (255, 255, 255)        # White
            wins_color = (87, 242, 135)          # #57F287 in RGB (green)
            losses_color = (255, 72, 72)        # #FF4848 in RGB (red)
            
            # Add league name at (102, 186) with font size 54 - use display_name instead of league_table_name
            draw.text((98, 168), str(display_name), font=fonts[54], fill=text_color, anchor="lm")
            
            # Avatar and nickname configuration
            avatar_size = 60  # 60x60 as specified
            avatar_x = 176   # X coordinate for avatar
            nickname_x = avatar_x + avatar_size + 15  # Nickname right after avatar with small gap
            
            # Other column coordinates
            level_icon_x = 560
            points_x = 700
            rating_x = 847
            rating_square_x = 847  # Poziție X independentă pentru pătratul de rating
            wins_x = 993
            losses_x = 1139
            win_rate_x = 1285
            
            # Base Y coordinate and gap between players
            base_y = 383
            player_gap = 105  # Gap between players (you can adjust this)
            
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
                # Calculate Y position for this player
                player_y = base_y + (i * player_gap)
                
                # Add round avatar if available
                if i in avatar_lookup:
                    avatar = avatar_lookup[i]
                    avatar_y = player_y - (avatar_size // 2)  # Center vertically
                    image.paste(avatar, (avatar_x, avatar_y), avatar)
                
                # Nickname - right after avatar, font size 24
                nickname = player.get('nickname', 'Unknown')
                draw.text((nickname_x, player_y), str(nickname), font=fonts[24], fill=text_color, anchor="lm")
                
                # Level icon - 574, 381 with 47 width 37 height
                try:
                    points = player.get('points', 0)
                    level_icon_path = LevelsCog.get_level_icon_path(points)
                    if level_icon_path and os.path.exists(level_icon_path):
                        level_icon = Image.open(level_icon_path)
                        
                        # Convert to RGBA if needed
                        level_icon = self._ensure_rgba(level_icon)
                        
                        # Resize to specified dimensions (47x37)
                        level_icon = level_icon.resize((47, 37), Image.Resampling.LANCZOS)
                        
                        # Position the level icon
                        icon_y = player_y - 37 // 2  # Center vertically
                        image.paste(level_icon, (level_icon_x, icon_y), level_icon)
                except Exception as e:
                    logger.warning(f"Could not load level icon for player {i}: {e}")
                
                # Points - font size 32, 697 381
                points = player.get('points', 0)
                draw.text((points_x, player_y), str(points), font=fonts[32], fill=text_color, anchor="mm")
                
                # Rating (K/D ratio) - font size 32, 844 381
                kills = player.get('kills', 0)
                deaths = player.get('deaths', 0)
                if deaths == 0:
                    rating_value = float(kills) if kills > 0 else 0.0
                    rating = f"{kills:.2f}" if kills > 0 else "0.00"
                else:
                    rating_value = kills / deaths
                    rating = f"{rating_value:.2f}"
                
                # Add rating square indicator based on rating value
                try:
                    rating_square_path = self._get_rating_square_path(rating_value)
                    if rating_square_path and os.path.exists(rating_square_path):
                        rating_square = Image.open(rating_square_path)
                        
                        # Convert to RGBA if needed
                        rating_square = self._ensure_rgba(rating_square)
                        
                        # Position the rating square using independent X coordinate
                        square_x = rating_square_x - rating_square.width // 2  # Center horizontally at rating_square_x
                        square_y = player_y - rating_square.height // 2  # Center vertically at player's Y
                        image.paste(rating_square, (square_x, square_y), rating_square)
                except Exception as e:
                    logger.warning(f"Could not load rating square for player {i}: {e}")
                
                # Determine rating text color based on rating value
                if rating_value <= 0.50:
                    # Rating 0-0.50: red color
                    rating_text_color = (255, 72, 72)    # #FF4848 in RGB (red)
                elif rating_value <= 1.0:
                    # Rating 0.50-1.0: yellow color
                    rating_text_color = (255, 197, 51)   # #FFC533 in RGB (yellow)
                else:
                    # Rating >1.0: green color
                    rating_text_color = (87, 242, 135)   # #57F287 in RGB (green)
                
                # Display the rating text with color based on rating value
                draw.text((rating_x, player_y), rating, font=fonts[32], fill=rating_text_color, anchor="mm")
                
                # Wins - font size 32, 992 381, color 57F287
                wins = player.get('wins', 0)
                draw.text((wins_x, player_y), str(wins), font=fonts[32], fill=wins_color, anchor="mm")
                
                # Losses - font size 32, 1133 381, color FF4848
                losses = player.get('losses', 0)
                draw.text((losses_x, player_y), str(losses), font=fonts[32], fill=losses_color, anchor="mm")
                
                # Win rate - font size 32, 1280 381
                total_games = wins + losses
                if total_games > 0:
                    win_rate = f"{round((wins / total_games) * 100)}%"
                else:
                    win_rate = "0%"
                draw.text((win_rate_x, player_y), win_rate, font=fonts[32], fill=text_color, anchor="mm")
            
            # Save the image to a temporary file
            temp_path = os.path.join(
                tempfile.gettempdir(),
                f"leaderboard_{league_table_name}_{int(datetime.now().timestamp())}.png",
            )
            image.save(temp_path, "PNG", quality=95)
            
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
                f"leaderboard_error_{league_table_name}_{int(datetime.now().timestamp())}.png"
            )
            fallback_image.save(temp_path, "PNG", quality=95)
            
            logger.info(f"Created fallback leaderboard image: {temp_path}")
            return temp_path
            
        except Exception as e:
            logger.error(f"Failed to create fallback image: {e}")
            # Return a dummy path - calling code should handle this gracefully
            return ""

    def _get_rating_square_path(self, rating_value: float) -> str:
        """Get the path to the appropriate rating square based on rating value"""
        try:
            project_root = self._get_project_root()
            template_images_dir = os.path.join(project_root, 'template_images')
            
            # Determine which square to use based on rating value
            if rating_value <= 0.50:
                # Rating 0-0.50: red square
                square_name = "leaderboard_square_red.png"
            elif rating_value <= 1.0:
                # Rating 0.50-1.0: yellow square
                square_name = "leaderboard_square_yellow.png"
            else:
                # Rating >1.0: green square
                square_name = "leaderboard_square_green.png"
            
            square_path = os.path.join(template_images_dir, square_name)
            
            # Check if the square image exists
            if os.path.exists(square_path):
                return square_path
            else:
                logger.warning(f"Rating square not found: {square_path}")
                return ""
                
        except Exception as e:
            logger.warning(f"Error determining rating square path: {e}")
            return ""
