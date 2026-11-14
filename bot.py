import os

import discord
from discord.ext import commands

TOKEN = os.getenv("DISCORD_TOKEN")

if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN fehlt in den Umgebungsvariablen des Hostings.")

intents = discord.Intents.default()
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"Nexora ist online als {bot.user}.")

    try:
        synced = await bot.tree.sync()
        print(f"{len(synced)} Slash-Befehle synchronisiert.")
    except discord.DiscordException as error:
        print(f"Fehler beim Synchronisieren der Befehle: {error}")

@bot.tree.command(name="ping", description="Prüft, ob Nexora online ist.")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("Nexora ist online.", ephemeral=True)

bot.run(TOKEN)
