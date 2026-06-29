import discord
from discord.ext import commands
from database import db
from config import Config
from utils.checks import dev_only, is_owner_or_authorized
from utils.helpers import get_member_from_mention, create_embed


class DevCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="dev")
    async def dev(self, ctx, action: str = None, *, mention: str = None):
        """Manage bot devs. Usage: I?dev add @user | I?dev remove @user | I?dev list"""
        if not ctx.guild:
            await ctx.send(embed=create_embed("Error", "This command can only be used in a server.", discord.Color.red()))
            return
        if not await is_owner_or_authorized(ctx):
            await ctx.send(embed=create_embed("Permission Denied", "Only the bot owner can manage devs.", discord.Color.red()))
            return
        if not action:
            await ctx.send(embed=create_embed(
                "Dev Management",
                "Usage:\n`I?dev add @user`\n`I?dev remove @user`\n`I?dev list`",
                discord.Color.blue(),
            ))
            return

        action = action.lower()

        if action == "list":
            devs = await db.get_devs(ctx.guild.id)
            if not devs:
                await ctx.send(embed=create_embed("Devs", "No devs set. Only the owner has access.", discord.Color.blue()))
                return
            dev_list = "\n".join([f"<@{d['user_id']}>" for d in devs])
            await ctx.send(embed=create_embed("Bot Devs", dev_list, discord.Color.blue()))
            return

        if not mention:
            await ctx.send(embed=create_embed("Error", "Please mention a user.", discord.Color.red()))
            return

        member = await get_member_from_mention(ctx.guild, mention.strip())
        if not member:
            await ctx.send(embed=create_embed("Error", "Invalid user mention.", discord.Color.red()))
            return

        if action == "add":
            if member.id == Config.OWNER_ID:
                await ctx.send(embed=create_embed("Error", "Owner is already a dev by default.", discord.Color.red()))
                return
            await db.add_dev(ctx.guild.id, member.id)
            await ctx.send(embed=create_embed("Dev Added", f"{member.mention} has been added as a bot dev.", discord.Color.green()))

        elif action == "remove":
            if member.id == Config.OWNER_ID:
                await ctx.send(embed=create_embed("Error", "Cannot remove the owner.", discord.Color.red()))
                return
            await db.remove_dev(ctx.guild.id, member.id)
            await ctx.send(embed=create_embed("Dev Removed", f"{member.mention} has been removed from bot devs.", discord.Color.orange()))

        else:
            await ctx.send(embed=create_embed("Error", "Invalid action. Use `add`, `remove`, or `list`.", discord.Color.red()))

    @commands.command(name="give")
    @dev_only()
    async def give(self, ctx, mention: str, item_id: str, quantity: int = 1):
        """Give items to a user (dev only). Usage: I?give @user item_id quantity"""
        member = await get_member_from_mention(ctx.guild, mention)
        if not member:
            await ctx.send(embed=create_embed("Error", "Invalid user mention.", discord.Color.red()))
            return

        if item_id not in Config.SHOP_ITEMS:
            valid_items = ", ".join(Config.SHOP_ITEMS.keys())
            await ctx.send(embed=create_embed("Error", f"Invalid item. Valid items: {valid_items}", discord.Color.red()))
            return

        await db.add_to_inventory(ctx.guild.id, member.id, item_id, quantity)
        await ctx.send(embed=create_embed(
            "Item Given",
            f"Gave **{quantity}x {Config.SHOP_ITEMS[item_id]['name']}** to {member.mention}.",
            discord.Color.green(),
        ))

    @commands.command(name="givemoney")
    @dev_only()
    async def givemoney(self, ctx, mention: str, amount: int):
        """Give currency to a user (dev only). Usage: I?givemoney @user amount"""
        member = await get_member_from_mention(ctx.guild, mention)
        if not member:
            await ctx.send(embed=create_embed("Error", "Invalid user mention.", discord.Color.red()))
            return

        await db.add_balance(ctx.guild.id, member.id, amount)
        await ctx.send(embed=create_embed(
            "Money Given",
            f"Gave **{amount}** {Config.CURRENCY_NAME} to {member.mention}.",
            discord.Color.green(),
        ))

    @commands.command(name="resetuser")
    @dev_only()
    async def resetuser(self, ctx, mention: str):
        """Reset a user's data (dev only). Usage: I?resetuser @user"""
        member = await get_member_from_mention(ctx.guild, mention)
        if not member:
            await ctx.send(embed=create_embed("Error", "Invalid user mention.", discord.Color.red()))
            return

        await db.users.delete_one({"guild_id": ctx.guild.id, "user_id": member.id})
        await ctx.send(embed=create_embed(
            "User Reset",
            f"Reset all data for {member.mention}.",
            discord.Color.orange(),
        ))

    @commands.command(name="hecker")
    @dev_only()
    async def hecker(self, ctx, mention: str, flag: str = None):
        """Give/remove infinite items to a user. Usage: I?hecker @user -y | I?hecker @user -r"""
        if not ctx.guild:
            await ctx.send(embed=create_embed("Error", "This command can only be used in a server.", discord.Color.red()))
            return
        member = await get_member_from_mention(ctx.guild, mention)
        if not member:
            await ctx.send(embed=create_embed("Error", "Invalid user mention.", discord.Color.red()))
            return

        if flag == "-y":
            await db.add_hecker(ctx.guild.id, member.id)
            await ctx.send(embed=create_embed("Hecker Added", f"{member.mention} now has infinite items!", discord.Color.green()))
        elif flag == "-r":
            await db.remove_hecker(ctx.guild.id, member.id)
            await ctx.send(embed=create_embed("Hecker Removed", f"{member.mention} no longer has infinite items.", discord.Color.orange()))
        else:
            await ctx.send(embed=create_embed("Hecker Status", f"{'✅' if await db.is_hecker(ctx.guild.id, member.id) else '❌'} {member.mention} is {'a hecker' if await db.is_hecker(ctx.guild.id, member.id) else 'not a hecker'}.", discord.Color.blue()))

    @commands.command(name="resetserver")
    async def resetserver(self, ctx):
        """Reset all server data (owner only). Usage: I?resetserver"""
        if not ctx.guild:
            await ctx.send(embed=create_embed("Error", "This command can only be used in a server.", discord.Color.red()))
            return
        if not await is_owner_or_authorized(ctx):
            await ctx.send(embed=create_embed("Permission Denied", "Only the bot owner can reset the server.", discord.Color.red()))
            return
        await db.guilds.delete_one({"guild_id": ctx.guild.id})
        await db.users.delete_many({"guild_id": ctx.guild.id})
        await db.active_items.delete_many({"guild_id": ctx.guild.id})
        await db.jail_logs.delete_many({"guild_id": ctx.guild.id})

        await ctx.send(embed=create_embed(
            "Server Reset",
            "All server data has been reset.",
            discord.Color.orange(),
        ))


async def setup(bot):
    await bot.add_cog(DevCog(bot))
