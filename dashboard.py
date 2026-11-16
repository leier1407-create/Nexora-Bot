import asyncio
import html
import os
import re
import secrets
import sqlite3
from urllib.parse import urlencode

import discord
import requests
from dotenv import load_dotenv
from flask import Flask, abort, redirect, render_template_string, request, session, url_for

load_dotenv()
app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "")
if not app.secret_key:
    raise RuntimeError("Die Umgebungsvariable FLASK_SECRET_KEY fehlt.")

DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID", "")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET", "")
DISCORD_REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI", "")
DATABASE_PATH = os.getenv("DATABASE_PATH", "warnings.db")
DISCORD_API = "https://discord.com/api/v10"
discord_bot = None

PAGE = """<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Nexora Dashboard</title>
  <style>
    body { margin: 0; padding: 24px; background: #111827; color: #f9fafb; font: 16px system-ui, sans-serif; }
    main { max-width: 1100px; margin: 24px auto; padding: 24px; border: 1px solid #374151; border-radius: 16px; background: #1f2937; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(290px, 1fr)); gap: 16px; }
    .card { margin: 18px 0; padding: 18px; background: #111827; border: 1px solid #374151; border-radius: 12px; }
    input, textarea, select, button { box-sizing: border-box; width: 100%; margin: 7px 0 16px; padding: 11px; border: 1px solid #4b5563; border-radius: 8px; background: #111827; color: #f9fafb; font: inherit; }
    textarea { min-height: 95px; resize: vertical; }
    button, .button { display: inline-block; width: auto; padding: 11px 16px; border: 0; border-radius: 8px; background: #4E5B31; color: white; cursor: pointer; text-decoration: none; }
    a { color: #93c5fd; }
    label { display: block; margin-top: 10px; }
    .muted { color: #9ca3af; }
    ul { padding-left: 20px; }
    hr { border: 1px solid #374151; }
  </style>
</head>
<body><main>{{ content|safe }}</main></body>
</html>"""


def configure_bot(bot):
    global discord_bot
    discord_bot = bot


def initialize_dashboard_database():
    with sqlite3.connect(DATABASE_PATH) as db:
        db.execute("""CREATE TABLE IF NOT EXISTS guild_settings (
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
        db.execute("""CREATE TABLE IF NOT EXISTS panel_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id TEXT NOT NULL,
            guild_id TEXT NOT NULL,
            name TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            body TEXT NOT NULL DEFAULT '',
            color TEXT NOT NULL DEFAULT '#4E5B31',
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS reaction_roles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL,
            channel_id TEXT NOT NULL,
            message_id TEXT NOT NULL,
            emoji TEXT NOT NULL,
            role_id TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        db.commit()


initialize_dashboard_database()


def esc(value):
    return html.escape(str(value), quote=True)


def csrf_input():
    token = session.setdefault("csrf_token", secrets.token_urlsafe(32))
    return f'<input type="hidden" name="csrf_token" value="{esc(token)}">'


@app.before_request
def check_csrf():
    if request.method == "POST":
        expected = session.get("csrf_token", "")
        supplied = request.form.get("csrf_token", "")
        if not expected or not secrets.compare_digest(expected, supplied):
            abort(400, "Ungültige oder abgelaufene Formularsitzung. Bitte Seite neu laden.")


def render_page(content):
    return render_template_string(PAGE, content=content)


def require_login():
    if not session.get("discord_id") or not session.get("access_token"):
        return redirect(url_for("login"))
    return None


def get_user_guilds():
    response = requests.get(
        f"{DISCORD_API}/users/@me/guilds",
        headers={"Authorization": f"Bearer {session['access_token']}"},
        timeout=15,
    )
    response.raise_for_status()
    return response.json()


def can_manage_guild(guild_data):
    permissions = int(guild_data.get("permissions", "0"))
    return bool(permissions & 32 or permissions & 8)


def authorize_guild(guild_id):
    for guild_data in get_user_guilds():
        if guild_data["id"] == str(guild_id) and can_manage_guild(guild_data):
            return guild_data
    abort(403, "Du brauchst auf diesem Server Serververwaltungsrechte oder Administrator.")


def db_settings(guild_id):
    with sqlite3.connect(DATABASE_PATH) as db:
        db.row_factory = sqlite3.Row
        row = db.execute("SELECT * FROM guild_settings WHERE guild_id = ?", (str(guild_id),)).fetchone()
    defaults = {
        "guild_id": str(guild_id),
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
    if row:
        defaults.update(dict(row))
    return defaults


def get_template(template_id, owner_id, guild_id):
    with sqlite3.connect(DATABASE_PATH) as db:
        db.row_factory = sqlite3.Row
        row = db.execute(
            "SELECT id,name,title,body,color FROM panel_templates WHERE id=? AND owner_id=? AND guild_id=?",
            (template_id, str(owner_id), str(guild_id)),
        ).fetchone()
    return dict(row) if row else None


def safe_color(value):
    if isinstance(value, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", value):
        return value
    return "#4E5B31"


def run_bot_coroutine(coro, timeout=30):
    if discord_bot is None or not discord_bot.is_ready():
        raise RuntimeError("Der Bot ist noch nicht bereit.")
    return asyncio.run_coroutine_threadsafe(coro, discord_bot.loop).result(timeout=timeout)


@app.get("/")
def index():
    if session.get("discord_id"):
        content = (
            f"<h1>Nexora Dashboard</h1>"
            f"<p>Angemeldet als {esc(session.get('username', 'Discord-Nutzer'))}</p>"
            f"<p><a class='button' href='{url_for('servers')}'>Server auswählen</a> "
            f"<a href='{url_for('logout')}'>Abmelden</a></p>"
        )
    else:
        content = (
            "<h1>Nexora Dashboard</h1>"
            "<p>Konfiguriere Bot-Funktionen für Server, die du verwalten darfst.</p>"
            "<a class='button' href='{url_for('login')}'>Mit Discord anmelden</a>"
        )
    return render_page(content)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/login")
def login():
    if not DISCORD_CLIENT_ID or not DISCORD_CLIENT_SECRET or not DISCORD_REDIRECT_URI:
        return "DISCORD_CLIENT_ID, DISCORD_CLIENT_SECRET und DISCORD_REDIRECT_URI müssen gesetzt sein.", 500
    state = secrets.token_urlsafe(32)
    session["oauth_state"] = state
    params = {
        "client_id": DISCORD_CLIENT_ID,
        "redirect_uri": DISCORD_REDIRECT_URI,
        "response_type": "code",
        "scope": "identify guilds",
        "state": state,
    }
    return redirect(f"https://discord.com/oauth2/authorize?{urlencode(params)}")


@app.get("/callback")
def callback():
    if request.args.get("error"):
        return "Discord-Anmeldung wurde abgebrochen.", 400
    code = request.args.get("code")
    state = request.args.get("state", "")
    expected_state = session.pop("oauth_state", "")
    if not code or not expected_state or not secrets.compare_digest(state, expected_state):
        return "Ungültige OAuth-Anfrage. Bitte erneut anmelden.", 400

    token_response = requests.post(
        f"{DISCORD_API}/oauth2/token",
        data={
            "client_id": DISCORD_CLIENT_ID,
            "client_secret": DISCORD_CLIENT_SECRET,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": DISCORD_REDIRECT_URI,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=15,
    )
    if not token_response.ok:
        app.logger.error("Discord-Token-Austausch fehlgeschlagen: %s", token_response.text)
        return "Discord-Anmeldung fehlgeschlagen. Prüfe Client-Daten und Redirect-URL.", 400

    access_token = token_response.json().get("access_token")
    if not access_token:
        return "Discord hat kein Zugriffstoken zurückgegeben.", 400

    user_response = requests.get(
        f"{DISCORD_API}/users/@me",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=15,
    )
    if not user_response.ok:
        return "Discord-Benutzer konnte nicht geladen werden.", 400

    user = user_response.json()
    session.clear()
    session["access_token"] = access_token
    session["username"] = user.get("global_name") or user.get("username") or "Discord-Nutzer"
    session["discord_id"] = user["id"]
    session["csrf_token"] = secrets.token_urlsafe(32)
    return redirect(url_for("servers"))


@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


@app.get("/servers")
def servers():
    login_redirect = require_login()
    if login_redirect:
        return login_redirect
    try:
        manageable = [g for g in get_user_guilds() if can_manage_guild(g)]
    except requests.RequestException:
        return "Die Discord-Serverliste konnte nicht geladen werden.", 502
    if not manageable:
        return render_page("<h1>Keine verwaltbaren Server gefunden</h1><p>Du brauchst Server verwalten oder Administrator.</p>")

    links = "".join(
        f'<li><a href="{url_for("guild_dashboard", guild_id=g["id"])}">{esc(g["name"])}</a></li>'
        for g in manageable
    )
    return render_page(f"<h1>Server auswählen</h1><ul>{links}</ul><p><a href='{url_for('logout')}'>Abmelden</a></p>")


@app.get("/guild/<guild_id>")
def guild_dashboard(guild_id):
    login_redirect = require_login()
    if login_redirect:
        return login_redirect
    try:
        guild_data = authorize_guild(guild_id)
    except requests.RequestException:
        return "Die Serverberechtigung konnte nicht geprüft werden.", 502
    if discord_bot is None or not discord_bot.is_ready():
        return "Der Bot ist noch nicht bereit. Lade die Seite erneut.", 503
    guild = discord_bot.get_guild(int(guild_id))
    if guild is None:
        return "Der Bot ist diesem Server nicht beigetreten.", 404

    settings = db_settings(guild_id)
    text_channels = [c for c in guild.text_channels]
    voice_channels = [c for c in guild.voice_channels]

    def select_options(items, current_value):
        html_out = '<option value="">— Nicht festgelegt —</option>'
        for item in items:
            selected = ' selected' if str(current_value) == str(item.id) else ''
            html_out += f'<option value="{item.id}"{selected}>{esc(item.name)}</option>'
        return html_out

    def role_options(roles, current_value):
        html_out = '<option value="">— Nicht festgelegt —</option>'
        for role in roles:
            if role.name == '@everyone':
                continue
            selected = ' selected' if str(current_value) == str(role.id) else ''
            html_out += f'<option value="{role.id}"{selected}>{esc(role.name)}</option>'
        return html_out

    ticket_support = role_options(guild.roles, settings["support_role_id"])
    ticket_category = select_options(guild.categories, settings["ticket_category_id"])
    ticket_log = select_options(guild.text_channels, settings["ticket_log_channel_id"])
    mod_log = select_options(guild.text_channels, settings["mod_log_channel_id"])
    welcome_channel = select_options(guild.text_channels, settings["welcome_channel_id"])
    leave_channel = select_options(guild.text_channels, settings["leave_channel_id"])
    verify_role = role_options(guild.roles, settings["verification_role_id"])
    verify_channel = select_options(guild.text_channels, settings["verification_channel_id"])
    afk_channel = select_options(guild.voice_channels, settings["afk_channel_id"])
    afk_role = role_options(guild.roles, settings["afk_role_id"])

    templates = ""
    with sqlite3.connect(DATABASE_PATH) as db:
        rows = db.execute(
            "SELECT id,name FROM panel_templates WHERE owner_id=? AND guild_id=? ORDER BY id DESC",
            (str(session["discord_id"]), str(guild_id)),
        ).fetchall()
    templates = "".join(
        f'<li><a href="{url_for("edit_template", guild_id=guild_id, template_id=row[0])}">{esc(row[1])}</a></li>'
        for row in rows
    ) or "<li>Noch keine Vorlagen.</li>"

    content = f"""
    <p><a href='{url_for('servers')}'>← Serverauswahl</a></p>
    <h1>{esc(guild_data['name'])}</h1>

    <div class='card'>
      <h2>Grundfunktionen</h2>
      <form method='post' action='{url_for('save_settings', guild_id=guild_id)}'>{csrf_input()}
        <label>Moderations-Log-Kanal<select name='mod_log_channel_id'>{mod_log}</select></label>
        <label>Begrüßungskanal<select name='welcome_channel_id'>{welcome_channel}</select></label>
        <label>Begrüßungsnachricht<textarea name='welcome_message'>{esc(settings['welcome_message'])}</textarea></label>
        <label>Abschiedskanal<select name='leave_channel_id'>{leave_channel}</select></label>
        <label>Abschiedsnachricht<textarea name='leave_message'>{esc(settings['leave_message'])}</textarea></label>
        <label><input style='width:auto' type='checkbox' name='links_enabled' {'checked' if settings['links_enabled'] else ''}> Links blockieren</label>
        <label><input style='width:auto' type='checkbox' name='spam_enabled' {'checked' if settings['spam_enabled'] else ''}> Spam erkennen</label>
        <label>Spam-Limit (3–12)<input type='number' name='spam_limit' min='3' max='12' value='{int(settings['spam_limit'])}'></label>
        <label>Spam-Fenster (3–30 Sekunden)<input type='number' name='spam_window_seconds' min='3' max='30' value='{int(settings['spam_window_seconds'])}'></label>
        <button>Konfiguration speichern</button>
      </form>
    </div>

    <div class='grid'>
      <div class='card'>
        <h2>Support & Tickets</h2>
        <form method='post' action='{url_for('save_support_settings', guild_id=guild_id)}'>{csrf_input()}
          <label>Support-Rolle<select name='support_role_id'>{ticket_support}</select></label>
          <label>Ticket-Kategorie<select name='ticket_category_id'>{ticket_category}</select></label>
          <label>Ticket-Log-Kanal<select name='ticket_log_channel_id'>{ticket_log}</select></label>
          <button>Support speichern</button>
        </form>
      </div>

      <div class='card'>
        <h2>Verifizierung</h2>
        <form method='post' action='{url_for('save_verification_settings', guild_id=guild_id)}'>{csrf_input()}
          <label>Verifizierungs-Rolle<select name='verification_role_id'>{verify_role}</select></label>
          <label>Verifizierungs-Kanal<select name='verification_channel_id'>{verify_channel}</select></label>
          <label>Nachricht<textarea name='verification_message'>{esc(settings['verification_message'])}</textarea></label>
          <button>Verifizierung speichern</button>
        </form>
      </div>

      <div class='card'>
        <h2>AFK & Rollen</h2>
        <form method='post' action='{url_for('save_afk_settings', guild_id=guild_id)}'>{csrf_input()}
          <label>AFK-Sprachkanal<select name='afk_channel_id'>{afk_channel}</select></label>
          <label>AFK-Rolle<select name='afk_role_id'>{afk_role}</select></label>
          <label>Backup-Stunde (0–23)<input type='number' name='backup_hour' min='0' max='23' value='{int(settings['backup_hour'])}'></label>
          <label><input style='width:auto' type='checkbox' name='backup_enabled' {'checked' if settings['backup_enabled'] else ''}> Backups aktivieren</label>
          <button>AFK/Backup speichern</button>
        </form>
      </div>
    </div>

    <div class='grid'>
      <div class='card'>
        <h2>Rolle erstellen</h2>
        <form method='post' action='{url_for('create_role', guild_id=guild_id)}'>{csrf_input()}
          <label>Rollenname<input name='name' maxlength='100' required></label>
          <button>Rolle erstellen</button>
        </form>
      </div>

      <div class='card'>
        <h2>Kanal erstellen</h2>
        <form method='post' action='{url_for('create_channel', guild_id=guild_id)}'>{csrf_input()}
          <label>Kanalname<input name='name' maxlength='100' required placeholder='allgemein'></label>
          <label>Typ<select name='kind'><option value='text'>Textkanal</option><option value='voice'>Sprachkanal</option></select></label>
          <button>Kanal erstellen</button>
        </form>
      </div>

      <div class='card'>
        <h2>Reaktionsrolle hinzufügen</h2>
        <form method='post' action='{url_for('add_reaction_role', guild_id=guild_id)}'>{csrf_input()}
          <label>Emoji<input name='emoji' maxlength='10' required placeholder='✅'></label>
          <label>Rolle<select name='role_id'>{role_options(guild.roles, '')}</select></label>
          <label>Nachrichtenkanal<select name='channel_id'>{select_options(guild.text_channels, '')}</select></label>
          <label>Nachrichten-ID<input name='message_id' maxlength='20' required placeholder='1234567890'></label>
          <button>Reaktionsrolle speichern</button>
        </form>
      </div>
    </div>

    <div class='card'>
      <h2>Panel-Vorlagen</h2>
      <form method='post' action='{url_for('create_template', guild_id=guild_id)}'>{csrf_input()}
        <label>Vorlagenname<input name='name' maxlength='80' required></label>
        <button>Vorlage erstellen</button>
      </form>
      <ul>{templates}</ul>
    </div>

    <p class='muted'>Automatische Moderation, Tickets, Reaktionsrollen und Verifizierung können über das Dashboard aktiviert werden. Die Bot-Berechtigungen müssen im Discord-Server gesetzt sein.</p>
    """
    return render_page(content)


@app.post("/guild/<guild_id>/settings")
def save_settings(guild_id):
    login_redirect = require_login()
    if login_redirect:
        return login_redirect
    try:
        authorize_guild(guild_id)
    except requests.RequestException:
        return "Die Serverberechtigung konnte nicht geprüft werden.", 502

    guild = discord_bot.get_guild(int(guild_id)) if discord_bot and discord_bot.is_ready() else None
    if guild is None:
        return "Der Bot ist diesem Server nicht beigetreten.", 404

    def channel_value(field):
        value = request.form.get(field, "").strip()
        if not value:
            return ""
        try:
            channel_id = int(value)
        except ValueError:
            abort(400, "Ungültiger Kanal.")
        if not isinstance(guild.get_channel(channel_id), discord.TextChannel):
            abort(400, "Der ausgewählte Kanal ist kein Textkanal dieses Servers.")
        return str(channel_id)

    try:
        limit = max(3, min(12, int(request.form.get("spam_limit", "5"))))
        window = max(3, min(30, int(request.form.get("spam_window_seconds", "8"))))
    except ValueError:
        abort(400, "Spam-Limit und Zeitfenster müssen Zahlen sein.")

    values = (
        str(guild_id),
        channel_value("mod_log_channel_id"),
        channel_value("welcome_channel_id"),
        channel_value("leave_channel_id"),
        request.form.get("welcome_message", "").strip()[:1000],
        request.form.get("leave_message", "").strip()[:1000],
        int("links_enabled" in request.form),
        int("spam_enabled" in request.form),
        limit,
        window,
        "",
        "",
        "",
        "",
        "",
        "Bitte verifiziere dich mit dem Button oder Befehl.",
        "",
        "",
        3,
        0,
    )

    with sqlite3.connect(DATABASE_PATH) as db:
        db.execute(
            """INSERT INTO guild_settings (
                guild_id, mod_log_channel_id, welcome_channel_id, leave_channel_id,
                welcome_message, leave_message, links_enabled, spam_enabled,
                spam_limit, spam_window_seconds, support_role_id, ticket_category_id,
                ticket_log_channel_id, verification_role_id, verification_channel_id,
                verification_message, afk_channel_id, afk_role_id, backup_hour,
                backup_enabled
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                mod_log_channel_id=excluded.mod_log_channel_id,
                welcome_channel_id=excluded.welcome_channel_id,
                leave_channel_id=excluded.leave_channel_id,
                welcome_message=excluded.welcome_message,
                leave_message=excluded.leave_message,
                links_enabled=excluded.links_enabled,
                spam_enabled=excluded.spam_enabled,
                spam_limit=excluded.spam_limit,
                spam_window_seconds=excluded.spam_window_seconds,
                support_role_id=excluded.support_role_id,
                ticket_category_id=excluded.ticket_category_id,
                ticket_log_channel_id=excluded.ticket_log_channel_id,
                verification_role_id=excluded.verification_role_id,
                verification_channel_id=excluded.verification_channel_id,
                verification_message=excluded.verification_message,
                afk_channel_id=excluded.afk_channel_id,
                afk_role_id=excluded.afk_role_id,
                backup_hour=excluded.backup_hour,
                backup_enabled=excluded.backup_enabled""",
            values,
        )
        db.commit()
    return redirect(url_for("guild_dashboard", guild_id=guild_id))


@app.post("/guild/<guild_id>/support")
def save_support_settings(guild_id):
    login_redirect = require_login();
    if login_redirect:
        return login_redirect
    try:
        authorize_guild(guild_id)
    except requests.RequestException:
        return "Die Serverberechtigung konnte nicht geprüft werden.", 502
    guild = discord_bot.get_guild(int(guild_id)) if discord_bot and discord_bot.is_ready() else None
    if guild is None:
        return "Der Bot ist diesem Server nicht beigetreten.", 404

    support_role_id = request.form.get("support_role_id", "").strip()
    ticket_category_id = request.form.get("ticket_category_id", "").strip()
    ticket_log_channel_id = request.form.get("ticket_log_channel_id", "").strip()
    if support_role_id and not any(str(r.id) == support_role_id for r in guild.roles):
        abort(400, "Die Support-Rolle existiert nicht auf diesem Server.")
    if ticket_category_id and not any(str(c.id) == ticket_category_id for c in guild.categories):
        abort(400, "Die Ticket-Kategorie existiert nicht auf diesem Server.")
    if ticket_log_channel_id and not any(str(c.id) == ticket_log_channel_id for c in guild.text_channels):
        abort(400, "Der Ticket-Log-Kanal existiert nicht auf diesem Server.")

    with sqlite3.connect(DATABASE_PATH) as db:
        db.execute(
            """INSERT INTO guild_settings (guild_id, support_role_id, ticket_category_id, ticket_log_channel_id)
            VALUES (?, ?, ?, ?) ON CONFLICT(guild_id) DO UPDATE SET
                support_role_id=excluded.support_role_id,
                ticket_category_id=excluded.ticket_category_id,
                ticket_log_channel_id=excluded.ticket_log_channel_id""",
            (str(guild_id), support_role_id, ticket_category_id, ticket_log_channel_id),
        )
        db.commit()
    return redirect(url_for("guild_dashboard", guild_id=guild_id))


@app.post("/guild/<guild_id>/verify")
def save_verification_settings(guild_id):
    login_redirect = require_login();
    if login_redirect:
        return login_redirect
    try:
        authorize_guild(guild_id)
    except requests.RequestException:
        return "Die Serverberechtigung konnte nicht geprüft werden.", 502
    guild = discord_bot.get_guild(int(guild_id)) if discord_bot and discord_bot.is_ready() else None
    if guild is None:
        return "Der Bot ist diesem Server nicht beigetreten.", 404

    verification_role_id = request.form.get("verification_role_id", "").strip()
    verification_channel_id = request.form.get("verification_channel_id", "").strip()
    verification_message = request.form.get("verification_message", "").strip()[:1000]
    if verification_role_id and not any(str(r.id) == verification_role_id for r in guild.roles):
        abort(400, "Die Verifizierungs-Rolle existiert nicht auf diesem Server.")
    if verification_channel_id and not any(str(c.id) == verification_channel_id for c in guild.text_channels):
        abort(400, "Der Verifizierungs-Kanal existiert nicht auf diesem Server.")

    with sqlite3.connect(DATABASE_PATH) as db:
        db.execute(
            """INSERT INTO guild_settings (guild_id, verification_role_id, verification_channel_id, verification_message)
            VALUES (?, ?, ?, ?) ON CONFLICT(guild_id) DO UPDATE SET
                verification_role_id=excluded.verification_role_id,
                verification_channel_id=excluded.verification_channel_id,
                verification_message=excluded.verification_message""",
            (str(guild_id), verification_role_id, verification_channel_id, verification_message or "Bitte verifiziere dich mit dem Button oder Befehl."),
        )
        db.commit()
    return redirect(url_for("guild_dashboard", guild_id=guild_id))


@app.post("/guild/<guild_id>/afk")
def save_afk_settings(guild_id):
    login_redirect = require_login();
    if login_redirect:
        return login_redirect
    try:
        authorize_guild(guild_id)
    except requests.RequestException:
        return "Die Serverberechtigung konnte nicht geprüft werden.", 502
    guild = discord_bot.get_guild(int(guild_id)) if discord_bot and discord_bot.is_ready() else None
    if guild is None:
        return "Der Bot ist diesem Server nicht beigetreten.", 404

    afk_channel_id = request.form.get("afk_channel_id", "").strip()
    afk_role_id = request.form.get("afk_role_id", "").strip()
    backup_hour = request.form.get("backup_hour", "3").strip()
    backup_enabled = int("backup_enabled" in request.form)

    if afk_channel_id and not any(str(c.id) == afk_channel_id for c in guild.voice_channels):
        abort(400, "Der AFK-Sprachkanal existiert nicht auf diesem Server.")
    if afk_role_id and not any(str(r.id) == afk_role_id for r in guild.roles):
        abort(400, "Die AFK-Rolle existiert nicht auf diesem Server.")

    try:
        backup_hour_int = max(0, min(23, int(backup_hour)))
    except ValueError:
        abort(400, "Die Backup-Stunde muss eine Zahl von 0 bis 23 sein.")

    with sqlite3.connect(DATABASE_PATH) as db:
        db.execute(
            """INSERT INTO guild_settings (guild_id, afk_channel_id, afk_role_id, backup_hour, backup_enabled)
            VALUES (?, ?, ?, ?, ?) ON CONFLICT(guild_id) DO UPDATE SET
                afk_channel_id=excluded.afk_channel_id,
                afk_role_id=excluded.afk_role_id,
                backup_hour=excluded.backup_hour,
                backup_enabled=excluded.backup_enabled""",
            (str(guild_id), afk_channel_id, afk_role_id, backup_hour_int, backup_enabled),
        )
        db.commit()
    return redirect(url_for("guild_dashboard", guild_id=guild_id))


@app.post("/guild/<guild_id>/reaction-role")
def add_reaction_role(guild_id):
    login_redirect = require_login();
    if login_redirect:
        return login_redirect
    try:
        authorize_guild(guild_id)
    except requests.RequestException:
        return "Die Serverberechtigung konnte nicht geprüft werden.", 502

    guild = discord_bot.get_guild(int(guild_id)) if discord_bot and discord_bot.is_ready() else None
    if guild is None:
        return "Der Bot ist diesem Server nicht beigetreten.", 404

    emoji = request.form.get("emoji", "").strip()
    role_id = request.form.get("role_id", "").strip()
    channel_id = request.form.get("channel_id", "").strip()
    message_id = request.form.get("message_id", "").strip()

    valid_role = any(str(r.id) == role_id for r in guild.roles)
    valid_channel = any(str(c.id) == channel_id for c in guild.text_channels)
    if not emoji or not valid_role or not valid_channel or not message_id:
        abort(400, "Emoji, Rolle, Kanal und Nachrichten-ID sind erforderlich.")

    with sqlite3.connect(DATABASE_PATH) as db:
        db.execute(
            "INSERT INTO reaction_roles (guild_id, channel_id, message_id, emoji, role_id) VALUES (?, ?, ?, ?, ?)",
            (str(guild_id), str(channel_id), str(message_id), emoji, str(role_id)),
        )
        db.commit()

    return redirect(url_for("guild_dashboard", guild_id=guild_id))


@app.post("/guild/<guild_id>/role")
def create_role(guild_id):
    login_redirect = require_login()
    if login_redirect:
        return login_redirect
    try:
        authorize_guild(guild_id)
    except requests.RequestException:
        return "Die Serverberechtigung konnte nicht geprüft werden.", 502

    guild = discord_bot.get_guild(int(guild_id)) if discord_bot and discord_bot.is_ready() else None
    name = request.form.get("name", "").strip()[:100]
    if guild is None:
        return "Bot oder Server ist nicht bereit.", 503
    if not name:
        abort(400, "Bitte gib einen Rollennamen ein.")
    if guild.me is None or not guild.me.guild_permissions.manage_roles:
        abort(403, "Dem Bot fehlt die Berechtigung Rollen verwalten.")

    try:
        run_bot_coroutine(guild.create_role(name=name, reason="Über Nexora Dashboard erstellt"))
    except Exception:
        app.logger.exception("Rolle konnte nicht erstellt werden.")
        return "Rolle konnte nicht erstellt werden. Prüfe Bot-Berechtigungen und Rollenreihenfolge.", 502
    return redirect(url_for("guild_dashboard", guild_id=guild_id))


@app.post("/guild/<guild_id>/channel")
def create_channel(guild_id):
    login_redirect = require_login();
    if login_redirect:
        return login_redirect
    try:
        authorize_guild(guild_id)
    except requests.RequestException:
        return "Die Serverberechtigung konnte nicht geprüft werden.", 502

    guild = discord_bot.get_guild(int(guild_id)) if discord_bot and discord_bot.is_ready() else None
    raw_name = request.form.get("name", "").strip().lower()
    name = re.sub(r"[^a-z0-9_-]+", "-", raw_name).strip("-")[:100]
    kind = request.form.get("kind", "text")
    if guild is None:
        return "Bot oder Server ist nicht bereit.", 503
    if not name or kind not in {"text", "voice"}:
        abort(400, "Ungültiger Kanalname oder Kanaltyp.")
    if guild.me is None or not guild.me.guild_permissions.manage_channels:
        abort(403, "Dem Bot fehlt die Berechtigung Kanäle verwalten.")

    try:
        if kind == "text":
            run_bot_coroutine(guild.create_text_channel(name=name, reason="Über Nexora Dashboard erstellt"))
        else:
            run_bot_coroutine(guild.create_voice_channel(name=name, reason="Über Nexora Dashboard erstellt"))
    except Exception:
        app.logger.exception("Kanal konnte nicht erstellt werden.")
        return "Kanal konnte nicht erstellt werden. Prüfe Bot-Berechtigungen.", 502
    return redirect(url_for("guild_dashboard", guild_id=guild_id))


@app.post("/guild/<guild_id>/panel/new")
def create_template(guild_id):
    login_redirect = require_login();
    if login_redirect:
        return login_redirect
    try:
        authorize_guild(guild_id)
    except requests.RequestException:
        return "Die Serverberechtigung konnte nicht geprüft werden.", 502

    name = request.form.get("name", "").strip()[:80]
    if not name:
        abort(400, "Bitte gib einen Vorlagennamen ein.")

    with sqlite3.connect(DATABASE_PATH) as db:
        cursor = db.execute(
            "INSERT INTO panel_templates (owner_id, guild_id, name) VALUES (?, ?, ?)",
            (str(session["discord_id"]), str(guild_id), name),
        )
        template_id = cursor.lastrowid
        db.commit()
    return redirect(url_for("edit_template", guild_id=guild_id, template_id=template_id))


@app.route("/guild/<guild_id>/panel/<int:template_id>", methods=["GET", "POST"])
def edit_template(guild_id, template_id):
    login_redirect = require_login();
    if login_redirect:
        return login_redirect
    try:
        authorize_guild(guild_id)
    except requests.RequestException:
        return "Die Serverberechtigung konnte nicht geprüft werden.", 502

    template = get_template(template_id, session["discord_id"], guild_id)
    if template is None:
        abort(404)

    if request.method == "POST":
        name = request.form.get("name", "").strip()[:80]
        title = request.form.get("title", "").strip()[:256]
        body = request.form.get("body", "").strip()[:4000]
        color = safe_color(request.form.get("color"))
        if not name or not body:
            abort(400, "Name und Nachricht müssen ausgefüllt sein.")
        with sqlite3.connect(DATABASE_PATH) as db:
            db.execute(
                "UPDATE panel_templates SET name=?, title=?, body=?, color=?, updated_at=CURRENT_TIMESTAMP WHERE id=? AND owner_id=? AND guild_id=?",
                (name, title, body, color, template_id, str(session["discord_id"]), str(guild_id)),
            )
            db.commit()
        return redirect(url_for("edit_template", guild_id=guild_id, template_id=template_id))

    guild = discord_bot.get_guild(int(guild_id)) if discord_bot and discord_bot.is_ready() else None
    if guild is None:
        return "Der Bot ist noch nicht bereit oder nicht auf diesem Server.", 503
    channels = [
        c for c in guild.text_channels
        if c.permissions_for(guild.me).send_messages and c.permissions_for(guild.me).embed_links
    ]
    options = "".join(f'<option value="{c.id}">#{esc(c.name)}</option>' for c in channels)
    content = f"""
    <p><a href='{url_for('guild_dashboard', guild_id=guild_id)}'>← Zurück</a></p>
    <h1>Panel-Vorlage bearbeiten</h1>
    <form method='post'>{csrf_input()}
      <label>Name<input name='name' maxlength='80' value='{esc(template['name'])}' required></label>
      <label>Titel<input name='title' maxlength='256' value='{esc(template['title'])}'></label>
      <label>Nachricht<textarea name='body' maxlength='4000' required>{esc(template['body'])}</textarea></label>
      <label>Farbe<input type='color' name='color' value='{safe_color(template['color'])}'></label>
      <button>Speichern</button>
    </form>
    <h2>Panel senden</h2>
    <form method='post' action='{url_for('send_panel', guild_id=guild_id, template_id=template_id)}'>{csrf_input()}
      <label>Zielkanal<select name='channel_id' required>{options}</select></label>
      <button>Panel senden</button>
    </form>
    """
    return render_page(content)


@app.post("/guild/<guild_id>/panel/<int:template_id>/send")
def send_panel(guild_id, template_id):
    login_redirect = require_login();
    if login_redirect:
        return login_redirect
    try:
        authorize_guild(guild_id)
    except requests.RequestException:
        return "Die Serverberechtigung konnte nicht geprüft werden.", 502

    template = get_template(template_id, session["discord_id"], guild_id)
    if template is None:
        abort(404)

    guild = discord_bot.get_guild(int(guild_id)) if discord_bot and discord_bot.is_ready() else None
    try:
        channel_id = int(request.form.get("channel_id", ""))
    except ValueError:
        abort(400, "Ungültiger Zielkanal.")

    channel = guild.get_channel(channel_id) if guild else None
    if not isinstance(channel, discord.TextChannel):
        abort(404, "Zielkanal wurde nicht gefunden.")

    permissions = channel.permissions_for(guild.me)
    if not permissions.send_messages or not permissions.embed_links:
        abort(403, "Dem Bot fehlen Nachrichten senden oder Embeds verlinken.")

    embed = discord.Embed(
        title=template["title"] or None,
        description=template["body"],
        color=discord.Color(int(safe_color(template["color"])[1:], 16)),
    )
    try:
        run_bot_coroutine(channel.send(embed=embed))
    except Exception:
        app.logger.exception("Panel konnte nicht gesendet werden.")
        return "Panel konnte nicht gesendet werden. Prüfe Bot-Berechtigungen.", 502
    return redirect(url_for("guild_dashboard", guild_id=guild_id))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)





























































































































































































































































































































































































































































































































