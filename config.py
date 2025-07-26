import os
from dotenv import load_dotenv
from supabase import create_client, Client
from threading import Lock
import discord

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

    # Map pool with names and emojis
    MAP_POOL = [
        {"name": "Sandstone", "emoji": "<:sandstone_emoji:1397905555057807382>"},
        {"name": "Rust", "emoji": "<:rust_emoji:1397905569825951774>"},
        {"name": "Province", "emoji": "<:province_emoji:1397905564520022037>"},
        {"name": "Breeze", "emoji": "<:breeze_emoji:1397905561407979570>"},
        {"name": "Dune", "emoji": "<:dune_emoji:1397905722372521997>"},
        {"name": "Zone7", "emoji": "<:zone7_emoji:1397905558006136963>"},
        {"name": "Sakura", "emoji": "<:sakura_emoji:1397905551526203452>"},
    ]

    PLAYERS_REQUIRED = 4
    MAX_PARTY_MEMBERS = 2
    
    # Authorized user IDs for restricted commands
    AUTHORIZED_USERS = [245974474111844354,1102901572519858206,301123036487352321]
    
    ICONS = {
        "changes": "<:discord_changes:1397568117839761580>",
        "success": "<:discord_check:1397568155379040256>",
        "success_playcolor": "<:discord_check_playcolor:1397568120607998004>",
        "unsuccess": "<:discord_x:1397568284722724914>",
        "not_allowed": "<:discord_notallowed:1397568243425611933>",
        "info": "<:discord_info:1397568211746033805>",
        "leave": "<:discord_leave:1397569300868632607>",
        "season": "<:discord_season:1397568107496603758>",
        "stats": "<:discord_stats:1397568109472251954>",
        "stats_yellow": "<:discord_stats_yellow:1398291689738862723>",
        "stats_lightgreen":"<:discord_stats_lightgreen:1398376023120281641>",
        "user": "<:discord_user:1397568111208562688>",
        "calendar": "<:discord_calendar:1397568115478499458>",
        "camera": "<:discord_camera:1397568144813330452>",
        "crosshair": "<:discord_crosshair_1:1398370225652629614>",
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
        "wave": "<:discord_wave:1397568113679274044>",
        "id": "<:discord_id:1398370238017437818>",
        "game": "<:discord_game:1398371217760587776>",
        "search_yellow": "<:discord_search_yellow:1398622369467207831>",
        "question_yellow":"<:discord_question_yellow:1398624918207467583>",
        "bell_red":"<:discord_bell:1398627099811774555>",
        "bell_lightgreen":"<:discord_bell_lightgreen:1398627732471943239>"
}


    EMBED_COLORS = {
        "waiting": 0xFFFF00,  # Yellow
        "draft": 0x6969E7,  # Blue
        "veto": 0xFB5B29,  # Orange
        "final": 0xe91515,  # Red
        "light_green": 0x57F287,  # Light Green
        "red": 0xFF4848,  # Red
        "yellow": 0xFFC533,  # Yellow
    }
    MAIN_COLOR=0xe91515 # Red

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
