import logging
import os
import re
from collections import defaultdict, deque
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
FALLBACK_MOD_LOG_CHANNEL_ID = int(os.getenv("MOD_LOG_CHANNEL_ID", "0"))
if not TOKEN:
    raise RuntimeError("Die Umgebungsvariable DISCORD_TOKEN fehlt.")

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)
configure_bot(bot)
message_timestamps = defaultdict(lambda: deque(maxlen=20))
LINK_PATTERN = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)


async def initialize_database():
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("""CREATE TABLE IF NOT EXISTS warnings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            moderator_id INTEGER NOT NULL,
            reason TEXT NOT NULL,
            created_at TEXT NOT NULL
        )""")
        await db.execute("""CREATE TABLE IF NOT EXISTS guild_settings (
            guild_id TEXT PRIMARY KEY,
            mod_log_channel_id TEXT NOT NULL DEFAULT '',
            welcome_channel_id TEXT NOT NULL DEFAULT '',
            leave_channel_id TEXT NOT NULL DEFAULT '',
            welcome_message TEXT NOT NULL DEFAULT 'Willkommen {user} auf {server}!',
            leave_message TEXT NOT NULL DEFAULT '{user} hat den Server verlassen.',
            links_enabled INTEGER NOT NULL DEFAULT 0,
            spam_enabled INTEGER NOT NULL DEFAULT 0,
            spam_limit INTEGER NOT NULL DEFAULT 5,
            spam_window_seconds INTEGER NOT NULL DEFAULT 8,
            support_role_id TEXT NOT NULL DEFAULT '',
            ticket_category_id TEXT NOT NULL DEFAULT '',
            ticket_log_channel_id TEXT NOT NULL DEFAULT '',
            verification_role_id TEXT NOT NULL DEFAULT '',
            verification_channel_id TEXT NOT NULL DEFAULT '',
            verification_message TEXT NOT NULL DEFAULT 'Bitte verifiziere dich mit dem Button oder Befehl.',
            afk_channel_id TEXT NOT NULL DEFAULT '',
            afk_role_id TEXT NOT NULL DEFAULT '',
            backup_hour INTEGER NOT NULL DEFAULT 3,
            backup_enabled INTEGER NOT NULL DEFAULT 0
        )""")
        await db.execute("""CREATE TABLE IF NOT EXISTS reaction_roles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL,
            channel_id TEXT NOT NULL,
            message_id TEXT NOT NULL,
            emoji TEXT NOT NULL,
            role_id TEXT NOT NULL
        )""")
        await db.commit()


async def get_guild_settings(guild_id):
    defaults = {
        "mod_log_channel_id": "",
        "welcome_channel_id": "",
        "leave_channel_id": "",
        "welcome_message": "Willkommen {user} auf {server}!",
        "leave_message": "{user} hat den Server verlassen.",
        "links_enabled": 0,
        "spam_enabled": 0,
        "spam_limit": 5,
        "spam_window_seconds": 8,
        "support_role_id": "",
        "ticket_category_id": "",
        "ticket_log_channel_id": "",
        "verification_role_id": "",
        "verification_channel_id": "",
        "verification_message": "Bitte verifiziere dich mit dem Button oder Befehl.",
        "afk_channel_id": "",
        "afk_role_id": "",
        "backup_hour": 3,
        "backup_enabled": 0,
    }
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM guild_settings WHERE guild_id=?", (str(guild_id),))
        row = await cursor.fetchone()
    if row:
        defaults.update(dict(row))
    return defaults


async def log_moderation(guild, title, description):
    if guild is None:
        return
    settings = await get_guild_settings(guild.id)
    try:
        channel_id = int(settings["mod_log_channel_id"] or FALLBACK_MOD_LOG_CHANNEL_ID)
    except (TypeError, ValueError):
        channel_id = 0
    channel = guild.get_channel(channel_id) if channel_id else None
    if not isinstance(channel, discord.TextChannel):
        return
    embed = discord.Embed(
        title=title,
        description=(description or "")[:4000],
        color=discord.Color.orange(),
        timestamp=discord.utils.utcnow(),
    )
    try:
        await channel.send(embed=embed)
    except (discord.Forbidden, discord.HTTPException):
        log.exception("Moderationslog konnte nicht gesendet werden.")


async def check_target(interaction, member):
    guild = interaction.guild
    moderator = interaction.user
    if guild is None or not isinstance(moderator, discord.Member):
        return "Dieser Befehl kann nur auf einem Server verwendet werden."
    if member.id == moderator.id:
        return "Du kannst diese Aktion nicht gegen dich selbst verwenden."
    if member.id == guild.owner_id:
        return "Der Serverinhaber kann nicht moderiert werden."
    if bot.user is not None and member.id == bot.user.id:
        return "Der Bot kann sich nicht selbst moderieren."
    if moderator.id != guild.owner_id and member.top_role >= moderator.top_role:
        return "Deine höchste Rolle muss über der höchsten Rolle des Ziels liegen."
    if guild.me is None or member.top_role >= guild.me.top_role:
        return "Die höchste Rolle des Bots muss über der höchsten Rolle des Ziels liegen."
    return None


async def has_bot_permission(interaction, permission_name):
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


@bot.event
async def on_message(message):
    if message.guild is None or message.author.bot or not isinstance(message.author, discord.Member):
        return
    settings = await get_guild_settings(message.guild.id)
    reason = None
    if settings["links_enabled"] and LINK_PATTERN.search(message.content):
        reason = "Link-Filter"
    if settings["spam_enabled"]:
        key = (message.guild.id, message.author.id)
        timestamps = message_timestamps[key]
        now = discord.utils.utcnow().timestamp()
        while timestamps and now - timestamps[0] > int(settings["spam_window_seconds"]):
            timestamps.popleft()
        timestamps.append(now)
        if len(timestamps) >= int(settings["spam_limit"]):
            reason = reason or "Spam-Filter"
            timestamps.clear()
    if reason:
        try:
            await message.delete()
            await log_moderation(
                message.guild,
                "Auto-Moderation",
                f"Nachricht von {message.author.mention} in {message.channel.mention} entfernt. Regel: {reason}.",
            )
        except discord.Forbidden:
            log.warning("Keine Berechtigung, eine Auto-Mod-Nachricht zu löschen.")
        except discord.HTTPException:
            log.exception("Auto-Mod konnte Nachricht nicht löschen.")
    await bot.process_commands(message)


def format_member_message(template, member):
    return (
        template.replace("{user}", str(member))
        .replace("{server}", member.guild.name)
        .replace("{member_count}", str(member.guild.member_count or 0))
    )[:1900]


@bot.event
async def on_member_join(member):
    settings = await get_guild_settings(member.guild.id)
    channel_id = int(settings["welcome_channel_id"] or 0)
    channel = member.guild.get_channel(channel_id) if channel_id else None
    if isinstance(channel, discord.TextChannel):
        try:
            await channel.send(
                format_member_message(settings["welcome_message"], member),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException:
            log.exception("Willkommensnachricht konnte nicht gesendet werden.")
    verification_role_id = settings.get("verification_role_id")
    if verification_role_id:
        role = member.guild.get_role(int(verification_role_id))
        if role is not None:
            try:
                await member.add_roles(role, reason="Verifizierung")
            except discord.Forbidden:
                pass
    await log_moderation(member.guild, "Mitglied beigetreten", f"{member} (`{member.id}`) ist beigetreten.")


@bot.event
async def on_member_remove(member):
    settings = await get_guild_settings(member.guild.id)
    channel_id = int(settings["leave_channel_id"] or 0)
    channel = member.guild.get_channel(channel_id) if channel_id else None
    if isinstance(channel, discord.TextChannel):
        try:
            await channel.send(
                format_member_message(settings["leave_message"], member),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException:
            log.exception("Abschiedsnachricht konnte nicht gesendet werden.")
    await log_moderation(member.guild, "Mitglied ausgetreten", f"{member} (`{member.id}`) hat den Server verlassen.")


@bot.event
async def on_member_update(before, after):
    before_roles = {role.id for role in before.roles}
    after_roles = {role.id for role in after.roles}
    if before_roles != after_roles:
        added = [r.mention for r in after.roles if r.id not in before_roles]
        removed = [r.mention for r in before.roles if r.id not in after_roles]
        details = []
        if added:
            details.append("Hinzugefügt: " + ", ".join(added))
        if removed:
            details.append("Entfernt: " + ", ".join(removed))
        await log_moderation(after.guild, "Rollen geändert", f"Mitglied: {after.mention}\n" + "\n".join(details))
    if before.timed_out_until != after.timed_out_until:
        status = f"bis {discord.utils.format_dt(after.timed_out_until, style='F')}" if after.timed_out_until else "aufgehoben"
        await log_moderation(after.guild, "Timeout geändert", f"Mitglied: {after.mention}\nTimeout {status}.")


@bot.event
async def on_member_ban(guild, user):
    await log_moderation(guild, "Mitglied gebannt", f"{user} (`{user.id}`) wurde gebannt.")


@bot.event
async def on_voice_state_update(member, before, after):
    settings = await get_guild_settings(member.guild.id)
    afk_channel_id = settings.get("afk_channel_id")
    afk_role_id = settings.get("afk_role_id")
    if not afk_channel_id or not afk_role_id:
        return
    afk_role = member.guild.get_role(int(afk_role_id))
    if afk_role is None:
        return
    target_id = int(afk_channel_id)
    if after.channel and after.channel.id == target_id:
        try:
            await member.add_roles(afk_role, reason="AFK-Rolle")
        except discord.Forbidden:
            pass
    if before.channel and before.channel.id == target_id and (after.channel is None or after.channel.id != target_id):
        try:
            await member.remove_roles(afk_role, reason="AFK-Rolle entfernt")
        except discord.Forbidden:
            pass


@bot.event
async def on_raw_reaction_add(payload):
    if payload.member is None or payload.member.bot:
        return
    async with aiosqlite.connect(DATABASE_PATH) as db:
        rows = await db.execute(
            "SELECT role_id FROM reaction_roles WHERE guild_id=? AND channel_id=? AND message_id=? AND emoji=?",
            (str(payload.guild_id), str(payload.channel_id), str(payload.message_id), str(payload.emoji)),
        )
        row = await rows.fetchone()
    if row is None:
        return
    guild = bot.get_guild(payload.guild_id)
    if guild is None:
        return
    role = guild.get_role(int(row[0]))
    if role is None:
        return
    try:
        await payload.member.add_roles(role, reason="Reaktionsrolle")
    except discord.Forbidden:
        pass


@bot.event
async def on_raw_reaction_remove(payload):
    guild = bot.get_guild(payload.guild_id)
    if guild is None:
        return
    async with aiosqlite.connect(DATABASE_PATH) as db:
        rows = await db.execute(
            "SELECT role_id FROM reaction_roles WHERE guild_id=? AND channel_id=? AND message_id=? AND emoji=?",
            (str(payload.guild_id), str(payload.channel_id), str(payload.message_id), str(payload.emoji)),
        )
        row = await rows.fetchone()
    if row is None:
        return
    role = guild.get_role(int(row[0]))
    member = guild.get_member(payload.user_id)
    if role is not None and member is not None:
        try:
            await member.remove_roles(role, reason="Reaktionsrolle entfernt")
        except discord.Forbidden:
            pass


@bot.tree.command(name="ticket", description="Öffnet ein Support-Ticket.")
@app_commands.describe(grund="Grund für das Ticket")
async def ticket(interaction: discord.Interaction, grund: str = "Kein Grund angegeben"):
    settings = await get_guild_settings(interaction.guild_id)
    category_id = settings.get("ticket_category_id")
    support_role_id = settings.get("support_role_id")
    category = interaction.guild.get_channel(int(category_id)) if category_id else None
    if category_id and category is None:
        await interaction.response.send_message("Die Ticket-Kategorie ist nicht mehr vorhanden.", ephemeral=True)
        return
    if not category_id:
        await interaction.response.send_message("Im Dashboard wurde noch keine Ticket-Kategorie konfiguriert.", ephemeral=True)
        return
    name = f"ticket-{interaction.user.name.lower()}"
    overwrites = {
        interaction.guild.default_role: discord.PermissionOverwrite(read_messages=False),
        interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
        interaction.guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True),
    }
    if support_role_id:
        support_role = interaction.guild.get_role(int(support_role_id))
        if support_role:
            overwrites[support_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
    channel = await interaction.guild.create_text_channel(name=name, category=category, overwrites=overwrites, reason=f"Ticket von {interaction.user}")
    await interaction.response.send_message(f"Ticket erstellt: {channel.mention}", ephemeral=False)
    await channel.send(f"{interaction.user.mention} hat ein Ticket geöffnet.\nGrund: {grund}\nSupport wird informiert.")
    if settings.get("ticket_log_channel_id"):
        log_channel = interaction.guild.get_channel(int(settings["ticket_log_channel_id"]))
        if isinstance(log_channel, discord.TextChannel):
            await log_channel.send(f"Ticket erstellt: {channel.mention} von {interaction.user.mention}\nGrund: {grund}")


@bot.tree.command(name="close_ticket", description="Schließt ein Ticket.")
async def close_ticket(interaction: discord.Interaction):
    if not isinstance(interaction.channel, discord.TextChannel):
        await interaction.response.send_message("Dieser Befehl funktioniert nur in einem Textkanal.", ephemeral=True)
        return
    if not interaction.channel.name.startswith("ticket-"):
        await interaction.response.send_message("Dieser Kanal ist kein Ticket.", ephemeral=True)
        return
    settings = await get_guild_settings(interaction.guild_id)
    support_role_id = settings.get("support_role_id")
    support_role = interaction.guild.get_role(int(support_role_id)) if support_role_id else None
    is_support = bool(support_role and support_role in interaction.user.roles)
    if interaction.user != interaction.guild.owner and not is_support and interaction.channel.permissions_for(interaction.user).manage_channels is False:
        await interaction.response.send_message("Du darfst dieses Ticket nicht schließen.", ephemeral=True)
        return
    await interaction.response.send_message(f"Ticket {interaction.channel.mention} wird geschlossen.")
    await interaction.channel.send("Das Ticket wird in 5 Sekunden geschlossen.")
    await discord.utils.sleep_until(discord.utils.utcnow() + timedelta(seconds=5))
    await interaction.channel.delete(reason="Ticket geschlossen")


@bot.tree.command(name="supportping", description="Pingt das Support-Team.")
async def supportping(interaction: discord.Interaction, text: str = "Support benötigt Hilfe!"):
    settings = await get_guild_settings(interaction.guild_id)
    support_role_id = settings.get("support_role_id")
    if not support_role_id:
        await interaction.response.send_message("Es ist noch keine Support-Rolle konfiguriert.", ephemeral=True)
        return
    role = interaction.guild.get_role(int(support_role_id))
    if role is None:
        await interaction.response.send_message("Die Support-Rolle konnte nicht gefunden werden.", ephemeral=True)
        return
    await interaction.response.send_message(f"{role.mention} {text}")


@bot.tree.command(name="verify", description="Gibt die Verifizierungsrolle.")
async def verify(interaction: discord.Interaction):
    settings = await get_guild_settings(interaction.guild_id)
    role_id = settings.get("verification_role_id")
    if not role_id:
        await interaction.response.send_message("Es ist noch keine Verifizierungsrolle konfiguriert.", ephemeral=True)
        return
    role = interaction.guild.get_role(int(role_id))
    if role is None:
        await interaction.response.send_message("Die Verifizierungsrolle konnte nicht gefunden werden.", ephemeral=True)
        return
    if role in interaction.user.roles:
        await interaction.response.send_message("Du bist bereits verifiziert.", ephemeral=True)
        return
    await interaction.user.add_roles(role, reason="Verifiziert")
    await interaction.response.send_message("Du bist nun verifiziert.", ephemeral=True)


@bot.tree.command(name="timeout", description="Gibt einem Mitglied einen Timeout.")
@app_commands.describe(mitglied="Mitglied", minuten="Dauer in Minuten, maximal 40320", grund="Grund")
async def timeout(interaction: discord.Interaction, mitglied: discord.Member, minuten: app_commands.Range[int, 1, 40320], grund: str = "Kein Grund angegeben"):
    if not await has_bot_permission(interaction, "moderate_members"):
        return
    error = await check_target(interaction, mitglied)
    if error:
        await interaction.response.send_message(error, ephemeral=True)
        return
    try:
        await mitglied.timeout(timedelta(minutes=minuten), reason=grund)
        await interaction.response.send_message(f"{mitglied.mention} hat einen Timeout für {minuten} Minuten erhalten. Grund: {grund}")
        await log_moderation(interaction.guild, "Timeout", f"Mitglied: {mitglied.mention}\nModerator: {interaction.user.mention}\nDauer: {minuten} Minuten\nGrund: {grund}")
    except discord.Forbidden:
        await interaction.response.send_message("Discord hat die Aktion verweigert. Prüfe Bot-Berechtigungen und Rollenreihenfolge.", ephemeral=True)
    except discord.HTTPException:
        log.exception("Timeout fehlgeschlagen.")
        await interaction.response.send_message("Der Timeout ist fehlgeschlagen.", ephemeral=True)


@bot.tree.command(name="kick", description="Wirft ein Mitglied vom Server.")
@app_commands.describe(mitglied="Mitglied, das gekickt werden soll", grund="Grund für den Kick")
async def kick(interaction: discord.Interaction, mitglied: discord.Member, grund: str = "Kein Grund angegeben"):
    if not await has_bot_permission(interaction, "kick_members"):
        return
    error = await check_target(interaction, mitglied)
    if error:
        await interaction.response.send_message(error, ephemeral=True)
        return
    try:
        await mitglied.kick(reason=grund)
        await interaction.response.send_message(f"{mitglied} wurde gekickt. Grund: {grund}")
        await log_moderation(interaction.guild, "Kick", f"Mitglied: {mitglied} (`{mitglied.id}`)\nModerator: {interaction.user.mention}\nGrund: {grund}")
    except discord.Forbidden:
        await interaction.response.send_message("Discord hat die Aktion verweigert. Prüfe Bot-Berechtigungen und Rollenreihenfolge.", ephemeral=True)
    except discord.HTTPException:
        log.exception("Kick fehlgeschlagen.")
        await interaction.response.send_message("Der Kick ist fehlgeschlagen.", ephemeral=True)


@bot.tree.command(name="ban", description="Bannt ein Mitglied vom Server.")
@app_commands.describe(mitglied="Mitglied, das gebannt werden soll", grund="Grund für den Ban")
async def ban(interaction: discord.Interaction, mitglied: discord.Member, grund: str = "Kein Grund angegeben"):
    if not await has_bot_permission(interaction, "ban_members"):
        return
    error = await check_target(interaction, mitglied)
    if error:
        await interaction.response.send_message(error, ephemeral=True)
        return
    try:
        await mitglied.ban(reason=grund)
        await interaction.response.send_message(f"{mitglied} wurde gebannt. Grund: {grund}")
        await log_moderation(interaction.guild, "Ban", f"Mitglied: {mitglied} (`{mitglied.id}`)\nModerator: {interaction.user.mention}\nGrund: {grund}")
    except discord.Forbidden:
        await interaction.response.send_message("Discord hat die Aktion verweigert. Prüfe Bot-Berechtigungen und Rollenreihenfolge.", ephemeral=True)
    except discord.HTTPException:
        log.exception("Ban fehlgeschlagen.")
        await interaction.response.send_message("Der Ban ist fehlgeschlagen.", ephemeral=True)


@bot.tree.command(name="warn", description="Vermerkt eine Verwarnung für ein Mitglied.")
@app_commands.describe(mitglied="Mitglied, das verwarnt werden soll", grund="Grund für die Verwarnung")
async def warn(interaction: discord.Interaction, mitglied: discord.Member, grund: str):
    if not isinstance(interaction.user, discord.Member) or not interaction.user.guild_permissions.moderate_members:
        await interaction.response.send_message("Du benötigst die Berechtigung Mitglieder moderieren.", ephemeral=True)
        return
    error = await check_target(interaction, mitglied)
    if error:
        await interaction.response.send_message(error, ephemeral=True)
        return
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO warnings (guild_id, user_id, moderator_id, reason, created_at) VALUES (?, ?, ?, ?, ?)",
            (interaction.guild_id, mitglied.id, interaction.user.id, grund, discord.utils.utcnow().isoformat()),
        )
        await db.commit()
        warning_id = cursor.lastrowid
    try:
        await mitglied.send(f"Du wurdest auf **{interaction.guild.name}** verwarnt.\nGrund: {grund}\nVerwarnungs-ID: `{warning_id}`")
    except (discord.Forbidden, discord.HTTPException):
        pass
    await interaction.response.send_message(f"{mitglied.mention} wurde verwarnt. Verwarnungs-ID: `{warning_id}`. Grund: {grund}")
    await log_moderation(interaction.guild, "Verwarnung", f"Mitglied: {mitglied.mention}\nModerator: {interaction.user.mention}\nID: `{warning_id}`\nGrund: {grund}")


@bot.tree.command(name="warnungen", description="Zeigt die letzten zehn Verwarnungen eines Mitglieds.")
@app_commands.describe(mitglied="Mitglied, dessen Verwarnungen angezeigt werden")
async def warnungen(interaction: discord.Interaction, mitglied: discord.Member):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cursor = await db.execute(
            "SELECT id, moderator_id, reason, created_at FROM warnings WHERE guild_id=? AND user_id=? ORDER BY id DESC LIMIT 10",
            (interaction.guild_id, mitglied.id),
        )
        rows = await cursor.fetchall()
    if not rows:
        await interaction.response.send_message(f"Für {mitglied.mention} wurden keine Verwarnungen gefunden.", ephemeral=True)
        return
    lines = [f"`#{row[0]}` — {row[2]} (Moderator: <@{row[1]}>, {row[3][:10]})" for row in rows]
    await interaction.response.send_message(f"Letzte Verwarnungen für {mitglied.mention}:\n" + "\n".join(lines), ephemeral=True)


@bot.tree.command(name="warnung_loeschen", description="Löscht eine Verwarnung anhand ihrer ID.")
@app_commands.describe(warnungs_id="ID der zu löschenden Verwarnung")
async def warnung_loeschen(interaction: discord.Interaction, warnungs_id: int):
    if not isinstance(interaction.user, discord.Member) or not interaction.user.guild_permissions.moderate_members:
        await interaction.response.send_message("Du benötigst die Berechtigung Mitglieder moderieren.", ephemeral=True)
        return
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cursor = await db.execute("DELETE FROM warnings WHERE id=? AND guild_id=?", (warnungs_id, interaction.guild_id))
        await db.commit()
        deleted = cursor.rowcount
    if not deleted:
        await interaction.response.send_message("Diese Verwarnungs-ID wurde auf diesem Server nicht gefunden.", ephemeral=True)
        return
    await interaction.response.send_message(f"Verwarnung `{warnungs_id}` wurde gelöscht.")
    await log_moderation(interaction.guild, "Verwarnung gelöscht", f"Verwarnungs-ID: `{warnungs_id}`\nModerator: {interaction.user.mention}")


def run_dashboard():
    port = int(os.getenv("SERVER_PORT", "5000"))
    log.info("Flask-Dashboard startet auf 0.0.0.0:%s", port)
    app.run(host="0.0.0.0", port=port, use_reloader=False)


if __name__ == "__main__":
    Thread(target=run_dashboard, daemon=True).start()
    bot.run(TOKEN)













































































































































































































































































































































































































