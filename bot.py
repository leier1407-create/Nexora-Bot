import os
from datetime import timedelta

import discord
from discord import app_commands
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

def bot_can_moderate(interaction: discord.Interaction) -> bool:
    return interaction.guild is not None and interaction.guild.me.guild_permissions.moderate_members

@bot.tree.command(name="timeout", description="Gibt einem Mitglied eine Auszeit.")
@app_commands.describe(
    mitglied="Mitglied, das eine Auszeit erhalten soll",
    minuten="Dauer der Auszeit in Minuten, maximal 40320",
    grund="Grund für die Auszeit",
)
@app_commands.checks.has_permissions(moderate_members=True)
async def timeout(
    interaction: discord.Interaction,
    mitglied: discord.Member,
    minuten: app_commands.Range[int, 1, 40320],
    grund: str,
):
    if mitglied.id == interaction.user.id:
        await interaction.response.send_message("Du kannst dich nicht selbst timeouten.", ephemeral=True)
        return
    if mitglied.id == interaction.guild.owner_id:
        await interaction.response.send_message("Der Serverinhaber kann nicht timeoutet werden.", ephemeral=True)
        return
    if mitglied.top_role >= interaction.user.top_role and interaction.user.id != interaction.guild.owner_id:
        await interaction.response.send_message("Die Rolle dieses Mitglieds ist gleich hoch oder höher als deine.", ephemeral=True)
        return
    if not bot_can_moderate(interaction):
        await interaction.response.send_message("Nexora braucht die Berechtigung „Mitglieder moderieren“.", ephemeral=True)
        return

    try:
        await mitglied.timeout(timedelta(minutes=minuten), reason=grund)
        await interaction.response.send_message(
            f"{mitglied.mention} hat einen Timeout für {minuten} Minuten erhalten. Grund: {grund}"
        )
    except discord.Forbidden:
        await interaction.response.send_message(
            "Nexora kann dieses Mitglied nicht timeouten. Prüfe die Bot-Berechtigungen und die Rollenreihenfolge.",
            ephemeral=True,
        )

@bot.tree.command(name="kick", description="Wirft ein Mitglied vom Server.")
@app_commands.describe(mitglied="Mitglied, das entfernt werden soll", grund="Grund für den Kick")
@app_commands.checks.has_permissions(kick_members=True)
async def kick(interaction: discord.Interaction, mitglied: discord.Member, grund: str):
    if mitglied.id == interaction.user.id:
        await interaction.response.send_message("Du kannst dich nicht selbst kicken.", ephemeral=True)
        return
    if mitglied.id == interaction.guild.owner_id:
        await interaction.response.send_message("Der Serverinhaber kann nicht gekickt werden.", ephemeral=True)
        return
    if mitglied.top_role >= interaction.user.top_role and interaction.user.id != interaction.guild.owner_id:
        await interaction.response.send_message("Die Rolle dieses Mitglieds ist gleich hoch oder höher als deine.", ephemeral=True)
        return
    if not interaction.guild.me.guild_permissions.kick_members:
        await interaction.response.send_message("Nexora braucht die Berechtigung „Mitglieder kicken“.", ephemeral=True)
        return

    try:
        await mitglied.kick(reason=grund)
        await interaction.response.send_message(f"{mitglied} wurde gekickt. Grund: {grund}")
    except discord.Forbidden:
        await interaction.response.send_message(
            "Nexora kann dieses Mitglied nicht kicken. Prüfe die Bot-Berechtigungen und die Rollenreihenfolge.",
            ephemeral=True,
        )

@bot.tree.command(name="ban", description="Bannt ein Mitglied vom Server.")
@app_commands.describe(mitglied="Mitglied, das gebannt werden soll", grund="Grund für den Ban")
@app_commands.checks.has_permissions(ban_members=True)
async def ban(interaction: discord.Interaction, mitglied: discord.Member, grund: str):
    if mitglied.id == interaction.user.id:
        await interaction.response.send_message("Du kannst dich nicht selbst bannen.", ephemeral=True)
        return
    if mitglied.id == interaction.guild.owner_id:
        await interaction.response.send_message("Der Serverinhaber kann nicht gebannt werden.", ephemeral=True)
        return
    if mitglied.top_role >= interaction.user.top_role and interaction.user.id != interaction.guild.owner_id:
        await interaction.response.send_message("Die Rolle dieses Mitglieds ist gleich hoch oder höher als deine.", ephemeral=True)
        return
    if not interaction.guild.me.guild_permissions.ban_members:
        await interaction.response.send_message("Nexora braucht die Berechtigung „Mitglieder bannen“.", ephemeral=True)
        return

    try:
        await mitglied.ban(reason=grund)
        await interaction.response.send_message(f"{mitglied} wurde gebannt. Grund: {grund}")
    except discord.Forbidden:
        await interaction.response.send_message(
            "Nexora kann dieses Mitglied nicht bannen. Prüfe die Bot-Berechtigungen und die Rollenreihenfolge.",
            ephemeral=True,
        )

@bot.tree.error
async def on_app_command_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError,
):
    if isinstance(error, app_commands.MissingPermissions):
        message = "Du hast nicht die erforderliche Discord-Berechtigung für diesen Befehl."
    else:
        print(f"Slash-Befehl-Fehler: {error}")
        message = "Beim Ausführen des Befehls ist ein Fehler aufgetreten."

    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)

bot.run(TOKEN)

