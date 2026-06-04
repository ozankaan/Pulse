import discord
from discord.ext import commands
import os

TOKEN = os.environ.get("DISCORD_BOT_TOKEN")

if not TOKEN:
    raise RuntimeError("DISCORD_BOT_TOKEN environment secret is not set.")

intents = discord.Intents.default()

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")

@bot.event
async def on_member_ban(guild, user):
    try:
        await user.send(
            "You have been banned from Decimated.\n\n"
            "Appeal here:\n"
            "https://discord.gg/APPEALSUNUCUN"
        )
        print(f"DM sent to {user}")
    except Exception as e:
        print(f"Could not DM {user}: {e}")

bot.run(TOKEN)
