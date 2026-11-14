import os
from datetime import timedelta

import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands

TOKEN = os.getenv("DISCORD_TOKEN")
LOG_CHANNEL_ID = os.getenv("MOD_LOG_CHANNEL_ID")
DATABASE_PATH = os.getenv("DATABASE_PATH", "warnings.db")

if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN fehlt in den Umgebungsvariablen des Hostings.")

intents = discord.Intents.default()
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

async def initialize_database():
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS warnings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                moderator_id INTEGER NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await db.commit()

async def add_warning(guild_id: int, user_id: int, moderator_id: int, reason: str) -> int:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cursor = await db.execute(
            """
            INSERT INTO warnings (guild_id, user_id, moderator_id, reason)
            VALUES (?, ?, ?, ?)
            """,
            (guild_id, user_id, moderator_id, reason),
        )
        await db.commit()
        return cursor.lastrowid

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
    await initialize_database()
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

@bot.tree.command(name="warn", description="Verwarnt ein Mitglied und speichert die Verwarnung.")
@app_commands.describe(
    mitglied="Mitglied, das verwarnt werden soll",
    grund="Grund für die Verwarnung",
)
@app_commands.checks.has_permissions(moderate_members=True)
async def warn(interaction: discord.Interaction, mitglied: discord.Member, grund: str):
    if mitglied.id == interaction.user.id:
        await interaction.response.send_message("Du kannst dich nicht selbst verwarnen.", ephemeral=True)
        return

    if mitglied.id == interaction.guild.owner_id:
        await interaction.response.send_message("Der Serverinhaber kann nicht verwarnt werden.", ephemeral=True)
        return

    if target_is_higher(interaction, mitglied):
        await interaction.response.send_message(
            "Die Rolle dieses Mitglieds ist gleich hoch oder höher als deine.",
            ephemeral=True,
        )
        return

    warning_id = await add_warning(
        interaction.guild.id,
        mitglied.id,
        interaction.user.id,
        grund,
    )

    await interaction.response.send_message(
        f"{mitglied.mention} wurde verwarnt. Verwarnungs-ID: `{warning_id}`. Grund: {grund}"
    )
    await log_action(
        interaction.guild,
        f"Verwarnung #{warning_id}",
        interaction.user,
        mitglied,
        grund,
    )

    try:
        await mitglied.send(
            f"Du wurdest auf **{interaction.guild.name}** verwarnt.\n"
            f"Verwarnungs-ID: `{warning_id}`\n"
            f"Grund: {grund}"
        )
    except discord.Forbidden:
        pass

@bot.tree.command(name="warnungen", description="Zeigt die gespeicherten Verwarnungen eines Mitglieds.")
@app_commands.describe(mitglied="Mitglied, dessen Verwarnungen angezeigt werden sollen")
@app_commands.checks.has_permissions(moderate_members=True)
async def warnungen(interaction: discord.Interaction, mitglied: discord.Member):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cursor = await db.execute(
            """
            SELECT id, moderator_id, reason, created_at
            FROM warnings
            WHERE guild_id = ? AND user_id = ?
            ORDER BY id DESC
            LIMIT 10
            """,
            (interaction.guild.id, mitglied.id),
        )
        rows = await cursor.fetchall()

        count_cursor = await db.execute(
            """
            SELECT COUNT(*)
            FROM warnings
            WHERE guild_id = ? AND user_id = ?
            """,
            (interaction.guild.id, mitglied.id),
        )
        count_row = await count_cursor.fetchone()

    total_count = count_row[0] if count_row else 0

    if not rows:
        await interaction.response.send_message(
            f"{mitglied.mention} hat keine gespeicherten Verwarnungen.",
            ephemeral=True,
        )
        return

    embed = discord.Embed(
        title=f"Verwarnungen für {mitglied}",
        description=f"Gesamt: **{total_count}**. Angezeigt werden höchstens die letzten 10.",
        color=discord.Color.orange(),
    )

    for warning_id, moderator_id, reason, created_at in rows:
        embed.add_field(
            name=f"ID {warning_id} · {created_at} UTC",
            value=f"Moderator: <@{moderator_id}>\nGrund: {reason[:900]}",
            inline=False,
        )

    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="warnung_löschen", description="Löscht eine gespeicherte Verwarnung anhand ihrer ID.")
@app_commands.describe(warnungs_id="ID der zu löschenden Verwarnung")
@app_commands.checks.has_permissions(moderate_members=True)
async def warnung_loeschen(interaction: discord.Interaction, warnungs_id: int):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cursor = await db.execute(
            """
            SELECT user_id, reason
            FROM warnings
            WHERE id = ? AND guild_id = ?
            """,
            (warnungs_id, interaction.guild.id),
        )
        row = await cursor.fetchone()

        if row is None:
            await interaction.response.send_message(
                f"Verwarnung mit ID `{warnungs_id}` wurde nicht gefunden.",
                ephemeral=True,
            )
            return

        user_id, reason = row

        await db.execute(
            "DELETE FROM warnings WHERE id = ? AND guild_id = ?",
            (warnungs_id, interaction.guild.id),
        )
        await db.commit()

    await interaction.response.send_message(
        f"Verwarnung `{warnungs_id}` für <@{user_id}> wurde gelöscht.",
        ephemeral=True,
    )

    user = bot.get_user(user_id)
    if user is None:
        try:
            user = await bot.fetch_user(user_id)
        except discord.HTTPException:
            user = None

    if user is not None:
        await log_action(
            interaction.guild,
            f"Verwarnung #{warnungs_id} gelöscht",
            interaction.user,
            user,
            reason,
        )

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
        await interaction.response.send_message(
            "Die Rolle dieses Mitglieds ist gleich hoch oder höher als deine.",
            ephemeral=True,
        )
        return

    bot_member = interaction.guild.me
    if bot_member is None or not bot_member.guild_permissions.moderate_members:
        await interaction.response.send_message(
            "Nexora braucht die Berechtigung „Mitglieder moderieren“.",
            ephemeral=True,
        )
        return

    try:
        await mitglied.timeout(timedelta(minutes=minuten), reason=grund)
        await interaction.response.send_message(
            f"{mitglied.mention} hat einen Timeout für {minuten} Minuten erhalten. Grund: {grund}"
        )
        await log_action(
            interaction.guild,
            "Timeout",
            interaction.user,
            mitglied,
            grund,
            f"{minuten} Minuten",
        )
    except discord.Forbidden:
        await interaction.response.send_message(
            "Nexora kann dieses Mitglied nicht timeouten. Prüfe Berechtigungen und Rollenreihenfolge.",
            ephemeral=True,
        )

@bot.tree.command(name="kick", description="Wirft ein Mitglied vom Server.")
@app_commands.describe(
    mitglied="Mitglied, das entfernt werden soll",
    grund="Grund für den Kick",
)
@app_commands.checks.has_permissions(kick_members=True)
async def kick(interaction: discord.Interaction, mitglied: discord.Member, grund: str):
    if mitglied.id == interaction.user.id:
        await interaction.response.send_message("Du kannst dich nicht selbst kicken.", ephemeral=True)
        return

    if mitglied.id == interaction.guild.owner_id:
        await interaction.response.send_message("Der Serverinhaber kann nicht gekickt werden.", ephemeral=True)
        return

    if target_is_higher(interaction, mitglied):
        await interaction.response.send_message(
            "Die Rolle dieses Mitglieds ist gleich hoch oder höher als deine.",
            ephemeral=True,
        )
        return

    bot_member = interaction.guild.me
    if bot_member is None or not bot_member.guild_permissions.kick_members:
        await interaction.response.send_message(
            "Nexora braucht die Berechtigung „Mitglieder kicken“.",
            ephemeral=True,
        )
        return

    try:
        await mitglied.kick(reason=grund)
        await interaction.response.send_message(f"{mitglied} wurde gekickt. Grund: {grund}")
        await log_action(interaction.guild, "Kick", interaction.user, mitglied, grund)
    except discord.Forbidden:
        await interaction.response.send_message(
            "Nexora kann dieses Mitglied nicht kicken. Prüfe Berechtigungen und Rollenreihenfolge.",
            ephemeral=True,
        )

@bot.tree.command(name="ban", description="Bannt ein Mitglied vom Server.")
@app_commands.describe(
    mitglied="Mitglied, das gebannt werden soll",
    grund="Grund für den Ban",
)
@app_commands.checks.has_permissions(ban_members=True)
async def ban(interaction: discord.Interaction, mitglied: discord.Member, grund: str):
    if mitglied.id == interaction.user.id:
        await interaction.response.send_message("Du kannst dich nicht selbst bannen.", ephemeral=True)
        return

    if mitglied.id == interaction.guild.owner_id:
        await interaction.response.send_message("Der Serverinhaber kann nicht gebannt werden.", ephemeral=True)
        return

    if target_is_higher(interaction, mitglied):
        await interaction.response.send_message(
            "Die Rolle dieses Mitglieds ist gleich hoch oder höher als deine.",
            ephemeral=True,
        )
        return

    bot_member = interaction.guild.me
    if bot_member is None or not bot_member.guild_permissions.ban_members:
        await interaction.response.send_message(
            "Nexora braucht die Berechtigung „Mitglieder bannen“.",
            ephemeral=True,
        )
        return

    try:
        await mitglied.ban(reason=grund)
        await interaction.response.send_message(f"{mitglied} wurde gebannt. Grund: {grund}")
        await log_action(interaction.guild, "Ban", interaction.user, mitglied, grund)
    except discord.Forbidden:
        await interaction.response.send_message(
            "Nexora kann dieses Mitglied nicht bannen. Prüfe Berechtigungen und Rollenreihenfolge.",
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
