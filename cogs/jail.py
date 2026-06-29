import discord
from discord import app_commands
from discord.ext import commands
import asyncio
from datetime import datetime, timedelta
from database import db
from config import Config
from utils.checks import dev_only
from utils.helpers import (
    get_role_from_mention, get_member_from_mention,
    has_active_item, format_time, create_embed
)

class JailCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.active_jails = {}

    @app_commands.command(name="jail", description="Jail a user with your purchased silence power")
    @app_commands.describe(user="The user to jail", power="The silence power to use")
    @app_commands.choices(power=[
        app_commands.Choice(name="Silence 2min", value="silence_2min"),
        app_commands.Choice(name="Silence 5min", value="silence_5min"),
        app_commands.Choice(name="Silence Pro 2min", value="silence_pro_2min"),
        app_commands.Choice(name="Silence Pro 5min", value="silence_pro_5min"),
    ])
    async def jail_slash(self, interaction: discord.Interaction, user: discord.Member, power: app_commands.Choice[str]):
        await self.execute_jail(interaction, user, power.value)

    async def execute_jail(self, interaction, target, power_id):
        guild = interaction.guild
        author = interaction.user

        guild_config = await db.get_guild_config(guild.id)
        if not guild_config.get("setup_complete"):
            await interaction.response.send_message(
                embed=create_embed("Not Set Up", "Jail system is not set up yet. Use `I?jsetup` or `I?jsetup_all` to configure it.", discord.Color.red()),
                ephemeral=True
            )
            return

        silence_role_id = guild_config.get("silence_role")
        if not silence_role_id:
            await interaction.response.send_message(
                embed=create_embed("Error", "Silence role not configured.", discord.Color.red()),
                ephemeral=True
            )
            return

        silence_role = guild.get_role(silence_role_id)
        if not silence_role:
            await interaction.response.send_message(
                embed=create_embed("Error", "Silence role not found. Please reconfigure.", discord.Color.red()),
                ephemeral=True
            )
            return

        if target.id == author.id:
            await interaction.response.send_message(
                embed=create_embed("Error", "You cannot jail yourself!", discord.Color.red()),
                ephemeral=True
            )
            return

        if target.id == self.bot.user.id:
            await interaction.response.send_message(
                embed=create_embed("Error", "You cannot jail the bot!", discord.Color.red()),
                ephemeral=True
            )
            return

        user_data = await db.get_user(guild.id, author.id)
        inventory = user_data.get("inventory", {})
        if inventory.get(power_id, 0) <= 0:
            await interaction.response.send_message(
                embed=create_embed("No Item", f"You don't have any **{Config.SHOP_ITEMS[power_id]['name']}** in your inventory. Buy it from the shop first!", discord.Color.red()),
                ephemeral=True
            )
            return

        is_pro = Config.SHOP_ITEMS[power_id]["pro"]
        duration = Config.SHOP_ITEMS[power_id]["duration"]

        role_order = guild_config.get("role_order", []) or []
        if role_order:
            author_top_role = max(author.roles, key=lambda r: r.position, default=None)
            target_top_role = max(target.roles, key=lambda r: r.position, default=None)

            if author_top_role and target_top_role:
                author_rank = next((i for i, r in enumerate(role_order) if r == author_top_role.id), -1)
                target_rank = next((i for i, r in enumerate(role_order) if r == target_top_role.id), -1)

                if target_rank != -1 and author_rank != -1 and target_rank < author_rank:
                    await interaction.response.send_message(
                        embed=create_embed("Error", "You cannot jail someone with a higher role rank!", discord.Color.red()),
                        ephemeral=True
                    )
                    return

        # Check FULL immunity (blocks everything, no reverse)
        full_immunity_roles = guild_config.get("full_immunity_roles", []) or []
        if any(role.id in full_immunity_roles for role in target.roles):
            await db.remove_from_inventory(guild.id, author.id, power_id)
            log_msg = f"<@{author.id}> tried bribe and jail <@{target.id}> but failed"
            await self.log_jail(guild, log_msg, author, target, "blocked_full_immunity", power_id)
            await interaction.response.send_message(
                embed=create_embed("Blocked!", f"{target.mention} has full immunity. Your jail was wasted!", discord.Color.orange())
            )
            return

        if await has_active_item(guild.id, target.id, "full_immunity"):
            await db.remove_from_inventory(guild.id, author.id, power_id)
            log_msg = f"<@{author.id}> tried bribe and jail <@{target.id}> but failed"
            await self.log_jail(guild, log_msg, author, target, "blocked_full_immunity_item", power_id)
            await interaction.response.send_message(
                embed=create_embed("Blocked!", f"{target.mention} has active Full Immunity. Your jail was wasted!", discord.Color.orange())
            )
            return

        # Check REVERSE immunity roles
        reverse_immunity_roles = guild_config.get("reverse_immunity_roles", []) or []
        reverse_messages = guild_config.get("reverse_immunity_messages", {}) or {}

        if any(role.id in reverse_immunity_roles for role in target.roles):
            await db.remove_from_inventory(guild.id, author.id, power_id)
            matched_role_id = next((r for r in reverse_immunity_roles if any(role.id == r for role in target.roles)), None)
            custom_msg = reverse_messages.get(str(matched_role_id), "[user] tried to jail [target], but [target] reverse jailed them!")
            custom_msg = custom_msg.replace("[user]", f"<@{author.id}>").replace("[target]", f"<@{target.id}>")
            await self.apply_silence(guild, author, silence_role, duration, power_id)
            log_msg = f"<@{author.id}> tried to jail <@{target.id}>, but <@{target.id}> pulled out an **UNO REVERSE**"
            await self.log_jail(guild, log_msg, author, target, "reversed_role", power_id)
            await interaction.response.send_message(
                embed=create_embed("UNO REVERSE!", custom_msg, discord.Color.gold())
            )
            return

        # Check active reverse item (only works on non-pro)
        if not is_pro and await has_active_item(guild.id, target.id, "reverse"):
            await db.remove_from_inventory(guild.id, author.id, power_id)
            await self.apply_silence(guild, author, silence_role, duration, power_id)
            log_msg = f"<@{author.id}> tried to jail <@{target.id}>, but <@{target.id}> pulled out an **UNO REVERSE**"
            await self.log_jail(guild, log_msg, author, target, "reversed_item", power_id)
            await interaction.response.send_message(
                embed=create_embed("UNO REVERSE!", f"{target.mention} used Reverse! {author.mention} got jailed instead!", discord.Color.gold())
            )
            return

        # Check IMMUNITY (blocks non-pro, pro jails the user using immunity)
        # FIX: Use .get() with default empty list, handle None
        immunity_roles = guild_config.get("immunity_roles", []) or []
        if not is_pro:
            if any(role.id in immunity_roles for role in target.roles):
                await db.remove_from_inventory(guild.id, author.id, power_id)
                log_msg = f"<@{author.id}> tried bribe and jail <@{target.id}> but failed"
                await self.log_jail(guild, log_msg, author, target, "blocked_immunity", power_id)
                await interaction.response.send_message(
                    embed=create_embed("Blocked!", f"{target.mention} has immunity. Your jail was wasted!", discord.Color.orange())
                )
                return

            if await has_active_item(guild.id, target.id, "immunity"):
                await db.remove_from_inventory(guild.id, author.id, power_id)
                log_msg = f"<@{author.id}> tried bribe and jail <@{target.id}> but failed"
                await self.log_jail(guild, log_msg, author, target, "blocked_immunity_item", power_id)
                await interaction.response.send_message(
                    embed=create_embed("Blocked!", f"{target.mention} has active Immunity. Your jail was wasted!", discord.Color.orange())
                )
                return
        else:
            has_immunity = False
            if any(role.id in immunity_roles for role in target.roles):
                has_immunity = True
            if await has_active_item(guild.id, target.id, "immunity"):
                has_immunity = True

            if has_immunity:
                await db.remove_from_inventory(guild.id, author.id, power_id)
                await self.apply_silence(guild, author, silence_role, duration, power_id)
                log_msg = f"<@{author.id}> bribed the commissioner and jailed <@{target.id}> for {format_time(duration)}"
                await self.log_jail(guild, log_msg, author, target, "pro_reflected_immunity", power_id)
                await interaction.response.send_message(
                    embed=create_embed("Reflected!", f"{target.mention} had Immunity, but Pro Jail reflected! {author.mention} got jailed instead!", discord.Color.gold())
                )
                return

        # SUCCESS - Apply jail to target
        await db.remove_from_inventory(guild.id, author.id, power_id)
        await self.apply_silence(guild, target, silence_role, duration, power_id)
        await db.update_user(guild.id, author.id, {"$inc": {"total_jails_done": 1}})
        await db.update_user(guild.id, target.id, {"$inc": {"total_jailed": 1}})

        if is_pro:
            log_msg = f"<@{author.id}> bribed the commissioner and jailed <@{target.id}> for {format_time(duration)}"
        else:
            log_msg = f"<@{author.id}> bribed the policemen and jailed <@{target.id}> for {format_time(duration)}"

        await self.log_jail(guild, log_msg, author, target, "success", power_id)
        await interaction.response.send_message(
            embed=create_embed("Jailed!", f"{target.mention} has been silenced for **{format_time(duration)}**!", discord.Color.green())
        )

    async def apply_silence(self, guild, member, role, duration, power_id):
        try:
            await member.add_roles(role, reason=f"Jailed: {power_id}")
        except discord.Forbidden:
            return False

        if guild.id not in self.active_jails:
            self.active_jails[guild.id] = {}

        if member.id in self.active_jails[guild.id]:
            self.active_jails[guild.id][member.id].cancel()

        async def remove_silence():
            await asyncio.sleep(duration)
            try:
                if member and role in member.roles:
                    await member.remove_roles(role, reason="Jail duration expired")
            except:
                pass
            if guild.id in self.active_jails and member.id in self.active_jails[guild.id]:
                del self.active_jails[guild.id][member.id]

        task = asyncio.create_task(remove_silence())
        self.active_jails[guild.id][member.id] = task
        return True

    async def log_jail(self, guild, message, author, target, result_type, power_id):
        guild_config = await db.get_guild_config(guild.id)
        log_channel_id = guild_config.get("log_channel")
        if not log_channel_id:
            return

        log_channel = guild.get_channel(log_channel_id)
        if not log_channel:
            return

        author_has_invis = await has_active_item(guild.id, author.id, "invis_pot")

        if author_has_invis:
            message = message.replace(f"<@{author.id}>", "**Someone**")

        embed = create_embed("Jail Log", message, discord.Color.purple())
        embed.add_field(name="Result", value=result_type, inline=True)
        embed.add_field(name="Power", value=Config.SHOP_ITEMS[power_id]["name"], inline=True)
        embed.timestamp = datetime.utcnow()

        try:
            await log_channel.send(embed=embed)
        except:
            pass

        await db.log_jail(guild.id, {
            "guild_id": guild.id,
            "author_id": author.id,
            "target_id": target.id,
            "power_id": power_id,
            "result": result_type,
            "timestamp": datetime.utcnow()
        })

async def setup(bot):
    await bot.add_cog(JailCog(bot))
