import asyncio
import logging
import discord
from discord.ext import commands
from config import Config
from database import db

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)-8s] %(message)s")
log = logging.getLogger("bot")


class JailBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        intents.guilds = True
        super().__init__(
            command_prefix=Config.PREFIX,
            intents=intents,
            help_command=None,
            case_insensitive=True,
        )
        self._startup_done = False

    async def setup_hook(self):
        log.info("Connecting to MongoDB...")
        try:
            await db.connect()
            log.info("MongoDB connected")
        except Exception as e:
            log.error(f"MongoDB connection failed: {e}")
            return

        await self.load_extension("cogs.shop")
        await self.load_extension("cogs.items")
        await self.load_extension("cogs.dev")
        await self.load_extension("cogs.currency")
        await self.load_extension("cogs.jail")
        await self.load_extension("cogs.setup")
        await self.load_extension("cogs.autoresponder")

        try:
            synced = await self.tree.sync()
            log.info(f"Synced {len(synced)} slash commands")
        except Exception as e:
            log.error(f"Failed to sync commands: {e}")

        self.loop.create_task(self._cleanup_loop())

    async def _cleanup_loop(self):
        await asyncio.sleep(60)
        while True:
            try:
                await db.cleanup_expired_items()
            except Exception as e:
                log.error(f"Cleanup error: {e}")
            await asyncio.sleep(60)

    async def on_ready(self):
        if self._startup_done:
            return
        self._startup_done = True
        log.info(f"Logged in as {self.user} ({self.user.id}) on {len(self.guilds)} guilds")
        owner_id = Config.OWNER_ID
        if not owner_id:
            try:
                app = await self.application_info()
                owner_id = app.owner.id
            except Exception:
                pass
        if owner_id:
            try:
                owner = await self.fetch_user(owner_id)
                await owner.send("Bot is online and ready.")
            except Exception:
                pass

    async def on_message(self, message):
        if message.author.bot:
            return
        await self.process_commands(message)

    async def on_command_error(self, ctx, error):
        if isinstance(error, commands.CheckFailure):
            await ctx.send(embed=discord.Embed(
                description="You don't have permission to use this command.",
                color=discord.Color.red(),
            ))
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(embed=discord.Embed(
                description=f"Missing required argument: `{error.param.name}`",
                color=discord.Color.red(),
            ))
        elif isinstance(error, commands.CommandNotFound):
            return
        elif isinstance(error, commands.BadArgument):
            await ctx.send(embed=discord.Embed(
                description=str(error),
                color=discord.Color.red(),
            ))
        else:
            log.error(f"Command error in {ctx.command}: {error}", exc_info=True)
            await ctx.send(embed=discord.Embed(
                description="An error occurred while executing this command.",
                color=discord.Color.red(),
            ))


bot = JailBot()

if __name__ == "__main__":
    if not Config.TOKEN:
        log.error("DISCORD_TOKEN not set in .env")
        raise SystemExit(1)
    bot.run(Config.TOKEN)
