import os
from dotenv import load_dotenv
from supabase import create_client, Client
from threading import Lock
import discord
import redis

load_dotenv()


class Config:
    # Application configuration
    APP_NAME = os.getenv("APP_NAME", "PlayHub")

    # Banner URLs
    REGISTRATION_BANNER = os.getenv("REGISTRATION_BANNER")
    LOBBIES_WAITING_BANNER = os.getenv("LOBBIES_WAITING_BANNER")

    # Discord configuration
    TOKEN = os.getenv("DISCORD_TOKEN")
    REGISTERED_ROLE = "Registered"
    REGISTRATION_CHANNEL = "registration"

    # Redis configuration
    REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
    REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
    REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", None)
    REDIS_DB = int(os.getenv("REDIS_DB", 0))

    # Map pool with names and emojis
    MAP_POOL = [
        {"name": "Sandstone", "emoji": "<:sandstone_emoji:1397905555057807382>"},
        {"name": "Rust", "emoji": "<:rust_emoji:1397905569825951774>"},
        # {"name": "Province", "emoji": "<:province_emoji:1397905564520022037>"},
        # {"name": "Breeze", "emoji": "<:breeze_emoji:1397905561407979570>"},
        # {"name": "Dune", "emoji": "<:dune_emoji:1397905722372521997>"},
        # {"name": "Zone7", "emoji": "<:zone7_emoji:1397905558006136963>"},
        # {"name": "Sakura", "emoji": "<:sakura_emoji:1397905551526203452>"},
    ]

    PLAYERS_REQUIRED = 4
    MAX_PARTY_MEMBERS = 2
    
    # Authorized user IDs for restricted commands
    AUTHORIZED_USERS = [245974474111844354]
    
    ICONS = {
        "changes": "<:discord_changes:1407739857282928753>",
        "success": "<:discord_check:1397568155379040256>",
        "success_playcolor": "<:discord_check_playcolor:1407739859656904724>",
        "unsuccess": "<:discord_x:1397568284722724914>",
        "not_allowed": "<:discord_notallowed:1397568243425611933>",
        "info": "<:discord_info:1397568211746033805>",
        "leave": "<:discord_leave:1397569300868632607>",
        "season": "<:discord_season:1407739868427325522>",
        "stats": "<:discord_stats:1407739870297854032>",
        "stats_yellow": "<:discord_stats_yellow:1398291689738862723>",
        "stats_lightgreen":"<:discord_stats_lightgreen:1398376023120281641>",
        "user": "<:discord_user:1407739850572173344>",
        "calendar": "<:discord_calendar:1407739854456099008>",
        "camera": "<:discord_camera:1397568144813330452>",
        "crosshair": "<:discord_crosshair:1407739861858914304>",
        "clock": "<:discord_clock:1397568165432656033>",
        "clock_yellow": "<:discord_clock_yellow:1397568180528091277>",
        "clock_lightgreen": "<:discord_clock_lightgreen:1397568172827086900>",
        "updating": "<:discord_updating:1397568277986938980>",
        "lock": "<:discord_lock:1397568236975034398>",
        "unlock": "<:discord_unlock:1397568267769352344>",
        "instructions": "<:discord_instructions:1397568218591133737>",
        "league": "<:discord_league:1397568105319760003>",
        "tag": "<:discord_tag:1397568259766878249>",
        "sql": "<:discord_sql:1397568253295067177>",
        "fix": "<:discord_fix:1397568204867633252>",
        "wave": "<:discord_wave:1407739852665126942>",
        "id": "<:discord_id:1407739866003017910>",
        "game": "<:discord_game:1407739864341807256>",
        "search_yellow": "<:discord_search_yellow:1398622369467207831>",
        "question_yellow":"<:discord_question_yellow:1398624918207467583>",
        "bell_red":"<:discord_bell:1398627099811774555>",
        "bell_lightgreen":"<:discord_bell_lightgreen:1398627732471943239>"
}


    EMBED_COLORS = {
        "waiting": 0xFFFF00,  # Yellow
        "draft": 0x6969E7,  # Blue
        "veto": 0xFB5B29,  # Orange
        "final": 0xb2fa19,  # Green
        "light_green": 0x57F287,  # Light Green
        "red": 0xFF4848,  # Red
        "yellow": 0xFFC533,  # Yellow
    }
    MAIN_COLOR=0xb2fa19 # Green

    # Supabase configuration
    SUPABASE_URL = os.getenv("SUPABASE_URL")
    SUPABASE_KEY = os.getenv("SUPABASE_KEY")
    _supabase_client = None
    _supabase_lock = Lock()

    # Redis client singleton
    _redis_client = None
    _redis_lock = Lock()

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

    @classmethod
    def redis(cls) -> redis.Redis:
        """Initialize and return a singleton Redis client"""
        if cls._redis_client is None:
            with cls._redis_lock:
                if cls._redis_client is None:
                    cls._redis_client = redis.Redis(
                        host=cls.REDIS_HOST,
                        port=cls.REDIS_PORT,
                        password=cls.REDIS_PASSWORD,
                        db=cls.REDIS_DB,
                        decode_responses=True,  # Automatic string decoding
                        socket_connect_timeout=5,
                        socket_timeout=5,
                        retry_on_timeout=True
                    )
        return cls._redis_client

    @classmethod
    def is_authorized_user(cls, user_id: int) -> bool:
        """Check if a user ID is in the authorized users list"""
        return user_id in cls.AUTHORIZED_USERS
    
    @classmethod
    async def check_authorization(cls, interaction) -> bool:
        """
        Funcție reutilizabilă pentru verificarea autorizației utilizatorului.
        Returnează True dacă utilizatorul este autorizat, False dacă nu.
        Trimite automat un embed de eroare dacă utilizatorul nu este autorizat.
        """
        if not cls.is_authorized_user(interaction.user.id):
            embed = discord.Embed(
                title=f"{cls.ICONS['unsuccess']} Restricted Access",
                description="You are not authorized to use this command.",
                color=cls.EMBED_COLORS["red"],
            )
            embed.add_field(
                name="Information",
                value="This command requires special authorization beyond administrator permissions.",
                inline=False
            )
            embed.set_footer(text="Contact an higher administrator if you believe this is an error.")

            await interaction.response.send_message(embed=embed, ephemeral=True)
            return False
        return True
