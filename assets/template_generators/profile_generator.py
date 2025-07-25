"""
Profile Template Generator using Pillow
Generates user profile images with stats overlay
"""

import os
import tempfile
from PIL import Image, ImageDraw, ImageFont
import logging

logger = logging.getLogger(__name__)


class ProfileTemplateGenerator:
    """Generator pentru template-ul de profil folosind Pillow"""
    
    def __init__(self, background_image_path: str = "template_images/arena_faceit_profile_image.webp"):
        self.background_image_path = background_image_path
        self.default_font_path = "fonts/font-black.ttf"
        
    async def generate_profile_image(
        self,
        user_data: dict,
        target_user,
        points: int,
        wins: int,
        losses: int,
        total_games: int,
        kills: int,
        deaths: int,
        kd_ratio: str,
        win_rate: str
    ) -> str:
        """
        Generează imaginea pentru profilul utilizatorului folosind Pillow
        
        Args:
            user_data: Datele utilizatorului din baza de date
            target_user: Discord user object
            points: Punctele utilizatorului
            wins: Numărul de victorii
            losses: Numărul de înfrângeri
            total_games: Totalul jocurilor
            kills: Numărul de eliminări
            deaths: Numărul de decese
            kd_ratio: Raportul K/D ca string
            win_rate: Rata de victorie ca string
            
        Returns:
            str: Calea către fișierul imaginii generate
        """
        try:
            # Get the project root directory
            project_root = os.path.dirname(os.path.dirname(__file__))
            
            # Load the background image
            background_path = os.path.join(project_root, self.background_image_path)
            if not os.path.exists(background_path):
                logger.error(f"Background image not found: {background_path}")
                return await self._create_fallback_image(target_user.id)
                
            image = Image.open(background_path).copy()
            
            # Create a drawing context
            draw = ImageDraw.Draw(image)
            
            # Load the custom font
            font_path = os.path.join(project_root, self.default_font_path)
            try:
                font = ImageFont.truetype(font_path, 100)
                logger.info(f"Loaded custom font: {font_path}")
            except (IOError, OSError) as e:
                logger.warning(f"Could not load font from {font_path}, using default font: {e}")
                font = ImageFont.load_default()
            
            # Define text color (white)
            text_color = (255, 255, 255)
            
            # Add text overlays at specified coordinates
            # Points - (820, 620)
            draw.text((821, 631), str(points), font=font, fill=text_color, anchor="mm")
            
            # Total games - (823, 932)
            draw.text((826, 950), str(total_games), font=font, fill=text_color, anchor="mm")
            
            # Win rate - (823, 1247)
            draw.text((839, 1260), win_rate, font=font, fill=text_color, anchor="mm")
            
            # Wins - (1293, 597)
            draw.text((1294, 608), str(wins), font=font, fill=text_color, anchor="mm")
            
            # Losses - (1296, 925)
            draw.text((1303, 941), str(losses), font=font, fill=text_color, anchor="mm")
            
            # Kills - (1709, 554)
            draw.text((1715, 570), str(kills), font=font, fill=text_color, anchor="mm")
            
            # Deaths - (1716, 876)
            draw.text((1716, 888), str(deaths), font=font, fill=text_color, anchor="mm")
            
            # K/D ratio - (1719, 1191)
            draw.text((1719, 1200), f"{kd_ratio}", font=font, fill=text_color, anchor="mm")
            
            # Save the image to a temporary file
            temp_path = os.path.join(tempfile.gettempdir(), f"profile_{target_user.id}.webp")
            image.save(temp_path, "WebP", quality=85)
            
            logger.info(f"Profile image generated successfully: {temp_path}")
            return temp_path
            
        except Exception as e:
            logger.error(f"Error generating profile image with Pillow: {e}")
            return await self._create_fallback_image(target_user.id)

    async def _create_fallback_image(self, user_id: int) -> str:
        """Create a simple fallback image in case of errors"""
        try:
            # Create a simple colored image with error text
            fallback_image = Image.new("RGB", (1920, 1080), (255, 0, 0))  # Red background
            draw = ImageDraw.Draw(fallback_image)
            
            try:
                font = ImageFont.load_default()
                draw.text(
                    (960, 540), 
                    "Error generating profile image", 
                    font=font, 
                    fill=(255, 255, 255), 
                    anchor="mm"
                )
            except Exception:
                # If even default font fails, just create red image
                pass
                
            temp_path = os.path.join(tempfile.gettempdir(), f"profile_error_{user_id}.webp")
            fallback_image.save(temp_path, "WebP", quality=85)
            
            logger.info(f"Created fallback profile image: {temp_path}")
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
