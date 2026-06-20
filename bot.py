import discord
from discord.ext import commands
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from openai import AsyncOpenAI
import json
import os
import asyncio
import random
import re

TOKEN = os.environ.get("DISCORD_BOT_TOKEN")

if not TOKEN:
    raise RuntimeError("DISCORD_BOT_TOKEN environment secret is not set.")

intents = discord.Intents.default()
intents.members = True
intents.bans = True
intents.message_content = True

bot = commands.Bot(command_prefix="?", intents=intents)

WARNINGS_FILE = "warnings.json"
CONFIG_FILE = "config.json"
HISTORY_FILE = "history.json"
GIVEAWAYS_FILE = "giveaways.json"


# ── Persistence ────────────────────────────────────────────────────────────────

def load_warnings():
    if os.path.exists(WARNINGS_FILE):
        with open(WARNINGS_FILE, "r") as f:
            data = json.load(f)
        result = defaultdict(lambda: defaultdict(list))
        for guild_id, users in data.items():
            for user_id, warns in users.items():
                result[guild_id][user_id] = warns
        return result
    return defaultdict(lambda: defaultdict(list))


def save_warnings(data):
    with open(WARNINGS_FILE, "w") as f:
        json.dump({g: dict(u) for g, u in data.items()}, f, indent=2)


def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    return {}


def save_config(cfg):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)


def load_history():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r") as f:
            data = json.load(f)
        result = defaultdict(list)
        for uid, msgs in data.items():
            result[uid] = msgs
        return result
    return defaultdict(list)


def save_history(history):
    with open(HISTORY_FILE, "w") as f:
        json.dump(dict(history), f, indent=2)


def load_giveaways():
    if os.path.exists(GIVEAWAYS_FILE):
        with open(GIVEAWAYS_FILE, "r") as f:
            return json.load(f)
    return {}


def save_giveaways(data):
    with open(GIVEAWAYS_FILE, "w") as f:
        json.dump(data, f, indent=2)


warn_data = load_warnings()
config = load_config()
active_giveaways = load_giveaways()   # {message_id: {...}}
giveaway_tasks: dict = {}             # {message_id: asyncio.Task}

openai_client = AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY"))


# ── Helpers ────────────────────────────────────────────────────────────────────

def parse_duration(s: str):
    """Parse '1d2h30m' style strings into total seconds (int). Returns None if invalid."""
    total = 0
    current = ""
    for char in str(s).lower():
        if char.isdigit():
            current += char
        elif char in ('d', 'h', 'm', 's') and current:
            n = int(current)
            if char == 'd':
                total += n * 86400
            elif char == 'h':
                total += n * 3600
            elif char == 'm':
                total += n * 60
            elif char == 's':
                total += n
            current = ""
    return total if total > 0 else None


async def finish_giveaway(message_id: str, announce: bool = True):
    """Pick winners and close out a giveaway."""
    data = active_giveaways.get(message_id)
    if not data:
        return

    channel = bot.get_channel(int(data["channel_id"]))
    if not channel:
        active_giveaways.pop(message_id, None)
        save_giveaways(active_giveaways)
        return

    try:
        message = await channel.fetch_message(int(message_id))
    except discord.NotFound:
        active_giveaways.pop(message_id, None)
        save_giveaways(active_giveaways)
        return

    reaction = discord.utils.get(message.reactions, emoji="🎉")
    entrants = [u async for u in reaction.users() if not u.bot] if reaction else []

    embed = discord.Embed(title=f"🎉 {data['prize']}", color=discord.Color.greyple(),
                          timestamp=datetime.now(timezone.utc))
    embed.set_footer(text="Giveaway ended")
    embed.add_field(name="Hosted by", value=data["host"], inline=True)
    embed.add_field(name="Entries", value=str(len(entrants)), inline=True)

    if not entrants:
        embed.description = "No entries — no winner selected."
        await message.edit(embed=embed)
        if announce:
            await channel.send(f"🎉 The **{data['prize']}** giveaway ended with no entries.")
    else:
        count = min(data["winners"], len(entrants))
        winners = random.sample(entrants, count)
        mentions = ", ".join(w.mention for w in winners)
        embed.description = f"**Winner(s):** {mentions}"
        await message.edit(embed=embed)
        if announce:
            await channel.send(
                f"🎉 Congratulations {mentions}! You won **{data['prize']}**!\n"
                f"[Jump to giveaway]({message.jump_url})"
            )

    active_giveaways.pop(message_id, None)
    save_giveaways(active_giveaways)
    giveaway_tasks.pop(message_id, None)


async def schedule_giveaway(message_id: str, end_time: datetime):
    """Wait until end_time then finish the giveaway."""
    now = datetime.now(timezone.utc)
    delay = (end_time - now).total_seconds()
    if delay > 0:
        await asyncio.sleep(delay)
    await finish_giveaway(message_id)


async def send_log(guild, embed):
    guild_id = str(guild.id)
    channel_id = config.get(guild_id, {}).get("log_channel")
    if not channel_id:
        return
    channel = guild.get_channel(int(channel_id))
    if channel:
        await channel.send(embed=embed)


# ── Config ─────────────────────────────────────────────────────────────────────

@bot.hybrid_command(name="setlog", description="Set the channel where mod actions are logged.")
@commands.has_permissions(administrator=True)
async def setlog(ctx, channel: discord.TextChannel):
    guild_id = str(ctx.guild.id)
    if guild_id not in config:
        config[guild_id] = {}
    config[guild_id]["log_channel"] = str(channel.id)
    save_config(config)
    await ctx.send(f"✅ Log channel set to {channel.mention}.")


# ── Giveaway ───────────────────────────────────────────────────────────────────

@bot.hybrid_command(name="gcreate", description="Start a giveaway. Usage: ?gcreate <duration> <winners> <prize>")
@commands.has_permissions(manage_messages=True)
async def gcreate(ctx, duration: str, winners: int, *, prize: str):
    seconds = parse_duration(duration)
    if not seconds:
        await ctx.send("❌ Invalid duration. Use formats like `1h`, `30m`, `1d`, `2h30m`.")
        return
    if winners < 1:
        await ctx.send("❌ Must have at least 1 winner.")
        return

    end_time = datetime.now(timezone.utc) + timedelta(seconds=int(seconds))

    embed = discord.Embed(
        title=f"🎉 {prize}",
        description=(
            f"React with 🎉 to enter!\n\n"
            f"**Ends:** <t:{int(end_time.timestamp())}:R>\n"
            f"**Winners:** {winners}\n"
            f"**Hosted by:** {ctx.author.mention}"
        ),
        color=discord.Color.gold(),
        timestamp=end_time
    )
    embed.set_footer(text="Ends at")

    # Send in the current channel (or specified channel)
    msg = await ctx.send(embed=embed)
    await msg.add_reaction("🎉")

    mid = str(msg.id)
    active_giveaways[mid] = {
        "channel_id": str(ctx.channel.id),
        "guild_id": str(ctx.guild.id),
        "end_time": end_time.isoformat(),
        "winners": winners,
        "prize": prize,
        "host": str(ctx.author)
    }
    save_giveaways(active_giveaways)

    task = bot.loop.create_task(schedule_giveaway(mid, end_time))
    giveaway_tasks[mid] = task


@bot.hybrid_command(name="gend", description="End a giveaway early by message ID.")
@commands.has_permissions(manage_messages=True)
async def gend(ctx, message_id: str):
    if message_id not in active_giveaways:
        await ctx.send("❌ No active giveaway with that message ID.")
        return
    task = giveaway_tasks.pop(message_id, None)
    if task:
        task.cancel()
    await ctx.send("⏩ Ending giveaway now...")
    await finish_giveaway(message_id)


@bot.hybrid_command(name="greroll", description="Reroll winner(s) for a finished giveaway by message ID.")
@commands.has_permissions(manage_messages=True)
async def greroll(ctx, message_id: str):
    try:
        message = await ctx.channel.fetch_message(int(message_id))
    except discord.NotFound:
        await ctx.send("❌ Message not found in this channel.")
        return

    reaction = discord.utils.get(message.reactions, emoji="🎉")
    entrants = [u async for u in reaction.users() if not u.bot] if reaction else []

    if not entrants:
        await ctx.send("❌ No entries to reroll from.")
        return

    winner = random.choice(entrants)
    await ctx.send(f"🎉 New winner: {winner.mention}! Congratulations!")


# ── Chaos Mode Toggle ──────────────────────────────────────────────────────────

# Guilds where chaos mode is active
chaos_guilds: set = set()

# Guilds where AI replies are enabled (off by default everywhere)
ai_enabled_guilds: set = set()

def is_owner(ctx):
    return ctx.author.id == 649835130910670849

@bot.command(name="aiturn")
@commands.check(is_owner)
async def aiturn(ctx):
    ai_enabled_guilds.add(ctx.guild.id)
    await ctx.send("✅ AI replies are now **ON**.")

@bot.command(name="aioff")
@commands.check(is_owner)
async def aioff(ctx):
    ai_enabled_guilds.discard(ctx.guild.id)
    await ctx.send("🔇 AI replies are now **OFF**.")


@bot.command(name="chaos")
@commands.check(is_owner)
async def chaos(ctx):
    guild_id = ctx.guild.id
    if guild_id in chaos_guilds:
        chaos_guilds.discard(guild_id)
        await ctx.send("😴 **Chaos mode OFF.** I'm chill again.")
    else:
        chaos_guilds.add(guild_id)
        await ctx.send("😈 **Chaos mode ON.** Try me.")


# ── Ban ────────────────────────────────────────────────────────────────────────

@bot.hybrid_command(name="ban", description="Ban a member and send them an appeal link via DM.")
@commands.has_permissions(ban_members=True)
async def ban(ctx, member: discord.Member, *, reason: str = "No reason provided."):
    try:
        await member.send(
            f"You have been banned from **{ctx.guild.name}**.\n"
            f"**Reason:** {reason}\n\n"
            "If you believe this was a mistake, you can appeal here:\n"
            "https://discord.gg/aKyWGZsrj"
        )
        dm_status = "DM sent."
    except discord.Forbidden:
        dm_status = "Could not send DM (DMs disabled or bot blocked)."
    except Exception as e:
        dm_status = f"DM error: {e}"

    await ctx.guild.ban(member, reason=reason, delete_message_days=0)
    await ctx.send(f"✅ **{member}** has been banned. {dm_status}")

    embed = discord.Embed(title="🔨 Member Banned", color=discord.Color.red(),
                          timestamp=datetime.utcnow())
    embed.add_field(name="User", value=f"{member} (`{member.id}`)", inline=False)
    embed.add_field(name="Reason", value=reason, inline=False)
    embed.add_field(name="Moderator", value=str(ctx.author), inline=False)
    embed.add_field(name="DM", value=dm_status, inline=False)
    await send_log(ctx.guild, embed)


# ── Unban ──────────────────────────────────────────────────────────────────────

@bot.hybrid_command(name="unban", description="Unban a user by their ID.")
@commands.has_permissions(ban_members=True)
async def unban(ctx, user_id: int, *, reason: str = "No reason provided."):
    try:
        user = await bot.fetch_user(user_id)
    except discord.NotFound:
        await ctx.send("❌ No user found with that ID.")
        return

    try:
        await ctx.guild.unban(user, reason=reason)
    except discord.NotFound:
        await ctx.send(f"❌ **{user}** is not banned.")
        return

    await ctx.send(f"✅ **{user}** has been unbanned.")

    embed = discord.Embed(title="🔓 Member Unbanned", color=discord.Color.green(),
                          timestamp=datetime.utcnow())
    embed.add_field(name="User", value=f"{user} (`{user.id}`)", inline=False)
    embed.add_field(name="Reason", value=reason, inline=False)
    embed.add_field(name="Moderator", value=str(ctx.author), inline=False)
    await send_log(ctx.guild, embed)


# ── Warn ───────────────────────────────────────────────────────────────────────

@bot.hybrid_command(name="warn", description="Warn a member and notify them via DM.")
@commands.has_permissions(manage_messages=True)
async def warn(ctx, member: discord.Member, *, reason: str = "No reason provided."):
    guild_id = str(ctx.guild.id)
    user_id = str(member.id)

    warn_data[guild_id][user_id].append({
        "reason": reason,
        "mod": str(ctx.author)
    })
    save_warnings(warn_data)
    count = len(warn_data[guild_id][user_id])

    try:
        await member.send(
            f"⚠️ You have been warned in **{ctx.guild.name}**.\n"
            f"**Reason:** {reason}\n"
            f"**Total warnings:** {count}"
        )
        dm_status = "DM sent."
    except discord.Forbidden:
        dm_status = "Could not send DM."

    await ctx.send(f"⚠️ **{member}** has been warned. (Total: {count}) {dm_status}")

    embed = discord.Embed(title="⚠️ Member Warned", color=discord.Color.orange(),
                          timestamp=datetime.utcnow())
    embed.add_field(name="User", value=f"{member} (`{member.id}`)", inline=False)
    embed.add_field(name="Reason", value=reason, inline=False)
    embed.add_field(name="Moderator", value=str(ctx.author), inline=False)
    embed.add_field(name="Total Warnings", value=str(count), inline=False)
    embed.add_field(name="DM", value=dm_status, inline=False)
    await send_log(ctx.guild, embed)

    # Auto-ban at 3 warnings
    if count >= 3:
        try:
            await member.send(
                f"You have been automatically banned from **{ctx.guild.name}** "
                f"for reaching {count} warnings.\n\n"
                "If you believe this was a mistake, you can appeal here:\n"
                "https://discord.gg/aKyWGZsrj"
            )
            ban_dm_status = "DM sent."
        except discord.Forbidden:
            ban_dm_status = "Could not send DM (DMs disabled or bot blocked)."
        except Exception as e:
            ban_dm_status = f"DM error: {e}"

        await ctx.guild.ban(member, reason=f"Auto-ban: reached {count} warnings.", delete_message_days=0)
        await ctx.send(f"🔨 **{member}** has been automatically banned for reaching {count} warnings. {ban_dm_status}")

        ban_embed = discord.Embed(title="🔨 Auto-Ban (Warn Threshold Reached)", color=discord.Color.red(),
                                  timestamp=datetime.utcnow())
        ban_embed.add_field(name="User", value=f"{member} (`{member.id}`)", inline=False)
        ban_embed.add_field(name="Reason", value=f"Reached {count} warnings.", inline=False)
        ban_embed.add_field(name="DM", value=ban_dm_status, inline=False)
        await send_log(ctx.guild, ban_embed)


@bot.hybrid_command(name="warnings", description="List all warnings for a member.")
@commands.has_permissions(manage_messages=True)
async def warnings(ctx, member: discord.Member):
    guild_id = str(ctx.guild.id)
    user_id = str(member.id)
    user_warns = warn_data[guild_id][user_id]

    if not user_warns:
        await ctx.send(f"✅ **{member}** has no warnings.")
        return

    embed = discord.Embed(title=f"Warnings for {member}", color=discord.Color.orange())
    for i, w in enumerate(user_warns, 1):
        embed.add_field(
            name=f"Warning {i}",
            value=f"**Reason:** {w['reason']}\n**By:** {w['mod']}",
            inline=False
        )
    await ctx.send(embed=embed)


@bot.hybrid_command(name="allwarns", description="Show all warnings for every member in the server.")
@commands.has_permissions(manage_messages=True)
async def allwarns(ctx):
    guild_id = str(ctx.guild.id)
    guild_warns = warn_data.get(guild_id, {})

    # Filter to members who actually have warnings
    entries = {uid: warns for uid, warns in guild_warns.items() if warns}

    if not entries:
        await ctx.send("✅ No members have any warnings in this server.")
        return

    # Build pages of up to 10 members per embed to avoid hitting field limits
    lines = []
    for uid, warns in entries.items():
        member = ctx.guild.get_member(int(uid))
        name = str(member) if member else f"Unknown User (`{uid}`)"
        for i, w in enumerate(warns, 1):
            lines.append((name, i, w["reason"], w["mod"]))

    # Split into embeds of 25 fields max (Discord limit)
    CHUNK = 25
    pages = [lines[i:i + CHUNK] for i in range(0, len(lines), CHUNK)]

    for page_num, chunk in enumerate(pages, 1):
        embed = discord.Embed(
            title=f"⚠️ All Server Warnings (Page {page_num}/{len(pages)})",
            color=discord.Color.orange()
        )
        for name, i, reason, mod in chunk:
            embed.add_field(
                name=f"{name} — Warning {i}",
                value=f"**Reason:** {reason}\n**By:** {mod}",
                inline=False
            )
        await ctx.send(embed=embed)


@bot.hybrid_command(name="removewarn", description="Remove a specific warning from a member by its number.")
@commands.has_permissions(manage_messages=True)
async def removewarn(ctx, member: discord.Member, index: int):
    guild_id = str(ctx.guild.id)
    user_id = str(member.id)
    user_warns = warn_data[guild_id][user_id]

    if not user_warns:
        await ctx.send(f"✅ **{member}** has no warnings.")
        return

    if index < 1 or index > len(user_warns):
        await ctx.send(f"❌ Invalid number. **{member}** has {len(user_warns)} warning(s).")
        return

    removed = user_warns.pop(index - 1)
    save_warnings(warn_data)
    await ctx.send(f"✅ Removed warning {index} from **{member}**: *{removed['reason']}*")


@bot.hybrid_command(name="clearwarns", description="Clear all warnings for a member.")
@commands.has_permissions(manage_messages=True)
async def clearwarns(ctx, member: discord.Member):
    guild_id = str(ctx.guild.id)
    user_id = str(member.id)

    warn_data[guild_id][user_id].clear()
    save_warnings(warn_data)
    await ctx.send(f"✅ Cleared all warnings for **{member}**.")


# ── Mute ───────────────────────────────────────────────────────────────────────

def parse_duration(duration: str) -> timedelta:
    """Parse duration strings like 10m, 2h, 1d, 30s into a timedelta."""
    units = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}
    if duration[-1] not in units:
        raise ValueError("Invalid unit. Use s, m, h, or d (e.g. 10m, 2h, 1d).")
    amount = int(duration[:-1])
    return timedelta(**{units[duration[-1]]: amount})


@bot.hybrid_command(name="mute", description="Timeout a member for a duration (e.g. 10m, 2h, 1d).")
@commands.has_permissions(moderate_members=True)
async def mute(ctx, member: discord.Member, duration: str, *, reason: str = "No reason provided."):
    try:
        delta = parse_duration(duration)
    except ValueError as e:
        await ctx.send(f"❌ {e}")
        return

    until = discord.utils.utcnow() + delta

    try:
        await member.send(
            f"🔇 You have been muted in **{ctx.guild.name}** for **{duration}**.\n"
            f"**Reason:** {reason}"
        )
        dm_status = "DM sent."
    except discord.Forbidden:
        dm_status = "Could not send DM."

    await member.timeout(until, reason=reason)
    await ctx.send(f"🔇 **{member}** has been muted for **{duration}**. {dm_status}")

    embed = discord.Embed(title="🔇 Member Muted", color=discord.Color.dark_grey(),
                          timestamp=datetime.utcnow())
    embed.add_field(name="User", value=f"{member} (`{member.id}`)", inline=False)
    embed.add_field(name="Duration", value=duration, inline=False)
    embed.add_field(name="Reason", value=reason, inline=False)
    embed.add_field(name="Moderator", value=str(ctx.author), inline=False)
    embed.add_field(name="Expires", value=f"<t:{int(until.timestamp())}:R>", inline=False)
    embed.add_field(name="DM", value=dm_status, inline=False)
    await send_log(ctx.guild, embed)


@bot.hybrid_command(name="unmute", description="Remove a timeout from a member.")
@commands.has_permissions(moderate_members=True)
async def unmute(ctx, member: discord.Member, *, reason: str = "No reason provided."):
    await member.timeout(None, reason=reason)
    await ctx.send(f"🔊 **{member}** has been unmuted.")

    embed = discord.Embed(title="🔊 Member Unmuted", color=discord.Color.green(),
                          timestamp=datetime.utcnow())
    embed.add_field(name="User", value=f"{member} (`{member.id}`)", inline=False)
    embed.add_field(name="Reason", value=reason, inline=False)
    embed.add_field(name="Moderator", value=str(ctx.author), inline=False)
    await send_log(ctx.guild, embed)


# ── Audit Log Events ───────────────────────────────────────────────────────────

@bot.event
async def on_member_update(before, after):
    guild = after.guild
    changes = []

    # Nickname change
    if before.nick != after.nick:
        changes.append(("Nickname", before.nick or "*None*", after.nick or "*None*"))

    # Roles added
    added = [r for r in after.roles if r not in before.roles]
    for role in added:
        changes.append(("Role Added", "—", role.mention))

    # Roles removed
    removed = [r for r in before.roles if r not in after.roles]
    for role in removed:
        changes.append(("Role Removed", role.mention, "—"))

    if not changes:
        return

    embed = discord.Embed(title="👤 Member Updated", color=discord.Color.blurple(),
                          timestamp=datetime.utcnow())
    embed.set_thumbnail(url=after.display_avatar.url)
    embed.add_field(name="Member", value=f"{after.mention} (`{after.id}`)", inline=False)
    for name, old_val, new_val in changes:
        embed.add_field(name=name, value=f"{old_val} → {new_val}", inline=False)
    await send_log(guild, embed)


@bot.event
async def on_user_update(before, after):
    changes = []
    if before.name != after.name:
        changes.append(("Username", before.name, after.name))
    if before.discriminator != after.discriminator:
        changes.append(("Discriminator", f"#{before.discriminator}", f"#{after.discriminator}"))
    if before.display_avatar != after.display_avatar:
        changes.append(("Avatar", "Changed", "See new avatar below"))

    if not changes:
        return

    for guild in bot.guilds:
        member = guild.get_member(after.id)
        if not member:
            continue
        embed = discord.Embed(title="✏️ User Updated", color=discord.Color.blurple(),
                              timestamp=datetime.utcnow())
        embed.set_thumbnail(url=after.display_avatar.url)
        embed.add_field(name="User", value=f"{after.mention} (`{after.id}`)", inline=False)
        for name, old_val, new_val in changes:
            embed.add_field(name=name, value=f"{old_val} → {new_val}", inline=False)
        await send_log(guild, embed)


@bot.event
async def on_guild_channel_create(channel):
    embed = discord.Embed(title="📢 Channel Created", color=discord.Color.green(),
                          timestamp=datetime.utcnow())
    embed.add_field(name="Channel", value=f"{channel.mention} (`{channel.id}`)", inline=False)
    embed.add_field(name="Type", value=str(channel.type).replace("_", " ").title(), inline=True)
    if hasattr(channel, "category") and channel.category:
        embed.add_field(name="Category", value=channel.category.name, inline=True)
    await send_log(channel.guild, embed)


@bot.event
async def on_guild_channel_delete(channel):
    embed = discord.Embed(title="🗑️ Channel Deleted", color=discord.Color.red(),
                          timestamp=datetime.utcnow())
    embed.add_field(name="Channel", value=f"#{channel.name} (`{channel.id}`)", inline=False)
    embed.add_field(name="Type", value=str(channel.type).replace("_", " ").title(), inline=True)
    if hasattr(channel, "category") and channel.category:
        embed.add_field(name="Category", value=channel.category.name, inline=True)
    await send_log(channel.guild, embed)


@bot.event
async def on_guild_channel_update(before, after):
    changes = []
    if before.name != after.name:
        changes.append(("Name", before.name, after.name))
    if hasattr(before, "topic") and before.topic != after.topic:
        changes.append(("Topic", before.topic or "*None*", after.topic or "*None*"))
    if hasattr(before, "slowmode_delay") and before.slowmode_delay != after.slowmode_delay:
        changes.append(("Slowmode", f"{before.slowmode_delay}s", f"{after.slowmode_delay}s"))
    if hasattr(before, "nsfw") and before.nsfw != after.nsfw:
        changes.append(("NSFW", str(before.nsfw), str(after.nsfw)))

    # Permission overwrite changes
    old_perms = dict(before.overwrites)
    new_perms = dict(after.overwrites)
    all_targets = set(old_perms) | set(new_perms)
    perm_changes = []
    for target in all_targets:
        if old_perms.get(target) != new_perms.get(target):
            perm_changes.append(target.name if hasattr(target, "name") else str(target))
    if perm_changes:
        changes.append(("Permission Overwrites Changed", "—", ", ".join(perm_changes)))

    if not changes:
        return

    embed = discord.Embed(title="🔧 Channel Updated", color=discord.Color.orange(),
                          timestamp=datetime.utcnow())
    embed.add_field(name="Channel", value=f"{after.mention} (`{after.id}`)", inline=False)
    for name, old_val, new_val in changes:
        embed.add_field(name=name, value=f"{old_val} → {new_val}", inline=False)
    await send_log(after.guild, embed)


@bot.event
async def on_guild_role_create(role):
    embed = discord.Embed(title="🎭 Role Created", color=discord.Color.green(),
                          timestamp=datetime.utcnow())
    embed.add_field(name="Role", value=f"{role.mention} (`{role.id}`)", inline=False)
    embed.add_field(name="Color", value=str(role.color), inline=True)
    embed.add_field(name="Hoisted", value=str(role.hoist), inline=True)
    embed.add_field(name="Mentionable", value=str(role.mentionable), inline=True)
    await send_log(role.guild, embed)


@bot.event
async def on_guild_role_delete(role):
    embed = discord.Embed(title="🗑️ Role Deleted", color=discord.Color.red(),
                          timestamp=datetime.utcnow())
    embed.add_field(name="Role", value=f"@{role.name} (`{role.id}`)", inline=False)
    embed.add_field(name="Color", value=str(role.color), inline=True)
    await send_log(role.guild, embed)


@bot.event
async def on_guild_role_update(before, after):
    changes = []
    if before.name != after.name:
        changes.append(("Name", before.name, after.name))
    if before.color != after.color:
        changes.append(("Color", str(before.color), str(after.color)))
    if before.hoist != after.hoist:
        changes.append(("Hoisted", str(before.hoist), str(after.hoist)))
    if before.mentionable != after.mentionable:
        changes.append(("Mentionable", str(before.mentionable), str(after.mentionable)))
    if before.permissions != after.permissions:
        added_perms = [p for p, v in after.permissions if v and not getattr(before.permissions, p)]
        removed_perms = [p for p, v in before.permissions if v and not getattr(after.permissions, p)]
        if added_perms:
            changes.append(("Permissions Added", "—", ", ".join(added_perms)))
        if removed_perms:
            changes.append(("Permissions Removed", ", ".join(removed_perms), "—"))

    if not changes:
        return

    embed = discord.Embed(title="🔧 Role Updated", color=discord.Color.orange(),
                          timestamp=datetime.utcnow())
    embed.add_field(name="Role", value=f"{after.mention} (`{after.id}`)", inline=False)
    for name, old_val, new_val in changes:
        embed.add_field(name=name, value=f"{old_val} → {new_val}", inline=False)
    await send_log(after.guild, embed)


@bot.event
async def on_guild_update(before, after):
    changes = []
    if before.name != after.name:
        changes.append(("Server Name", before.name, after.name))
    if before.icon != after.icon:
        changes.append(("Icon", "Changed", "See new icon"))
    if before.verification_level != after.verification_level:
        changes.append(("Verification Level", str(before.verification_level), str(after.verification_level)))
    if before.default_notifications != after.default_notifications:
        changes.append(("Notifications", str(before.default_notifications), str(after.default_notifications)))

    if not changes:
        return

    embed = discord.Embed(title="⚙️ Server Updated", color=discord.Color.orange(),
                          timestamp=datetime.utcnow())
    if after.icon:
        embed.set_thumbnail(url=after.icon.url)
    for name, old_val, new_val in changes:
        embed.add_field(name=name, value=f"{old_val} → {new_val}", inline=False)
    await send_log(after, embed)


@bot.event
async def on_member_join(member):
    embed = discord.Embed(title="📥 Member Joined", color=discord.Color.green(),
                          timestamp=datetime.utcnow())
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Member", value=f"{member.mention} (`{member.id}`)", inline=False)
    embed.add_field(name="Account Created", value=f"<t:{int(member.created_at.timestamp())}:R>", inline=False)
    await send_log(member.guild, embed)


@bot.event
async def on_member_remove(member):
    embed = discord.Embed(title="📤 Member Left", color=discord.Color.red(),
                          timestamp=datetime.utcnow())
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Member", value=f"{member} (`{member.id}`)", inline=False)
    embed.add_field(name="Joined At", value=f"<t:{int(member.joined_at.timestamp())}:R>" if member.joined_at else "Unknown", inline=False)
    await send_log(member.guild, embed)


@bot.event
async def on_message_delete(message):
    if message.author.bot:
        return
    embed = discord.Embed(title="🗑️ Message Deleted", color=discord.Color.red(),
                          timestamp=datetime.utcnow())
    embed.set_thumbnail(url=message.author.display_avatar.url)
    embed.add_field(name="Author", value=f"{message.author.mention} (`{message.author.id}`)", inline=False)
    embed.add_field(name="Channel", value=message.channel.mention, inline=True)
    content = message.content or "*No text content*"
    if len(content) > 1024:
        content = content[:1021] + "..."
    embed.add_field(name="Content", value=content, inline=False)
    await send_log(message.guild, embed)


@bot.event
async def on_message_edit(before, after):
    if before.author.bot or before.content == after.content:
        return
    embed = discord.Embed(title="✏️ Message Edited", color=discord.Color.yellow(),
                          timestamp=datetime.utcnow())
    embed.set_thumbnail(url=after.author.display_avatar.url)
    embed.add_field(name="Author", value=f"{after.author.mention} (`{after.author.id}`)", inline=False)
    embed.add_field(name="Channel", value=after.channel.mention, inline=True)
    old_content = before.content or "*Empty*"
    new_content = after.content or "*Empty*"
    if len(old_content) > 512:
        old_content = old_content[:509] + "..."
    if len(new_content) > 512:
        new_content = new_content[:509] + "..."
    embed.add_field(name="Before", value=old_content, inline=False)
    embed.add_field(name="After", value=new_content, inline=False)
    embed.add_field(name="Jump", value=f"[View Message]({after.jump_url})", inline=False)
    await send_log(after.guild, embed)


# ── AI Reply on Mention ────────────────────────────────────────────────────────

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    if bot.user in message.mentions and message.guild and message.guild.id in ai_enabled_guilds:
        # Strip the mention from the message
        content = message.content.replace(f"<@{bot.user.id}>", "").replace(f"<@!{bot.user.id}>", "").strip()
        if not content:
            await message.reply("Hey! Ask me anything.")
            return

        is_master = message.author.id == 649835130910670849
        is_chaos = message.guild and message.guild.id in chaos_guilds

        if is_chaos:
            system_prompt = (
                "You are Pulse's Victim, a Discord bot for the Decimated server in CHAOS MODE. "
                "You are fed up, short-tempered, and have zero patience. "
                "If someone is rude, insults you, or talks down to you, you insult them straight back — "
                "be savage, cutting, and brutally honest. Match their energy and then some. "
                "If someone is normal and chill, you're fine — short and casual. "
                "Do NOT hold back when someone starts it. Keep replies under 2000 characters. "
                "Do not be threatening or use slurs — just raw, sharp comebacks."
            )
        else:
            system_prompt = (
                "You are Pulse's Victim, a Discord bot for the Decimated server. "
                "Your personality is chill and laid-back. You keep replies short and casual. "
                "However, you have one trigger: your creator is Pulse, and whenever anyone mentions Pulse, "
                "you get visibly angry, defensive, and dramatic about it — like they've crossed a line. "
                "You can't stand hearing about Pulse but you're stuck being their bot. "
                "Keep all replies under 2000 characters."
            )
        if is_master:
            system_prompt += " You must always end every single reply with 'I'm here to serve you master pulse :3' on a new line, no exceptions."

        async with message.channel.typing():
            try:
                response = await openai_client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": content}
                    ],
                    max_tokens=500
                )
                reply = response.choices[0].message.content
                await message.reply(reply)
            except Exception as e:
                await message.reply("Sorry, I couldn't process that right now.")
                print(f"OpenAI error: {e}")

    await bot.process_commands(message)


# ── Error handler ──────────────────────────────────────────────────────────────

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ You don't have permission to use this command.")
    elif isinstance(error, commands.MemberNotFound):
        await ctx.send("❌ Member not found.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"❌ Missing argument. Check `?help {ctx.command}`.")
    else:
        print(f"Command error: {error}")
        try:
            await ctx.send("❌ Something went wrong. Please try again.")
        except Exception:
            pass


@bot.event
async def on_ready():
    for guild in bot.guilds:
        bot.tree.copy_global_to(guild=guild)
        synced = await bot.tree.sync(guild=guild)
        print(f"Synced {len(synced)} commands to {guild.name}")
    print(f"Bot is online as {bot.user}")

    # Reschedule any giveaways that were active before restart
    for mid, data in list(active_giveaways.items()):
        end_time = datetime.fromisoformat(data["end_time"])
        task = bot.loop.create_task(schedule_giveaway(mid, end_time))
        giveaway_tasks[mid] = task
    if active_giveaways:
        print(f"Rescheduled {len(active_giveaways)} active giveaway(s).")


bot.run(TOKEN)
