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
        user = await bot.fetch_user(user.id)
        await user.send(
            "You have been banned from Decimated.\n"
            "Appeal here: https://discord.gg/aKyWGZsrj"
        )
    except Exception as e:
        print(e)

bot.run(TOKEN)
