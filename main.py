import discord
from discord.ext import commands
import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
from dotenv import load_dotenv

# -------------------------
# Load token
# -------------------------
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

# -------------------------
# Intents / Bot
# -------------------------
intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# -------------------------
# Files
# -------------------------
TIMERS_FILE = "timers.json"
SETTINGS_FILE = "settings.json"

# -------------------------
# Global variables
# -------------------------
timers = {}
admins = set()
admin_roles = set()
timer_roles = set()
channel_master_message = {}
active_timer_tasks = {}

admin_master_message_id = None
admin_list_channel_id = 123456789012345678  # Replace with your admin-list channel ID


# -------------------------
# Save / Load Helpers
# -------------------------
def save_settings():
    data = {
        "admins": list(admins),
        "admin_roles": list(admin_roles),
        "timer_roles": list(timer_roles),
        "admin_master_message_id": admin_master_message_id,
    }
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def load_settings():
    global admin_master_message_id

    if not os.path.exists(SETTINGS_FILE):
        return

    with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    admins.update(data.get("admins", []))
    admin_roles.update(data.get("admin_roles", []))
    timer_roles.update(data.get("timer_roles", []))
    admin_master_message_id = data.get("admin_master_message_id")


def save_timers():
    data = {}

    for msg_id, info in timers.items():
        data[str(msg_id)] = {
            "name": info.get("name", "Timer"),
            "end_time": info["end_time"].timestamp(),
            "role_id": info["role_id"],
            "pinged": info["pinged"],
            "channel_id": info["channel_id"],
            "duration": info["duration"].total_seconds(),
            "reminder_msg_id": info.get("reminder_msg_id"),
            "reminder_duration": info["reminder_duration"].total_seconds(),
            "last_reset_msg_id": info.get("last_reset_msg_id"),
            "message_ids": info.get("message_ids", [msg_id]),
        }

    with open(TIMERS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def load_timers():
    if not os.path.exists(TIMERS_FILE):
        return

    with open(TIMERS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    timers.clear()

    for msg_id, info in data.items():
        timers[int(msg_id)] = {
            "name": info.get("name", "Timer"),
            "end_time": datetime.fromtimestamp(info["end_time"], tz=timezone.utc),
            "role_id": info["role_id"],
            "pinged": info.get("pinged", False),
            "channel_id": info["channel_id"],
            "duration": timedelta(seconds=info["duration"]),
            "reminder_msg_id": info.get("reminder_msg_id"),
            "reminder_duration": timedelta(seconds=info.get("reminder_duration", 8 * 3600)),
            "last_reset_msg_id": info.get("last_reset_msg_id"),
            "message_ids": info.get("message_ids", [int(msg_id)]),
        }


# -------------------------
# Permission Helpers
# -------------------------
def is_admin(user_id, guild: discord.Guild = None):
    member = guild.get_member(user_id) if guild else None

    if user_id in admins:
        return True

    if member:
        for role in member.roles:
            if role.id in admin_roles:
                return True

    return False


def can_use_timer(member: discord.Member):
    if is_admin(member.id, member.guild):
        return True

    for role in member.roles:
        if role.id in timer_roles:
            return True

    return False


# -------------------------
# Formatting Helpers
# -------------------------
def format_time(seconds):
    seconds = int(seconds)
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, sec = divmod(remainder, 60)

    time_str = ""
    if days > 0:
        time_str += f"{days}d "
    if hours > 0 or days > 0:
        time_str += f"{hours:02d}h "
    time_str += f"{minutes:02d}m {sec:02d}s"
    return time_str


def make_timer_embed(channel: discord.TextChannel):
    embed = discord.Embed(
        title="⏱ Active Timers",
        color=discord.Color.blurple(),
        timestamp=datetime.now(timezone.utc)
    )

    channel_timers = []

    for message_id, info in timers.items():
        if info["channel_id"] != channel.id:
            continue

        remaining = int((info["end_time"] - datetime.now(timezone.utc)).total_seconds())
        if remaining < 0:
            remaining = 0

        role = channel.guild.get_role(info["role_id"])
        role_text = role.mention if role else "Deleted Role"

        channel_timers.append(
            {
                "message_id": message_id,
                "name": info.get("name", "Timer"),
                "remaining": remaining,
                "role_text": role_text,
                "reminder_hours": info["reminder_duration"].total_seconds() / 3600,
            }
        )

    if not channel_timers:
        embed.description = "No timers running."
        embed.set_footer(text=f"Channel: #{channel.name}")
        return embed

    channel_timers.sort(key=lambda t: t["remaining"])

    for timer in channel_timers:
        embed.add_field(
            name=timer["name"],
            value=(
                f"**Time Left:** {format_time(timer['remaining'])}\n"
                f"**Role:** {timer['role_text']}\n"
                f"**Reminder:** {timer['reminder_hours']:.2f}h\n"
                f"**Delete:** `!delete_timer \"{timer['name']}\"`\n"
                f"**Rename:** `!rename_timer \"{timer['name']}\" \"New Name\"`"
            ),
            inline=False
        )

    embed.set_footer(text=f"Channel: #{channel.name}")
    return embed


# -------------------------
# Utility Helpers
# -------------------------
async def delete_timer_messages(channel, info, original_message_id=None):
    message_ids = set(info.get("message_ids", []))

    if original_message_id is not None:
        message_ids.add(original_message_id)

    for msg_id in message_ids:
        try:
            msg = await channel.fetch_message(msg_id)
            await msg.delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass


async def cleanup_command(ctx, bot_msg=None, delay=5):
    await asyncio.sleep(delay)

    try:
        await ctx.message.delete()
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        pass

    if bot_msg:
        try:
            await bot_msg.delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass


def schedule_cleanup(ctx, bot_msg=None, delay=5):
    bot.loop.create_task(cleanup_command(ctx, bot_msg, delay))


def start_timer_task(message_id, channel):
    existing_task = active_timer_tasks.get(message_id)
    if existing_task and not existing_task.done():
        return

    task = bot.loop.create_task(timer_task(message_id, channel))
    active_timer_tasks[message_id] = task


async def ensure_master_message(channel):
    if channel.id in channel_master_message:
        return

    msg = await channel.send(embed=make_timer_embed(channel))
    channel_master_message[channel.id] = msg.id


# -------------------------
# Events
# -------------------------
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")

    load_settings()

    owner = (await bot.application_info()).owner
    admins.add(owner.id)
    save_settings()

    print(f"Initial admin: {owner}")

    load_timers()
    print(f"Loaded {len(timers)} timer(s) from disk")

    # Recreate per-channel timer boards and restart timer tasks
    active_timer_tasks.clear()
    channels_with_timers = set()

    for message_id, info in timers.items():
        channel = bot.get_channel(info["channel_id"])
        if channel is not None:
            channels_with_timers.add(channel.id)
            start_timer_task(message_id, channel)

    for channel_id in channels_with_timers:
        channel = bot.get_channel(channel_id)
        if channel is not None:
            await ensure_master_message(channel)
            await update_master_message(channel)

    await update_admin_list()


@bot.event
async def on_reaction_add(reaction, user):
    if user.bot:
        return

    if reaction.emoji != "🔄":
        return

    if reaction.message.id not in timers:
        return

    if not can_use_timer(user):
        await reaction.message.channel.send(f"{user.mention} ❌ You cannot reset this timer!")
        return

    timer_info = timers[reaction.message.id]
    channel = reaction.message.channel

    if timer_info.get("reminder_msg_id"):
        try:
            old_msg = await channel.fetch_message(timer_info["reminder_msg_id"])
            await old_msg.delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass
        timer_info["reminder_msg_id"] = None

    if timer_info.get("last_reset_msg_id"):
        try:
            last_reset_msg = await channel.fetch_message(timer_info["last_reset_msg_id"])
            await last_reset_msg.delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass
        timer_info["last_reset_msg_id"] = None

    timer_info["end_time"] = datetime.now(timezone.utc) + timer_info["duration"]
    timer_info["pinged"] = False

    reset_msg = await channel.send(f"{user.mention} 🔄 Timer **{timer_info['name']}** has been reset!")
    timer_info["last_reset_msg_id"] = reset_msg.id
    timer_info.setdefault("message_ids", []).append(reset_msg.id)

    save_timers()
    await update_master_message(channel)


# -------------------------
# Timer Commands
# -------------------------
@bot.command()
async def set_timer(ctx, name: str, hours: float, role: discord.Role, reminder_hours: float = 8):
    if not can_use_timer(ctx.author):
        await ctx.send(f"{ctx.author.mention} ❌ You cannot set timers!")
        return

    if hours <= 0:
        await ctx.send("❌ Timer duration must be greater than 0.")
        return

    if reminder_hours < 0:
        await ctx.send("❌ Reminder time cannot be negative.")
        return

    duration = timedelta(hours=hours)
    reminder_duration = timedelta(hours=reminder_hours)
    end_time = datetime.now(timezone.utc) + duration

    message = await ctx.send(
        f"⏳ Timer **{name}** for {role.mention} started for {hours:g} hour(s). "
        f"Reminder set for {reminder_hours:g} hour(s). React 🔄 to reset."
    )

    timers[message.id] = {
        "name": name,
        "end_time": end_time,
        "role_id": role.id,
        "pinged": False,
        "channel_id": ctx.channel.id,
        "duration": duration,
        "reminder_msg_id": None,
        "reminder_duration": reminder_duration,
        "last_reset_msg_id": None,
        "message_ids": [message.id],
    }

    save_timers()

    await message.add_reaction("🔄")
    await ensure_master_message(ctx.channel)
    start_timer_task(message.id, ctx.channel)
    await update_master_message(ctx.channel)


@bot.command()
async def time_left(ctx):
    if ctx.channel.id not in channel_master_message:
        if any(info["channel_id"] == ctx.channel.id for info in timers.values()):
            await ensure_master_message(ctx.channel)
            await update_master_message(ctx.channel)
        else:
            await ctx.send("No timers are running in this channel.")
        return

    await update_master_message(ctx.channel)


@bot.command()
async def delete_timer(ctx, *, name: str):
    if not can_use_timer(ctx.author):
        msg = await ctx.send(f"{ctx.author.mention} ❌ You cannot delete timers!")
        schedule_cleanup(ctx, msg)
        return

    target_message_id = None
    target_info = None

    for message_id, info in timers.items():
        if info["channel_id"] == ctx.channel.id and info.get("name", "").lower() == name.lower():
            target_message_id = message_id
            target_info = info
            break

    if target_message_id is None:
        msg = await ctx.send(f"❌ No timer named **{name}** found in this channel.")
        schedule_cleanup(ctx, msg)
        return

    await delete_timer_messages(ctx.channel, target_info, original_message_id=target_message_id)

    task = active_timer_tasks.pop(target_message_id, None)
    if task and not task.done():
        task.cancel()

    del timers[target_message_id]
    save_timers()
    await update_master_message(ctx.channel)

    msg = await ctx.send(f"🧹 Timer **{name}** deleted.")
    schedule_cleanup(ctx, msg)


@bot.command()
async def rename_timer(ctx, old_name: str, *, new_name: str):
    if not can_use_timer(ctx.author):
        msg = await ctx.send(f"{ctx.author.mention} ❌ You cannot rename timers!")
        schedule_cleanup(ctx, msg)
        return

    target_message_id = None
    target_info = None

    for message_id, info in timers.items():
        if info["channel_id"] == ctx.channel.id and info.get("name", "").lower() == old_name.lower():
            target_message_id = message_id
            target_info = info
            break

    if target_message_id is None:
        msg = await ctx.send(f"❌ No timer named **{old_name}** found in this channel.")
        schedule_cleanup(ctx, msg)
        return

    old_display = target_info.get("name", "Timer")
    target_info["name"] = new_name
    save_timers()

    try:
        original_msg = await ctx.channel.fetch_message(target_message_id)
        role = ctx.guild.get_role(target_info["role_id"])
        role_text = role.mention if role else "@deleted-role"
        hours_total = target_info["duration"].total_seconds() / 3600
        reminder_hours = target_info["reminder_duration"].total_seconds() / 3600

        await original_msg.edit(
            content=(
                f"⏳ Timer **{new_name}** for {role_text} started for {hours_total:g} hour(s). "
                f"Reminder set for {reminder_hours:g} hour(s). React 🔄 to reset."
            )
        )
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        pass

    await update_master_message(ctx.channel)

    msg = await ctx.send(f"✏️ Renamed timer **{old_display}** to **{new_name}**.")
    schedule_cleanup(ctx, msg)


# -------------------------
# Admin Commands
# -------------------------
@bot.command()
async def add_admin(ctx, member: discord.Member):
    if not is_admin(ctx.author.id, ctx.guild):
        await ctx.send(f"{ctx.author.mention} ❌ Only admins can add new admins!")
        return

    admins.add(member.id)
    save_settings()
    await ctx.send(f"{member.mention} ✅ Added as admin!")
    await update_admin_list()


@bot.command()
async def remove_admin(ctx, member: discord.Member):
    if not is_admin(ctx.author.id, ctx.guild):
        await ctx.send(f"{ctx.author.mention} ❌ Only admins can remove admins!")
        return

    owner = (await bot.application_info()).owner
    if member.id == owner.id:
        await ctx.send("❌ You cannot remove the bot owner from admins.")
        return

    if member.id in admins:
        admins.remove(member.id)
        save_settings()
        await ctx.send(f"{member.mention} ❌ Removed from admins!")
        await update_admin_list()
    else:
        await ctx.send(f"{member.mention} is not an admin!")


@bot.command()
async def admin_list(ctx):
    content = "**Current Bot Admins (Users + Roles):**\n"

    for admin_id in admins:
        member = ctx.guild.get_member(admin_id)
        content += f"- {member.display_name if member else 'Unknown User'}\n"

    for role_id in admin_roles:
        role = ctx.guild.get_role(role_id)
        if role:
            content += f"- Role: {role.name}\n"

    await ctx.send(content)


@bot.command()
async def add_admin_role(ctx, role: discord.Role):
    if not is_admin(ctx.author.id, ctx.guild):
        await ctx.send(f"{ctx.author.mention} ❌ Only admins can add admin roles!")
        return

    admin_roles.add(role.id)
    save_settings()
    await ctx.send(f"{role.mention} ✅ This role is now an admin role!")
    await update_admin_list()


@bot.command()
async def remove_admin_role(ctx, role: discord.Role):
    if not is_admin(ctx.author.id, ctx.guild):
        await ctx.send(f"{ctx.author.mention} ❌ Only admins can remove admin roles!")
        return

    if role.id in admin_roles:
        admin_roles.remove(role.id)
        save_settings()
        await ctx.send(f"{role.mention} ❌ This role is no longer an admin role!")
        await update_admin_list()
    else:
        await ctx.send(f"{role.mention} was not an admin role!")


@bot.command()
async def add_timer_role(ctx, role: discord.Role):
    if not is_admin(ctx.author.id, ctx.guild):
        await ctx.send(f"{ctx.author.mention} ❌ Only admins can add timer roles!")
        return

    timer_roles.add(role.id)
    save_settings()
    await ctx.send(f"{role.mention} ✅ Can now use timers!")


@bot.command()
async def remove_timer_role(ctx, role: discord.Role):
    if not is_admin(ctx.author.id, ctx.guild):
        await ctx.send(f"{ctx.author.mention} ❌ Only admins can remove timer roles!")
        return

    if role.id in timer_roles:
        timer_roles.remove(role.id)
        save_settings()
        await ctx.send(f"{role.mention} ❌ Cannot use timers anymore!")
    else:
        await ctx.send(f"{role.mention} was not a timer role!")


@bot.command()
async def help_timer(ctx):
    embed = discord.Embed(
        title="⏱ Timer Bot Commands",
        description="Here are all available commands:",
        color=discord.Color.green()
    )

    embed.add_field(
        name="Timer Commands",
        value=(
            "`!set_timer \"name\" <hours> @role [reminder_hours]`\n"
            "`!time_left`\n"
            "`!delete_timer \"name\"`\n"
            "`!rename_timer \"old name\" \"new name\"`\n"
            "React 🔄 to reset a timer"
        ),
        inline=False
    )

    embed.add_field(
        name="Admin Commands",
        value=(
            "`!add_admin @member`\n"
            "`!remove_admin @member`\n"
            "`!admin_list`\n"
            "`!add_admin_role @role`\n"
            "`!remove_admin_role @role`"
        ),
        inline=False
    )

    embed.add_field(
        name="Permission Commands",
        value=(
            "`!add_timer_role @role`\n"
            "`!remove_timer_role @role`"
        ),
        inline=False
    )

    embed.add_field(
        name="Test Command",
        value="`!test_timer`",
        inline=False
    )

    embed.set_footer(text="Admins or allowed roles can manage timers.")
    await ctx.send(embed=embed)


@bot.command()
async def test_timer(ctx):
    await ctx.send(f"✅ Timer bot is working! Hello {ctx.author.mention} 👋")


# -------------------------
# Timer Task
# -------------------------
async def timer_task(message_id, channel):
    try:
        while True:
            if message_id not in timers:
                return

            info = timers[message_id]
            remaining = (info["end_time"] - datetime.now(timezone.utc)).total_seconds()

            if not info["pinged"] and info["duration"].total_seconds() >= info["reminder_duration"].total_seconds():
                elapsed = info["duration"].total_seconds() - remaining
                if elapsed >= info["reminder_duration"].total_seconds():
                    role = channel.guild.get_role(info["role_id"])
                    if role:
                        reminder_msg = await channel.send(
                            f"{role.mention} ⏰ Timer **{info['name']}** has reached "
                            f"{info['reminder_duration'].total_seconds() / 3600:.2f} hour(s)!"
                        )
                        info["reminder_msg_id"] = reminder_msg.id
                        info.setdefault("message_ids", []).append(reminder_msg.id)
                        info["pinged"] = True
                        save_timers()

            if remaining <= 0:
                role = channel.guild.get_role(info["role_id"])
                if role:
                    ended_msg = await channel.send(f"{role.mention} ✅ Timer **{info['name']}** has ended!")
                    info.setdefault("message_ids", []).append(ended_msg.id)
                    save_timers()

                await asyncio.sleep(2)
                await delete_timer_messages(channel, info, original_message_id=message_id)

                if message_id in timers:
                    del timers[message_id]
                    save_timers()

                active_timer_tasks.pop(message_id, None)
                await update_master_message(channel)
                return

            await update_master_message(channel)
            await asyncio.sleep(5)
    except asyncio.CancelledError:
        active_timer_tasks.pop(message_id, None)
        raise


# -------------------------
# Master Timer Update
# -------------------------
async def update_master_message(channel):
    if channel.id not in channel_master_message:
        if any(info["channel_id"] == channel.id for info in timers.values()):
            await ensure_master_message(channel)
        else:
            return

    master_id = channel_master_message[channel.id]

    try:
        master_msg = await channel.fetch_message(master_id)
    except discord.NotFound:
        master_msg = await channel.send(embed=make_timer_embed(channel))
        channel_master_message[channel.id] = master_msg.id
        return

    try:
        await master_msg.edit(embed=make_timer_embed(channel), content=None)
    except discord.HTTPException:
        pass


# -------------------------
# Admin List Update
# -------------------------
async def update_admin_list():
    global admin_master_message_id

    channel = bot.get_channel(admin_list_channel_id)
    if channel is None:
        return

    content = "**Current Bot Admins (Users + Roles):**\n"

    for admin_id in admins:
        member = channel.guild.get_member(admin_id)
        content += f"- {member.display_name if member else 'Unknown User'}\n"

    for role_id in admin_roles:
        role = channel.guild.get_role(role_id)
        if role:
            content += f"- Role: {role.name}\n"

    if admin_master_message_id:
        try:
            msg = await channel.fetch_message(admin_master_message_id)
            await msg.edit(content=content)
            return
        except discord.NotFound:
            pass
        except discord.HTTPException:
            return

    try:
        msg = await channel.send(content)
        admin_master_message_id = msg.id
        save_settings()
    except discord.HTTPException:
        pass


# -------------------------
# Run Bot
# -------------------------
if not TOKEN:
    raise ValueError("DISCORD_TOKEN not found in .env file")

bot.run(TOKEN)
