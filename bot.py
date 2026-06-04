import discord
from discord.ext import commands
import os

TOKEN = os.environ.get("DISCORD_BOT_TOKEN")

if not TOKEN:
    raise RuntimeError("DISCORD_BOT_TOKEN environment secret is not set.")

intents = discord.Intents.default()
intents.members = True
intents.bans = True

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_member_ban(guild, user):
    try:
        target_user = await bot.fetch_user(user.id)

        await target_user.send(
            "You have been banned from Decimated.\n"
            "If you believe this was a mistake, you can appeal here:\n"
            "https://discord.gg/aKyWGZsrj"
        )

        print(f"DM successfully sent to {target_user}")

    except discord.Forbidden:
        print("Could not send DM (DMs disabled or bot blocked).")

    except Exception as error:
        print(f"An error occurred: {error}")


@bot.event
async def on_ready():
    print(f"Bot is online as {bot.user}")


bot.run(TOKEN)
