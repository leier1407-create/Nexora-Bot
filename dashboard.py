import os
from urllib.parse import urlencode

import requests
from flask import Flask, redirect, render_template_string, request, session, url_for

app = Flask(__name__)

app.secret_key = os.getenv("FLASK_SECRET_KEY", "BITTE-DURCH-EINEN-LANGEN-ZUFAELLIGEN-WERT-ERSETZEN")

DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID", "")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET", "")

# Diese exakte URL muss auch in der Discord Developer Console unter
# OAuth2 → Redirects eingetragen sein.
DISCORD_REDIRECT_URI = os.getenv(
    "DISCORD_REDIRECT_URI",
    "https://61x696szc1.apps.bot-hosting.cloud/callback",
)

DISCORD_API = "https://discord.com/api/v10"

@app.get("/")
def index():
    username = session.get("username")

    if username:
        content = f"""
        <h1>Willkommen, {username}</h1>
        <p>Du bist mit Discord angemeldet.</p>
        <p><a href="{url_for('logout')}">Abmelden</a></p>
        """
    else:
        content = f"""
        <h1>Nexora Dashboard</h1>
        <p><a href="{url_for('login')}">Mit Discord anmelden</a></p>
        """

    return render_template_string(PAGE, content=content)

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/login")
def login():
    if not DISCORD_CLIENT_ID or not DISCORD_CLIENT_SECRET:
        return "DISCORD_CLIENT_ID oder DISCORD_CLIENT_SECRET fehlt in den Umgebungsvariablen.", 500

    params = {
        "client_id": DISCORD_CLIENT_ID,
        "redirect_uri": DISCORD_REDIRECT_URI,
        "response_type": "code",
        "scope": "identify",
    }
    return redirect(f"https://discord.com/oauth2/authorize?{urlencode(params)}")

@app.get("/callback")
def callback():
    error = request.args.get("error")
    if error:
        return f"Discord-Anmeldung abgebrochen: {error}", 400

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
        return "Discord-Anmeldung fehlgeschlagen. Prüfe Client-ID, Client-Secret und Redirect-URL.", 400

    access_token = token_response.json().get("access_token")
    user_response = requests.get(
        f"{DISCORD_API}/users/@me",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=15,
    )

    if not user_response.ok:
        app.logger.error("Discord-Benutzerabfrage fehlgeschlagen: %s", user_response.text)
        return "Discord-Benutzer konnte nicht geladen werden.", 400

    user = user_response.json()
    session["username"] = user.get("global_name") or user.get("username", "Discord-Nutzer")
    session["discord_id"] = user["id"]
    return redirect(url_for("index"))

@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))

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
      min-height: 100vh;
      display: grid;
      place-items: center;
      background: #111827;
      color: #f9fafb;
      font: 16px system-ui, sans-serif;
    }
    main {
      width: min(90%, 600px);
      padding: 32px;
      border: 1px solid #374151;
      border-radius: 16px;
      background: #1f2937;
    }
    a { color: #93c5fd; }
  </style>
</head>
<body><main>{{ content|safe }}</main></body>
</html>
"""
