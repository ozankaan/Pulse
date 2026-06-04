import discord
from discord.ext import commands
from collections import defaultdict
import os

TOKEN = os.environ.get("DISCORD_BOT_TOKEN")

if not TOKEN:
    raise RuntimeError("DISCORD_BOT_TOKEN environment secret is not set.")

intents = discord.Intents.default()
intents.members = True
intents.bans = True
intents.message_content = True

bot = commands.Bot(command_prefix="?", intents=intents)

# warnings[guild_id][user_id] = [{"reason": ..., "mod": ...}, ...]
warnings = defaultdict(lambda: defaultdict(list))


# ── Ban ────────────────────────────────────────────────────────────────────────

@bot.command()
@commands.has_permissions(ban_members=True)
async def ban(ctx, member: discord.Member, *, reason: str = "No reason provided."):
    """?ban @user [reason]"""
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
    print(f"Banned {member} | Reason: {reason} | {dm_status}")


# ── Warn ───────────────────────────────────────────────────────────────────────

@bot.command()
@commands.has_permissions(manage_messages=True)
async def warn(ctx, member: discord.Member, *, reason: str = "No reason provided."):
    """?warn @user [reason]"""
    warnings[ctx.guild.id][member.id].append({
        "reason": reason,
        "mod": str(ctx.author)
    })
    count = len(warnings[ctx.guild.id][member.id])

    # DM the warned user
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
    print(f"Warned {member} | Reason: {reason} | Total: {count}")


@bot.command()
@commands.has_permissions(manage_messages=True)
async def warnings(ctx, member: discord.Member):
    """?warnings @user — list a user's warnings"""
    user_warns = warnings[ctx.guild.id][member.id]

    if not user_warns:
        await ctx.send(f"✅ **{member}** has no warnings.")
        return

    embed = discord.Embed(
        title=f"Warnings for {member}",
        color=discord.Color.orange()
    )
    for i, w in enumerate(user_warns, 1):
        embed.add_field(
            name=f"Warning {i}",
            value=f"**Reason:** {w['reason']}\n**By:** {w['mod']}",
            inline=False
        )
    await ctx.send(embed=embed)


@bot.command()
@commands.has_permissions(manage_messages=True)
async def clearwarns(ctx, member: discord.Member):
    """?clearwarns @user — clear all warnings"""
    warnings[ctx.guild.id][member.id].clear()
    await ctx.send(f"✅ Cleared all warnings for **{member}**.")
    print(f"Cleared warnings for {member}")


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
    print(f"Bot is online as {bot.user}")


bot.run(TOKEN)
