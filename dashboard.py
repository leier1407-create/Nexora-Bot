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
<html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Nexora Dashboard</title><style>
body{margin:0;padding:24px;background:#111827;color:#f9fafb;font:16px system-ui,sans-serif}main{max-width:980px;margin:24px auto;padding:24px;border:1px solid #374151;border-radius:16px;background:#1f2937}
input,textarea,select{box-sizing:border-box;width:100%;margin:7px 0 16px;padding:11px;border:1px solid #4b5563;border-radius:8px;background:#111827;color:#f9fafb;font:inherit}textarea{min-height:90px}button,.button{display:inline-block;width:auto;padding:11px 16px;border:0;border-radius:8px;background:#4E5B31;color:white;cursor:pointer;text-decoration:none}a{color:#93c5fd}label{display:block;margin-top:12px}.card{margin:18px 0;padding:18px;background:#111827;border:1px solid #374151;border-radius:12px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:16px}.muted{color:#9ca3af}hr{border-color:#374151}
</style></head><body><main>{{ content|safe }}</main></body></html>"""


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
            spam_window_seconds INTEGER NOT NULL DEFAULT 8
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS panel_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT, owner_id TEXT NOT NULL,
            guild_id TEXT NOT NULL, name TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '', body TEXT NOT NULL DEFAULT '',
            color TEXT NOT NULL DEFAULT '#4E5B31',
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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
        "guild_id": str(guild_id), "mod_log_channel_id": "", "welcome_channel_id": "",
        "leave_channel_id": "", "welcome_message": "Willkommen {user} auf {server}!",
        "leave_message": "{user} hat den Server verlassen.", "links_enabled": 0,
        "spam_enabled": 0, "spam_limit": 5, "spam_window_seconds": 8,
    }
    if row:
        defaults.update(dict(row))
    return defaults


def get_template(template_id, owner_id, guild_id):
    with sqlite3.connect(DATABASE_PATH) as db:
        db.row_factory = sqlite3.Row
        row = db.execute("SELECT id,name,title,body,color FROM panel_templates WHERE id=? AND owner_id=? AND guild_id=?",
                         (template_id, str(owner_id), str(guild_id))).fetchone()
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
        content = f"<h1>Nexora Dashboard</h1><p>Angemeldet als {esc(session.get('username', 'Discord-Nutzer'))}</p><p><a class='button' href='{url_for('servers')}'>Server auswählen</a> <a href='{url_for('logout')}'>Abmelden</a></p>"
    else:
        content = f"<h1>Nexora Dashboard</h1><p>Konfiguriere Bot-Funktionen für Server, die du verwalten darfst.</p><a class='button' href='{url_for('login')}'>Mit Discord anmelden</a>"
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
    params = {"client_id": DISCORD_CLIENT_ID, "redirect_uri": DISCORD_REDIRECT_URI,
              "response_type": "code", "scope": "identify guilds", "state": state}
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
    token_response = requests.post(f"{DISCORD_API}/oauth2/token", data={
        "client_id": DISCORD_CLIENT_ID, "client_secret": DISCORD_CLIENT_SECRET,
        "grant_type": "authorization_code", "code": code, "redirect_uri": DISCORD_REDIRECT_URI,
    }, headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=15)
    if not token_response.ok:
        app.logger.error("Discord-Token-Austausch fehlgeschlagen: %s", token_response.text)
        return "Discord-Anmeldung fehlgeschlagen. Prüfe Client-Daten und Redirect-URL.", 400
    access_token = token_response.json().get("access_token")
    if not access_token:
        return "Discord hat kein Zugriffstoken zurückgegeben.", 400
    user_response = requests.get(f"{DISCORD_API}/users/@me",
                                 headers={"Authorization": f"Bearer {access_token}"}, timeout=15)
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
    links = "".join(f'<li><a href="{url_for("guild_dashboard", guild_id=g["id"])}">{esc(g["name"])}</a></li>' for g in manageable)
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
    options = '<option value="">— Nicht festgelegt —</option>'
    for channel in text_channels:
        options += f'<option value="{channel.id}">{esc("#" + channel.name)}</option>'
    def selected_option(current):
        return options.replace(f'value="{current}"', f'value="{current}" selected') if current else options
    mod_options = selected_option(settings["mod_log_channel_id"])
    welcome_options = selected_option(settings["welcome_channel_id"])
    leave_options = selected_option(settings["leave_channel_id"])
    templates = ""
    with sqlite3.connect(DATABASE_PATH) as db:
        rows = db.execute("SELECT id,name FROM panel_templates WHERE owner_id=? AND guild_id=? ORDER BY id DESC",
                          (str(session["discord_id"]), str(guild_id))).fetchall()
    templates = "".join(f'<li><a href="{url_for("edit_template", guild_id=guild_id, template_id=row[0])}">{esc(row[1])}</a></li>' for row in rows) or "<li>Noch keine Vorlagen.</li>"
    checked_links = "checked" if settings["links_enabled"] else ""
    checked_spam = "checked" if settings["spam_enabled"] else ""
    content = f"""<p><a href='{url_for('servers')}'>← Serverauswahl</a></p><h1>{esc(guild_data['name'])}</h1>
    <div class='card'><h2>Serverfunktionen</h2><form method='post' action='{url_for('save_settings', guild_id=guild_id)}'>{csrf_input()}
    <label>Moderations- und Ereignis-Log<select name='mod_log_channel_id'>{mod_options}</select></label>
    <label>Begrüßungskanal<select name='welcome_channel_id'>{welcome_options}</select></label>
    <label>Begrüßungsnachricht (Platzhalter: {{user}}, {{server}}, {{member_count}})<textarea name='welcome_message' maxlength='1000'>{esc(settings['welcome_message'])}</textarea></label>
    <label>Abschiedskanal<select name='leave_channel_id'>{leave_options}</select></label>
    <label>Abschiedsnachricht (Platzhalter: {{user}}, {{server}}, {{member_count}})<textarea name='leave_message' maxlength='1000'>{esc(settings['leave_message'])}</textarea></label>
    <label><input style='width:auto' type='checkbox' name='links_enabled' {checked_links}> Links in Nachrichten blockieren</label>
    <label><input style='width:auto' type='checkbox' name='spam_enabled' {checked_spam}> Nachrichten-Spam erkennen und löschen</label>
    <label>Spam-Limit (3–12 Nachrichten)<input type='number' name='spam_limit' min='3' max='12' value='{int(settings['spam_limit'])}'></label>
    <label>Zeitfenster in Sekunden (3–30)<input type='number' name='spam_window_seconds' min='3' max='30' value='{int(settings['spam_window_seconds'])}'></label>
    <button>Konfiguration speichern</button></form></div>
    <div class='grid'><section class='card'><h2>Rolle erstellen</h2><form method='post' action='{url_for('create_role', guild_id=guild_id)}'>{csrf_input()}<label>Rollenname<input name='name' maxlength='100' required></label><button>Rolle erstellen</button></form></section>
    <section class='card'><h2>Kanal erstellen</h2><form method='post' action='{url_for('create_channel', guild_id=guild_id)}'>{csrf_input()}<label>Kanalname<input name='name' maxlength='100' required placeholder='allgemein'></label><label>Typ<select name='kind'><option value='text'>Textkanal</option><option value='voice'>Sprachkanal</option></select></label><button>Kanal erstellen</button></form></section></div>
    <section class='card'><h2>Discord-Panels</h2><form method='post' action='{url_for('create_template', guild_id=guild_id)}'>{csrf_input()}<label>Vorlagenname<input name='name' maxlength='80' required></label><button>Vorlage erstellen</button></form><ul>{templates}</ul></section>
    <p class='muted'>Automatische Moderation entfernt passende Nachrichten. Bot-Berechtigungen und aktivierte Discord-Intents sind erforderlich.</p>"""
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
    values = (str(guild_id), channel_value("mod_log_channel_id"), channel_value("welcome_channel_id"),
              channel_value("leave_channel_id"), request.form.get("welcome_message", "").strip()[:1000],
              request.form.get("leave_message", "").strip()[:1000], int("links_enabled" in request.form),
              int("spam_enabled" in request.form), limit, window)
    with sqlite3.connect(DATABASE_PATH) as db:
        db.execute("""INSERT INTO guild_settings (guild_id,mod_log_channel_id,welcome_channel_id,leave_channel_id,welcome_message,leave_message,links_enabled,spam_enabled,spam_limit,spam_window_seconds)
        VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(guild_id) DO UPDATE SET mod_log_channel_id=excluded.mod_log_channel_id,welcome_channel_id=excluded.welcome_channel_id,leave_channel_id=excluded.leave_channel_id,welcome_message=excluded.welcome_message,leave_message=excluded.leave_message,links_enabled=excluded.links_enabled,spam_enabled=excluded.spam_enabled,spam_limit=excluded.spam_limit,spam_window_seconds=excluded.spam_window_seconds""", values)
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
    login_redirect = require_login()
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
    login_redirect = require_login()
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
        cursor = db.execute("INSERT INTO panel_templates (owner_id,guild_id,name) VALUES (?,?,?)",
                            (str(session["discord_id"]), str(guild_id), name))
        template_id = cursor.lastrowid
        db.commit()
    return redirect(url_for("edit_template", guild_id=guild_id, template_id=template_id))


@app.route("/guild/<guild_id>/panel/<int:template_id>", methods=["GET", "POST"])
def edit_template(guild_id, template_id):
    login_redirect = require_login()
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
            db.execute("UPDATE panel_templates SET name=?,title=?,body=?,color=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND owner_id=? AND guild_id=?",
                       (name, title, body, color, template_id, str(session["discord_id"]), str(guild_id)))
            db.commit()
        return redirect(url_for("edit_template", guild_id=guild_id, template_id=template_id))
    guild = discord_bot.get_guild(int(guild_id)) if discord_bot and discord_bot.is_ready() else None
    if guild is None:
        return "Der Bot ist noch nicht bereit oder nicht auf diesem Server.", 503
    channels = [c for c in guild.text_channels if c.permissions_for(guild.me).send_messages and c.permissions_for(guild.me).embed_links]
    options = "".join(f'<option value="{c.id}">#{esc(c.name)}</option>' for c in channels)
    content = f"""<p><a href='{url_for('guild_dashboard', guild_id=guild_id)}'>← Zurück</a></p><h1>Panel-Vorlage bearbeiten</h1>
    <form method='post'>{csrf_input()}<label>Name<input name='name' maxlength='80' value='{esc(template['name'])}' required></label><label>Titel<input name='title' maxlength='256' value='{esc(template['title'])}'></label><label>Nachricht<textarea name='body' maxlength='4000' required>{esc(template['body'])}</textarea></label><label>Farbe<input type='color' name='color' value='{safe_color(template['color'])}'></label><button>Speichern</button></form>
    <h2>Panel senden</h2><form method='post' action='{url_for('send_panel',guild_id=guild_id,template_id=template_id)}'>{csrf_input()}<label>Zielkanal<select name='channel_id' required>{options}</select></label><button>Panel senden</button></form>"""
    return render_page(content)


@app.post("/guild/<guild_id>/panel/<int:template_id>/send")
def send_panel(guild_id, template_id):
    login_redirect = require_login()
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
    embed = discord.Embed(title=template["title"] or None, description=template["body"],
                          color=discord.Color(int(safe_color(template["color"])[1:], 16)))
    try:
        run_bot_coroutine(channel.send(embed=embed))
    except Exception:
        app.logger.exception("Panel konnte nicht gesendet werden.")
        return "Panel konnte nicht gesendet werden. Prüfe Bot-Berechtigungen.", 502
    return redirect(url_for("guild_dashboard", guild_id=guild_id))
