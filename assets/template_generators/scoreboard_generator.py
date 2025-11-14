"""
Scoreboard Results Template Generator using Pillow
Generates scoreboard results images with team stats and player performance
"""

import os
import io
import asyncio
import sys
import tempfile
import time
import json
import aiohttp
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont
import logging
from typing import List, Dict, Optional

# Add parent directories to path to import from cogs
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from cogs.levels import LevelsCog

logger = logging.getLogger(__name__)


class ScoreboardResultsTemplateGenerator:
    """Generator pentru template-ul de rezultate scoreboard folosind Pillow cu cache optimizat"""

    def __init__(
        self, background_image_path: str = "template_images/match_results.webp"
    ):
        self.background_image_path = background_image_path
        self.default_font_path = "fonts/fira_code_semibold.ttf"

        # Cache pentru optimizare
        self._font_cache: Dict[int, tuple] = {}  # size -> (font, timestamp)
        self._level_icon_cache: Dict[str, tuple] = {}  # path -> (image, timestamp)
        self._template_cache: Optional[Image.Image] = None
        self._template_cache_time: float = 0
        self._project_root_cache: Optional[str] = None

        # Cache settings - durate în secunde
        self.font_cache_duration = 1800  # 30 minutes
        self.level_icon_cache_duration = 600  # 10 minutes
        self.template_cache_duration = 1800  # 30 minutes

    def _get_project_root(self) -> str:
        """Get the project root directory with caching"""
        if not self._project_root_cache:
            self._project_root_cache = os.path.dirname(
                os.path.dirname(os.path.dirname(__file__))
            )
        return self._project_root_cache

    def _get_rating_square_path(self, rating_value: float) -> str:
        """Get the path to the appropriate rating square based on rating value"""
        try:
            project_root = self._get_project_root()
            template_images_dir = os.path.join(
                project_root, "assets", "template_images"
            )

            # Determine which square to use based on rating value
            if rating_value < 0.50:
                square_name = "match_result_rating_red.png"
            elif rating_value >= 1.0:
                square_name = "match_result_rating_green.png"
            else:
                square_name = "match_result_rating_yellow.png"

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

    def _ensure_rgba(self, image: Image.Image) -> Image.Image:
        """Ensure image is in RGBA format"""
        if image.mode != "RGBA":
            return image.convert("RGBA")
        return image

    async def _get_cached_template(self) -> Optional[Image.Image]:
        """Încarcă template-ul de fundal cu cache"""
        current_time = time.time()

        if (
            self._template_cache
            and (current_time - self._template_cache_time)
            < self.template_cache_duration
        ):
            return self._template_cache.copy()

        try:
            project_root = self._get_project_root()
            background_path = os.path.join(
                project_root, "assets", self.background_image_path
            )

            if not os.path.exists(background_path):
                logger.error(f"Background image not found: {background_path}")
                return None

            self._template_cache = Image.open(background_path)
            self._template_cache_time = current_time
            logger.debug("Template cached successfully")

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
            project_root = self._get_project_root()
            font_path = os.path.join(project_root, "assets", self.default_font_path)

            font = ImageFont.truetype(font_path, size)
            self._font_cache[size] = (font, current_time)
            logger.debug(f"Font cached for size {size}")

            return font

        except (OSError, IOError) as e:
            logger.warning(f"Custom font not found ({e}), using default")
            return ImageFont.load_default()

    async def _get_cached_level_icon(self, icon_path: str) -> Optional[Image.Image]:
        """Încarcă level icon cu cache"""
        current_time = time.time()

        # Verifică cache-ul
        if icon_path in self._level_icon_cache:
            cached_icon, timestamp = self._level_icon_cache[icon_path]
            if current_time - timestamp < self.level_icon_cache_duration:
                return cached_icon.copy()

        try:
            if os.path.exists(icon_path):
                icon_image = Image.open(icon_path)
                icon_image = self._ensure_rgba(icon_image)
                self._level_icon_cache[icon_path] = (icon_image, current_time)
                logger.debug(f"Level icon cached: {icon_path}")
                return icon_image.copy()
            else:
                logger.warning(f"Level icon not found: {icon_path}")
                return None

        except Exception as e:
            logger.warning(f"Error loading level icon {icon_path}: {e}")
            return None

    async def _batch_load_level_icons(
        self, players: List[Dict], project_root: str, size=(47, 37)
    ) -> Dict[str, Image.Image]:
        """Încarcă toate level icon-urile în paralel"""
        try:
            level_icons_dict = {}

            if not players:
                return level_icons_dict

            # Generează task-uri pentru încărcarea icon-urilor
            tasks = []
            for i, player in enumerate(players[:5]):  # Max 5 players
                # Use current_points (total after match) for level icon, fallback to points
                current_points = player.get("current_points", player.get("points", 0))
                level_icon_path = LevelsCog.get_level_icon_path(current_points)

                if level_icon_path:
                    if not os.path.isabs(level_icon_path):
                        full_path = os.path.join(project_root, level_icon_path)
                    else:
                        full_path = level_icon_path
                    task = self._get_cached_level_icon(full_path)
                    tasks.append((i, task))

            # Execută toate task-urile în paralel
            if tasks:
                results = await asyncio.gather(
                    *[task for _, task in tasks], return_exceptions=True
                )

                for idx, (player_idx, _) in enumerate(tasks):
                    result = results[idx]
                    if not isinstance(result, Exception) and result is not None:
                        # Redimensionează icon-ul la mărimea specificată
                        resized_icon = result.resize(size, Image.Resampling.LANCZOS)
                        level_icons_dict[player_idx] = resized_icon

            logger.debug(f"Batch loaded {len(level_icons_dict)} level icons")
            return level_icons_dict

        except Exception as e:
            logger.error(f"Error in batch load level icons: {e}")
            return {}

    def _draw_player_level_icon(
        self,
        image: Image.Image,
        level_icon: Image.Image,
        icon_x: int,
        player_y: int,
        size=(47, 37),
    ):
        """Helper pentru desenarea level icon-ului unui jucător"""
        try:
            if level_icon:
                icon_y = player_y - size[1] // 2
                image.paste(level_icon, (icon_x - size[0] // 2, icon_y), level_icon)
        except Exception as e:
            logger.warning(f"Error drawing level icon: {e}")

    def _draw_player_rating_square(
        self, image: Image.Image, rating_value: float, rating_x: int, player_y: int
    ):
        """Helper pentru desenarea rating square-ului unui jucător"""
        try:
            rating_square_path = self._get_rating_square_path(rating_value)
            if rating_square_path and os.path.exists(rating_square_path):
                rating_square = Image.open(rating_square_path)
                rating_square = self._ensure_rgba(rating_square)

                square_x = rating_x - rating_square.width // 2
                square_y = player_y - rating_square.height // 2
                image.paste(rating_square, (square_x, square_y), rating_square)
        except Exception as e:
            logger.debug(f"Could not load rating square: {e}")

    def _get_rating_text_color(self, rating_value: float) -> tuple:
        """Determină culoarea textului pentru rating bazat pe valoare"""
        if rating_value < 0.50:
            return (255, 72, 72)  # #FF4848 in RGB (red)
        elif rating_value >= 1.0:
            return (87, 242, 135)  # #57F287 in RGB (green)
        else:
            return (255, 197, 51)  # #FFC533 in RGB (yellow)

    def cleanup_caches(self):
        """Curăță cache-urile manual pentru economisirea memoriei"""
        try:
            current_time = time.time()

            # Curăță font cache
            expired_fonts = [
                size
                for size, (_, timestamp) in self._font_cache.items()
                if current_time - timestamp > self.font_cache_duration
            ]
            for size in expired_fonts:
                del self._font_cache[size]

            # Curăță level icon cache
            expired_icons = [
                path
                for path, (_, timestamp) in self._level_icon_cache.items()
                if current_time - timestamp > self.level_icon_cache_duration
            ]
            for path in expired_icons:
                del self._level_icon_cache[path]

            # Curăță template cache dacă este expirat
            if (
                self._template_cache
                and current_time - self._template_cache_time
                > self.template_cache_duration
            ):
                self._template_cache = None
                self._template_cache_time = 0

            logger.debug(
                f"Cache cleanup: removed {len(expired_fonts)} fonts, {len(expired_icons)} icons"
            )

        except Exception as e:
            logger.warning(f"Error during cache cleanup: {e}")

    def get_cache_stats(self) -> Dict[str, int]:
        """Returnează statistici despre cache-uri pentru monitoring"""
        return {
            "font_cache_size": len(self._font_cache),
            "level_icon_cache_size": len(self._level_icon_cache),
            "template_cached": 1 if self._template_cache else 0,
        }

    async def download_and_create_round_avatar(
        self, bot, discord_user_id: int, size: int = 80
    ) -> Optional[Image.Image]:
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
                        logger.warning(
                            f"Failed to download avatar for user {discord_user_id}: HTTP {response.status}"
                        )
                        return None

            # Process the image to make it round
            avatar_image = Image.open(io.BytesIO(avatar_data))

            # Resize to the desired size
            avatar_image = avatar_image.resize((size, size), Image.LANCZOS)

            # Create a round mask
            mask = Image.new("L", (size, size), 0)
            draw_mask = ImageDraw.Draw(mask)
            draw_mask.ellipse((0, 0, size, size), fill=255)

            # Apply the mask to create a round avatar
            avatar_image = avatar_image.convert("RGBA")
            avatar_image.putalpha(mask)

            return avatar_image

        except Exception as e:
            logger.warning(
                f"Error creating round avatar for user {discord_user_id}: {e}"
            )
            return None

    def _draw_downloaded_avatar(
        self, background: Image.Image, avatar_image: Image.Image, x: int, y: int
    ):
        """Desenează avatar pre-descărcat pe background"""
        try:
            if not avatar_image:
                return

            size = avatar_image.size[0]  # Avatar este deja rotund și dimensionat corect

            # Calculează poziția pentru centrare (anchor mm)
            paste_x = x - size // 2
            paste_y = y - size // 2

            # Desenează pe background
            background.paste(avatar_image, (paste_x, paste_y), avatar_image)

        except Exception as e:
            logger.warning(f"Error drawing avatar: {e}")

    async def generate_scoreboard_image(
        self,
        bot,
        match_data: Dict,
        team1: List[Dict],
        team2: List[Dict],
        team1_score: int,
        team2_score: int,
    ) -> str:
        """Generate scoreboard results image using Pillow with optimizations"""
        try:
            # Swap teams if team2 won - winner should always be Team A (left side)
            if team2_score > team1_score:
                team1, team2 = team2, team1
                team1_score, team2_score = team2_score, team1_score

            # Get the project root directory
            project_root = self._get_project_root()

            # Load the background image with cache
            image = await self._get_cached_template()
            if image is None:
                logger.error("Failed to load background template")
                return await self._create_fallback_image(
                    match_data.get("match_id", "unknown")
                )

            # Create a drawing context
            draw = ImageDraw.Draw(image)

            # Load custom fonts with cache
            font_42 = await self._get_cached_font(42)
            font_26 = await self._get_cached_font(26)
            font_24 = await self._get_cached_font(24)
            font_16 = await self._get_cached_font(16)

            if not all([font_42, font_26, font_24, font_16]):
                logger.warning("Failed to load some fonts, using defaults")
                font_42 = font_42 or ImageFont.load_default()
                font_26 = font_26 or ImageFont.load_default()
                font_24 = font_24 or ImageFont.load_default()
                font_16 = font_16 or ImageFont.load_default()

            # Define colors
            text_color = (255, 255, 255)  # White for regular text
            gray_color = (128, 128, 128)  # Gray for subtitles
            green_color = (87, 242, 135)  # #57F287 Green for positive points
            red_color = (255, 72, 72)  # #FF4848 Red for negative points

            # ==================== COORDONATE PRINCIPALE ====================
            # Score
            SCORE_X = 1057
            SCORE_Y = 458

            # Team A Captain
            TEAM_A_CAP_AVATAR_X = 105
            TEAM_A_CAP_AVATAR_Y = 460
            TEAM_A_CAP_NAME_X = 165
            TEAM_A_CAP_NAME_Y = 476

            # Team B Captain
            TEAM_B_CAP_AVATAR_X = 2004
            TEAM_B_CAP_AVATAR_Y = 464
            TEAM_B_CAP_NAME_X = 1948
            TEAM_B_CAP_NAME_Y = 476

            # Team A Players (starting Y position and gap)
            TEAM_A_START_Y = 654
            PLAYER_Y_GAP = 110  # Gap între jucători

            TEAM_A_AVATAR_X = 105
            TEAM_A_NAME_X = 160
            TEAM_A_NAME_Y_OFFSET = -9  # Offset față de Y de bază
            TEAM_A_POINTS_Y_OFFSET = +13  # Offset pentru points
            TEAM_A_LEVEL_ICON_X = 461
            TEAM_A_KILLS_X = 575
            TEAM_A_ASSISTS_X = 685
            TEAM_A_DEATHS_X = 795
            TEAM_A_RATING_X = 907

            # Team B Players
            TEAM_B_START_Y = 654

            TEAM_B_AVATAR_X = 2010
            TEAM_B_NAME_X = 1947
            TEAM_B_NAME_Y_OFFSET = -9
            TEAM_B_POINTS_Y_OFFSET = +13
            TEAM_B_LEVEL_ICON_X = 1645
            TEAM_B_KILLS_X = 1538
            TEAM_B_ASSISTS_X = 1426
            TEAM_B_DEATHS_X = 1318
            TEAM_B_RATING_X = 1207

            # Bottom info
            MAP_X = 69
            MAP_Y = 1256

            WINNER_NAME_X = 645
            WINNER_NAME_Y = 1256
            WINNER_SCORE_X = 645
            WINNER_SCORE_Y = 1285

            # MVP info
            MVP_LEVEL_ICON_X = 1265
            MVP_LEVEL_ICON_Y = 1265

            MVP_NAME_X = 1325
            MVP_NAME_Y = 1256

            MVP_LEVEL_TEXT_X = 1325
            MVP_LEVEL_TEXT_Y = 1285

            # ==============================================================

            # Header information
            # Score
            score_text = f"{team1_score}-{team2_score}"
            draw.text(
                (SCORE_X, SCORE_Y),
                score_text,
                font=font_42,
                fill=text_color,
                anchor="mm",
            )

            # Get team data
            teams_data = match_data.get("teams", {})
            if isinstance(teams_data, str):
                teams_data = json.loads(teams_data)

            # Determine winner
            team1_won = team1_score > team2_score
            winner_team_name = (
                teams_data.get("team1", {}).get("captain", "Unknown")
                if team1_won
                else teams_data.get("team2", {}).get("captain", "Unknown")
            )
            winner_score_text = (
                f"{max(team1_score, team2_score)}-{min(team1_score, team2_score)}"
            )

            # Download all avatars concurrently (captains + players)
            avatar_tasks = []
            avatar_ids = []

            # Get captain IDs from first player in each team (captain is always first)
            team1_captain_id = (
                team1[0].get("discord_id") if team1 and len(team1) > 0 else None
            )
            team2_captain_id = (
                team2[0].get("discord_id") if team2 and len(team2) > 0 else None
            )

            if team1_captain_id:
                avatar_tasks.append(
                    self.download_and_create_round_avatar(
                        bot, int(team1_captain_id), 70
                    )
                )
                avatar_ids.append(("captain1", team1_captain_id))
            else:
                avatar_tasks.append(None)
                avatar_ids.append(("captain1", None))

            if team2_captain_id:
                avatar_tasks.append(
                    self.download_and_create_round_avatar(
                        bot, int(team2_captain_id), 70
                    )
                )
                avatar_ids.append(("captain2", team2_captain_id))
            else:
                avatar_tasks.append(None)
                avatar_ids.append(("captain2", None))

            # Add player avatars
            for i, player in enumerate(team1[:5]):
                discord_id = player.get("discord_id")
                if discord_id:
                    avatar_tasks.append(
                        self.download_and_create_round_avatar(bot, int(discord_id), 60)
                    )
                    avatar_ids.append(("team1", i, discord_id))
                else:
                    avatar_tasks.append(None)
                    avatar_ids.append(("team1", i, None))

            for i, player in enumerate(team2[:5]):
                discord_id = player.get("discord_id")
                if discord_id:
                    avatar_tasks.append(
                        self.download_and_create_round_avatar(bot, int(discord_id), 60)
                    )
                    avatar_ids.append(("team2", i, discord_id))
                else:
                    avatar_tasks.append(None)
                    avatar_ids.append(("team2", i, None))

            # Wait for all avatar downloads to complete
            avatars = await asyncio.gather(
                *[task for task in avatar_tasks if task is not None],
                return_exceptions=True,
            )

            # Create avatar lookup (optimized with zip)
            avatar_lookup = {}
            avatar_index = 0
            for avatar_id, task in zip(avatar_ids, avatar_tasks):
                if task is not None:
                    avatar_result = (
                        avatars[avatar_index] if avatar_index < len(avatars) else None
                    )
                    if not isinstance(avatar_result, Exception) and avatar_result:
                        if len(avatar_id) == 2:  # Captain
                            avatar_lookup[avatar_id[0]] = avatar_result
                        else:  # Player
                            avatar_lookup[f"{avatar_id[0]}_{avatar_id[1]}"] = (
                                avatar_result
                            )
                    elif isinstance(avatar_result, Exception):
                        logger.warning(
                            f"Error downloading avatar for {avatar_id}: {avatar_result}"
                        )
                    avatar_index += 1

            # Batch load all level icons in parallel
            team1_level_icons = await self._batch_load_level_icons(
                team1, project_root, size=(47, 37)
            )
            team2_level_icons = await self._batch_load_level_icons(
                team2, project_root, size=(47, 37)
            )

            # Load MVP level icon if needed
            mvp_level_icon = None
            mvp_data = match_data.get("mvp")
            if mvp_data:
                mvp_points = mvp_data.get("points", 0)
                mvp_level_icon_path = LevelsCog.get_level_icon_path(mvp_points)
                if mvp_level_icon_path:
                    if not os.path.isabs(mvp_level_icon_path):
                        mvp_level_icon_path = os.path.join(
                            project_root, mvp_level_icon_path
                        )
                    mvp_level_icon = await self._get_cached_level_icon(
                        mvp_level_icon_path
                    )
                    if mvp_level_icon:
                        mvp_level_icon = mvp_level_icon.resize(
                            (79, 63), Image.Resampling.LANCZOS
                        )

            # Team A Captain (always team1)
            team_a_captain_name = teams_data.get("team1", {}).get("captain", "Unknown")
            draw.text(
                (TEAM_A_CAP_NAME_X, TEAM_A_CAP_NAME_Y),
                f"team_{team_a_captain_name}",
                font=font_26,
                fill=text_color,
                anchor="lm",
            )

            # Draw captain avatar
            if "captain1" in avatar_lookup:
                self._draw_downloaded_avatar(
                    image,
                    avatar_lookup["captain1"],
                    TEAM_A_CAP_AVATAR_X,
                    TEAM_A_CAP_AVATAR_Y,
                )

            # Team B Captain (always team2)
            team_b_captain_name = teams_data.get("team2", {}).get("captain", "Unknown")
            draw.text(
                (TEAM_B_CAP_NAME_X, TEAM_B_CAP_NAME_Y),
                f"team_{team_b_captain_name}",
                font=font_26,
                fill=text_color,
                anchor="rm",
            )

            # Draw captain avatar
            if "captain2" in avatar_lookup:
                self._draw_downloaded_avatar(
                    image,
                    avatar_lookup["captain2"],
                    TEAM_B_CAP_AVATAR_X,
                    TEAM_B_CAP_AVATAR_Y,
                )

            # Team A Players (max 5 players)
            for i, player in enumerate(team1[:5]):
                player_y = TEAM_A_START_Y + (i * PLAYER_Y_GAP)

                # Avatar
                avatar_key = f"team1_{i}"
                if avatar_key in avatar_lookup:
                    self._draw_downloaded_avatar(
                        image, avatar_lookup[avatar_key], TEAM_A_AVATAR_X, player_y
                    )

                # Player Name
                name_y = player_y + TEAM_A_NAME_Y_OFFSET
                draw.text(
                    (TEAM_A_NAME_X, name_y),
                    str(player.get("nickname", "Unknown")),
                    font=font_24,
                    fill=text_color,
                    anchor="lm",
                )

                # Points gained/deducted
                points_gained = player.get("points_gained", player.get("points", 0))
                points_text = f"{points_gained:+d}"
                points_color = (
                    green_color
                    if points_gained > 0
                    else (red_color if points_gained < 0 else text_color)
                )
                points_y = player_y + TEAM_A_POINTS_Y_OFFSET
                draw.text(
                    (TEAM_A_NAME_X, points_y),
                    points_text,
                    font=font_16,
                    fill=points_color,
                    anchor="lm",
                )

                # Level icon (from batch loaded icons)
                if i in team1_level_icons:
                    self._draw_player_level_icon(
                        image, team1_level_icons[i], TEAM_A_LEVEL_ICON_X, player_y
                    )

                # Stats
                draw.text(
                    (TEAM_A_KILLS_X, player_y),
                    str(player.get("kills", 0)),
                    font=font_26,
                    fill=text_color,
                    anchor="mm",
                )
                draw.text(
                    (TEAM_A_ASSISTS_X, player_y),
                    str(player.get("assists", 0)),
                    font=font_26,
                    fill=text_color,
                    anchor="mm",
                )
                draw.text(
                    (TEAM_A_DEATHS_X, player_y),
                    str(player.get("deaths", 0)),
                    font=font_26,
                    fill=text_color,
                    anchor="mm",
                )

                # Rating with square and color
                rating = player.get("rating", 0.0)
                rating_value = (
                    float(rating) if isinstance(rating, (int, float)) else 0.0
                )
                rating_text = f"{rating_value:.1f}"

                # Add rating square indicator
                self._draw_player_rating_square(
                    image, rating_value, TEAM_A_RATING_X, player_y
                )

                # Determine rating text color based on rating value
                rating_text_color = self._get_rating_text_color(rating_value)
                draw.text(
                    (TEAM_A_RATING_X, player_y),
                    rating_text,
                    font=font_26,
                    fill=rating_text_color,
                    anchor="mm",
                )

            # Team B Players (max 5 players)
            for i, player in enumerate(team2[:5]):
                player_y = TEAM_B_START_Y + (i * PLAYER_Y_GAP)

                # Avatar
                avatar_key = f"team2_{i}"
                if avatar_key in avatar_lookup:
                    self._draw_downloaded_avatar(
                        image, avatar_lookup[avatar_key], TEAM_B_AVATAR_X, player_y
                    )

                # Player Name
                name_y = player_y + TEAM_B_NAME_Y_OFFSET
                draw.text(
                    (TEAM_B_NAME_X, name_y),
                    str(player.get("nickname", "Unknown")),
                    font=font_24,
                    fill=text_color,
                    anchor="rm",
                )

                # Points gained/deducted
                points_gained = player.get("points_gained", player.get("points", 0))
                points_text = f"{points_gained:+d}"
                points_color = (
                    green_color
                    if points_gained > 0
                    else (red_color if points_gained < 0 else text_color)
                )
                points_y = player_y + TEAM_B_POINTS_Y_OFFSET
                draw.text(
                    (TEAM_B_NAME_X, points_y),
                    points_text,
                    font=font_16,
                    fill=points_color,
                    anchor="rm",
                )

                # Level icon (from batch loaded icons)
                if i in team2_level_icons:
                    self._draw_player_level_icon(
                        image, team2_level_icons[i], TEAM_B_LEVEL_ICON_X, player_y
                    )

                # Stats
                draw.text(
                    (TEAM_B_KILLS_X, player_y),
                    str(player.get("kills", 0)),
                    font=font_26,
                    fill=text_color,
                    anchor="mm",
                )
                draw.text(
                    (TEAM_B_ASSISTS_X, player_y),
                    str(player.get("assists", 0)),
                    font=font_26,
                    fill=text_color,
                    anchor="mm",
                )
                draw.text(
                    (TEAM_B_DEATHS_X, player_y),
                    str(player.get("deaths", 0)),
                    font=font_26,
                    fill=text_color,
                    anchor="mm",
                )

                # Rating with square and color
                rating = player.get("rating", 0.0)
                rating_value = (
                    float(rating) if isinstance(rating, (int, float)) else 0.0
                )
                rating_text = f"{rating_value:.1f}"

                # Add rating square indicator
                self._draw_player_rating_square(
                    image, rating_value, TEAM_B_RATING_X, player_y
                )

                # Determine rating text color based on rating value
                rating_text_color = self._get_rating_text_color(rating_value)
                draw.text(
                    (TEAM_B_RATING_X, player_y),
                    rating_text,
                    font=font_26,
                    fill=rating_text_color,
                    anchor="mm",
                )

            # Bottom information
            # Map
            map_name = match_data.get("map", "Unknown")
            draw.text(
                (MAP_X, MAP_Y), map_name, font=font_42, fill=text_color, anchor="lm"
            )

            # Winner Team Name
            draw.text(
                (WINNER_NAME_X, WINNER_NAME_Y),
                f"team_{winner_team_name}",
                font=font_42,
                fill=text_color,
                anchor="lm",
            )

            # Winner Team Score
            draw.text(
                (WINNER_SCORE_X, WINNER_SCORE_Y),
                winner_score_text,
                font=font_16,
                fill=gray_color,
                anchor="lm",
            )

            # MVP Section
            if mvp_data:
                # MVP Level icon (pre-loaded)
                if mvp_level_icon:
                    icon_x = MVP_LEVEL_ICON_X - 79 // 2
                    icon_y = MVP_LEVEL_ICON_Y - 63 // 2
                    image.paste(mvp_level_icon, (icon_x, icon_y), mvp_level_icon)

                # MVP Name
                mvp_name = mvp_data.get("nickname", "Unknown")
                draw.text(
                    (MVP_NAME_X, MVP_NAME_Y),
                    mvp_name,
                    font=font_42,
                    fill=text_color,
                    anchor="lm",
                )

                # MVP Level text
                mvp_points = mvp_data.get("points", 0)
                mvp_level = LevelsCog.get_level_from_points(mvp_points)
                if mvp_points >= LevelsCog.CHALLENGER_CONFIG["min_points"]:
                    level_text = "Challenger"
                else:
                    level_text = f"Level {mvp_level}"
                draw.text(
                    (MVP_LEVEL_TEXT_X, MVP_LEVEL_TEXT_Y),
                    level_text,
                    font=font_16,
                    fill=gray_color,
                    anchor="lm",
                )

            # Save the image to a temporary file
            temp_path = os.path.join(
                tempfile.gettempdir(),
                f"scoreboard_results_{match_data['match_id']}_{int(datetime.now().timestamp())}.webp",
            )
            image.save(temp_path, "WebP", quality=85)

            logger.info(f"Scoreboard results image generated successfully: {temp_path}")
            return temp_path

        except Exception as e:
            logger.error(f"Error generating scoreboard results image with Pillow: {e}")
            return await self._create_fallback_image(
                match_data.get("match_id", "unknown")
            )

    async def _create_fallback_image(self, match_id: str) -> str:
        """Create a simple fallback image in case of errors"""
        try:
            # Create a simple colored image with error text
            fallback_image = Image.new(
                "RGB", (2700, 1700), (255, 0, 0)
            )  # Red background
            draw = ImageDraw.Draw(fallback_image)

            try:
                font = ImageFont.load_default()
                draw.text(
                    (1350, 850),
                    "Error generating scoreboard results image",
                    font=font,
                    fill=(255, 255, 255),
                    anchor="mm",
                )
            except Exception:
                # If even default font fails, just create red image
                pass

            temp_path = os.path.join(
                tempfile.gettempdir(),
                f"scoreboard_error_{match_id}_{int(datetime.now().timestamp())}.webp",
            )
            fallback_image.save(temp_path, "WebP", quality=85)

            logger.info(f"Created fallback scoreboard results image: {temp_path}")
            return temp_path

        except Exception as e:
            logger.error(f"Failed to create fallback image: {e}")
            # Return a dummy path - calling code should handle this gracefully
            return ""
