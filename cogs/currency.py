import discord
from discord import app_commands
from discord.ext import commands
from database import db
from config import Config
from utils.helpers import create_embed
from datetime import datetime, timedelta, timezone


class CurrencyCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.daily_cooldowns = {}

    @app_commands.command(name="daily", description="Claim your daily reward")
    async def daily(self, interaction: discord.Interaction):
        key = (interaction.guild.id, interaction.user.id)
        now = datetime.now(timezone.utc)

        if key in self.daily_cooldowns:
            last_claim = self.daily_cooldowns[key]
            if now - last_claim < timedelta(hours=24):
                remaining = timedelta(hours=24) - (now - last_claim)
                hours = int(remaining.total_seconds() // 3600)
                minutes = int((remaining.total_seconds() % 3600) // 60)
                await interaction.response.send_message(
                    embed=create_embed(
                        "Cooldown",
                        f"You already claimed your daily reward!\nCome back in **{hours}h {minutes}m**.",
                        discord.Color.red(),
                    ),
                    ephemeral=True,
                )
                return

        reward = 100
        await db.add_balance(interaction.guild.id, interaction.user.id, reward)
        self.daily_cooldowns[key] = now

        await interaction.response.send_message(
            embed=create_embed(
                "Daily Reward!",
                f"You claimed **{reward}** {Config.CURRENCY_NAME}!\nCome back tomorrow for more.",
                discord.Color.green(),
            )
        )

    @app_commands.command(name="pay", description="Give currency to another user")
    @app_commands.describe(user="The user to pay", amount="Amount to give")
    async def pay(self, interaction: discord.Interaction, user: discord.Member, amount: int):
        if amount <= 0:
            await interaction.response.send_message(
                embed=create_embed("Error", "Amount must be positive.", discord.Color.red()),
                ephemeral=True,
            )
            return

        if user.id == interaction.user.id:
            await interaction.response.send_message(
                embed=create_embed("Error", "You can't pay yourself!", discord.Color.red()),
                ephemeral=True,
            )
            return

        sender_data = await db.get_user(interaction.guild.id, interaction.user.id)
        if sender_data["balance"] < amount:
            await interaction.response.send_message(
                embed=create_embed("Insufficient Funds", f"You only have {sender_data['balance']} {Config.CURRENCY_NAME}.", discord.Color.red()),
                ephemeral=True,
            )
            return

        await db.add_balance(interaction.guild.id, interaction.user.id, -amount)
        await db.add_balance(interaction.guild.id, user.id, amount)

        await interaction.response.send_message(
            embed=create_embed(
                "Payment Sent!",
                f"{interaction.user.mention} paid **{amount}** {Config.CURRENCY_NAME} to {user.mention}!",
                discord.Color.green(),
            )
        )

    @app_commands.command(name="leaderboard", description="View the richest users")
    async def leaderboard(self, interaction: discord.Interaction):
        cursor = db.users.find({"guild_id": interaction.guild.id}).sort("balance", -1).limit(10)
        users = await cursor.to_list(length=10)

        if not users:
            await interaction.response.send_message(
                embed=create_embed("Leaderboard", "No users found yet!", discord.Color.blue()),
                ephemeral=True,
            )
            return

        embed = create_embed("Richest Users", "Top 10 by balance", discord.Color.gold())

        for i, user_data in enumerate(users, 1):
            member = interaction.guild.get_member(user_data["user_id"])
            name = member.display_name if member else f"User {user_data['user_id']}"
            embed.add_field(
                name=f"#{i} {name}",
                value=f"{user_data['balance']} {Config.CURRENCY_NAME}",
                inline=False,
            )

        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="stats", description="View your jail stats")
    async def stats(self, interaction: discord.Interaction):
        user_data = await db.get_user(interaction.guild.id, interaction.user.id)

        embed = create_embed(
            f"{interaction.user.display_name}'s Stats",
            f"Balance: **{user_data['balance']}** {Config.CURRENCY_NAME}",
            discord.Color.blue(),
        )
        embed.add_field(name="Times Jailed", value=user_data.get("total_jailed", 0), inline=True)
        embed.add_field(name="Jails Done", value=user_data.get("total_jails_done", 0), inline=True)

        inv = user_data.get("inventory", {})
        total_items = sum(max(0, v) for v in inv.values())
        embed.add_field(name="Items Owned", value=total_items, inline=True)

        await interaction.response.send_message(embed=embed)


async def setup(bot):
    await bot.add_cog(CurrencyCog(bot))
