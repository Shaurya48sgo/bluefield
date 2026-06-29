import discord
from discord.ext import commands
import asyncio
from database import db
from utils.checks import dev_only
from utils.helpers import get_role_from_mention, get_channel_from_mention, create_embed

active_wizards = {}
menu_listeners = {}  # guild_id: {user_id: True}

class SetupWizard:
    def __init__(self, bot, ctx):
        self.bot = bot
        self.ctx = ctx
        self.guild = ctx.guild
        self.config = {}
        self.message = None

    async def start(self):
        existing = await db.get_guild_config(self.guild.id)
        self.config = {
            "silence_role": existing.get("silence_role"),
            "log_channel": existing.get("log_channel"),
            "reverse_immunity_roles": list(existing.get("reverse_immunity_roles", [])),
            "reverse_immunity_messages": dict(existing.get("reverse_immunity_messages", {})),
            "full_immunity_roles": list(existing.get("full_immunity_roles", [])),
            "full_immunity_messages": dict(existing.get("full_immunity_messages", {})),
            "role_order": list(existing.get("role_order", [])),
            "setup_complete": False
        }
        await self.show_step_1()

    async def show_step_1(self):
        current = f"<@&{self.config['silence_role']}>" if self.config.get("silence_role") else "Not set"
        embed = create_embed("⚙️ Jail Setup Wizard", f"**Step 1/5: Silence Role**\n\nCurrent: {current}\n\nReply with `@role` to set the silence role.\nReply with `skip` to skip this step.", discord.Color.blue())
        self.message = await self.ctx.send(embed=embed)
        await self.wait_for_msg(self.handle_silence_role)

    async def handle_silence_role(self, msg):
        if msg.content.lower() == "skip":
            await self.show_step_2()
            return
        role = await get_role_from_mention(self.guild, msg.content)
        if not role:
            await self.ctx.send("❌ Invalid role. Please mention a valid role or type `skip`.", delete_after=5)
            await self.wait_for_msg(self.handle_silence_role)
            return
        self.config["silence_role"] = role.id
        await self.show_step_2()

    async def show_step_2(self):
        current = f"<#{self.config['log_channel']}>" if self.config.get("log_channel") else "Not set"
        embed = create_embed("⚙️ Jail Setup Wizard", f"**Step 2/5: Log Channel**\n\nCurrent: {current}\n\nReply with `#channel` to set the log channel.\nReply with `skip` to skip this step.", discord.Color.blue())
        await self.message.edit(embed=embed)
        await self.wait_for_msg(self.handle_log_channel)

    async def handle_log_channel(self, msg):
        if msg.content.lower() == "skip":
            await self.show_step_3()
            return
        channel = await get_channel_from_mention(self.guild, msg.content)
        if not channel:
            await self.ctx.send("❌ Invalid channel. Please mention a valid channel or type `skip`.", delete_after=5)
            await self.wait_for_msg(self.handle_log_channel)
            return
        self.config["log_channel"] = channel.id
        await self.show_step_3()

    async def show_step_3(self):
        roles_list = "\n".join([f"{i+1}. <@&{r}>" for i, r in enumerate(self.config["reverse_immunity_roles"])]) or "None set"
        embed = create_embed("⚙️ Jail Setup Wizard", f"**Step 3/5: Reverse Immunity Roles**\n\n{roles_list}\n\nReply with `@role` to add a role, then reply with the custom message using `[user]` and `[target]`.\nReply with `remove <number>` to remove a role.\nReply with `skip` to skip this step.\nReply with `done` to finish setup.", discord.Color.blue())
        embed.add_field(name="Example", value="`[user] tried to jail [target], but [target] reverse jailed them!`", inline=False)
        await self.message.edit(embed=embed)
        await self.wait_for_msg(self.handle_reverse_immunity)

    async def handle_reverse_immunity(self, msg):
        content = msg.content.lower()

        if content == "skip":
            await self.show_step_4()
            return
        if content == "done":
            await self.finish_setup()
            return
        if content.startswith("remove "):
            try:
                idx = int(content.split(" ", 1)[1]) - 1
                roles = self.config["reverse_immunity_roles"]
                if 0 <= idx < len(roles):
                    removed = roles.pop(idx)
                    self.config["reverse_immunity_messages"].pop(str(removed), None)
                    await self.ctx.send(f"✅ Removed <@&{removed}>.")
                else:
                    await self.ctx.send("❌ Invalid number.")
            except (ValueError, IndexError):
                await self.ctx.send("❌ Usage: `remove 1`")
            await self.show_step_3()
            return

        role = await get_role_from_mention(self.guild, msg.content)
        if not role:
            await self.ctx.send("❌ Invalid role. Mention a valid role, or use `skip` / `done` / `remove <number>`.", delete_after=5)
            await self.wait_for_msg(self.handle_reverse_immunity)
            return

        if role.id in self.config["reverse_immunity_roles"]:
            await self.ctx.send("❌ That role is already added.", delete_after=5)
            await self.wait_for_msg(self.handle_reverse_immunity)
            return

        await self.ctx.send(f"✅ Role selected: {role.mention}\nNow reply with the custom message for this role.")

        def check(m):
            return m.author == self.ctx.author and m.channel == self.ctx.channel

        try:
            msg2 = await self.bot.wait_for("message", check=check, timeout=120)
            self.config["reverse_immunity_roles"].append(role.id)
            self.config["reverse_immunity_messages"][str(role.id)] = msg2.content
            await self.ctx.send(f"✅ Added {role.mention} with custom message.")
            await self.show_step_3()
        except asyncio.TimeoutError:
            await self.ctx.send("⏰ Timed out.")
            await self.show_step_3()

    async def show_step_4(self):
        roles_list = "\n".join([f"{i+1}. <@&{r}>" for i, r in enumerate(self.config["full_immunity_roles"])]) or "None set"
        embed = create_embed("⚙️ Jail Setup Wizard", f"**Step 4/5: Full Immunity Roles**\n\n{roles_list}\n\nReply with `@role` to add a role, then reply with the custom message.\nReply with `remove <number>` to remove a role.\nReply with `skip` to skip this step.\nReply with `done` to finish setup.", discord.Color.blue())
        embed.add_field(name="Example", value="`[user] tried to jail [target], but [target] is untouchable!`", inline=False)
        await self.message.edit(embed=embed)
        await self.wait_for_msg(self.handle_full_immunity)

    async def handle_full_immunity(self, msg):
        content = msg.content.lower()

        if content == "skip":
            await self.show_step_5()
            return
        if content == "done":
            await self.finish_setup()
            return
        if content.startswith("remove "):
            try:
                idx = int(content.split(" ", 1)[1]) - 1
                roles = self.config["full_immunity_roles"]
                if 0 <= idx < len(roles):
                    removed = roles.pop(idx)
                    self.config["full_immunity_messages"].pop(str(removed), None)
                    await self.ctx.send(f"✅ Removed <@&{removed}>.")
                else:
                    await self.ctx.send("❌ Invalid number.")
            except (ValueError, IndexError):
                await self.ctx.send("❌ Usage: `remove 1`")
            await self.show_step_4()
            return

        role = await get_role_from_mention(self.guild, msg.content)
        if not role:
            await self.ctx.send("❌ Invalid role. Mention a valid role, or use `skip` / `done` / `remove <number>`.", delete_after=5)
            await self.wait_for_msg(self.handle_full_immunity)
            return

        if role.id in self.config["full_immunity_roles"]:
            await self.ctx.send("❌ That role is already added.", delete_after=5)
            await self.wait_for_msg(self.handle_full_immunity)
            return

        await self.ctx.send(f"✅ Role selected: {role.mention}\nNow reply with the custom message for this role.")

        def check(m):
            return m.author == self.ctx.author and m.channel == self.ctx.channel

        try:
            msg2 = await self.bot.wait_for("message", check=check, timeout=120)
            self.config["full_immunity_roles"].append(role.id)
            self.config["full_immunity_messages"][str(role.id)] = msg2.content
            await self.ctx.send(f"✅ Added {role.mention} with custom message.")
            await self.show_step_4()
        except asyncio.TimeoutError:
            await self.ctx.send("⏰ Timed out.")
            await self.show_step_4()

    async def show_step_5(self):
        roles_list = "\n".join([f"{i+1}. <@&{r}>" for i, r in enumerate(self.config["role_order"])]) or "None set"
        embed = create_embed("⚙️ Jail Setup Wizard", f"**Step 5/5: Role Order**\n\n{roles_list}\n\nReply with `-y` to set up role order.\nReply with `-n` to skip.\nReply with `done` to finish setup.", discord.Color.blue())
        await self.message.edit(embed=embed)
        await self.wait_for_msg(self.handle_role_order_choice)

    async def handle_role_order_choice(self, msg):
        if msg.content.lower() == "-n":
            await self.finish_setup()
            return
        elif msg.content.lower() == "-y":
            await self.ask_highest_role()
        else:
            await self.ctx.send("❌ Please reply with `-y` or `-n`.", delete_after=5)
            await self.wait_for_msg(self.handle_role_order_choice)

    async def ask_highest_role(self):
        roles_list = "\n".join([f"{i+1}. <@&{r}>" for i, r in enumerate(self.config["role_order"])]) or "None set"
        embed = create_embed("⚙️ Role Order Setup", f"Current order:\n{roles_list}\n\nWhat is the **highest** role?\nReply with `@role`\nType `done` when finished.", discord.Color.blue())
        await self.message.edit(embed=embed)
        await self.wait_for_msg(self.handle_role_order_input)

    async def handle_role_order_input(self, msg):
        if msg.content.lower() == "done":
            await self.finish_setup()
            return

        role = await get_role_from_mention(self.guild, msg.content)
        if not role:
            await self.ctx.send("❌ Invalid role. Mention a valid role or type `done`.", delete_after=5)
            await self.wait_for_msg(self.handle_role_order_input)
            return

        if role.id in self.config["role_order"]:
            await self.ctx.send("❌ That role is already in the order.", delete_after=5)
            await self.wait_for_msg(self.handle_role_order_input)
            return

        self.config["role_order"].append(role.id)
        await self.ask_next_role()

    async def ask_next_role(self):
        roles_list = "\n".join([f"{i+1}. <@&{r}>" for i, r in enumerate(self.config["role_order"])]) or "None set"
        last_role = self.config["role_order"][-1] if self.config["role_order"] else None
        embed = create_embed("⚙️ Role Order Setup", f"Current order:\n{roles_list}\n\nRole below <@&{last_role}>?\nReply with `@role`\nType `done` when finished.", discord.Color.blue())
        await self.message.edit(embed=embed)
        await self.wait_for_msg(self.handle_role_order_input)

    async def finish_setup(self):
        self.config["setup_complete"] = True
        await db.update_guild_config(self.guild.id, self.config)
        embed = create_embed("✅ Setup Complete!", "Your jail system has been configured successfully!\n\nYou can now use `/jail` and `/use` commands.", discord.Color.green())
        await self.message.edit(embed=embed)
        if self.ctx.author.id in active_wizards:
            del active_wizards[self.ctx.author.id]

    async def wait_for_msg(self, callback, timeout=120):
        def check(m):
            return m.author == self.ctx.author and m.channel == self.ctx.channel
        try:
            msg = await self.bot.wait_for("message", check=check, timeout=timeout)
            await callback(msg)
        except asyncio.TimeoutError:
            await self.ctx.send("⏰ Setup timed out. Run `I?jsetup_all` again.")
            if self.ctx.author.id in active_wizards:
                del active_wizards[self.ctx.author.id]

    async def go_to_step(self, step_num):
        if step_num == 1: await self.show_step_1()
        elif step_num == 2: await self.show_step_2()
        elif step_num == 3: await self.show_step_3()
        elif step_num == 4: await self.show_step_4()
        elif step_num == 5: await self.show_step_5()


class SetupCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message):
        """Listen for numbers 1-5 after I?jsetup menu is shown"""
        if message.author.bot:
            return
        
        guild_id = message.guild.id if message.guild else None
        if not guild_id:
            return
        
        # Check if this user has an active menu listener
        key = (guild_id, message.author.id)
        if key not in menu_listeners:
            return
        
        content = message.content.strip()
        if content not in ("1", "2", "3", "4", "5"):
            return
        
        # Remove listener so it doesn't fire again
        del menu_listeners[key]
        
        # Invoke jsetup with the step number
        ctx = await self.bot.get_context(message)
        if ctx.valid:
            await self.jsetup(ctx, step=content)

    @commands.command(name="jsetup_all")
    @dev_only()
    async def jsetup_all(self, ctx):
        """Run the full jail setup wizard"""
        if ctx.author.id in active_wizards:
            await ctx.send("You already have an active setup wizard! Finish it or wait for it to time out.")
            return
        wizard = SetupWizard(self.bot, ctx)
        active_wizards[ctx.author.id] = wizard
        await wizard.start()

    @commands.command(name="jsetup")
    @dev_only()
    async def jsetup(self, ctx, step: str = None):
        """Show setup menu or jump to a step. Usage: I?jsetup then type 1-5, or I?jsetup 3"""
        guild_config = await db.get_guild_config(ctx.guild.id)

        # If step provided directly (e.g., I?jsetup 3)
        if step:
            try:
                step_num = int(step)
                if step_num < 1 or step_num > 5:
                    await ctx.send("❌ Invalid step. Use 1-5.")
                    return
                if ctx.author.id in active_wizards:
                    await ctx.send("You already have an active setup wizard!")
                    return
                wizard = SetupWizard(self.bot, ctx)
                active_wizards[ctx.author.id] = wizard
                existing = await db.get_guild_config(ctx.guild.id)
                wizard.config = {
                    "silence_role": existing.get("silence_role"),
                    "log_channel": existing.get("log_channel"),
                    "reverse_immunity_roles": list(existing.get("reverse_immunity_roles", [])),
                    "reverse_immunity_messages": dict(existing.get("reverse_immunity_messages", {})),
                    "full_immunity_roles": list(existing.get("full_immunity_roles", [])),
                    "full_immunity_messages": dict(existing.get("full_immunity_messages", {})),
                    "role_order": list(existing.get("role_order", [])),
                    "setup_complete": False
                }
                await wizard.go_to_step(step_num)
                return
            except ValueError:
                pass

        # Build menu with ACTUAL values shown
        silence_role = ctx.guild.get_role(guild_config.get("silence_role")) if guild_config.get("silence_role") else None
        log_channel = ctx.guild.get_channel(guild_config.get("log_channel")) if guild_config.get("log_channel") else None
        
        reverse_roles = [ctx.guild.get_role(r).mention for r in guild_config.get("reverse_immunity_roles", []) if ctx.guild.get_role(r)]
        full_roles = [ctx.guild.get_role(r).mention for r in guild_config.get("full_immunity_roles", []) if ctx.guild.get_role(r)]
        role_order = [ctx.guild.get_role(r).mention for r in guild_config.get("role_order", []) if ctx.guild.get_role(r)]

        val1 = silence_role.mention if silence_role else "Not set"
        val2 = log_channel.mention if log_channel else "Not set"
        val3 = "\n".join(reverse_roles) if reverse_roles else "None set"
        val4 = "\n".join(full_roles) if full_roles else "None set"
        val5 = " -> ".join(role_order) if role_order else "None set"

        desc = (
            f"{'✅' if silence_role else '⬜'} **1. Silence Role**\n`{val1}`\n\n"
            f"{'✅' if log_channel else '⬜'} **2. Log Channel**\n`{val2}`\n\n"
            f"{'✅' if reverse_roles else '⬜'} **3. Reverse Immunity Roles**\n`{val3}`\n\n"
            f"{'✅' if full_roles else '⬜'} **4. Full Immunity Roles**\n`{val4}`\n\n"
            f"{'✅' if role_order else '⬜'} **5. Role Order**\n`{val5}`\n\n"
            f"Type a number **1-5** to configure that step.\nOr use `I?jsetup_all` for the full wizard."
        )

        embed = create_embed("⚙️ Jail Setup Menu", desc, discord.Color.blue())
        await ctx.send(embed=embed)

        # Register listener for this user in this guild
        menu_listeners[(ctx.guild.id, ctx.author.id)] = True

    @commands.command(name="jailconfig")
    @dev_only()
    async def jailconfig(self, ctx):
        """View current jail configuration"""
        guild_config = await db.get_guild_config(ctx.guild.id)
        embed = create_embed("📋 Jail Configuration", "Current settings for this server.", discord.Color.blue())

        silence_role = ctx.guild.get_role(guild_config.get("silence_role")) if guild_config.get("silence_role") else None
        log_channel = ctx.guild.get_channel(guild_config.get("log_channel")) if guild_config.get("log_channel") else None

        embed.add_field(name="Silence Role", value=silence_role.mention if silence_role else "Not set", inline=False)
        embed.add_field(name="Log Channel", value=log_channel.mention if log_channel else "Not set", inline=False)

        reverse_roles = [ctx.guild.get_role(r).mention for r in guild_config.get("reverse_immunity_roles", []) if ctx.guild.get_role(r)]
        embed.add_field(name="Reverse Immunity Roles", value="\n".join(reverse_roles) or "None", inline=False)

        full_roles = [ctx.guild.get_role(r).mention for r in guild_config.get("full_immunity_roles", []) if ctx.guild.get_role(r)]
        embed.add_field(name="Full Immunity Roles", value="\n".join(full_roles) or "None", inline=False)

        role_order = [ctx.guild.get_role(r).mention for r in guild_config.get("role_order", []) if ctx.guild.get_role(r)]
        embed.add_field(name="Role Order", value=" -> ".join(role_order) or "None", inline=False)

        embed.add_field(name="Setup Complete", value="✅ Yes" if guild_config.get("setup_complete") else "❌ No", inline=False)
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(SetupCog(bot))
