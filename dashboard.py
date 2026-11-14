import os
from urllib.parse import urlencode

import requests
from dotenv import load_dotenv

from flask import Flask, redirect, render_template_string, request, session, url_for


load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY")

DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
REDIRECT_URI = os.getenv(
    "DISCORD_REDIRECT_URI",
    "http://localhost:5000/callback",
)

HTML = """
<h1>Nexora Dashboard</h1>
{% if user %}
  <p>Angemeldet als {{ user.username }}</p>
  <h2>Deine Discord-Server</h2>
  <ul>
    {% for guild in guilds %}
      <li>{{ guild.name }}</li>
    {% else %}
      <li>Keine Server gefunden.</li>
    {% endfor %}
  </ul>
  <a href="{{ url_for('logout') }}">Abmelden</a>
{% else %}
  <a href="{{ url_for('login') }}">Mit Discord anmelden</a>
{% endif %}
"""

@app.get("/")
def index():
    user = session.get("user")
    return render_template_string(HTML, user=user, guilds=session.get("guilds", []))

@app.get("/login")
def login():
    params = {
        "client_id": DISCORD_CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": "identify guilds",
    }
    return redirect(
        "https://discord.com/oauth2/authorize?" + urlencode(params)
    )

@app.get("/callback")
def callback():
    code = request.args.get("code")
    if not code:
        return "Discord-Anmeldung fehlgeschlagen: Kein Code erhalten.", 400

    token_response = requests.post(
        "https://discord.com/api/oauth2/token",
        data={
            "client_id": DISCORD_CLIENT_ID,
            "client_secret": DISCORD_CLIENT_SECRET,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=15,
    )
    token_response.raise_for_status()
    access_token = token_response.json()["access_token"]
    headers = {"Authorization": f"Bearer {access_token}"}

    user_response = requests.get(
        "https://discord.com/api/users/@me",
        headers=headers,
        timeout=15,
    )
    user_response.raise_for_status()

    guild_response = requests.get(
        "https://discord.com/api/users/@me/guilds",
        headers=headers,
        timeout=15,
    )
    guild_response.raise_for_status()

    session["user"] = user_response.json()
    session["guilds"] = guild_response.json()
    return redirect(url_for("index"))

@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))

if __name__ == "__main__":
    required = [
        "FLASK_SECRET_KEY",
        "DISCORD_CLIENT_ID",
        "DISCORD_CLIENT_SECRET",
    ]
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise RuntimeError("Fehlende Umgebungsvariablen: " + ", ".join(missing))

    port = int(os.environ.get("SERVER_PORT", "25386"))
    app.run(host="0.0.0.0", port=port)
