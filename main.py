import os
import discord
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
bot = discord.Bot(intents=intents) if hasattr(discord, "Bot") else discord.Client(intents=intents)


@bot.event
async def on_ready():
    print(f"{bot.user} is ready!")


if __name__ == "__main__":
    bot.run(TOKEN)
