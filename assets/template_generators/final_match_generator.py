"""
Final Match Template Generator using Pillow
Replaces HTML2Image for final match embed generation
"""

import os
from PIL import Image, ImageDraw, ImageFont
from typing import List, Optional, Union
import logging
import io

logger = logging.getLogger(__name__)


class FinalMatchTemplateGenerator:
    """Generator pentru template-ul final de meci folosind Pillow"""
    
    def __init__(self, background_image_path: str = "assets/template_images/arena_faceit_match_details.png"):
        self.background_image_path = background_image_path
        self.default_font_path = "assets/fonts/font-black.ttf"
        
    async def generate_final_match_image(
        self,
        match_id: str,
        team1_players: List[Union[str, object]],  # Poate fi string sau Discord Member
        team2_players: List[Union[str, object]],  # Poate fi string sau Discord Member
        team1_captain: Union[str, object],
        team2_captain: Union[str, object],
        map_name: str,
        output_path: str
    ) -> bool:
        """
        Generează imaginea pentru meciul final folosind Pillow
        
        Args:
            match_id: ID-ul meciului (ex: "Match #123")
            team1_players: Lista jucătorilor din echipa 1 (Discord Members sau strings)
            team2_players: Lista jucătorilor din echipa 2 (Discord Members sau strings)
            team1_captain: Căpitanul echipei 1 (Discord Member sau string)
            team2_captain: Căpitanul echipei 2 (Discord Member sau string)
            map_name: Numele hărții selectate
            output_path: Calea unde să salveze imaginea generată
            
        Returns:
            bool: True dacă generarea a reușit, False altfel
        """
        try:
            # Get the project root directory (din assets/template_generators/ urcăm 2 niveluri)
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
            
            # Încarcă imaginea de fundal
            background_path = os.path.join(project_root, self.background_image_path)
            if not os.path.exists(background_path):
                logger.error(f"Background image not found: {background_path}")
                return False
                
            background = Image.open(background_path)
            draw = ImageDraw.Draw(background)
            
            # Încarcă fontul cu mărimea 130
            font_size = 130
            try:
                font_path = os.path.join(project_root, self.default_font_path)
                font = ImageFont.truetype(font_path, font_size)
                logger.info(f"Loaded custom font: {font_path} with size {font_size}")
            except (OSError, IOError) as e:
                logger.warning(f"Custom font not found ({e}), using default")
                font = ImageFont.load_default()
            
            # Culoare text
            white = (255, 255, 255)
            
            # Map name la (274, 197)
            draw.text((274, 213), map_name, fill=white, font=font, anchor="lm")
            
            # Match ID la (2071, 192) cu anchor lm (left-middle)
            draw.text((2090, 213), match_id, fill=white, font=font, anchor="rm")
            
            # Team 1 Captain Name la (568, 727) cu anchor rm (right-middle)
            captain1_name = f"Team {self._get_display_name(team1_captain)}"
            draw.text((550, 717), captain1_name, fill=white, font=font, anchor="lm")
            
            # Team 1 players cu coordonate Y specifice
            team1_y_positions = [1138, 1383, 1623, 1866, 2105]
            team1_avatar_y_positions = [1028, 1271, 1518, 1762, 2007]
            for i, player in enumerate(team1_players):
                if i < len(team1_y_positions):  # Verifică că nu depășim numărul de poziții disponibile
                    y_pos = team1_y_positions[i]
                    player_name = self._get_display_name(player)
                    
                    # Adaugă avatar dacă este disponibil
                    avatar = self._get_avatar(player)
                    if avatar and i < len(team1_avatar_y_positions):
                        # Avatar pentru Team 1 cu coordonate Y fixe
                        avatar_x = 419 - 334
                        avatar_y = team1_avatar_y_positions[i]
                        await self._draw_avatar_async(background, avatar, avatar_x, avatar_y, 217)
                    
                    # Adaugă level icon în stânga numelui
                    level_icon_path = await self._get_player_level_icon_path(player)
                    if level_icon_path:
                        # Level icon pentru Team 1 în stânga numelui
                        level_x = 515 - 120  # În stânga numelui cu un offset mai mare
                        level_y = y_pos - 51  # Centrat pe linia textului
                        self._draw_level_icon(background, level_icon_path, level_x, level_y, 100)
                    
                    draw.text((515, y_pos), player_name, fill=white, font=font, anchor="lm")
            
            # Team 2 Captain Name la (1830, 717) cu anchor lm (left-middle)
            captain2_name = f"Team {self._get_display_name(team2_captain)}"
            draw.text((1860, 717), captain2_name, fill=white, font=font, anchor="rm")
            
            # Team 2 players cu coordonate Y specifice
            team2_y_positions = [1138, 1383, 1623, 1866, 2105]
            team2_avatar_y_positions = [1028, 1271, 1517, 1762, 2006]
            for i, player in enumerate(team2_players):
                if i < len(team2_y_positions):  # Verifică că nu depășim numărul de poziții disponibile
                    y_pos = team2_y_positions[i]
                    player_name = self._get_display_name(player)
                    
                    # Adaugă avatar dacă este disponibil
                    avatar = self._get_avatar(player)
                    if avatar and i < len(team2_avatar_y_positions):
                        # Avatar pentru Team 2 cu coordonate Y fixe
                        avatar_x = 1945 + 115
                        avatar_y = team2_avatar_y_positions[i]
                        await self._draw_avatar_async(background, avatar, avatar_x, avatar_y, 217)
                    
                    # Adaugă level icon în dreapta numelui
                    level_icon_path = await self._get_player_level_icon_path(player)
                    if level_icon_path:
                        # Level icon pentru Team 2 în dreapta numelui
                        level_x = 1849 + 28  # În dreapta numelui cu un offset mai mare
                        level_y = y_pos - 49  # Centrat pe linia textului
                        self._draw_level_icon(background, level_icon_path, level_x, level_y, 100)
                    
                    draw.text((1849, y_pos), player_name, fill=white, font=font, anchor="rm")
            
            # Salvează imaginea
            background.save(output_path, "PNG")
            logger.info(f"Final match image generated successfully: {output_path}")
            return True
            
        except Exception as e:
            logger.error(f"Error generating final match image: {e}")
            return False
    
    def _get_display_name(self, player):
        """Returnează numele de afișaj al jucătorului"""
        if hasattr(player, 'display_name'):
            return player.display_name
        elif hasattr(player, 'name'):
            return player.name
        else:
            return str(player)
    
    def _get_avatar(self, player):
        """Returnează avatarul jucătorului (binary data sau URL)"""
        if hasattr(player, 'display_avatar'):
            return player.display_avatar  # Returnează obiectul avatar pentru read()
        elif hasattr(player, 'avatar') and player.avatar:
            return player.avatar  # Returnează obiectul avatar pentru read()
        return None
    
    async def _get_player_level_icon_path(self, player, league_table=None):
        """Returnează calea către iconița de level pentru jucător"""
        try:
            if not hasattr(player, 'id'):
                return None
            
            # Import local pentru a evita circular imports
            from models.stats import Stats
            from cogs.levels import LevelsCog
            
            # Obține statisticile jucătorului
            stats_result = await Stats.get_user_stats(player.id, league_table)
            if not stats_result or not stats_result.get("success"):
                points = 0
                logger.info(f"No stats found for player {getattr(player, 'display_name', player)}, using 0 points")
            else:
                stats_data = stats_result.get("stats", {})
                points = stats_data.get("points", 0)
                logger.info(f"Player {getattr(player, 'display_name', player)} has {points} points")
            
            # Folosește funcția existentă din LevelsCog pentru a obține calea
            level_icon_path = LevelsCog.get_level_icon_path(points)
            logger.info(f"LevelsCog returned path: {level_icon_path}")
            
            # LevelsCog returnează "assets/level_icons/level1.png" 
            # Trebuie să construim calea completă față de project_root
            if level_icon_path:
                # Din assets/template_generators/, project_root este ../../ (urcăm 2 niveluri)
                project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
                full_level_icon_path = os.path.join(project_root, level_icon_path)
                logger.info(f"Full level icon path: {full_level_icon_path}")
                
                # Verifică dacă fișierul există
                if os.path.exists(full_level_icon_path):
                    logger.info(f"Level icon found for {getattr(player, 'display_name', player)}: {full_level_icon_path}")
                    return full_level_icon_path
                else:
                    logger.warning(f"Level icon not found at: {full_level_icon_path}")
            
            # Fallback la level 1 dacă fișierul nu există
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
            fallback_path = os.path.join(project_root, "assets", "level_icons", "level1.png")
            if os.path.exists(fallback_path):
                logger.info(f"Using fallback level icon for {getattr(player, 'display_name', player)}: {fallback_path}")
                return fallback_path
            else:
                logger.warning(f"Fallback level icon not found at: {fallback_path}")
                return None
                
        except Exception as e:
            logger.warning(f"Failed to get level icon for player {player}: {e}")
            return None
    
    def _draw_level_icon(self, background, level_icon_path, x, y, size=120):
        """Desenează iconița de level pe imagine"""
        try:
            if not level_icon_path or not os.path.exists(level_icon_path):
                return
            
            level_icon = Image.open(level_icon_path)
            
            # Convertește la RGBA pentru transparență
            if level_icon.mode != 'RGBA':
                level_icon = level_icon.convert('RGBA')
            
            # Obține dimensiunile originale
            original_width, original_height = level_icon.size
            
            # Dacă imaginea nu este pătrată, o forțăm să fie pătrată
            if original_width != original_height:
                # Creează o imagine pătrată nouă transparentă
                max_size = max(original_width, original_height)
                square_icon = Image.new('RGBA', (max_size, max_size), (0, 0, 0, 0))
                
                # Calculează poziția pentru centrare
                paste_x = (max_size - original_width) // 2
                paste_y = (max_size - original_height) // 2
                
                # Paste imaginea originală în centrul imaginii pătrate
                square_icon.paste(level_icon, (paste_x, paste_y), level_icon)
                level_icon = square_icon
            
            # Acum redimensionează imaginea pătrată la dimensiunea dorită
            level_icon = level_icon.resize((size, size), Image.Resampling.LANCZOS)
            
            # Aplică iconița pe imagine
            background.paste(level_icon, (x, y), level_icon)
            
        except Exception as e:
            logger.warning(f"Failed to draw level icon from {level_icon_path}: {e}")
    

    

    
    async def _draw_avatar_async(self, background, avatar_obj, x, y, size):
        """Desenează avatarul pe imagine folosind discord.py avatar.read()"""
        try:
            # Citește avatarul folosind discord.py
            avatar_bytes = await avatar_obj.read()
            avatar_image = Image.open(io.BytesIO(avatar_bytes))
            
            # Convertește la RGB dacă este necesar
            if avatar_image.mode != 'RGB':
                avatar_image = avatar_image.convert('RGB')
            
            # Redimensionează avatarul
            avatar_image = avatar_image.resize((size, size), Image.Resampling.LANCZOS)
            
            # Creează un avatar circular
            mask = Image.new('L', (size, size), 0)
            mask_draw = ImageDraw.Draw(mask)
            mask_draw.ellipse((0, 0, size, size), fill=255)
            
            # Creează o imagine RGBA pentru avatarul circular
            circular_avatar = Image.new('RGBA', (size, size), (0, 0, 0, 0))
            circular_avatar.paste(avatar_image, (0, 0))
            circular_avatar.putalpha(mask)
            
            # Aplică avatarul pe imagine
            background.paste(circular_avatar, (x, y), circular_avatar)
            
        except Exception as e:
            logger.warning(f"Failed to load avatar: {e}")



    def _draw_centered_text(self, draw, text, x, y, font, fill):
        """Helper pentru a desena text centrat"""
        try:
            draw.text((x, y), text, font=font, fill=fill, anchor="mm")
        except Exception:
            # Fallback pentru versiuni mai vechi de Pillow
            bbox = draw.textbbox((0, 0), text, font=font)
            text_width = bbox[2] - bbox[0]
            text_height = bbox[3] - bbox[1]
            draw.text((x - text_width//2, y - text_height//2), text, font=font, fill=fill)
    
    def get_image_size(self) -> tuple:
        """Returnează dimensiunile imaginii de fundal"""
        try:
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
            background_path = os.path.join(project_root, self.background_image_path)
            if os.path.exists(background_path):
                with Image.open(background_path) as img:
                    return img.size
            return (740, 711)  # Dimensiuni default
        except Exception:
            return (740, 711)  # Dimensiuni default
