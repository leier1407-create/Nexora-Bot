import logging
import os
from datetime import timedelta
from threading import Thread

import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

from dashboard import app, configure_bot

load_dotenv()
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("nexora")

TOKEN = os.getenv("DISCORD_TOKEN")
DATABASE_PATH = os.getenv("DATABASE_PATH", "warnings.db")
MOD_LOG_CHANNEL_ID = int(os.getenv("MOD_LOG_CHANNEL_ID", "0"))

if not TOKEN:
    raise RuntimeError("Die Umgebungsvariable DISCORD_TOKEN fehlt.")

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)
configure_bot(bot)

async def initialize_database():
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS warnings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                moderator_id INTEGER NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        await db.commit()

async def log_moderation(guild: discord.Guild, title: str, description: str):
    if not MOD_LOG_CHANNEL_ID:
        return

    channel = guild.get_channel(MOD_LOG_CHANNEL_ID)
    if channel is None:
        log.warning("Moderationskanal %s wurde nicht gefunden.", MOD_LOG_CHANNEL_ID)
        return

    embed = discord.Embed(
        title=title,
        description=description,
        color=discord.Color.orange(),
        timestamp=discord.utils.utcnow(),
    )

    try:
        await channel.send(embed=embed)
    except discord.Forbidden:
        log.warning("Keine Berechtigung, in den Moderationskanal zu schreiben.")
    except discord.HTTPException:
        log.exception("Moderationslog konnte nicht gesendet werden.")

async def check_target(
    interaction: discord.Interaction,
    member: discord.Member,
) -> str | None:
    guild = interaction.guild
    moderator = interaction.user

    if guild is None or not isinstance(moderator, discord.Member):
        return "Dieser Befehl kann nur auf einem Server verwendet werden."

    if member.id == moderator.id:
        return "Du kannst diese Aktion nicht gegen dich selbst verwenden."

    if member.id == guild.owner_id:
        return "Der Serverinhaber kann nicht moderiert werden."

    if member.id == bot.user.id:
        return "Der Bot kann sich nicht selbst moderieren."

    if moderator.id != guild.owner_id and member.top_role >= moderator.top_role:
        return "Deine höchste Rolle muss über der höchsten Rolle des Ziels liegen."

    bot_member = guild.me
    if bot_member is None or member.top_role >= bot_member.top_role:
        return "Die höchste Rolle des Bots muss über der höchsten Rolle des Ziels liegen."

    return None

async def has_bot_permission(
    interaction: discord.Interaction,
    permission_name: str,
) -> bool:
    guild = interaction.guild
    bot_member = guild.me if guild else None

    if bot_member is None or not getattr(bot_member.guild_permissions, permission_name, False):
        await interaction.response.send_message(
            f"Dem Bot fehlt die erforderliche Berechtigung: `{permission_name}`.",
            ephemeral=True,
        )
        return False

    return True

@bot.event
async def setup_hook():
    await initialize_database()
    synced = await bot.tree.sync()
    log.info("%s Slash-Commands synchronisiert.", len(synced))

@bot.event
async def on_ready():
    log.info("Discord-Bot angemeldet als %s", bot.user)

@bot.tree.command(name="timeout", description="Gibt einem Mitglied einen Timeout.")
@app_commands.describe(
    mitglied="Mitglied, das den Timeout erhalten soll",
    minuten="Dauer in Minuten, maximal 40320",
    grund="Grund für den Timeout",
)
async def timeout(
    interaction: discord.Interaction,
    mitglied: discord.Member,
    minuten: app_commands.Range[int, 1, 40320],
    grund: str = "Kein Grund angegeben",
):
    if not await has_bot_permission(interaction, "moderate_members"):
        return

    error = await check_target(interaction, mitglied)
    if error:
        await interaction.response.send_message(error, ephemeral=True)
        return

    try:
        await mitglied.timeout(timedelta(minutes=minuten), reason=grund)
        await interaction.response.send_message(
            f"{mitglied.mention} hat einen Timeout für {minuten} Minuten erhalten. Grund: {grund}"
        )
        await log_moderation(
            interaction.guild,
            "Timeout",
            f"Mitglied: {mitglied.mention}\n"
            f"Moderator: {interaction.user.mention}\n"
            f"Dauer: {minuten} Minuten\nGrund: {grund}",
        )
    except discord.Forbidden:
        await interaction.response.send_message(
            "Discord hat die Aktion verweigert. Prüfe die Bot-Berechtigungen und Rollenreihenfolge.",
            ephemeral=True,
        )
    except discord.HTTPException:
        log.exception("Timeout fehlgeschlagen.")
        await interaction.response.send_message("Der Timeout ist fehlgeschlagen.", ephemeral=True)

@bot.tree.command(name="kick", description="Wirft ein Mitglied vom Server.")
@app_commands.describe(
    mitglied="Mitglied, das gekickt werden soll",
    grund="Grund für den Kick",
)
async def kick(
    interaction: discord.Interaction,
    mitglied: discord.Member,
    grund: str = "Kein Grund angegeben",
):
    if not await has_bot_permission(interaction, "kick_members"):
        return

    error = await check_target(interaction, mitglied)
    if error:
        await interaction.response.send_message(error, ephemeral=True)
        return

    try:
        await mitglied.kick(reason=grund)
        await interaction.response.send_message(f"{mitglied} wurde gekickt. Grund: {grund}")
        await log_moderation(
            interaction.guild,
            "Kick",
            f"Mitglied: {mitglied} (`{mitglied.id}`)\n"
            f"Moderator: {interaction.user.mention}\nGrund: {grund}",
        )
    except discord.Forbidden:
        await interaction.response.send_message(
            "Discord hat die Aktion verweigert. Prüfe die Bot-Berechtigungen und Rollenreihenfolge.",
            ephemeral=True,
        )
    except discord.HTTPException:
        log.exception("Kick fehlgeschlagen.")
        await interaction.response.send_message("Der Kick ist fehlgeschlagen.", ephemeral=True)

@bot.tree.command(name="ban", description="Bannt ein Mitglied vom Server.")
@app_commands.describe(
    mitglied="Mitglied, das gebannt werden soll",
    grund="Grund für den Ban",
)
async def ban(
    interaction: discord.Interaction,
    mitglied: discord.Member,
    grund: str = "Kein Grund angegeben",
):
    if not await has_bot_permission(interaction, "ban_members"):
        return

    error = await check_target(interaction, mitglied)
    if error:
        await interaction.response.send_message(error, ephemeral=True)
        return

    try:
        await mitglied.ban(reason=grund)
        await interaction.response.send_message(f"{mitglied} wurde gebannt. Grund: {grund}")
        await log_moderation(
            interaction.guild,
            "Ban",
            f"Mitglied: {mitglied} (`{mitglied.id}`)\n"
            f"Moderator: {interaction.user.mention}\nGrund: {grund}",
        )
    except discord.Forbidden:
        await interaction.response.send_message(
            "Discord hat die Aktion verweigert. Prüfe die Bot-Berechtigungen und Rollenreihenfolge.",
            ephemeral=True,
        )
    except discord.HTTPException:
        log.exception("Ban fehlgeschlagen.")
        await interaction.response.send_message("Der Ban ist fehlgeschlagen.", ephemeral=True)

@bot.tree.command(name="warn", description="Vermerkt eine Verwarnung für ein Mitglied.")
@app_commands.describe(
    mitglied="Mitglied, das verwarnt werden soll",
    grund="Grund für die Verwarnung",
)
async def warn(
    interaction: discord.Interaction,
    mitglied: discord.Member,
    grund: str,
):
    error = await check_target(interaction, mitglied)
    if error:
        await interaction.response.send_message(error, ephemeral=True)
        return

    async with aiosqlite.connect(DATABASE_PATH) as db:
        cursor = await db.execute(
            """
            INSERT INTO warnings (guild_id, user_id, moderator_id, reason, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                interaction.guild_id,
                mitglied.id,
                interaction.user.id,
                grund,
                discord.utils.utcnow().isoformat(),
            ),
        )
        await db.commit()
        warning_id = cursor.lastrowid

    try:
        await mitglied.send(
            f"Du wurdest auf **{interaction.guild.name}** verwarnt.\n"
            f"Grund: {grund}\nVerwarnungs-ID: `{warning_id}`"
        )
    except (discord.Forbidden, discord.HTTPException):
        pass

    await interaction.response.send_message(
        f"{mitglied.mention} wurde verwarnt. Verwarnungs-ID: `{warning_id}`. Grund: {grund}"
    )
    await log_moderation(
        interaction.guild,
        "Verwarnung",
        f"Mitglied: {mitglied.mention}\n"
        f"Moderator: {interaction.user.mention}\n"
        f"ID: `{warning_id}`\nGrund: {grund}",
    )

@bot.tree.command(name="warnungen", description="Zeigt die letzten zehn Verwarnungen eines Mitglieds.")
@app_commands.describe(mitglied="Mitglied, dessen Verwarnungen angezeigt werden")
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
            (interaction.guild_id, mitglied.id),
        )
        rows = await cursor.fetchall()

    if not rows:
        await interaction.response.send_message(
            f"Für {mitglied.mention} wurden keine Verwarnungen gefunden.",
            ephemeral=True,
        )
        return

    lines = [
        f"`#{row[0]}` — {row[2]} (Moderator: <@{row[1]}>, {row[3][:10]})"
        for row in rows
    ]
    await interaction.response.send_message(
        f"Letzte Verwarnungen für {mitglied.mention}:\n" + "\n".join(lines),
        ephemeral=True,
    )

@bot.tree.command(name="warnung_loeschen", description="Löscht eine Verwarnung anhand ihrer ID.")
@app_commands.describe(warnungs_id="ID der zu löschenden Verwarnung")
async def warnung_loeschen(
    interaction: discord.Interaction,
    warnungs_id: int,
):
    if not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message(
            "Dieser Befehl kann nur auf einem Server verwendet werden.",
            ephemeral=True,
        )
        return

    if not interaction.user.guild_permissions.moderate_members:
        await interaction.response.send_message(
            "Du benötigst die Berechtigung „Mitglieder moderieren“.",
            ephemeral=True,
        )
        return

    async with aiosqlite.connect(DATABASE_PATH) as db:
        cursor = await db.execute(
            "DELETE FROM warnings WHERE id = ? AND guild_id = ?",
            (warnungs_id, interaction.guild_id),
        )
        await db.commit()
        deleted = cursor.rowcount

    if not deleted:
        await interaction.response.send_message(
            "Diese Verwarnungs-ID wurde auf diesem Server nicht gefunden.",
            ephemeral=True,
        )
        return

    await interaction.response.send_message(f"Verwarnung `{warnungs_id}` wurde gelöscht.")
    await log_moderation(
        interaction.guild,
        "Verwarnung gelöscht",
        f"Verwarnungs-ID: `{warnungs_id}`\nModerator: {interaction.user.mention}",
    )
async def create_server_structure(
    guild: discord.Guild,
    roles: list[str],
    categories: list[dict],
):
    """Erstellt die vorgeschlagenen Rollen, Kategorien und Text-/Voice-Channels."""
    bot_member = guild.me
    if bot_member is None or not bot_member.guild_permissions.manage_roles:
        raise discord.Forbidden(
            response=None,
            message="Dem Bot fehlt die Berechtigung „Rollen verwalten“.",
        )

    if not bot_member.guild_permissions.manage_channels:
        raise discord.Forbidden(
            response=None,
            message="Dem Bot fehlt die Berechtigung „Kanäle verwalten“.",
        )

    # Rollen erstellen; @everyone und bereits vorhandene Rollen überspringen.
    existing_roles = {role.name.casefold() for role in guild.roles}
    for role_name in roles:
        role_name = str(role_name).strip()[:100]
        if not role_name or role_name.casefold() in existing_roles:
            continue

        await guild.create_role(name=role_name, reason="Serverstruktur aus dem Dashboard")
        existing_roles.add(role_name.casefold())

    # Kategorien und darin aufgeführte Channels erstellen.
    for category_data in categories:
        category_name = str(category_data.get("name", "")).strip()[:100]
        if not category_name:
            continue

        category = discord.utils.get(guild.categories, name=category_name)
        if category is None:
            category = await guild.create_category(
                name=category_name,
                reason="Serverstruktur aus dem Dashboard",
            )

        for channel_data in category_data.get("channels", []):
            channel_text = str(channel_data).strip()
            if not channel_text:
                continue

            is_voice = channel_text.lower().startswith("voice:")
            channel_name = (
                channel_text.split(":", 1)[1].strip()
                if is_voice
                else channel_text
            )
            channel_name = channel_name[:100]
            if not channel_name:
                continue

            if is_voice:
                exists = discord.utils.get(category.voice_channels, name=channel_name)
                if exists is None:
                    await guild.create_voice_channel(
                        name=channel_name,
                        category=category,
                        reason="Serverstruktur aus dem Dashboard",
                    )
            else:
                exists = discord.utils.get(category.text_channels, name=channel_name)
                if exists is None:
                    await guild.create_text_channel(
                        name=channel_name,
                        category=category,
                        reason="Serverstruktur aus dem Dashboard",
                    )

def run_dashboard():
    port = int(os.getenv("SERVER_PORT", "5000"))
    log.info("Flask-Dashboard startet auf 0.0.0.0:%s", port)
    app.run(host="0.0.0.0", port=port, use_reloader=False)

if __name__ == "__main__":
    Thread(target=run_dashboard, daemon=True).start()
    bot.run(TOKEN)

