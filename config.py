import os
from dotenv import load_dotenv
from supabase import create_client, Client
from threading import Lock

load_dotenv()


class Config:
    # Application configuration
    APP_NAME = os.getenv("APP_NAME", "PlayHub")

    # Discord configuration
    TOKEN = os.getenv("DISCORD_TOKEN")
    REGISTERED_ROLE = "Registered"
    REGISTRATION_CHANNEL = "registration"
    MAP_POOL = os.getenv("MAP_POOL", "Sandstone,Rust,Province").split(",")
    PLAYERS_REQUIRED = 6
    MAX_PARTY_MEMBERS = 2
    ICONS = {
        "changes": "<:discord_changes:1382829997563318343>",
        "success": "<:discord_check:1382829999576453202>",
        "success_playcolor": "<:discord_check_playcolor:1382911699673157663>",
        "unsuccess": "<:discord_x:1382829989212455184>",
        "not_allowed": "<:discord_notallowed:1382911672917688462>",
        "info": "<:discord_info:1382829979112312872>",
        "leave": "<:discord_leave:1382829981566107839>",
        "season": "<:discord_season:1382829983529173054>",
        "stats": "<:discord_stats:1382829985198510210>",
        "user": "<:discord_user:1382829987228418100>",
        "calendar": "<:discord_calendar:1382829995646386237>",
        "camera": "<:discord_camera:1382911706673582175>",
        "crosshair": "<:discord_crosshair:1382911687447019640>",
        "clock": "<:discord_clock:1382911693998522449>",
        "clock_yellow": "<:discord_clock_yellow:1382917411287601244>",
        "clock_lightgreen": "<:discord_clock_lightgreen:1382918269706567790>",
        "updating": "<:discord_updating:1382911658250469407>",
        "lock": "<:discord_lock:1382911679074926602>",
        "unlock": "<:discord_unlock:1382911664982327376>",
        "instructions": "<:discord_instructions:1383130937499455488>",
        "league": "<:discord_league:1383226342358908998>",
        "tag": "<:discord_tag:1383229700381605898>",
        "sql": "<:discord_sql:1383230660814180466>",
    }

    EMBED_COLORS = {
        "waiting": 0xFFFF00,  # Yellow
        "draft": 0x6969E7,  # Blue
        "veto": 0xFB5B29,  # Orange
        "final": 0xB2FA19,  # Green
        "light_green": 0x57F287,  # Light Green
        "red": 0xFF4848,  # Red
        "yellow": 0xFFC533,  # Yellow
    }

    # Supabase configuration
    SUPABASE_URL = os.getenv("SUPABASE_URL")
    SUPABASE_KEY = os.getenv("SUPABASE_KEY")
    _supabase_client = None
    _supabase_lock = Lock()

    @classmethod
    def supabase(cls) -> Client:
        """Initialize and return a singleton Supabase client"""
        if cls._supabase_client is None:
            with cls._supabase_lock:
                if cls._supabase_client is None:
                    cls._supabase_client = create_client(
                        cls.SUPABASE_URL, cls.SUPABASE_KEY
                    )
        return cls._supabase_client
