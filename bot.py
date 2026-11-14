import logging
import os
from threading import Thread

import discord
from discord.ext import commands
from dotenv import load_dotenv

from dashboard import app

load_dotenv()
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("nexora")

TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise RuntimeError("Die Umgebungsvariable DISCORD_TOKEN fehlt.")

intents = discord.Intents.default()
intents.members = True
intents.message_content = True  # Dafür auch Message Content Intent im Discord Developer Portal aktivieren.

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    log.info("Discord-Bot angemeldet als %s", bot.user)

def run_dashboard():
    port = int(os.getenv("SERVER_PORT", "5000"))
    log.info("Flask-Dashboard startet auf 0.0.0.0:%s", port)
    app.run(host="0.0.0.0", port=port, use_reloader=False)

if __name__ == "__main__":
    Thread(target=run_dashboard, daemon=True).start()
    bot.run(TOKEN)

