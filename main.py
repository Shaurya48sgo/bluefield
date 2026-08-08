import os
import asyncio

import discord
from discord.ext import commands
from dotenv import load_dotenv

from cogs.common import get_prefix

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
OWNER_ID = os.getenv("OWNER_ID")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix=get_prefix, intents=intents)
bot.remove_command("help")


@bot.event
async def on_ready():
    print(f"{bot.user} is ready!")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash command(s): {[c.name for c in synced]}")
    except Exception as e:
        print(f"Failed to sync slash commands: {e}")
    if OWNER_ID:
        try:
            owner = await bot.fetch_user(int(OWNER_ID))
            await owner.send(f"🟢 **{bot.user} is online!** Ready to work.")
            print(f"Pinged owner {owner}.")
        except Exception as e:
            print(f"Could not ping owner: {e}")


async def setup_hook():
    await bot.load_extension("cogs.core")
    await bot.load_extension("cogs.summons")


bot.setup_hook = setup_hook


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("DISCORD_TOKEN not set in .env")
    asyncio.run(bot.start(TOKEN))
