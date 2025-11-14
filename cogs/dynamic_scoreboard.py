import discord
import asyncio
import json
import os
import logging
from openai import OpenAI

logger = logging.getLogger(__name__)

# Initialize OpenAI client
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


async def process_with_gpt(
    image_url: str, team1_players: list, team2_players: list
) -> dict:
    """
    Process scoreboard screenshot using OpenAI GPT-4 Vision API.

    Args:
        image_url: Discord URL of the screenshot
        team1_players: List of players from team1 with format [{"nickname": str, "discord_id": str}, ...]
        team2_players: List of players from team2 with format [{"nickname": str, "discord_id": str}, ...]

    Returns:
        dict: Extracted data with structure:
        {
            "game_detected": "standoff2" or "unknown",
            "confidence": float (0.0-1.0),
            "teams": {
                "team1": {
                    "score": int,
                    "players": [
                        {
                            "nickname": str,
                            "discord_id": str,
                            "kills": int,
                            "assists": int,
                            "deaths": int,
                            "matched_from": str (clean name from screenshot or "default_stats")
                        }
                    ]
                },
                "team2": {...}
            },
            "matching_details": [str] (debugging info)
        }
    """
    try:
        # Create the expected players list for GPT with discord_ids
        team1_players_info = [
            {"nickname": p["nickname"], "discord_id": p["discord_id"]}
            for p in team1_players
        ]
        team2_players_info = [
            {"nickname": p["nickname"], "discord_id": p["discord_id"]}
            for p in team2_players
        ]

        # Define the advanced OpenAI call function with player matching
        def make_openai_call():
            return client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": f"""Analyze this Standoff 2 scoreboard screenshot and extract player statistics with smart player matching.

EXPECTED PLAYERS FROM DATABASE:
Team 1: {team1_players_info}
Team 2: {team2_players_info}

INSTRUCTIONS:
1. First verify this is a valid Standoff 2 scoreboard with Counter-Terrorists vs Terrorists teams
2. Extract all visible player names and their stats (kills, assists, deaths)
   - FIRST STEP: Remove ALL clan tags before any processing
   - Clan tags are in format "[TAG] PlayerName" - remove "[TAG] " completely
   - Work ONLY with clean names for all subsequent steps
3. CRITICAL: Player name extraction process
   - When you see "[EXTN] User-1" in screenshot → extract as "User-1" 
   - When you see "[PRO] Player123" in screenshot → extract as "Player123"
   - NEVER include clan tags in any extracted names
   - Use ONLY the clean names for matching and processing
4. Match the clean extracted names directly to database players:
   - Use ONLY clean names (no clan tags) from step 2
   - Try exact name matches first between clean extracted names and database
   - Then try substring matching (database name contained in clean extracted name)
   - Handle variations like special characters, numbers
   - Match case-insensitively
5. Use FIXED team mapping: Team1 = Terrorists, Team2 = Counter-Terrorists
6. For each player in the result, use the EXACT discord_id from the database
7. Assign unmatched database players default stats (0 kills, 0 assists, 13 deaths)

Return ONLY valid JSON in this exact format:
{{
  "game_detected": "standoff2" or "unknown",
  "confidence": number (0.0-1.0 confidence this is valid Standoff 2 scoreboard),
  "teams": {{
    "team1": {{
      "score": integer (Terrorists score),
      "players": [
        {{
          "nickname": "exact_database_player_name",
          "discord_id": "actual_discord_id_from_database",
          "kills": integer,
          "assists": integer, 
          "deaths": integer,
          "matched_from": "clean_extracted_name_from_screenshot" or "default_stats"
        }}
      ]
    }},
    "team2": {{
      "score": integer (Counter-Terrorists score),
      "players": [
        {{
          "nickname": "exact_database_player_name", 
          "discord_id": "actual_discord_id_from_database",
          "kills": integer,
          "assists": integer,
          "deaths": integer,
          "matched_from": "clean_extracted_name_from_screenshot" or "default_stats"
        }}
      ]
    }}
  }},
  "matching_details": [
    "details about each matching attempt for debugging"
  ]
}}

IMPORTANT RULES:
- Include ALL database players in the result (either with matched stats or default stats)
- Use exact database nicknames and discord_ids in the result
- Team1 = Terrorists (T side), Team2 = Counter-Terrorists (CT side)
- If a database player isn't found in screenshot, give them default stats: 0K/0A/13D
- Confidence should be 0.8+ only for clear Standoff 2 scoreboards
- Extract exact numbers for kills, assists, deaths as shown in screenshot
- CRITICAL: Remove "[TAG] " prefixes BEFORE any processing or matching
- Store clean extracted name (without clan tags) in "matched_from"
- VERY IMPORTANT: Return the exact discord_id from the database for each player
- VERY IMPORTANT: Extract players WITHOUT clan tags from the very beginning""",
                            },
                            {"type": "image_url", "image_url": {"url": image_url}},
                        ],
                    }
                ],
                max_tokens=2000,
            )

        # Run the OpenAI call in a separate thread to avoid blocking Discord's event loop
        response = await asyncio.to_thread(make_openai_call)

        # Log token usage
        usage = response.usage
        logger.info(
            f"🟢 OpenAI Token Usage — Prompt: {usage.prompt_tokens}, "
            f"Completion: {usage.completion_tokens}, "
            f"Total: {usage.total_tokens}"
        )

        # Parse the JSON response
        content = response.choices[0].message.content.strip()

        # Try to extract JSON from the response
        try:
            # Find JSON in the response
            if "```json" in content:
                json_start = content.find("```json") + 7
                json_end = content.find("```", json_start)
                json_content = content[json_start:json_end].strip()
            elif "{" in content:
                json_start = content.find("{")
                json_end = content.rfind("}") + 1
                json_content = content[json_start:json_end]
            else:
                json_content = content

            extracted_data = json.loads(json_content)
            logger.info(
                f"🟢 GPT extracted data: {json.dumps(extracted_data, indent=2)}"
            )

            return extracted_data

        except json.JSONDecodeError as e:
            logger.error(f"🟢 Failed to parse OpenAI JSON response: {e}")
            logger.error(f"🟢 Raw response: {content}")
            raise Exception(f"Failed to parse GPT response: {e}")

    except Exception as e:
        logger.error(f"🟢 Error in dynamic scoreboard GPT processing: {e}")
        raise


async def setup(bot):
    """Setup function for the cog (required but empty for now)"""
    pass
