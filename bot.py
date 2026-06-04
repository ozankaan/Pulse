import discord
from discord.ext import commands
import asyncio
import os

TOKEN = os.environ.get("DISCORD_BOT_TOKEN")

if not TOKEN:
    raise RuntimeError("DISCORD_BOT_TOKEN environment secret is not set.")

intents = discord.Intents.default()
intents.members = True
intents.bans = True

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_member_remove(member):
    # Wait briefly for the audit log to register the ban
    await asyncio.sleep(2)

    try:
        guild = member.guild
        async for entry in guild.audit_logs(limit=5, action=discord.AuditLogAction.ban):
            if entry.target.id == member.id:
                # It was a ban — send the DM
                try:
                    await member.send(
                        "You have been banned from Decimated.\n"
                        "If you believe this was a mistake, you can appeal here:\n"
                        "https://discord.gg/aKyWGZsrj"
                    )
                    print(f"DM successfully sent to {member}")
                except discord.Forbidden:
                    print(f"Could not send DM to {member} (DMs disabled or bot blocked).")
                except Exception as error:
                    print(f"Error sending DM: {error}")
                break

    except Exception as error:
        print(f"Error checking audit log: {error}")


@bot.event
async def on_ready():
    print(f"Bot is online as {bot.user}")


bot.run(TOKEN)
