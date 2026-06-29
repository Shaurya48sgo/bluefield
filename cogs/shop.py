import discord
from discord import app_commands
from discord.ext import commands
from database import db
from config import Config
from utils.helpers import create_embed


class ShopCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="shop", description="View the item shop")
    async def shop(self, interaction: discord.Interaction):
        embed = create_embed(
            "Item Shop",
            "Purchase items to use against your enemies!",
            discord.Color.gold(),
        )

        for item_id, item_data in Config.SHOP_ITEMS.items():
            if item_id.startswith("silence"):
                embed.add_field(
                    name=f"{item_data['name']} — {item_data['price']} {Config.CURRENCY_NAME}",
                    value=f"Duration: {item_data['duration']}min | Pro: {'Yes' if item_data['pro'] else 'No'}",
                    inline=False,
                )
            else:
                embed.add_field(
                    name=f"{item_data['name']} — {item_data['price']} {Config.CURRENCY_NAME}",
                    value=item_data.get("description", "No description"),
                    inline=False,
                )

        user_data = await db.get_user(interaction.guild.id, interaction.user.id)
        embed.set_footer(text=f"Your balance: {user_data['balance']} {Config.CURRENCY_NAME}")

        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="buy", description="Buy an item from the shop")
    @app_commands.describe(item="The item to buy", quantity="How many to buy (default 1)")
    @app_commands.choices(item=[
        app_commands.Choice(name="Silence 2min", value="silence_2min"),
        app_commands.Choice(name="Silence 5min", value="silence_5min"),
        app_commands.Choice(name="Silence Pro 2min", value="silence_pro_2min"),
        app_commands.Choice(name="Silence Pro 5min", value="silence_pro_5min"),
        app_commands.Choice(name="Immunity", value="immunity"),
        app_commands.Choice(name="Full Immunity", value="full_immunity"),
        app_commands.Choice(name="Reverse", value="reverse"),
        app_commands.Choice(name="Divine Eye", value="divine_eye"),
        app_commands.Choice(name="Invis Pot", value="invis_pot"),
    ])
    async def buy(self, interaction: discord.Interaction, item: str, quantity: int = 1):
        if quantity < 1:
            await interaction.response.send_message(
                embed=create_embed("Error", "Quantity must be at least 1.", discord.Color.red()),
                ephemeral=True,
            )
            return

        item_data = Config.SHOP_ITEMS[item]
        total_price = item_data["price"] * quantity

        user_data = await db.get_user(interaction.guild.id, interaction.user.id)
        if user_data["balance"] < total_price:
            await interaction.response.send_message(
                embed=create_embed(
                    "Insufficient Funds",
                    f"You need {total_price} {Config.CURRENCY_NAME} but only have {user_data['balance']}.",
                    discord.Color.red(),
                ),
                ephemeral=True,
            )
            return

        await db.add_balance(interaction.guild.id, interaction.user.id, -total_price)
        await db.add_to_inventory(interaction.guild.id, interaction.user.id, item, quantity)

        await interaction.response.send_message(
            embed=create_embed(
                "Purchase Successful!",
                f"You bought **{quantity}x {item_data['name']}** for **{total_price}** {Config.CURRENCY_NAME}!",
                discord.Color.green(),
            )
        )

    @app_commands.command(name="inventory", description="View your inventory")
    async def inventory(self, interaction: discord.Interaction):
        user_data = await db.get_user(interaction.guild.id, interaction.user.id)
        inv = user_data.get("inventory", {})

        if not inv or all(v <= 0 for v in inv.values()):
            await interaction.response.send_message(
                embed=create_embed("Inventory", "Your inventory is empty! Use `/shop` to buy items.", discord.Color.blue()),
                ephemeral=True,
            )
            return

        embed = create_embed("Your Inventory", f"Balance: {user_data['balance']} {Config.CURRENCY_NAME}", discord.Color.blue())

        for item_id, quantity in inv.items():
            if quantity > 0 and item_id in Config.SHOP_ITEMS:
                item_data = Config.SHOP_ITEMS[item_id]
                embed.add_field(
                    name=f"{item_data['name']} x{quantity}",
                    value=item_data.get("description", f"Item ID: {item_id}"),
                    inline=True,
                )

        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="balance", description="Check your balance")
    async def balance(self, interaction: discord.Interaction):
        user_data = await db.get_user(interaction.guild.id, interaction.user.id)
        await interaction.response.send_message(
            embed=create_embed(
                "Balance",
                f"You have **{user_data['balance']}** {Config.CURRENCY_NAME}.",
                discord.Color.gold(),
            )
        )


async def setup(bot):
    await bot.add_cog(ShopCog(bot))
