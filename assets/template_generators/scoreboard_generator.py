"""
Scoreboard Results Template Generator using Pillow
Generates scoreboard results images with team stats and player performance
"""

import os
import tempfile
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont
import logging
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)


class ScoreboardResultsTemplateGenerator:
    """Generator pentru template-ul de rezultate scoreboard folosind Pillow"""
    
    def __init__(self, background_image_path: str = "template_images/arena_faceit_match_results.png"):
        self.background_image_path = background_image_path
        self.default_font_path = "fonts/font-black.ttf"

    async def generate_scoreboard_image(
        self, 
        bot,
        match_data: Dict,
        team1: List[Dict],
        team2: List[Dict],
        team1_score: int,
        team2_score: int
    ) -> str:
        """Generate scoreboard results image using Pillow"""
        try:
            # Get the project root directory
            project_root = os.path.dirname(os.path.dirname(__file__))
            
            # Load the background image
            background_path = os.path.join(project_root, self.background_image_path)
            if not os.path.exists(background_path):
                logger.error(f"Background image not found: {background_path}")
                return await self._create_fallback_image(match_data["match_id"])
                
            image = Image.open(background_path).copy()
            
            # Create a drawing context
            draw = ImageDraw.Draw(image)
            
            # Load custom fonts
            font_path = os.path.join(project_root, self.default_font_path)
            try:
                font_130 = ImageFont.truetype(font_path, 130)  # For score, map, winner
                font_110 = ImageFont.truetype(font_path, 110)  # For player names
                font_90 = ImageFont.truetype(font_path, 90)    # For stats
                logger.info(f"Loaded custom fonts from: {font_path}")
            except (IOError, OSError) as e:
                logger.warning(f"Could not load font from {font_path}, using default font: {e}")
                font_130 = ImageFont.load_default()
                font_110 = ImageFont.load_default()
                font_90 = ImageFont.load_default()
            
            # Define colors
            text_color = (255, 255, 255)      # White for regular text
            green_color = (0, 255, 0)         # Green for positive points
            red_color = (255, 0, 0)           # Red for negative points
            
            # ==================== COORDONATE PRINCIPALE ====================
            # Header elements coordinates
            SCORE_X = 1365
            SCORE_Y = 66
            
            MAP_X = 240
            MAP_Y = 1628
            
            WINNER_X = 2340
            WINNER_Y = 1633
            
            # Team 1 coordinates (left side)
            TEAM1_AVATAR_X = 125
            TEAM1_NAME_X = 240
            TEAM1_KILLS_X = 703
            TEAM1_ASSISTS_X = 895
            TEAM1_DEATHS_X = 1080
            TEAM1_RATING_X = 1265
            
            # Team 1 Y coordinates for each player (5 players)
            TEAM1_PLAYER_Y = [480, 712, 948, 1165, 1398]  # Gap de 150px între jucători
            
            # Team 2 coordinates (right side)
            TEAM2_AVATAR_X = 2568
            TEAM2_NAME_X = 2460
            TEAM2_KILLS_X = 2020
            TEAM2_ASSISTS_X = 1839
            TEAM2_DEATHS_X = 1654
            TEAM2_RATING_X = 1469
            
            # Team 2 Y coordinates for each player (5 players)
            TEAM2_PLAYER_Y = [480, 712, 948, 1165, 1398]  # Gap de 150px între jucători
            
            # Points Y coordinates for each player (5 players)
            TEAM1_POINTS_Y = [478, 710, 953, 1179, 1405]  # Y diferit pentru puncte
            TEAM2_POINTS_Y = [486, 709, 953, 1177, 1404]  # Y diferit pentru puncte
            
            # Stats Y offset (pentru a poziționa statisticile sub nume)
            STATS_Y_OFFSET = 37

            # ==============================================================
            
            # Header information
            # Score
            score_text = f"{team1_score}-{team2_score}"
            draw.text((SCORE_X, SCORE_Y), score_text, font=font_130, fill=text_color, anchor="mm")
            
            # Map
            map_name = match_data.get("map", "Unknown")
            draw.text((MAP_X, MAP_Y), map_name, font=font_130, fill=text_color, anchor="lm")
            
            # Winner team
            team1_won = team1_score > team2_score
            teams_data = match_data.get("teams", {})
            if isinstance(teams_data, str):
                import json
                teams_data = json.loads(teams_data)
            
            if team1_won:
                winner_text = f"Team {teams_data.get('team1', {}).get('captain', 'Unknown')}"
            else:
                winner_text = f"Team {teams_data.get('team2', {}).get('captain', 'Unknown')}"
            
            draw.text((WINNER_X, WINNER_Y), winner_text, font=font_130, fill=text_color, anchor="rm")
            
            # Team 1 Players (max 5 players)
            for i, player in enumerate(team1[:5]):  # Max 5 players
                if i >= len(TEAM1_PLAYER_Y):
                    break  # Nu avem coordonate pentru mai mult de 5 jucători
                
                player_y = TEAM1_PLAYER_Y[i]
                
                # Points în loc de avatar
                points = player.get('points', 0)
                points_text = f"{points:+d}"  # Format cu + pentru pozitive
                
                # Alege culoarea în funcție de puncte
                if points > 0:
                    points_color = green_color
                elif points < 0:
                    points_color = red_color
                else:
                    points_color = text_color  # Alb pentru 0
                
                # Afișează punctele direct (fără cerc)
                # Y diferit pentru fiecare jucător
                points_y = TEAM1_POINTS_Y[i]  # Folosește array-ul dedicat
                draw.text(
                    (124, points_y), 
                    points_text, 
                    font=font_110, 
                    fill=points_color, 
                    anchor="mm"
                )
                
                # Name
                draw.text((TEAM1_NAME_X, player_y), str(player.get('nickname', 'Unknown')), font=font_110, fill=text_color, anchor="lm")
                
                # Stats
                stats_y = player_y + STATS_Y_OFFSET
                
                # Kills
                draw.text((TEAM1_KILLS_X, stats_y), str(player.get('kills', 0)), font=font_90, fill=text_color, anchor="mm")
                
                # Assists
                draw.text((TEAM1_ASSISTS_X, stats_y), str(player.get('assists', 0)), font=font_90, fill=text_color, anchor="mm")
                
                # Deaths
                draw.text((TEAM1_DEATHS_X, stats_y), str(player.get('deaths', 0)), font=font_90, fill=text_color, anchor="mm")
                
                # Rating
                rating = player.get('rating', 0.0)
                if isinstance(rating, (int, float)):
                    rating_text = f"{rating:.1f}"
                else:
                    rating_text = str(rating)
                draw.text((TEAM1_RATING_X, stats_y), rating_text, font=font_90, fill=text_color, anchor="mm")
            
            # Team 2 Players (max 5 players)
            team2_start_index = len(team1)
            
            for i, player in enumerate(team2[:5]):  # Max 5 players
                if i >= len(TEAM2_PLAYER_Y):
                    break  # Nu avem coordonate pentru mai mult de 5 jucători
                
                player_y = TEAM2_PLAYER_Y[i]
                
                # Points în loc de avatar
                points = player.get('points', 0)
                points_text = f"{points:+d}"  # Format cu + pentru pozitive
                
                # Alege culoarea în funcție de puncte
                if points > 0:
                    points_color = green_color
                elif points < 0:
                    points_color = red_color
                else:
                    points_color = text_color  # Alb pentru 0
                
                # Afișează punctele direct (fără cerc)
                # Y diferit pentru fiecare jucător
                points_y = TEAM2_POINTS_Y[i]  # Folosește array-ul dedicat
                draw.text(
                    (2570, points_y), 
                    points_text, 
                    font=font_110, 
                    fill=points_color, 
                    anchor="mm"
                )
                
                # Name
                draw.text((TEAM2_NAME_X, player_y), str(player.get('nickname', 'Unknown')), font=font_110, fill=text_color, anchor="rm")
                
                # Stats
                stats_y = player_y + STATS_Y_OFFSET
                
                # Kills
                draw.text((TEAM2_KILLS_X, stats_y), str(player.get('kills', 0)), font=font_90, fill=text_color, anchor="mm")
                
                # Assists
                draw.text((TEAM2_ASSISTS_X, stats_y), str(player.get('assists', 0)), font=font_90, fill=text_color, anchor="mm")
                
                # Deaths
                draw.text((TEAM2_DEATHS_X, stats_y), str(player.get('deaths', 0)), font=font_90, fill=text_color, anchor="mm")
                
                # Rating
                rating = player.get('rating', 0.0)
                if isinstance(rating, (int, float)):
                    rating_text = f"{rating:.1f}"
                else:
                    rating_text = str(rating)
                draw.text((TEAM2_RATING_X, stats_y), rating_text, font=font_90, fill=text_color, anchor="mm")
            
            # Save the image to a temporary file
            temp_path = os.path.join(
                tempfile.gettempdir(),
                f"scoreboard_results_{match_data['match_id']}_{int(datetime.now().timestamp())}.png",
            )
            image.save(temp_path, "PNG")
            
            logger.info(f"Scoreboard results image generated successfully: {temp_path}")
            return temp_path
            
        except Exception as e:
            logger.error(f"Error generating scoreboard results image with Pillow: {e}")
            return await self._create_fallback_image(match_data.get("match_id", "unknown"))

    async def _create_fallback_image(self, match_id: str) -> str:
        """Create a simple fallback image in case of errors"""
        try:
            # Create a simple colored image with error text
            fallback_image = Image.new("RGB", (2700, 1700), (255, 0, 0))  # Red background
            draw = ImageDraw.Draw(fallback_image)
            
            try:
                font = ImageFont.load_default()
                draw.text(
                    (1350, 850), 
                    "Error generating scoreboard results image", 
                    font=font, 
                    fill=(255, 255, 255), 
                    anchor="mm"
                )
            except Exception:
                # If even default font fails, just create red image
                pass
                
            temp_path = os.path.join(
                tempfile.gettempdir(), 
                f"scoreboard_error_{match_id}_{int(datetime.now().timestamp())}.png"
            )
            fallback_image.save(temp_path, "PNG")
            
            logger.info(f"Created fallback scoreboard results image: {temp_path}")
            return temp_path
            
        except Exception as e:
            logger.error(f"Failed to create fallback image: {e}")
            # Return a dummy path - calling code should handle this gracefully
            return ""
