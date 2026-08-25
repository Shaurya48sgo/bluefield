import os
import asyncio

import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

from cogs.common import M, P, get_prefix, is_bot_enabled
from cogs.summons import EasyJoinView
from cogs.confess import SecretReplyView

TOKEN = os.getenv("DISCORD_TOKEN")
OWNER_ID = os.getenv("OWNER_ID")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix=get_prefix, intents=intents)
bot.remove_command("help")


@bot.event
async def on_command_error(ctx, error):
    from discord.ext.commands import CheckFailure, CommandNotFound

    if isinstance(error, (CommandNotFound, CheckFailure)):
        return
    print(f"Command error: {error}")


async def guild_gate_check(ctx):
    """Block prefix commands in servers where the bot is disabled (I?server stays usable)."""
    if ctx.guild is None or ctx.command is None:
        return True
    if ctx.command.name == "server":
        return True
    return is_bot_enabled(ctx.guild.id)


bot.check(guild_gate_check)


async def tree_interaction_check(interaction):
    if interaction.guild_id is None:
        return True
    enabled = is_bot_enabled(interaction.guild_id)
    if not enabled:
        try:
            await interaction.response.send_message(
                "⛔ The bot is disabled in this server. An admin can re-enable it with `I?server enable`.",
                ephemeral=True,
            )
        except Exception:
            pass
    return enabled


bot.tree.interaction_check = tree_interaction_check


@bot.event
async def on_guild_join(guild):
    print(f"Joined new guild: {guild.name} ({guild.id})")
    try:
        await bot.tree.sync(guild=guild)
        print(f"Synced commands for {guild.name}")
    except Exception as e:
        print(f"Failed to sync for {guild.name}: {e}")


@bot.event
async def on_ready():
    print(f"{bot.user} is ready!")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} global slash command(s)")
    except Exception as e:
        print(f"Failed to sync global slash commands: {e}")
    for guild in bot.guilds:
        try:
            await bot.tree.sync(guild=guild)
            print(f"Synced commands for guild {guild.name} ({guild.id})")
        except Exception as e:
            print(f"Failed to sync guild {guild.name}: {e}")
    if OWNER_ID:
        try:
            owner = await bot.fetch_user(int(OWNER_ID))
            await owner.send(f"🟢 **{bot.user} is online!** Ready to work.")
            print(f"Pinged owner {owner}.")
        except Exception as e:
            print(f"Could not ping owner: {e}")


@bot.tree.error
async def on_app_command_error(interaction, error):
    try:
        if not interaction.response.is_done():
            await interaction.response.send_message(f"⚠️ {error}", ephemeral=True)
        else:
            await interaction.followup.send(f"⚠️ {error}", ephemeral=True)
    except Exception:
        pass
    print(f"Command error: {error}")


async def setup_hook():
    await bot.load_extension("cogs.core")
    await bot.load_extension("cogs.summons")
    await bot.load_extension("cogs.confess")
    for panel in P.find():
        try:
            bot.add_view(
                EasyJoinView(bot.cogs["SummonsCog"], panel["guild_id"], panel["summon_id"]),
                message_id=panel["message_id"],
            )
        except Exception:
            pass
    for sm in M.find():
        try:
            bot.add_view(
                SecretReplyView(bot.cogs["ConfessCog"], sm["guild_id"], sm["channel_id"], sm["code"]),
                message_id=sm["message_id"],
            )
        except Exception:
            pass


bot.setup_hook = setup_hook


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("DISCORD_TOKEN not set in .env")
    asyncio.run(bot.start(TOKEN))
