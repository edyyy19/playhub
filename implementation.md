# 🛠️ /create_league Command — League Creation + League Table + Discord Role
#
# Purpose:
# Creates a new league by:
# 1. Adding a sanitized name to the `leagues` table
# 2. Creating a dedicated table for the league with a predefined structure
# 3. Creating a Discord role with the original league name
#
# Flow:
# - Admin uses the command: `/create_league PRO League`
#
# 1. Sanitize the league name:
#    - Lowercase
#    - Replace spaces/symbols with underscores
#    - "PRO League" → `pro_league`
#
# 2. Add entry to the `leagues` table:
#    - `league_name`: `pro_league`
#    - Also include `league_id` (int4 primary key) and `created_at` (timestampz)
#
# 3. Create a new table in the database named exactly `pro_league`
#    - Use a Supabase RPC function to do this, e.g., `create_league_table(table_name TEXT)`
#    - The function should create a table with the following structure:
#
#      Columns:
#      - `discord_id` TEXT — must reference `users(discord_id)` primary key
#      - `nickname` TEXT
#      - `points` INT4
#      - `kills` INT4
#      - `deaths` INT4
#      - `wins` INT4
#      - `loses` INT4
#
# 4. Create a Discord role using the original input string ("PRO League")
#    - Do not sanitize the role name
#
# Constraints:
# - Use sanitized name only for DB-related logic
# - Use original name only for Discord role
# - Only allow trusted users/admins to run this command
# - Ensure the table creation logic is consistent and validated
