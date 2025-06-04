# Task Description:
# I want to implement a new functionality.
# 
# ✅ Goal:
# Create a command (e.g., /setscoreboardchannel) that allows an admin to **set a target channel** where final scoreboard results will be posted.
# 
# ✅ Command Behavior:
# - When an admin runs `/setscoreboardchannel [target_channel]`, the **bot should immediately send an embed with a "Submit Scoreboard" button in the SAME CHANNEL where the command was used**.
# - The `target_channel` (selected via command option) is used **later** to post the final stats embed after validation is complete.
# 
# ✅ When a user clicks the button:
# - A modal should appear with an input (number type) requiring the user to enter the **match ID**.
# - Then perform the following validation steps:
#   1. Search the Supabase `matches` table to find the match with the entered match ID.
#   2. Check the `results_submitted` field:
#      - If `true`, **do not proceed** (results already submitted).
#   3. Check if the user participated in the match:
#      - Look in both `ct_team` and `t_team` fields to see if the user is included.
#      - If not, **do not proceed** (not authorized to submit).
#   4. If valid, prompt the user to upload the scoreboard screenshot.
#   5. Extract the **image URL** from the attachment (e.g., `message.attachments[0].url` — Discord CDN URL like `https://cdn.discordapp.com/...`).
# 
# ✅ OpenAI Integration:
# - Use `gpt-4o` as the model.
# - Use the image **URL** directly in the OpenAI vision request.
# - API key is stored as `OPENAI_API_KEY` in environment variables.
# - Use the following as the user prompt:
#
#   Extract player stats from this Standoff 2 scoreboard screenshot. Return only the following JSON format:
#
#   {
#     "teams": {
#       "Counter-Terrorists": {
#         "score": int,
#         "players": [
#           { "name": string, "kills": int, "deaths": int }
#         ]
#       },
#       "Terrorists": {
#         "score": int,
#         "players": [
#           { "name": string, "kills": int, "deaths": int }
#         ]
#       }
#     }
#   }
#
# - Set `max_tokens` to 750 to reduce cost and avoid verbose output.
# - Do not ask for explanations — **return only JSON**.
# - Send only one image per request.
# 
# ✅ Token Usage Logging:
# - Log token usage from OpenAI API using the `usage` field:
#   Example: INFO:root:OpenAI Token Usage — Prompt: 421, Completion: 302, Total: 723
# 
# ✅ JSON Processing and Validation:
# - Match each player's name from GPT-4o JSON with the player list in the `ct_team` and `t_team` fields from Supabase.
# - Use a fuzzy/similarity check to handle variations in nicknames.
# - If everything matches:
#   - Update the `ct_team` and `t_team` player stats with:
#     - kills, deaths
#     - +20 points for players on the **winning** team
#     - -10 points for players on the **losing** team
#   - Mark the match as `results_submitted = true`
# 
# ✅ Final Embed:
# - After processing is complete and the database is updated:
#   - Send an embed message in the originally selected **target channel**.
#   - The embed should display both teams, their scores, and a list of each player with their:
#     - name, kills, deaths, and point adjustment (+20 or -10).
