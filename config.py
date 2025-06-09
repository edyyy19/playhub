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
