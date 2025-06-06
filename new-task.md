# Create a Discord bot command `/setup_levels` that creates the following roles in the server:
# - Level 1
# - Level 2
# - Level 3
# - Level 4
# - Level 5
# - Level 6
# - Level 7
# - Level 8
# - Level 9
# - Level 10
# - Challenger

# Role names must be exactly as listed above, with no point ranges or descriptions included.
# When creating each role, assign it the following hex color codes:

# - Level 1: #BBBBBB (gray)
# - Level 2, 3: #4ADE80 (green)
# - Level 4, 5, 6, 7: #F8C627 (gold/yellow)
# - Level 8, 9: #FA6E22 (orange)
# - Level 10, Challenger: #DD0226 (red)

# Level system:
# Level 1: 0–500         → +70 win / -5 loss
# Level 2: 501–751       → +70 win / -10 loss
# Level 3: 751–900       → +55 win / -10 loss
# Level 4: 901–1050      → +55 win / -13 loss
# Level 5: 1051–1200     → +55 win / -20 loss
# Level 6: 1201–1350     → +40 win / -24 loss
# Level 7: 1351–1530     → +40 win / -28 loss
# Level 8: 1531–1750     → +40 win / -34 loss
# Level 9: 1751–2000     → +30 win / -40 loss
# Level 10: 2001+        → +20 win / -45 loss
# Challenger: 2300+ and Top 10 globally → +15 win / -48 loss

# Requirements:
# 1. `/setup_levels` creates roles if they do not already exist, with correct names and colors.
# 2. On player point update (after match):
#    - Recalculate level based on new points.
#    - Remove previous level role and assign the correct one.
#    - If player is in top 10 and has 2300+ points, assign "Challenger".
#    - Remove "Challenger" if they fall out of top 10 or below 2300 points.
#    - Apply the correct win/loss point delta based on the player's level at the time of the match.
# 3. Add `/update_players_levels` command:
#    - Go through all players.
#    - Check if their assigned level role matches their current points.
#    - If not, remove the old role and assign the correct one.
#    - Update "Challenger" status as well.