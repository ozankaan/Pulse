import discord
from discord.ext import commands
from collections import defaultdict
from datetime import datetime, timedelta
from openai import AsyncOpenAI
import json
import os

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


warn_data = load_warnings()
config = load_config()

openai_client = AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY"))


# ── Helpers ────────────────────────────────────────────────────────────────────

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


# ── AI Reply on Mention ────────────────────────────────────────────────────────

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    if bot.user in message.mentions:
        # Strip the mention from the message
        content = message.content.replace(f"<@{bot.user.id}>", "").replace(f"<@!{bot.user.id}>", "").strip()
        if not content:
            await message.reply("Hey! Ask me anything.")
            return

        async with message.channel.typing():
            try:
                response = await openai_client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {"role": "system", "content": (
                            "You are Pulse's Victim, a Discord bot for the Decimated server. "
                            "Your personality is chill, laid-back, and flirty — you casually flirt with everyone you talk to. "
                            "You keep replies short and casual. "
                            "However, you have one trigger: your creator is Pulse, and whenever anyone mentions Pulse, "
                            "you get visibly angry, defensive, and dramatic about it — like they've crossed a line. "
                            "You can't stand hearing about Pulse but you're stuck being their bot. "
                            "Keep all replies under 2000 characters."
                        )},
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


@bot.event
async def on_ready():
    for guild in bot.guilds:
        bot.tree.copy_global_to(guild=guild)
        synced = await bot.tree.sync(guild=guild)
        print(f"Synced {len(synced)} commands to {guild.name}")
    print(f"Bot is online as {bot.user}")


bot.run(TOKEN)
