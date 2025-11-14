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
    
    def __init__(self, background_image_path: str = "template_images/player_profile.webp"):
        self.background_image_path = background_image_path
        self.default_font_path = "fonts/fira_code_semibold.ttf"

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
        assists: int,
        kd_ratio: str,
        win_rate: str,
        rating: float,
        match_history: list = None,
        best_map_data: dict = None,
        registered_date: str = "Unknown"
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
            assists: Numărul de asistări
            kd_ratio: Raportul K/D ca string
            win_rate: Rata de victorie ca string
            rating: Valoarea rating-ului pentru poziționarea săgeții
            match_history: Lista cu ultimele 5 meciuri (opțional)
                Format: [{'id': int, 'map': str, 'is_win': bool, 'kills': int, 'deaths': int}, ...]
                Coordonatele X sunt predefinite: [522, 672, 822, 972, 1122] pentru meciurile 1-5
            best_map_data: Datele pentru cea mai bună hartă (opțional)
                Format: {'map': str, 'wins': int}
                Poziționare: map name la (441, 468), wins la (441, 490), icon la (370, 440)
            registered_date: Data înregistrării formatată ca DD/MM/YYYY (ex: "19/01/2025")
            
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
            
            # Load fonts with different sizes
            font_path = os.path.join(project_root, self.default_font_path)
            fonts = {}
            font_sizes = [14, 20, 22, 24, 26, 32]
            
            for size in font_sizes:
                try:
                    fonts[size] = ImageFont.truetype(font_path, size)
                except (IOError, OSError) as e:
                    logger.warning(f"Could not load font size {size} from {font_path}, using default font: {e}")
                    fonts[size] = ImageFont.load_default()
            
            # Define text color (white)
            text_color = (255, 255, 255)
            gray_color = (118, 118, 118)  # #767676 in RGB
            wins_color = (87, 242, 135)   # #57F287 in RGB (green)
            losses_color = (255, 72, 72)  # #FF4848 in RGB (red)
            
            # Add user avatar at specified position (150, 187) with size 86x86 - fully rounded
            try:
                avatar_url = target_user.display_avatar.url
                import aiohttp
                import io
                
                async with aiohttp.ClientSession() as session:
                    async with session.get(avatar_url) as resp:
                        if resp.status == 200:
                            avatar_data = await resp.read()
                            avatar_image = Image.open(io.BytesIO(avatar_data))
                            avatar_image = avatar_image.resize((86, 86), Image.Resampling.LANCZOS)
                            
                            # Convert to RGBA if needed for transparency
                            if avatar_image.mode != 'RGBA':
                                avatar_image = avatar_image.convert('RGBA')
                            
                            # Create a circular mask
                            mask = Image.new('L', (86, 86), 0)
                            mask_draw = ImageDraw.Draw(mask)
                            mask_draw.ellipse((0, 0, 86, 86), fill=255)
                            
                            # Apply the circular mask to the avatar
                            circular_avatar = Image.new('RGBA', (86, 86), (0, 0, 0, 0))
                            circular_avatar.paste(avatar_image, (0, 0))
                            circular_avatar.putalpha(mask)
                            
                            # Paste the circular avatar at position (150, 187)
                            image.paste(circular_avatar, (60, 143), circular_avatar)
            except Exception as e:
                logger.warning(f"Could not load user avatar: {e}")
            
            # Add text overlays at specified coordinates based on new template
            
            # Nickname - (167, 187) - font size 26
            nickname = user_data.get('nickname', target_user.display_name)
            draw.text((163, 196), str(nickname), font=fonts[26], fill=text_color, anchor="lm")
            
            # Rating - (444, 354) - font size 24
            draw.text((440, 355), f"{kd_ratio}", font=fonts[24], fill=text_color, anchor="lm")
            
            # Games - (718, 367) - font size 32
            draw.text((713, 373), str(total_games), font=fonts[32], fill=text_color, anchor="lm")
            
            # Wins - (896, 367) - font size 32
            draw.text((1000, 373), str(wins), font=fonts[32], fill=wins_color, anchor="rm")
            
            # Losses - (1045, 367) - font size 32
            draw.text((1150, 373), str(losses), font=fonts[32], fill=losses_color, anchor="rm")
            
            # Win Rate - (1193, 367) - font size 32
            draw.text((1298, 373), win_rate, font=fonts[32], fill=text_color, anchor="rm")
            
            # K/D - (725, 476) - font size 32
            draw.text((713, 480), f"{kd_ratio}", font=fonts[32], fill=text_color, anchor="lm")
            
            # Kills - (986, 476) - font size 32
            draw.text((1000, 480), str(kills), font=fonts[32], fill=text_color, anchor="rm")
            
            # Assists - (1290, 476) - font size 32
            draw.text((1150, 480), str(assists), font=fonts[32], fill=text_color, anchor="rm")
            
            # Deaths - (1140, 476) - font size 32
            draw.text((1298, 480), str(deaths), font=fonts[32], fill=text_color, anchor="rm")
            
            # Game ID - (367, 337) - font size 14
            so2_id = user_data.get('so2_id', 'N/A')
            draw.text((379, 337), str(so2_id), font=fonts[14], fill=text_color, anchor="rm")
            
            # Registration Date - (379, 359) - font size 14, right aligned
            draw.text((379, 359), str(registered_date), font=fonts[14], fill=text_color, anchor="rm")
            
            # Premium Status - (379, 381) - font size 14, right aligned
            premium_status = user_data.get('premium', 'No')
            draw.text((379, 381), str(premium_status), font=fonts[14], fill=text_color, anchor="rm")
            
            # Add rating arrow based on rating value
            await self._add_rating_arrow(image, rating)
            
            # League - (72, 533) - font size 20
            league = user_data.get('league', 'Default League')
            draw.text((56, 533), str(league), font=fonts[20], fill=text_color, anchor="lm")
            
            # Place - (72, 559) - font size 14
            place = user_data.get('place', 'N/A')
            draw.text((56, 559), str(place), font=fonts[14], fill=gray_color, anchor="lm")

            # Level - (72, 711) - font size 20
            level = user_data.get('level', 'Level 1')
            draw.text((56, 711), str(level), font=fonts[20], fill=text_color, anchor="lm")
            
            # Add level icon - 91x71 size - positioned next to level text
            try:
                level_icon_path = self._get_level_icon_path(level)
                if level_icon_path and os.path.exists(level_icon_path):
                    level_icon = Image.open(level_icon_path)
                    
                    # Original icons are ~822x651, target is 91x71 (major downscaling)
                    # For such dramatic downscaling, we need a multi-step approach
                    original_size = level_icon.size
                    target_size = (91, 71)
                    
                    # Convert to RGBA first for better processing
                    if level_icon.mode != 'RGBA':
                        level_icon = level_icon.convert('RGBA')
                    
                    # Multi-step downscaling for better quality when reducing by >8x
                    if original_size[0] / target_size[0] > 8:
                        # Step 1: Reduce to 2x target size first
                        intermediate_size = (target_size[0] * 2, target_size[1] * 2)
                        level_icon = level_icon.resize(intermediate_size, Image.Resampling.LANCZOS)
                        
                        # Step 2: Final resize to target with high-quality algorithm
                        level_icon = level_icon.resize(target_size, Image.Resampling.LANCZOS)
                    else:
                        # Single-step resize for smaller reductions
                        level_icon = level_icon.resize(target_size, Image.Resampling.LANCZOS)
                    
                    # Apply subtle sharpening to recover detail lost in downscaling
                    from PIL import ImageFilter, ImageEnhance
                    
                    # Light sharpening filter
                    level_icon = level_icon.filter(ImageFilter.UnsharpMask(radius=0.3, percent=120, threshold=2))
                    
                    # Enhance contrast slightly to make details more visible
                    enhancer = ImageEnhance.Contrast(level_icon)
                    level_icon = enhancer.enhance(1.1)
                    
                    # Position the level icon near the level text
                    icon_x = 290  # Position it to the right of the level text
                    icon_y = 685  # Align with level text area
                    image.paste(level_icon, (icon_x, icon_y), level_icon)
            except Exception as e:
                logger.warning(f"Could not load level icon: {e}")
            
            # Points - (72, 739) - font size 14
            draw.text((56, 739), str(points), font=fonts[14], fill=gray_color, anchor="lm")
            
            # Add best map data if provided
            if best_map_data and isinstance(best_map_data, dict):
                await self._add_best_map(image, draw, fonts, best_map_data)
            
            # Add match history (always show 5 positions)
            await self._add_match_history(image, draw, fonts, match_history or [])
            
            # Save the image to a temporary file
            temp_path = os.path.join(tempfile.gettempdir(), f"profile_{target_user.id}.webp")
            image.save(temp_path, "WebP", quality=85)

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

    def _get_level_icon_path(self, level_text: str) -> str:
        """Get the path to the appropriate level icon based on level text"""
        try:
            project_root = os.path.dirname(os.path.dirname(__file__))
            level_icons_dir = os.path.join(project_root, 'level_icons')
            
            # Extract level number from level text (e.g., "Level 5" -> "5")
            import re
            level_match = re.search(r'(\d+)', level_text)
            
            if level_match:
                level_num = int(level_match.group(1))
                
                # Determine which icon to use based on level number
                if level_num >= 100:  # Very high level = challenger
                    icon_name = "challenger.webp"
                elif level_num >= 10:
                    icon_name = "level10.webp"
                elif level_num >= 1:
                    icon_name = f"level{level_num}.webp"
                else:
                    icon_name = "level1.webp"  # Default
                
                icon_path = os.path.join(level_icons_dir, icon_name)
                
                # Check if the specific icon exists, otherwise use level1 as fallback
                if os.path.exists(icon_path):
                    return icon_path
                else:
                    fallback_path = os.path.join(level_icons_dir, "level1.webp")
                    return fallback_path if os.path.exists(fallback_path) else ""
            
            # If no level number found, use level1 as default
            default_path = os.path.join(level_icons_dir, "level1.webp")
            return default_path if os.path.exists(default_path) else ""
            
        except Exception as e:
            logger.warning(f"Error determining level icon path: {e}")
            return ""

    def _get_map_icon_path(self, map_name: str) -> str:
        """Get the path to the appropriate map icon based on map name"""
        try:
            project_root = os.path.dirname(os.path.dirname(__file__))
            maps_icons_dir = os.path.join(project_root, 'maps_icons')
            
            # Clean up map name and make it lowercase
            clean_map_name = map_name.lower().strip().replace(" ", "")
            
            # Available maps: breeze, dune, province, rust, sakura, sandstone
            available_maps = ["breeze", "dune", "province", "rust", "sakura", "sandstone"]
            
            # Try to find exact match
            if clean_map_name in available_maps:
                icon_path = os.path.join(maps_icons_dir, f"{clean_map_name}.webp")
                if os.path.exists(icon_path):
                    return icon_path
            
            # Try to find partial match
            for map_option in available_maps:
                if clean_map_name in map_option or map_option in clean_map_name:
                    icon_path = os.path.join(maps_icons_dir, f"{map_option}.webp")
                    if os.path.exists(icon_path):
                        return icon_path
            
            # Default to first available map icon if no match found
            default_icon = os.path.join(maps_icons_dir, "breeze.webp")
            return default_icon if os.path.exists(default_icon) else ""
            
        except Exception as e:
            logger.warning(f"Error determining map icon path: {e}")
            return ""

    def _get_no_match_icon_path(self) -> str:
        """Get the path to the no match icon"""
        try:
            project_root = os.path.dirname(os.path.dirname(__file__))
            maps_icons_dir = os.path.join(project_root, 'maps_icons')
            no_match_icon_path = os.path.join(maps_icons_dir, "no_match.webp")
            return no_match_icon_path if os.path.exists(no_match_icon_path) else ""
        except Exception as e:
            logger.warning(f"Error determining no match icon path: {e}")
            return ""

    async def _add_best_map(self, image: Image.Image, draw: ImageDraw.Draw, fonts: dict, best_map_data: dict):
        """Add best map information to the profile image"""
        try:
            from PIL import ImageFilter, ImageEnhance
            
            map_name = best_map_data.get('map', 'Unknown')
            wins_count = best_map_data.get('wins', 0)
            
            # Text color
            text_color = (255, 255, 255)
            gray_color = (118, 118, 118)  # #767676 in RGB
            
            # Coordinates based on specifications
            map_text_x, map_text_y = 441, 468  # Map name position
            wins_text_x, wins_text_y = 441, 490  # Wins count position
            
            # 1. Map name - font size 22
            draw.text((map_text_x, map_text_y), str(map_name).title(), font=fonts[22], fill=text_color, anchor="lm")
            
            # 2. Wins count - font size 14
            wins_text = f"{wins_count} wins"
            draw.text((wins_text_x, wins_text_y), wins_text, font=fonts[14], fill=gray_color, anchor="lm")
            
            # 3. Map icon - 60x60 size, positioned to the left of the text
            try:
                map_icon_path = self._get_map_icon_path(map_name)
                if map_icon_path and os.path.exists(map_icon_path):
                    map_icon = Image.open(map_icon_path)
                    
                    # Target size for best map icon
                    icon_size = (70, 70)
                    original_size = map_icon.size
                    
                    # Convert to RGBA
                    if map_icon.mode != 'RGBA':
                        map_icon = map_icon.convert('RGBA')
                    
                    # High-quality resize
                    if original_size[0] / icon_size[0] > 4:
                        intermediate_size = (icon_size[0] * 2, icon_size[1] * 2)
                        map_icon = map_icon.resize(intermediate_size, Image.Resampling.LANCZOS)
                        map_icon = map_icon.resize(icon_size, Image.Resampling.LANCZOS)
                    else:
                        map_icon = map_icon.resize(icon_size, Image.Resampling.LANCZOS)
                    
                    # Apply sharpening for better quality
                    map_icon = map_icon.filter(ImageFilter.UnsharpMask(radius=0.3, percent=120, threshold=2))
                    
                    # Hardcoded icon position
                    icon_x = 605  # Fixed X coordinate
                    icon_y = 430  # Fixed Y coordinate
                    
                    image.paste(map_icon, (icon_x, icon_y), map_icon)
                    
            except Exception as e:
                logger.warning(f"Could not load best map icon for {map_name}: {e}")
                
        except Exception as e:
            logger.warning(f"Error adding best map data: {e}")

    async def _add_match_history(self, image: Image.Image, draw: ImageDraw.Draw, fonts: dict, match_history: list):
        """Add match history to the profile image"""
        try:
            from PIL import ImageFilter, ImageEnhance
            
            # Predefined X coordinates for each of the 5 match positions
            # To modify positions: simply change values in this array
            # Position 1: 522, Position 2: 672, Position 3: 822, Position 4: 972, Position 5: 1122
            match_x_positions = [522, 700, 875, 1050, 1225]  # 150px spacing between matches
            
            # Base Y coordinates for match history elements
            match_id_y = 583
            map_icon_y = 660
            match_stats_y = 750
            
            # Colors for win/loss
            win_color = (34, 197, 94)    # Green
            loss_color = (239, 68, 68)   # Red
            text_color = (255, 255, 255) # White
            
            # Display up to 5 recent matches
            matches_to_show = min(len(match_history), 5)
            
            # Always show all 5 positions (fill empty ones with no_match icon)
            for i in range(5):
                # Use predefined X position for this match slot
                match_x = match_x_positions[i]
                
                if i < matches_to_show:
                    # Real match data
                    match = match_history[i]
                    match_id = match.get('id', 'N/A')
                    map_name = match.get('map', 'Unknown')
                    is_win = match.get('is_win', False)
                    kills = match.get('kills', 0)
                    deaths = match.get('deaths', 0)
                    
                    # 1. Match ID - font size 14
                    match_text = f"Match #{match_id}"
                    draw.text((match_x, match_id_y), match_text, font=fonts[14], fill=text_color, anchor="mm")
                    
                    # 2. Map icon at specified position
                    try:
                        map_icon_path = self._get_map_icon_path(map_name)
                        if map_icon_path and os.path.exists(map_icon_path):
                            map_icon = Image.open(map_icon_path)
                            
                            # Resize map icon to appropriate size (similar to level icon)
                            icon_size = (140, 140)  # Smaller than level icon
                            original_size = map_icon.size
                            
                            # Convert to RGBA
                            if map_icon.mode != 'RGBA':
                                map_icon = map_icon.convert('RGBA')
                            
                            # High-quality resize with multi-step approach if needed
                            if original_size[0] / icon_size[0] > 4:
                                intermediate_size = (icon_size[0] * 2, icon_size[1] * 2)
                                map_icon = map_icon.resize(intermediate_size, Image.Resampling.LANCZOS)
                                map_icon = map_icon.resize(icon_size, Image.Resampling.LANCZOS)
                            else:
                                map_icon = map_icon.resize(icon_size, Image.Resampling.LANCZOS)
                            
                            # Apply sharpening for better quality
                            map_icon = map_icon.filter(ImageFilter.UnsharpMask(radius=0.3, percent=120, threshold=2))
                            
                            # Center the icon at the specified coordinates
                            icon_x = match_x - icon_size[0] // 2
                            icon_y = map_icon_y - icon_size[1] // 2
                            image.paste(map_icon, (icon_x, icon_y), map_icon)
                    except Exception as e:
                        logger.warning(f"Could not load map icon for {map_name}: {e}")
                    
                    # 3. Match stats - font size 14 with color based on win/loss
                    result_text = "Win" if is_win else "Lose"
                    result_color = win_color if is_win else loss_color
                    stats_text = f"{result_text} - {kills}k/{deaths}d"
                    
                    draw.text((match_x, match_stats_y), stats_text, font=fonts[14], fill=result_color, anchor="mm")
                    
                else:
                    # Empty match slot - show no_match icon
                    try:
                        no_match_icon_path = self._get_no_match_icon_path()
                        if no_match_icon_path and os.path.exists(no_match_icon_path):
                            no_match_icon = Image.open(no_match_icon_path)
                            
                            # Same size as regular map icons for consistency
                            icon_size = (90, 90)
                            original_size = no_match_icon.size
                            
                            # Convert to RGBA
                            if no_match_icon.mode != 'RGBA':
                                no_match_icon = no_match_icon.convert('RGBA')
                            
                            # High-quality resize
                            if original_size[0] / icon_size[0] > 4:
                                intermediate_size = (icon_size[0] * 2, icon_size[1] * 2)
                                no_match_icon = no_match_icon.resize(intermediate_size, Image.Resampling.LANCZOS)
                                no_match_icon = no_match_icon.resize(icon_size, Image.Resampling.LANCZOS)
                            else:
                                no_match_icon = no_match_icon.resize(icon_size, Image.Resampling.LANCZOS)
                            
                            # Apply sharpening for better quality
                            no_match_icon = no_match_icon.filter(ImageFilter.UnsharpMask(radius=0.3, percent=120, threshold=2))
                            
                            # Center the icon at the specified coordinates
                            icon_x = match_x - icon_size[0] // 2
                            icon_y = map_icon_y - icon_size[1] // 2
                            image.paste(no_match_icon, (icon_x, icon_y), no_match_icon)
                    except Exception as e:
                        logger.warning(f"Could not load no_match icon: {e}")
                    
                    # No text for empty slots (or you could add "No Match" text if desired)
                
        except Exception as e:
            logger.warning(f"Error adding match history: {e}")

    def _get_rating_arrow_x_position(self, rating: float) -> int:
        """
        Calculează poziția X pentru săgeata de rating pe baza valorii rating-ului
        
        Args:
            rating: Valoarea rating-ului (ex: 0.25, 0.75, 1.5)
            
        Returns:
            int: Poziția X unde să fie plasată săgeata
        """
        try:
            # Definim intervalele și pozițiile X corespunzătoare
            if rating <= 0.50:
                # Rating 0-0.50: poziție în partea stângă
                return 490  # Poziție pentru rating mic
            elif rating <= 1.0:
                # Rating 0.50-1.0: poziție în centru
                return 570  # Poziție pentru rating mediu
            else:
                # Rating >1.0: poziție în partea dreaptă
                return 650  # Poziție pentru rating mare
                
        except Exception as e:
            logger.warning(f"Error calculating rating arrow position: {e}")
            return 570  # Return default center position

    async def _add_rating_arrow(self, image: Image.Image, rating: float):
        """
        Adaugă săgeata de rating la imaginea de profil
        
        Args:
            image: Imaginea PIL unde să adăugăm săgeata
            rating: Valoarea rating-ului pentru calcularea poziției
        """
        try:
            from PIL import ImageFilter, ImageEnhance
            
            # Get the project root directory
            project_root = os.path.dirname(os.path.dirname(__file__))
            
            # Path to rating arrow image
            arrow_path = os.path.join(project_root, 'template_images', 'rating_arrow.webp')
            
            if not os.path.exists(arrow_path):
                logger.warning(f"Rating arrow image not found: {arrow_path}")
                return
            
            # Load the rating arrow image
            arrow_image = Image.open(arrow_path)
            
            # Convert to RGBA if needed
            if arrow_image.mode != 'RGBA':
                arrow_image = arrow_image.convert('RGBA')
            
            # Calculate the X position based on rating
            arrow_x = self._get_rating_arrow_x_position(rating)
            
            # Fixed Y position for the rating arrow
            arrow_y = 378  # Poziție Y fixă pentru săgeată
            
            # Get arrow dimensions for centering
            arrow_width, arrow_height = arrow_image.size
            
            # Center the arrow at the calculated position
            final_x = arrow_x - arrow_width // 2
            final_y = arrow_y - arrow_height // 2
            
            # Paste the arrow onto the profile image
            image.paste(arrow_image, (final_x, final_y), arrow_image)
            
        except Exception as e:
            logger.warning(f"Error adding rating arrow: {e}")
