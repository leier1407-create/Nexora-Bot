import asyncio
import html
import os
import sqlite3
from threading import Thread
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
DISCORD_REDIRECT_URI = os.getenv(
    "DISCORD_REDIRECT_URI",
    "https://61x696szc1.apps.bot-hosting.cloud/callback",
)
DATABASE_PATH = os.getenv("DATABASE_PATH", "warnings.db")
DISCORD_API = "https://discord.com/api/v10"

discord_bot = None

PAGE = """
<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Nexora Dashboard</title>
  <style>
    body {
      margin: 0;
      padding: 24px;
      background: #111827;
      color: #f9fafb;
      font: 16px system-ui, sans-serif;
    }
    main {
      max-width: 850px;
      margin: 24px auto;
      padding: 24px;
      border: 1px solid #374151;
      border-radius: 16px;
      background: #1f2937;
    }
    input, textarea, select, button {
      box-sizing: border-box;
      width: 100%;
      margin: 7px 0 16px;
      padding: 11px;
      border: 1px solid #4b5563;
      border-radius: 8px;
      background: #111827;
      color: #f9fafb;
      font: inherit;
    }
    textarea { min-height: 130px; resize: vertical; }
    button, .button {
      display: inline-block;
      width: auto;
      padding: 11px 16px;
      border: 0;
      border-radius: 8px;
      background: #4E5B31;
      color: white;
      cursor: pointer;
      text-decoration: none;
    }
    a { color: #93c5fd; }
    label { display: block; }
    .preview {
      margin: 16px 0;
      padding: 16px;
      border-left: 5px solid var(--panel-color, #4E5B31);
      border-radius: 8px;
      background: #111827;
      overflow-wrap: anywhere;
    }
    .muted { color: #9ca3af; }
  </style>
</head>
<body>
  <main>{{ content|safe }}</main>
</body>
</html>
"""

def configure_bot(bot):
    """Wird von bot.py aufgerufen, damit das Dashboard den Bot verwenden kann."""
    global discord_bot
    discord_bot = bot

def initialize_dashboard_database():
    with sqlite3.connect(DATABASE_PATH) as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS panel_templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id TEXT NOT NULL,
                guild_id TEXT NOT NULL,
                name TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                body TEXT NOT NULL DEFAULT '',
                color TEXT NOT NULL DEFAULT '#4E5B31',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        db.commit()

initialize_dashboard_database()

def render_page(content):
    return render_template_string(PAGE, content=content)

def escape(value):
    return html.escape(str(value), quote=True)

def safe_color(value):
    if isinstance(value, str) and len(value) == 7 and value.startswith("#"):
        try:
            int(value[1:], 16)
            return value
        except ValueError:
            pass
    return "#4E5B31"

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
    # Discord-Berechtigungsbit 32 = Server verwalten, Bit 8 = Administrator.
    permissions = int(guild_data.get("permissions", "0"))
    return bool(permissions & 32 or permissions & 8)

def authorize_guild(guild_id):
    guilds = get_user_guilds()
    for guild_data in guilds:
        if guild_data["id"] == str(guild_id) and can_manage_guild(guild_data):
            return guild_data
    abort(403, "Du brauchst auf diesem Server Serververwaltungsrechte oder Administrator.")

def get_template(template_id, owner_id, guild_id):
    with sqlite3.connect(DATABASE_PATH) as db:
        db.row_factory = sqlite3.Row
        row = db.execute(
            """
            SELECT id, name, title, body, color
            FROM panel_templates
            WHERE id = ? AND owner_id = ? AND guild_id = ?
            """,
            (template_id, str(owner_id), str(guild_id)),
        ).fetchone()
    return dict(row) if row else None

def get_templates(owner_id, guild_id):
    with sqlite3.connect(DATABASE_PATH) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute(
            """
            SELECT id, name
            FROM panel_templates
            WHERE owner_id = ? AND guild_id = ?
            ORDER BY id DESC
            """,
            (str(owner_id), str(guild_id)),
        ).fetchall()
    return [dict(row) for row in rows]

@app.get("/")
def index():
    if session.get("discord_id"):
        content = f"""
        <h1>Willkommen, {escape(session.get("username", "Discord-Nutzer"))}</h1>
        <p>Verwalte deine Panel-Nachrichten über das Dashboard.</p>
        <p><a class="button" href="{url_for('servers')}">Server auswählen</a></p>
        <p><a href="{url_for('logout')}">Abmelden</a></p>
        """
    else:
        content = f"""
        <h1>Nexora Dashboard</h1>
        <p><a class="button" href="{url_for('login')}">Mit Discord anmelden</a></p>
        """
    return render_page(content)

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/login")
def login():
    if not DISCORD_CLIENT_ID or not DISCORD_CLIENT_SECRET or not DISCORD_REDIRECT_URI:
        return "DISCORD_CLIENT_ID, DISCORD_CLIENT_SECRET oder DISCORD_REDIRECT_URI fehlt.", 500

    params = {
        "client_id": DISCORD_CLIENT_ID,
        "redirect_uri": DISCORD_REDIRECT_URI,
        "response_type": "code",
        "scope": "identify guilds",
    }
    return redirect(f"https://discord.com/oauth2/authorize?{urlencode(params)}")

@app.get("/callback")
def callback():
    if request.args.get("error"):
        return "Discord-Anmeldung wurde abgebrochen.", 400

    code = request.args.get("code")
    if not code:
        return "Discord hat keinen OAuth-Code zurückgegeben.", 400

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
    session["username"] = user.get("global_name") or user.get("username", "Discord-Nutzer")
    session["discord_id"] = user["id"]

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
        guilds = get_user_guilds()
    except requests.RequestException:
        return "Die Discord-Serverliste konnte nicht geladen werden.", 502

    manageable = [g for g in guilds if can_manage_guild(g)]
    if not manageable:
        return render_page(
            "<h1>Keine verwaltbaren Server gefunden</h1>"
            "<p>Du brauchst Server verwalten oder Administrator.</p>"
        )

    links = "".join(
        f'<li><a href="{url_for("guild_dashboard", guild_id=g["id"])}">'
        f'{escape(g["name"])}</a></li>'
        for g in manageable
    )
    return render_page(
        f'<h1>Server auswählen</h1><ul>{links}</ul>'
        f'<p><a href="{url_for("logout")}">Abmelden</a></p>'
    )

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

    templates = get_templates(session["discord_id"], guild_id)
    template_links = "".join(
        f'<li><a href="{url_for("edit_template", guild_id=guild_id, template_id=t["id"])}">'
        f'{escape(t["name"])}</a></li>'
        for t in templates
    )

    return render_page(f"""
      <p><a href="{url_for('servers')}">Zur Serverauswahl</a></p>
      <h1>{escape(guild_data["name"])}</h1>

      <h2>Panel-Vorlage erstellen</h2>
      <form method="post" action="{url_for('create_template', guild_id=guild_id)}">
        <label>Name der Vorlage
          <input name="name" maxlength="80" required placeholder="Zum Beispiel: Ticket-Panel">
        </label>
        <button type="submit">Vorlage erstellen</button>
      </form>

      <h2>Gespeicherte Vorlagen</h2>
      <ul>{template_links or "<li>Noch keine Vorlagen.</li>"}</ul>
    """)

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
        abort(400, "Bitte gib einen Namen für die Vorlage ein.")

    with sqlite3.connect(DATABASE_PATH) as db:
        cursor = db.execute(
            """
            INSERT INTO panel_templates (owner_id, guild_id, name)
            VALUES (?, ?, ?)
            """,
            (str(session["discord_id"]), str(guild_id), name),
        )
        template_id = cursor.lastrowid
        db.commit()

    return redirect(url_for("edit_template", guild_id=guild_id, template_id=template_id))

@app.route("/guild/<guild_id>/panel/<int:template_id>", methods=["GET", "POST"])
def edit_template(guild_id, template_id):
    login_redirect = require_login()
    if login_redirect:
        return login_redirect

    try:
        guild_data = authorize_guild(guild_id)
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
                """
                UPDATE panel_templates
                SET name = ?, title = ?, body = ?, color = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND owner_id = ? AND guild_id = ?
                """,
                (
                    name, title, body, color, template_id,
                    str(session["discord_id"]), str(guild_id),
                ),
            )
            db.commit()

        return redirect(url_for("edit_template", guild_id=guild_id, template_id=template_id))

    if discord_bot is None or not discord_bot.is_ready():
        return "Der Bot ist noch nicht bereit. Lade die Seite erneut.", 503

    guild = discord_bot.get_guild(int(guild_id))
    if guild is None:
        return "Der Bot ist diesem Server nicht beigetreten.", 404

    channels = [
        channel for channel in guild.text_channels
        if channel.permissions_for(guild.me).send_messages
        and channel.permissions_for(guild.me).embed_links
    ]
    options = "".join(
        f'<option value="{channel.id}">#{escape(channel.name)}</option>'
        for channel in channels
    )

    color = safe_color(template["color"])
    title = escape(template["title"])
    body = escape(template["body"])
    name = escape(template["name"])

    return render_page(f"""
      <p><a href="{url_for('guild_dashboard', guild_id=guild_id)}">
        Zurück zu {escape(guild_data["name"])}
      </a></p>

      <h1>Panel bearbeiten</h1>
      <form method="post">
        <label>Name der Vorlage
          <input name="name" maxlength="80" required value="{name}">
        </label>
        <label>Titel
          <input name="title" maxlength="256" value="{title}">
        </label>
        <label>Nachricht
          <textarea name="body" maxlength="4000" required>{body}</textarea>
        </label>
        <label>Farbe
          <input name="color" type="color" value="{color}">
        </label>
        <button type="submit">Änderungen speichern</button>
      </form>

      <h2>Vorschau</h2>
      <div class="preview" style="--panel-color:{color}">
        <strong>{title or "Panel-Titel"}</strong><br><br>
        {body or "Hier erscheint die Nachrichtenvorschau."}
      </div>

      <h2>Panel schicken</h2>
      <form method="post"
            action="{url_for('send_panel', guild_id=guild_id, template_id=template_id)}">
        <label>Zielkanal
          <select name="channel_id" required>{options}</select>
        </label>
        <button type="submit">Panel schicken</button>
      </form>
    """)

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

    if discord_bot is None or not discord_bot.is_ready():
        return "Der Bot ist noch nicht bereit.", 503

    try:
        channel_id = int(request.form.get("channel_id", ""))
    except ValueError:
        abort(400, "Ungültiger Zielkanal.")

    guild = discord_bot.get_guild(int(guild_id))
    channel = guild.get_channel(channel_id) if guild else None

    if not isinstance(channel, discord.TextChannel):
        abort(404, "Der Zielkanal wurde nicht gefunden.")

    permissions = channel.permissions_for(guild.me)
    if not permissions.send_messages or not permissions.embed_links:
        abort(403, "Dem Bot fehlen „Nachrichten senden“ oder „Embeds verlinken“.")

    embed = discord.Embed(
        title=template["title"] or None,
        description=template["body"],
        color=discord.Color(int(safe_color(template["color"])[1:], 16)),
    )

    try:
        future = asyncio.run_coroutine_threadsafe(
            channel.send(embed=embed),
            discord_bot.loop,
        )
        future.result(timeout=20)
    except Exception:
        app.logger.exception("Panel konnte nicht gesendet werden.")
        return "Panel konnte nicht gesendet werden. Prüfe die Bot-Berechtigungen.", 502

    return render_page(
        f"<h1>Panel wurde in #{escape(channel.name)} geschickt.</h1>"
        f'<p><a href="{url_for("edit_template", guild_id=guild_id, template_id=template_id)}">'
        "Zurück zur Vorlage</a></p>"
    )

