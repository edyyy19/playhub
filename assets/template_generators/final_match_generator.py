"""
Final Match Template Generator using Pillow
Replaces HTML2Image for final match embed generation
"""

import os
from PIL import Image, ImageDraw, ImageFont
from typing import List, Optional, Union, Dict
import logging
import io
import asyncio
import time
import weakref
from functools import lru_cache

logger = logging.getLogger(__name__)


class FinalMatchTemplateGenerator:
    """Generator pentru template-ul final de meci folosind Pillow cu cache optimizat"""
    
    def __init__(self, background_image_path: str = "assets/template_images/match_details.webp"):
        self.background_image_path = background_image_path
        self.default_font_path = "assets/fonts/fira_code_semibold.ttf"
        
        # Cache pentru optimizare cu toate variabilele necesare
        self._font_cache: Dict[int, tuple] = {}  # size -> (font, timestamp)
        self._avatar_cache: Dict[str, tuple] = {}  # user_id -> (avatar_data, timestamp)
        self._level_icon_cache: Dict[str, tuple] = {}  # path -> (image, timestamp)
        self._level_icons_cache: Dict[str, Image.Image] = {}  # path -> processed_image (legacy)
        self._circular_mask_cache: Dict[int, Image.Image] = {}  # size -> mask
        self._template_cache: Optional[Image.Image] = None
        self._template_cache_time: float = 0
        
        # Cache settings - durate în secunde
        self.avatar_cache_duration = 300  # 5 minutes
        self.font_cache_duration = 1800  # 30 minutes
        self.level_icon_cache_duration = 600  # 10 minutes
        self.template_cache_duration = 1800  # 30 minutes
        
        # Pre-load level icons at startup
        asyncio.create_task(self._preload_level_icons())
        
    async def _preload_level_icons(self):
        """Pre-încarcă toate icon-urile de level în cache pentru performanță maximă"""
        try:
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
            level_icons_dir = os.path.join(project_root, "assets", "level_icons")
            
            if not os.path.exists(level_icons_dir):
                logger.warning(f"Level icons directory not found: {level_icons_dir}")
                return
            
            # Pre-load all level icons (WebP format pentru performanță optimă)
            icon_files = [
                "level1.webp", "level2.webp", "level3.webp", "level4.webp", "level5.webp",
                "level6.webp", "level7.webp", "level8.webp", "level9.webp", "level10.webp",
                "challenger.webp"
            ]
            
            for icon_file in icon_files:
                icon_path = os.path.join(level_icons_dir, icon_file)
                if os.path.exists(icon_path):
                    try:
                        # Pre-process icon pentru toate mărimile comune
                        original_icon = Image.open(icon_path)
                        if original_icon.mode != 'RGBA':
                            original_icon = original_icon.convert('RGBA')
                        
                        # Pre-process pentru 60px și 70px (mărimile standard pentru avataruri)
                        processed_icon_60 = self._process_level_icon(original_icon, 60)
                        processed_icon_70 = self._process_level_icon(original_icon, 70)
                        self._level_icons_cache[f"{icon_path}_60"] = processed_icon_60
                        self._level_icons_cache[f"{icon_path}_70"] = processed_icon_70
                        
                        logger.debug(f"Pre-loaded level icon: {icon_file}")
                    except Exception as e:
                        logger.warning(f"Failed to pre-load level icon {icon_file}: {e}")
                        
            logger.info(f"Pre-loaded {len(self._level_icons_cache)} level icons into cache")
            
        except Exception as e:
            logger.error(f"Error pre-loading level icons: {e}")
    
    def _get_cached_font(self, size: int) -> ImageFont.FreeTypeFont:
        """Returnează font-ul din cache sau îl încarcă și îl cache-uiește"""
        if size not in self._font_cache:
            try:
                project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
                font_path = os.path.join(project_root, self.default_font_path)
                self._font_cache[size] = ImageFont.truetype(font_path, size)
                logger.debug(f"Cached font size {size}")
            except (OSError, IOError) as e:
                logger.warning(f"Custom font not found ({e}), using default for size {size}")
                self._font_cache[size] = ImageFont.load_default()
        
        return self._font_cache[size]
    
    def _get_circular_mask(self, size: int) -> Image.Image:
        """Returnează mask-ul circular din cache sau îl creează și îl cache-uiește"""
        if size not in self._circular_mask_cache:
            mask = Image.new('L', (size, size), 0)
            mask_draw = ImageDraw.Draw(mask)
            mask_draw.ellipse((0, 0, size, size), fill=255)
            self._circular_mask_cache[size] = mask
            logger.debug(f"Created circular mask for size {size}")
        
        return self._circular_mask_cache[size]
    
    def _process_level_icon(self, level_icon: Image.Image, size: int) -> Image.Image:
        """Procesează iconița de level (face pătrată și redimensionează)"""
        # Obține dimensiunile originale
        original_width, original_height = level_icon.size
        
        # Dacă imaginea nu este pătrată, o forțăm să fie pătrată
        if original_width != original_height:
            max_size = max(original_width, original_height)
            square_icon = Image.new('RGBA', (max_size, max_size), (0, 0, 0, 0))
            
            paste_x = (max_size - original_width) // 2
            paste_y = (max_size - original_height) // 2
            square_icon.paste(level_icon, (paste_x, paste_y), level_icon)
            level_icon = square_icon
        
        # Redimensionează la mărimea dorită
        return level_icon.resize((size, size), Image.Resampling.LANCZOS)
    
    async def _get_cached_avatar(self, player) -> Optional[Image.Image]:
        """Returnează avatarul din cache sau îl descarcă și îl cache-uiește"""
        if not hasattr(player, 'id'):
            return None
            
        user_id = str(player.id)
        current_time = time.time()
        
        # Verifică cache-ul
        if user_id in self._avatar_cache:
            avatar_data, timestamp = self._avatar_cache[user_id]
            if current_time - timestamp < self.avatar_cache_duration:
                return avatar_data
            else:
                # Cache expirat, șterge intrarea
                del self._avatar_cache[user_id]
        
        # Descarcă și procesează avatarul
        try:
            avatar_obj = self._get_avatar(player)
            if not avatar_obj:
                return None
                
            avatar_bytes = await avatar_obj.read()
            avatar_image = Image.open(io.BytesIO(avatar_bytes))
            
            if avatar_image.mode != 'RGB':
                avatar_image = avatar_image.convert('RGB')
            
            # Cache avatarul procesat
            self._avatar_cache[user_id] = (avatar_image, current_time)
            logger.debug(f"Cached avatar for user {user_id}")
            
            return avatar_image
            
        except Exception as e:
            logger.warning(f"Failed to get avatar for {player}: {e}")
            return None
    async def _batch_get_player_stats(self, players: List[Union[str, object]], league_table=None) -> Dict[str, int]:
        """Obține statisticile pentru toți jucătorii cu un singur batch query optimizat"""
        try:
            if not players:
                return {}
                
            # Import local pentru a evita circular imports
            from config import Config
            
            # Filtrează doar jucătorii cu ID valid
            valid_players = [p for p in players if hasattr(p, 'id')]
            if not valid_players:
                return {}
            
            # Obține config-ul o singură dată pentru toți jucătorii
            table_name = league_table
            if not table_name:
                try:
                    config_response = await asyncio.get_event_loop().run_in_executor(
                        None, 
                        lambda: Config.supabase().table("config").select("*").execute()
                    )
                    if config_response.data:
                        config_data = config_response.data[0]
                        table_name = config_data.get("default_league")
                        logger.info(f"Using default league: {table_name}")
                    else:
                        logger.warning("No config found, using fallback")
                        return {str(p.id): 0 for p in valid_players}
                except Exception as e:
                    logger.warning(f"Failed to get config: {e}")
                    return {str(p.id): 0 for p in valid_players}
            
            # BATCH QUERY: Folosește IN clause pentru toți jucătorii dintr-o dată
            player_ids = [str(p.id) for p in valid_players]
            
            try:
                # Un singur query cu IN clause în loc de 4 queries separate
                batch_response = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: Config.supabase()
                    .table(table_name)
                    .select("discord_id,points")
                    .in_("discord_id", player_ids)
                    .execute()
                )
                
                # Construiește dicționarul rezultat din batch response
                stats_dict = {}
                found_players = set()
                
                if batch_response.data:
                    for player_data in batch_response.data:
                        discord_id = player_data.get("discord_id")
                        points = player_data.get("points", 0)
                        stats_dict[discord_id] = points
                        found_players.add(discord_id)
                
                # Adaugă 0 points pentru jucătorii care nu sunt găsiți
                for player_id in player_ids:
                    if player_id not in found_players:
                        stats_dict[player_id] = 0
                
                logger.info(f"Batch loaded stats for {len(stats_dict)} players with single IN query")
                return stats_dict
                
            except Exception as e:
                logger.error(f"Batch query failed: {e}")
                # Fallback to individual queries if batch fails
                return await self._fallback_individual_stats(valid_players, table_name)
            
        except Exception as e:
            logger.error(f"Error in batch get player stats: {e}")
            return {}

    async def _fallback_individual_stats(self, players: List, table_name: str) -> Dict[str, int]:
        """Fallback pentru individual queries dacă batch eșuează"""
        try:
            from models.stats import Stats
            
            stats_tasks = []
            for player in players:
                task = Stats.get_user_stats(player.id, table_name)
                stats_tasks.append((player, task))
            
            results = await asyncio.gather(*[task for _, task in stats_tasks], return_exceptions=True)
            
            stats_dict = {}
            for i, (player, _) in enumerate(stats_tasks):
                result = results[i]
                if not isinstance(result, Exception) and result:
                    if result.get("success"):
                        stats_data = result.get("stats", {})
                        points = stats_data.get("points", 0)
                        stats_dict[str(player.id)] = points
                    else:
                        stats_dict[str(player.id)] = 0
                else:
                    stats_dict[str(player.id)] = 0
            
            logger.info(f"Fallback loaded stats for {len(stats_dict)} players")
            return stats_dict
            
        except Exception as e:
            logger.error(f"Fallback stats failed: {e}")
            return {str(p.id): 0 for p in players}

    async def _get_single_player_stats(self, player_id: str, league_table=None) -> int:
        """Obține statisticile pentru un singur jucător (async wrapper)"""
        try:
            from models.stats import Stats
            
            # Stats.get_user_stats este deja async, nu trebuie run_in_executor
            stats_result = await Stats.get_user_stats(player_id, league_table)
            
            if stats_result and stats_result.get("success"):
                stats_data = stats_result.get("stats", {})
                return stats_data.get("points", 0)
            else:
                return 0
                
        except Exception as e:
            logger.warning(f"Failed to get stats for player {player_id}: {e}")
            return 0
    
    async def _batch_process_avatars(self, players: List[Union[str, object]], size: int = 217) -> Dict[str, Image.Image]:
        """Procesează toate avatar-urile în paralel pentru performanță maximă"""
        try:
            # Filtrează doar jucătorii cu avatar valid
            valid_players = [(p, self._get_avatar(p)) for p in players if hasattr(p, 'id')]
            valid_players = [(p, avatar) for p, avatar in valid_players if avatar is not None]
            
            if not valid_players:
                return {}
            
            # Procesează avatar-urile în paralel
            tasks = []
            for player, avatar_obj in valid_players:
                task = self._process_single_avatar(player, avatar_obj, size)
                tasks.append(task)
            
            # Așteaptă ca toate să se termine
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # Construiește dicționarul rezultat
            avatar_dict = {}
            for i, (player, _) in enumerate(valid_players):
                result = results[i]
                if not isinstance(result, Exception) and result is not None:
                    avatar_dict[str(player.id)] = result
                    
            logger.info(f"Batch processed {len(avatar_dict)} avatars")
            return avatar_dict
            
        except Exception as e:
            logger.error(f"Error in batch process avatars: {e}")
            return {}
    
    async def _process_single_avatar(self, player, avatar_obj, size: int) -> Optional[Image.Image]:
        """Procesează un singur avatar (pentru folosire în batch)"""
        try:
            user_id = str(player.id)
            current_time = time.time()
            
            # Verifică cache-ul mai întâi
            if user_id in self._avatar_cache:
                cached_avatar, timestamp = self._avatar_cache[user_id]
                if current_time - timestamp < self.avatar_cache_duration:
                    # Returnează avatarul redimensionat din cache
                    return cached_avatar.resize((size, size), Image.Resampling.LANCZOS)
            
            # Descarcă și procesează avatarul
            avatar_bytes = await avatar_obj.read()
            avatar_image = Image.open(io.BytesIO(avatar_bytes))
            
            if avatar_image.mode != 'RGB':
                avatar_image = avatar_image.convert('RGB')
            
            # Cache-uiește avatarul original
            self._avatar_cache[user_id] = (avatar_image, current_time)
            
            # Returnează avatarul redimensionat
            return avatar_image.resize((size, size), Image.Resampling.LANCZOS)
            
        except Exception as e:
            logger.warning(f"Failed to process avatar for {player}: {e}")
            return None

    async def generate_final_match_image(
        self,
        match_id: str,
        team1_players: List[Union[str, object]],  # Poate fi string sau Discord Member
        team2_players: List[Union[str, object]],  # Poate fi string sau Discord Member
        team1_captain: Union[str, object],
        team2_captain: Union[str, object],
        map_name: str,
        output_path: str,
        server: str = 'PlayHub Server',
        league_table=None
    ) -> bool:
        """
        Generează imaginea pentru meciul final folosind Pillow cu optimizări async
        
        Args:
            match_id: ID-ul meciului (ex: "Match #123")
            team1_players: Lista jucătorilor din echipa 1 (Discord Members sau strings)
            team2_players: Lista jucătorilor din echipa 2 (Discord Members sau strings)
            team1_captain: Căpitanul echipei 1 (Discord Member sau string)
            team2_captain: Căpitanul echipei 2 (Discord Member sau string)
            map_name: Numele hărții selectate
            server: Numele serverului
            output_path: Calea unde să salveze imaginea generată
            league_table: Tabelul ligii pentru statistici
            
        Returns:
            bool: True dacă generarea a reușit, False altfel
        """
        try:
            start_time = time.time()
            
            # Get the project root directory (din assets/template_generators/ urcăm 2 niveluri)
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
            
            # Încarcă templateul de fundal (cu cache)
            background = await self._get_cached_template()
            if background is None:
                logger.error("Failed to load background template")
                return False
            
            # Creează o copie pentru editare
            background = background.copy()
            draw = ImageDraw.Draw(background)
            
            # Încarcă fonturile cu cache pentru toate mărimile necesare
            font_24 = await self._get_cached_font(24)
            font_26 = await self._get_cached_font(26)
            font_42 = await self._get_cached_font(42)
            if not all([font_24, font_26, font_42]):
                logger.warning("Failed to load some fonts, using defaults")
                font_24 = font_24 or ImageFont.load_default()
                font_26 = font_26 or ImageFont.load_default()
                font_42 = font_42 or ImageFont.load_default()
            
            # Procesează toate avatar-urile în paralel
            all_players = team1_players + team2_players
            captain_avatars = await self._batch_process_avatars([team1_captain, team2_captain], 70)  # 70x70 pentru căpitani
            player_avatars = await self._batch_process_avatars(all_players, 60)  # 60x60 pentru jucători
            
            # Obține statisticile în batch pentru calculul punctelor și avg kills
            stats_dict = await self._batch_get_player_stats(all_players, league_table)
            captain_stats = await self._batch_get_player_stats([team1_captain, team2_captain], league_table)
            
            # Încarcă toate level icon-urile în paralel pentru jucători (47x37)
            player_level_icons = await self._batch_load_level_icons(all_players, stats_dict, size=(47, 37))
            
            # Calculează și încarcă level-urile echipelor (59x47)
            team_level_icons = await self._calculate_and_load_team_level_icons([team1_players, team2_players], stats_dict, size=(59, 47))
            
            # Culoare text
            white = (255, 255, 255)
            
            # =====================================================================
            # COORDINATE CONFIGURATION - MODIFY HERE TO ADJUST ELEMENT POSITIONS
            # =====================================================================
            
            # --- Header coordinates ---
            MATCH_ID_X = 909
            MATCH_ID_Y = 130
            
            MAP_NAME_X = 69
            MAP_NAME_Y = 1122
            
            SERVER_X = 644
            SERVER_Y = 1122
            
            # --- Team A coordinates ---
            # Captain coordinates
            TEAM_A_CAPTAIN_AVATAR_X = 75
            TEAM_A_CAPTAIN_AVATAR_Y = 301
            TEAM_A_CAPTAIN_AVATAR_SIZE = 70
            
            TEAM_A_CAPTAIN_NAME_X = 164
            TEAM_A_CAPTAIN_NAME_Y = 346
            
            # Team average level coordinates
            TEAM_A_LEVEL_X = 725
            TEAM_A_LEVEL_Y = 314
            TEAM_A_LEVEL_SIZE = (59, 47)
            
            # Team A players list coordinates
            TEAM_A_PLAYER_AVATAR_X = 76
            TEAM_A_PLAYER_AVATAR_SIZE = 60
            
            TEAM_A_PLAYER_NICKNAME_X = 163  # +20 în stânga avatarului
            
            TEAM_A_LEVEL_ICON_X = 447
            TEAM_A_LEVEL_ICON_SIZE = (47, 37)
            
            TEAM_A_POINTS_X = 593
            TEAM_A_AVG_KILLS_X = 741
            
            # --- Team B coordinates ---
            # Captain coordinates
            TEAM_B_CAPTAIN_AVATAR_X = 1680
            TEAM_B_CAPTAIN_AVATAR_Y = 301
            TEAM_B_CAPTAIN_AVATAR_SIZE = 70
            
            TEAM_B_CAPTAIN_NAME_X = 1657
            TEAM_B_CAPTAIN_NAME_Y = 346
            
            # Team average level coordinates
            TEAM_B_LEVEL_X = 1035
            TEAM_B_LEVEL_Y = 315
            TEAM_B_LEVEL_SIZE = (59, 47)
            
            # Team B players list coordinates
            TEAM_B_PLAYER_AVATAR_X = 1683
            TEAM_B_PLAYER_AVATAR_SIZE = 60
            
            TEAM_B_PLAYER_NICKNAME_X = 1656  # +20 în stânga avatarului, right aligned
            
            TEAM_B_LEVEL_ICON_X = 1330
            TEAM_B_LEVEL_ICON_SIZE = (47, 37)
            
            TEAM_B_POINTS_X = 1229
            TEAM_B_AVG_KILLS_X = 1082
            
            # --- Player list settings ---
            PLAYERS_BASE_Y = 500
            PLAYERS_GAP_Y = 110  # Gap între jucători
            PLAYER_TEXT_OFFSET_Y = 30  # Offset pentru centrarea textului pe Y
            LEVEL_ICON_OFFSET_Y = 12  # Offset pentru centrarea icon-ului pe Y
            
            # =====================================================================
            # DRAWING ELEMENTS USING DEFINED COORDINATES
            # =====================================================================
            
            # Match ID - (font size 24, 909, 130)
            draw.text((MATCH_ID_X, MATCH_ID_Y), match_id, fill=white, font=font_24, anchor="mm")
            
            # Selected Map Name - (font size 42, 77, 1122)
            draw.text((MAP_NAME_X, MAP_NAME_Y), map_name, fill=white, font=font_42, anchor="lm")
            
            # Server - (font size 42, 649, 1122)
            draw.text((SERVER_X, SERVER_Y), server, fill=white, font=font_42, anchor="lm")
            
            # === TEAM A ===
            # Captain avatar - (70x70, 114, 335)
            captain1_id = str(team1_captain.id) if hasattr(team1_captain, 'id') else str(team1_captain)
            if captain1_id in captain_avatars:
                await self._draw_avatar_from_cache(background, captain_avatars[captain1_id], TEAM_A_CAPTAIN_AVATAR_X, TEAM_A_CAPTAIN_AVATAR_Y)
            
            # Team captain name - (font size 26, 167, 340)
            captain1_name = f"team_{self._get_display_name(team1_captain)}"
            draw.text((TEAM_A_CAPTAIN_NAME_X, TEAM_A_CAPTAIN_NAME_Y), captain1_name, fill=white, font=font_26, anchor="lm")
            
            # Average team level - (59x47, 755, 332)
            if len(team_level_icons) > 0:
                team1_level_icon = team_level_icons[0]  # Team A
                self._draw_level_icon_from_cache(background, team1_level_icon, TEAM_A_LEVEL_X, TEAM_A_LEVEL_Y, TEAM_A_LEVEL_SIZE)
            
            # === TEAM B ===
            # Captain avatar - (70x70, 1713, 335)
            captain2_id = str(team2_captain.id) if hasattr(team2_captain, 'id') else str(team2_captain)
            if captain2_id in captain_avatars:
                await self._draw_avatar_from_cache(background, captain_avatars[captain2_id], TEAM_B_CAPTAIN_AVATAR_X, TEAM_B_CAPTAIN_AVATAR_Y)
            
            # Team captain name - (font size 26, 1652, 340) - right aligned
            captain2_name = f"team_{self._get_display_name(team2_captain)}"
            draw.text((TEAM_B_CAPTAIN_NAME_X, TEAM_B_CAPTAIN_NAME_Y), captain2_name, fill=white, font=font_26, anchor="rm")
            
            # Average team level - (59x47, 1072, 332)
            if len(team_level_icons) > 1:
                team2_level_icon = team_level_icons[1]  # Team B
                self._draw_level_icon_from_cache(background, team2_level_icon, TEAM_B_LEVEL_X, TEAM_B_LEVEL_Y, TEAM_B_LEVEL_SIZE)
            
            # === LIST TEAM A ===
            # Coordonate pentru jucătorii echipei A (Y gap = 100)
            for i, player in enumerate(team1_players):
                if i >= 5:  # Maxim 5 jucători
                    break
                    
                y_pos = PLAYERS_BASE_Y + (i * PLAYERS_GAP_Y)
                player_id = str(player.id) if hasattr(player, 'id') else str(player)
                player_name = self._get_display_name(player)
                
                # Player avatar - (60x60, 109, y_pos)
                if player_id in player_avatars:
                    await self._draw_avatar_from_cache(background, player_avatars[player_id], TEAM_A_PLAYER_AVATAR_X, y_pos)
                
                # Nickname - (font size 24, +20 în stânga avatarului = 129, y_pos + 30 pentru centrare)
                draw.text((TEAM_A_PLAYER_NICKNAME_X, y_pos + PLAYER_TEXT_OFFSET_Y), player_name, fill=white, font=font_24, anchor="lm")
                
                # Level icon - (47x37, 462, y_pos + 12 pentru centrare)
                if player_id in player_level_icons:
                    self._draw_level_icon_from_cache(background, player_level_icons[player_id], TEAM_A_LEVEL_ICON_X, y_pos + LEVEL_ICON_OFFSET_Y, TEAM_A_LEVEL_ICON_SIZE)
                
                # Points - (font size 26, 593, y_pos + 30 pentru centrare)
                points = stats_dict.get(player_id, 0)
                draw.text((TEAM_A_POINTS_X, y_pos + PLAYER_TEXT_OFFSET_Y), str(points), fill=white, font=font_26, anchor="mm")
                
                # Avg kills - (font size 26, 742, y_pos + 30 pentru centrare)
                # Pentru acum folosim punctele ca placeholder pentru avg kills
                avg_kills = points // 100  # Formula temporară pentru avg kills
                draw.text((TEAM_A_AVG_KILLS_X, y_pos + PLAYER_TEXT_OFFSET_Y), str(avg_kills), fill=white, font=font_26, anchor="mm")
            
            # === LIST TEAM B ===
            # Coordonate pentru jucătorii echipei B (Y gap = 100)
            for i, player in enumerate(team2_players):
                if i >= 5:  # Maxim 5 jucători
                    break
                    
                y_pos = PLAYERS_BASE_Y + (i * PLAYERS_GAP_Y)
                player_id = str(player.id) if hasattr(player, 'id') else str(player)
                player_name = self._get_display_name(player)
                
                # Player avatar - (60x60, 1713, y_pos)
                if player_id in player_avatars:
                    await self._draw_avatar_from_cache(background, player_avatars[player_id], TEAM_B_PLAYER_AVATAR_X, y_pos)
                
                # Nickname - (font size 24, +20 în stânga avatarului = 1693, y_pos + 30 pentru centrare) - right aligned
                draw.text((TEAM_B_PLAYER_NICKNAME_X, y_pos + PLAYER_TEXT_OFFSET_Y), player_name, fill=white, font=font_24, anchor="rm")
                
                # Level icon - (47x37, 1364, y_pos + 12 pentru centrare)
                if player_id in player_level_icons:
                    self._draw_level_icon_from_cache(background, player_level_icons[player_id], TEAM_B_LEVEL_ICON_X, y_pos + LEVEL_ICON_OFFSET_Y, TEAM_B_LEVEL_ICON_SIZE)
                
                # Points - (font size 26, 1229, y_pos + 30 pentru centrare)
                points = stats_dict.get(player_id, 0)
                draw.text((TEAM_B_POINTS_X, y_pos + PLAYER_TEXT_OFFSET_Y), str(points), fill=white, font=font_26, anchor="mm")
                
                # Avg kills - (font size 26, 1082, y_pos + 30 pentru centrare)
                # Pentru acum folosim punctele ca placeholder pentru avg kills
                avg_kills = points // 100  # Formula temporară pentru avg kills
                draw.text((TEAM_B_AVG_KILLS_X, y_pos + PLAYER_TEXT_OFFSET_Y), str(avg_kills), fill=white, font=font_26, anchor="mm")
            
            # Salvează imaginea - detectează formatul pe baza extensiei
            if output_path.lower().endswith('.png'):
                background.save(output_path, "PNG", optimize=True)
            else:
                background.save(output_path, "WebP", quality=85, method=6)
            
            generation_time = time.time() - start_time
            logger.info(f"Final match image generated successfully in {generation_time:.2f}s: {output_path}")
            return True
            
        except Exception as e:
            logger.error(f"Error generating final match image: {e}")
            return False

    async def _get_cached_template(self) -> Optional[Image.Image]:
        """Încarcă template-ul de fundal cu cache"""
        current_time = time.time()
        
        if self._template_cache and (current_time - self._template_cache_time) < self.template_cache_duration:
            return self._template_cache.copy()
        
        try:
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
            background_path = os.path.join(project_root, self.background_image_path)
            
            if not os.path.exists(background_path):
                logger.error(f"Background image not found: {background_path}")
                return None
            
            self._template_cache = Image.open(background_path)
            self._template_cache_time = current_time
            logger.info("Template cached successfully")
            
            return self._template_cache.copy()
            
        except Exception as e:
            logger.error(f"Error loading template: {e}")
            return None

    async def _get_cached_font(self, size: int) -> Optional[ImageFont.FreeTypeFont]:
        """Încarcă fontul cu cache"""
        current_time = time.time()
        
        # Verifică cache-ul
        if size in self._font_cache:
            cached_font, timestamp = self._font_cache[size]
            if current_time - timestamp < self.font_cache_duration:
                return cached_font
        
        try:
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
            font_path = os.path.join(project_root, self.default_font_path)
            
            font = ImageFont.truetype(font_path, size)
            self._font_cache[size] = (font, current_time)
            logger.info(f"Font cached for size {size}")
            
            return font
            
        except (OSError, IOError) as e:
            logger.warning(f"Custom font not found ({e}), using default")
            return ImageFont.load_default()

    async def _batch_load_level_icons(self, players: List[Union[str, object]], stats_dict: Dict[str, int] = None, size=(47, 37)) -> Dict[str, Image.Image]:
        """Încarcă toate level icon-urile în paralel folosind stats pre-calculate"""
        try:
            level_icons_dict = {}
            valid_players = [p for p in players if hasattr(p, 'id')]
            
            if not valid_players:
                return level_icons_dict
            
            # Dacă nu avem stats, obține-le
            if stats_dict is None:
                stats_dict = await self._batch_get_player_stats(valid_players)
            
            # Generează task-uri pentru încărcarea icon-urilor
            tasks = []
            for player in valid_players:
                player_id = str(player.id)
                points = stats_dict.get(player_id, 0)
                
                # Obține calea direct din LevelsCog fără database calls
                from cogs.levels import LevelsCog
                level_icon_path = LevelsCog.get_level_icon_path(points)
                
                if level_icon_path:
                    project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
                    full_path = os.path.join(project_root, level_icon_path)
                    task = self._get_cached_level_icon(full_path)
                    tasks.append((player_id, task))
            
            # Execută toate task-urile în paralel
            if tasks:
                results = await asyncio.gather(*[task for _, task in tasks], return_exceptions=True)
                
                for i, (player_id, _) in enumerate(tasks):
                    result = results[i]
                    if not isinstance(result, Exception) and result is not None:
                        # Redimensionează icon-ul la mărimea specificată
                        if isinstance(size, tuple):
                            resized_icon = result.resize(size, Image.Resampling.LANCZOS)
                        else:
                            resized_icon = result.resize((size, size), Image.Resampling.LANCZOS)
                        level_icons_dict[player_id] = resized_icon
            
            logger.info(f"Batch loaded {len(level_icons_dict)} level icons")
            return level_icons_dict
            
        except Exception as e:
            logger.error(f"Error in batch load level icons: {e}")
            return {}

    async def _calculate_and_load_team_level_icons(self, teams: List[List], stats_dict: Dict[str, int], size=(59, 47)) -> List[Image.Image]:
        """Calculează level-ul mediu al echipei și încarcă icon-ul corespunzător"""
        try:
            team_icons = []
            
            for team_players in teams:
                if not team_players:
                    continue
                    
                # Calculează punctajul mediu al echipei
                total_points = 0
                player_count = 0
                
                for player in team_players:
                    player_id = str(player.id) if hasattr(player, 'id') else str(player)
                    if player_id in stats_dict:
                        total_points += stats_dict[player_id]
                        player_count += 1
                
                avg_points = total_points // player_count if player_count > 0 else 0
                
                # Determină level-ul bazat pe puncte (reutilizez logica din LevelsCog)
                from cogs.levels import LevelsCog
                levels_cog = LevelsCog(None)  # Temporar pentru acces la config
                
                team_level = 1  # Default
                for level, config in levels_cog.LEVEL_CONFIG.items():
                    if config["min_points"] <= avg_points <= config["max_points"]:
                        team_level = level
                        break
                
                # Încarcă icon-ul pentru level-ul echipei
                level_icon_path = await self._get_team_level_icon_path(team_level)
                if level_icon_path:
                    level_icon = await self._get_cached_level_icon(level_icon_path)
                    if level_icon:
                        # Redimensionează la mărimea dorită pentru echipă
                        resized_icon = level_icon.resize(size, Image.Resampling.LANCZOS)
                        team_icons.append(resized_icon)
                    else:
                        # Fallback la level 1
                        fallback_path = await self._get_team_level_icon_path(1)
                        if fallback_path:
                            fallback_icon = await self._get_cached_level_icon(fallback_path)
                            if fallback_icon:
                                resized_icon = fallback_icon.resize(size, Image.Resampling.LANCZOS)
                                team_icons.append(resized_icon)
            
            return team_icons
            
        except Exception as e:
            logger.error(f"Error calculating team level icons: {e}")
            return []

    async def _get_team_level_icon_path(self, level: int) -> Optional[str]:
        """Returnează calea către iconița de level pentru echipă"""
        try:
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
            level_icons_dir = os.path.join(project_root, "assets", "level_icons")
            
            # Mapare level la fișier
            if level >= 100:  # Challenger
                icon_name = "challenger.webp"
            elif level >= 10:
                icon_name = "level10.webp"
            elif 1 <= level <= 9:
                icon_name = f"level{level}.webp"
            else:
                icon_name = "level1.webp"  # Default
            
            icon_path = os.path.join(level_icons_dir, icon_name)
            return icon_path if os.path.exists(icon_path) else None
            
        except Exception as e:
            logger.warning(f"Error getting team level icon path: {e}")
            return None

    async def _get_cached_level_icon(self, icon_path: str) -> Optional[Image.Image]:
        """Încarcă level icon cu cache"""
        current_time = time.time()
        
        # Verifică cache-ul
        if icon_path in self._level_icon_cache:
            cached_icon, timestamp = self._level_icon_cache[icon_path]
            if current_time - timestamp < self.level_icon_cache_duration:
                return cached_icon
        
        try:
            if os.path.exists(icon_path):
                icon_image = Image.open(icon_path)
                self._level_icon_cache[icon_path] = (icon_image, current_time)
                return icon_image
            else:
                logger.warning(f"Level icon not found: {icon_path}")
                return None
                
        except Exception as e:
            logger.warning(f"Error loading level icon {icon_path}: {e}")
            return None

    async def _draw_avatar_from_cache(self, background: Image.Image, avatar: Image.Image, x: int, y: int):
        """Desenează avatar din cache cu mască circulară optimizată"""
        try:
            # Aplică masca circulară pre-calculată
            circular_avatar = await self._apply_circular_mask_async(avatar)
            if circular_avatar:
                background.paste(circular_avatar, (x, y), circular_avatar)
        except Exception as e:
            logger.warning(f"Error drawing avatar from cache: {e}")

    def _draw_level_icon_from_cache(self, background: Image.Image, level_icon: Image.Image, x: int, y: int, size):
        """Desenează level icon din cache păstrând aspect ratio-ul"""
        try:
            # Acceptă atât int cât și tuple pentru dimensiuni
            if isinstance(size, tuple):
                target_width, target_height = size
            else:
                target_width = target_height = size
            
            # Păstrează aspect ratio-ul original pentru level icons
            original_width, original_height = level_icon.size
            
            # Calculează new size păstrând aspect ratio
            ratio = min(target_width / original_width, target_height / original_height)
            new_width = int(original_width * ratio)
            new_height = int(original_height * ratio)
            
            # Resize cu aspect ratio păstrat
            resized_icon = level_icon.resize((new_width, new_height), Image.Resampling.LANCZOS)
            
            # Calculează poziția pentru centrare în spațiul alocat
            center_x = x + (target_width - new_width) // 2
            center_y = y + (target_height - new_height) // 2
            
            # Paste icon centrat
            if resized_icon.mode == 'RGBA':
                background.paste(resized_icon, (center_x, center_y), resized_icon)
            else:
                background.paste(resized_icon, (center_x, center_y))
        except Exception as e:
            logger.warning(f"Error drawing level icon from cache: {e}")

    async def _apply_circular_mask_async(self, image: Image.Image) -> Optional[Image.Image]:
        """Aplică masca circulară într-un mod async optimizat"""
        try:
            size = image.size
            # Creează masca circulară
            mask = Image.new('L', size, 0)
            draw = ImageDraw.Draw(mask)
            draw.ellipse((0, 0) + size, fill=255)
            
            # Aplică masca
            result = Image.new('RGBA', size, (0, 0, 0, 0))
            result.paste(image, (0, 0))
            result.putalpha(mask)
            
            return result
            
        except Exception as e:
            logger.warning(f"Error applying circular mask: {e}")
            return None
    
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
            
            # LevelsCog returnează "assets/level_icons/level1.webp" (actualizat la WebP)
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
            
            # Fallback la level 1 dacă fișierul nu există (WebP format)
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
            fallback_path = os.path.join(project_root, "assets", "level_icons", "level1.webp")
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
            return (1860, 1250)  # Dimensiuni default pentru noul template
        except Exception:
            return (1860, 1250)  # Dimensiuni default pentru noul template

    def cleanup_caches(self):
        """Curăță cache-urile manual pentru economisirea memoriei"""
        try:
            current_time = time.time()
            
            # Curăță avatar cache
            expired_avatars = [
                uid for uid, (_, timestamp) in self._avatar_cache.items()
                if current_time - timestamp > self.avatar_cache_duration
            ]
            for uid in expired_avatars:
                del self._avatar_cache[uid]
            
            # Curăță font cache
            expired_fonts = [
                size for size, (_, timestamp) in self._font_cache.items()
                if current_time - timestamp > self.font_cache_duration
            ]
            for size in expired_fonts:
                del self._font_cache[size]
            
            # Curăță level icon cache
            expired_icons = [
                path for path, (_, timestamp) in self._level_icon_cache.items()
                if current_time - timestamp > self.level_icon_cache_duration
            ]
            for path in expired_icons:
                del self._level_icon_cache[path]
            
            # Curăță template cache dacă este expirat
            if (self._template_cache and 
                current_time - self._template_cache_time > self.template_cache_duration):
                self._template_cache = None
                self._template_cache_time = 0
            
            logger.info(f"Cache cleanup: removed {len(expired_avatars)} avatars, "
                       f"{len(expired_fonts)} fonts, {len(expired_icons)} icons")
                       
        except Exception as e:
            logger.warning(f"Error during cache cleanup: {e}")

    def get_cache_stats(self) -> Dict[str, int]:
        """Returnează statistici despre cache-uri pentru monitoring"""
        return {
            "avatar_cache_size": len(self._avatar_cache),
            "font_cache_size": len(self._font_cache),
            "level_icon_cache_size": len(self._level_icon_cache),
            "template_cached": 1 if self._template_cache else 0
        }
