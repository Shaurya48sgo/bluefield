import discord
from discord import app_commands
from discord.ext import commands
from database import db
from config import Config
from utils.helpers import has_active_item, create_embed
from datetime import datetime


class ItemsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="use", description="Use an item from your inventory")
    @app_commands.describe(item="The item to use")
    @app_commands.choices(item=[
        app_commands.Choice(name="Immunity", value="immunity"),
        app_commands.Choice(name="Full Immunity", value="full_immunity"),
        app_commands.Choice(name="Reverse", value="reverse"),
        app_commands.Choice(name="Divine Eye", value="divine_eye"),
        app_commands.Choice(name="Invis Pot", value="invis_pot"),
    ])
    async def use(self, interaction: discord.Interaction, item: str):
        guild = interaction.guild
        user = interaction.user

        user_data = await db.get_user(guild.id, user.id)
        is_hecker = await db.is_hecker(guild.id, user.id)
        inv = user_data.get("inventory", {})
        if not is_hecker and inv.get(item, 0) <= 0:
            await interaction.response.send_message(
                embed=create_embed(
                    "No Item",
                    f"You don't have any **{Config.SHOP_ITEMS[item]['name']}** in your inventory.",
                    discord.Color.red(),
                ),
                ephemeral=True,
            )
            return

        if item == "immunity":
            if not is_hecker:
                await db.remove_from_inventory(guild.id, user.id, item)
            await db.activate_item(guild.id, user.id, "immunity", 24)

            await interaction.response.send_message(
                embed=create_embed(
                    "Immunity Activated!",
                    "You are now immune to **non-Pro** jails for **24 hours**!\n⚠️ Pro jails will reflect back on you instead!",
                    discord.Color.green(),
                )
            )

        elif item == "full_immunity":
            if not is_hecker:
                await db.remove_from_inventory(guild.id, user.id, item)
            await db.activate_item(guild.id, user.id, "full_immunity", 24)

            await interaction.response.send_message(
                embed=create_embed(
                    "Full Immunity Activated!",
                    "You are now immune to **ALL** jails for **24 hours**!",
                    discord.Color.green(),
                )
            )

        elif item == "reverse":
            if not is_hecker:
                await db.remove_from_inventory(guild.id, user.id, item)
            await db.activate_item(guild.id, user.id, "reverse", 24)

            await interaction.response.send_message(
                embed=create_embed(
                    "Reverse Activated!",
                    "You will now **reverse** non-Pro jails back to the sender for **24 hours**!\n⚠️ Pro jails will jail you instead!",
                    discord.Color.gold(),
                )
            )

        elif item == "divine_eye":
            success = True if is_hecker else await db.consume_divine_eye(guild.id, user.id)
            if not success:
                await interaction.response.send_message(
                    embed=create_embed("Error", "Could not consume Divine Eye.", discord.Color.red()),
                    ephemeral=True,
                )
                return

            all_active = await db.get_all_active_items(guild.id)

            if not all_active:
                await interaction.response.send_message(
                    embed=create_embed("Divine Eye", "No one currently has any active protections.", discord.Color.purple()),
                )
                return

            embed = create_embed("👁️ Divine Eye", "Here are all active protections:", discord.Color.purple())

            protections = {"immunity": [], "full_immunity": [], "reverse": [], "invis_pot": []}
            for active in all_active:
                p_type = active.get("type")
                if p_type in protections:
                    member = guild.get_member(active.get("user_id"))
                    if member:
                        protections[p_type].append(member.mention)

            for p_type, users in protections.items():
                if users:
                    name = p_type.replace("_", " ").title()
                    embed.add_field(name=name, value="\n".join(users), inline=False)

            await interaction.response.send_message(embed=embed)

        elif item == "invis_pot":
            if not is_hecker:
                await db.remove_from_inventory(guild.id, user.id, item)
            await db.activate_item(guild.id, user.id, "invis_pot", 24)

            await interaction.response.send_message(
                embed=create_embed(
                    "Invis Pot Activated!",
                    "You will now be hidden in everyone logs for **24 hours**! Your name will appear as **Someone**.",
                    discord.Color.dark_purple(),
                )
            )


async def setup(bot):
    await bot.add_cog(ItemsCog(bot))
