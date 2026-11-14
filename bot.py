import os
from datetime import timedelta

import discord
from discord import app_commands
from discord.ext import commands

TOKEN = os.getenv("DISCORD_TOKEN")
LOG_CHANNEL_ID = os.getenv("MOD_LOG_CHANNEL_ID")

if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN fehlt in den Umgebungsvariablen des Hostings.")

intents = discord.Intents.default()
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

async def log_action(
    guild: discord.Guild,
    action: str,
    moderator: discord.abc.User,
    member: discord.abc.User,
    reason: str,
    duration: str | None = None,
) -> None:
    if not LOG_CHANNEL_ID:
        return

    try:
        channel_id = int(LOG_CHANNEL_ID)
    except ValueError:
        print("MOD_LOG_CHANNEL_ID muss eine gültige Kanal-ID sein.")
        return

    channel = guild.get_channel(channel_id)
    if not isinstance(channel, discord.TextChannel):
        print(f"Moderations-Logkanal {channel_id} wurde auf diesem Server nicht gefunden.")
        return

    embed = discord.Embed(
        title=f"Moderation: {action}",
        color=discord.Color.orange(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Mitglied", value=f"{member.mention} (`{member.id}`)", inline=False)
    embed.add_field(name="Moderator", value=f"{moderator.mention} (`{moderator.id}`)", inline=False)
    embed.add_field(name="Grund", value=reason[:1024], inline=False)
    if duration:
        embed.add_field(name="Dauer", value=duration, inline=True)

    try:
        await channel.send(embed=embed)
    except discord.Forbidden:
        print("Nexora darf im Moderations-Logkanal keine Nachrichten oder Embeds senden.")
    except discord.HTTPException as error:
        print(f"Moderationsprotokoll konnte nicht gesendet werden: {error}")

@bot.event
async def on_ready():
    print(f"Nexora ist online als {bot.user}.")
    try:
        synced = await bot.tree.sync()
        print(f"{len(synced)} Slash-Befehle synchronisiert.")
    except discord.DiscordException as error:
        print(f"Fehler beim Synchronisieren der Befehle: {error}")

def target_is_higher(interaction: discord.Interaction, member: discord.Member) -> bool:
    return (
        member.top_role >= interaction.user.top_role
        and interaction.user.id != interaction.guild.owner_id
    )

@bot.tree.command(name="warn", description="Verwarnt ein Mitglied.")
@app_commands.describe(mitglied="Mitglied, das verwarnt werden soll", grund="Grund für die Verwarnung")
@app_commands.checks.has_permissions(moderate_members=True)
async def warn(interaction: discord.Interaction, mitglied: discord.Member, grund: str):
    if mitglied.id == interaction.user.id:
        await interaction.response.send_message("Du kannst dich nicht selbst verwarnen.", ephemeral=True)
        return
    if mitglied.id == interaction.guild.owner_id:
        await interaction.response.send_message("Der Serverinhaber kann nicht verwarnt werden.", ephemeral=True)
        return
    if target_is_higher(interaction, mitglied):
        await interaction.response.send_message("Die Rolle dieses Mitglieds ist gleich hoch oder höher als deine.", ephemeral=True)
        return

    await interaction.response.send_message(f"{mitglied.mention} wurde verwarnt. Grund: {grund}")
    await log_action(interaction.guild, "Verwarnung", interaction.user, mitglied, grund)

    try:
        await mitglied.send(
            f"Du wurdest auf **{interaction.guild.name}** verwarnt.\nGrund: {grund}"
        )
    except discord.Forbidden:
        pass

@bot.tree.command(name="timeout", description="Gibt einem Mitglied eine Auszeit.")
@app_commands.describe(
    mitglied="Mitglied, das eine Auszeit erhalten soll",
    minuten="Dauer in Minuten, maximal 40320",
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
    if target_is_higher(interaction, mitglied):
        await interaction.response.send_message("Die Rolle dieses Mitglieds ist gleich hoch oder höher als deine.", ephemeral=True)
        return
    if not interaction.guild.me.guild_permissions.moderate_members:
        await interaction.response.send_message("Nexora braucht die Berechtigung „Mitglieder moderieren“.", ephemeral=True)
        return

    try:
        await mitglied.timeout(timedelta(minutes=minuten), reason=grund)
        await interaction.response.send_message(
            f"{mitglied.mention} hat einen Timeout für {minuten} Minuten erhalten. Grund: {grund}"
        )
        await log_action(interaction.guild, "Timeout", interaction.user, mitglied, grund, f"{minuten} Minuten")
    except discord.Forbidden:
        await interaction.response.send_message(
            "Nexora kann dieses Mitglied nicht timeouten. Prüfe die Berechtigungen und Rollenreihenfolge.",
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
    if target_is_higher(interaction, mitglied):
        await interaction.response.send_message("Die Rolle dieses Mitglieds ist gleich hoch oder höher als deine.", ephemeral=True)
        return
    if not interaction.guild.me.guild_permissions.kick_members:
        await interaction.response.send_message("Nexora braucht die Berechtigung „Mitglieder kicken“.", ephemeral=True)
        return

    try:
        await mitglied.kick(reason=grund)
        await interaction.response.send_message(f"{mitglied} wurde gekickt. Grund: {grund}")
        await log_action(interaction.guild, "Kick", interaction.user, mitglied, grund)
    except discord.Forbidden:
        await interaction.response.send_message(
            "Nexora kann dieses Mitglied nicht kicken. Prüfe die Berechtigungen und Rollenreihenfolge.",
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
    if target_is_higher(interaction, mitglied):
        await interaction.response.send_message("Die Rolle dieses Mitglieds ist gleich hoch oder höher als deine.", ephemeral=True)
        return
    if not interaction.guild.me.guild_permissions.ban_members:
        await interaction.response.send_message("Nexora braucht die Berechtigung „Mitglieder bannen“.", ephemeral=True)
        return

    try:
        await mitglied.ban(reason=grund)
        await interaction.response.send_message(f"{mitglied} wurde gebannt. Grund: {grund}")
        await log_action(interaction.guild, "Ban", interaction.user, mitglied, grund)
    except discord.Forbidden:
        await interaction.response.send_message(
            "Nexora kann dieses Mitglied nicht bannen. Prüfe die Berechtigungen und Rollenreihenfolge.",
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
