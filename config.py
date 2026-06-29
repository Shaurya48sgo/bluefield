import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    TOKEN = os.getenv("DISCORD_TOKEN")
    MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
    DB_NAME = os.getenv("DB_NAME", "jailbot")

    PREFIX = "I?"
    OWNER_ID = int(os.getenv("OWNER_ID") or "0")

    SHOP_ITEMS = {
        "silence_2min": {"name": "Silence 2min", "price": 100, "duration": 2, "pro": False},
        "silence_5min": {"name": "Silence 5min", "price": 200, "duration": 5, "pro": False},
        "silence_pro_2min": {"name": "Silence Pro 2min", "price": 500, "duration": 2, "pro": True},
        "silence_pro_5min": {"name": "Silence Pro 5min", "price": 800, "duration": 5, "pro": True},
        "immunity": {"name": "Immunity", "price": 300, "description": "Blocks non-pro jails for 24h"},
        "full_immunity": {"name": "Full Immunity", "price": 600, "description": "Blocks all jails for 24h"},
        "reverse": {"name": "Reverse", "price": 400, "description": "Reverses non-pro jails for 24h"},
        "divine_eye": {"name": "Divine Eye", "price": 1000, "description": "One-time use. Shows all active protections"},
        "invis_pot": {"name": "Invis Pot", "price": 350, "description": "Hides you in everyone logs for 24h"},
    }

    CURRENCY_NAME = "Coins"
    STARTING_BALANCE = 500
