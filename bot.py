import discord
from discord.ext import commands
import os

TOKEN = os.environ.get("DISCORD_BOT_TOKEN")

if not TOKEN:
    raise RuntimeError("DISCORD_BOT_TOKEN environment secret is not set.")

intents = discord.Intents.default()
intents.members = True
intents.bans = True

bot = commands.Bot(command_prefix="?", intents=intents)


@bot.command()
@commands.has_permissions(ban_members=True)
async def ban(ctx, member: discord.Member, *, reason: str = "No reason provided."):
    """Usage: !ban @user [reason]"""

    # Send DM first while the user is still in the server
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

    # Now ban the user
    await ctx.guild.ban(member, reason=reason, delete_message_days=0)
    await ctx.send(f"✅ **{member}** has been banned. {dm_status}")
    print(f"Banned {member} | Reason: {reason} | {dm_status}")


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ You don't have permission to use this command.")
    elif isinstance(error, commands.MemberNotFound):
        await ctx.send("❌ Member not found.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("❌ Usage: `!ban @user [reason]`")
    else:
        print(f"Command error: {error}")


@bot.event
async def on_ready():
    print(f"Bot is online as {bot.user}")


bot.run(TOKEN)
