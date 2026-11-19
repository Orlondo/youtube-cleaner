"""Sign in with Google (the "installed app" way: a browser window, then a one-time code sent back to this computer),
and keep a sign-in per channel. The password is typed into Google's own page and never reaches this tool; what is
stored is a token that can be revoked at https://myaccount.google.com/permissions.

Setup the person does once in Google Cloud: create a project, turn on the YouTube Data API v3, make an OAuth client of
type "Desktop app", and download its JSON as client_secret.json (see README.md)."""
import base64
import hashlib
import http.server
import json
import os
import secrets
import threading
import time
import urllib.parse
import webbrowser

from . import api, store

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/youtube"   # needed to change titles and descriptions


class AuthError(Exception):
    pass


def load_client(path):
    """(client_id, client_secret) from the JSON file Google gives for a Desktop app."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as error:
        raise AuthError(f"Could not read {path}: {error}")
    section = data.get("installed") or data.get("web") or {}
    if not section.get("client_id") or not section.get("client_secret"):
        raise AuthError(f'{path} is not a Desktop app sign-in key (it should start with {{"installed": ...}}). See README.md')
    return section["client_id"], section["client_secret"]


def pkce_pair():
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def auth_url(client_id, redirect, challenge, state):
    return AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": client_id, "redirect_uri": redirect, "response_type": "code", "scope": SCOPE,
        "access_type": "offline", "prompt": "consent select_account",
        "code_challenge": challenge, "code_challenge_method": "S256", "state": state,
    })


def _post(http, url, fields):
    body = urllib.parse.urlencode(fields).encode()
    status, raw = http("POST", url, {"Content-Type": "application/x-www-form-urlencoded"}, body)
    try:
        data = json.loads(raw.decode())
    except ValueError:
        data = {}
    if status >= 400 or "error" in data:
        raise AuthError(data.get("error_description") or data.get("error") or f"Google answered {status}")
    return data


def exchange_code(http, client, code, redirect, verifier):
    client_id, secret = client
    return _post(http, TOKEN_URL, {"client_id": client_id, "client_secret": secret, "code": code, "redirect_uri": redirect,
                                   "grant_type": "authorization_code", "code_verifier": verifier})


def refresh(http, client, refresh_token):
    client_id, secret = client
    return _post(http, TOKEN_URL, {"client_id": client_id, "client_secret": secret, "refresh_token": refresh_token,
                                   "grant_type": "refresh_token"})


def _wait_for_code(port_holder, state, timeout):
    """Starts a one-visit server on this computer only; returns (server, result dict filled when Google redirects)."""
    result = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            if query.get("state", [""])[0] != state or ("code" not in query and "error" not in query):
                self.send_response(404)
                self.end_headers()
                return
            result["code"] = query.get("code", [""])[0]
            result["error"] = query.get("error", [""])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"<html><body style='font-family:sans-serif'><h3>Signed in.</h3>"
                             b"You can close this tab and go back to the terminal.</body></html>")

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    port_holder.append(server.server_address[1])
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, result


def login(client, http=api.urllib_http, open_browser=webbrowser.open, say=print, timeout=300):
    """Opens Google's sign-in, waits for the answer, and returns the token response (with a refresh token)."""
    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(16)
    port = []
    server, result = _wait_for_code(port, state, timeout)
    redirect = f"http://127.0.0.1:{port[0]}"
    url = auth_url(client[0], redirect, challenge, state)
    try:
        say("Opening Google's sign-in in your browser. If it does not open, paste this address into your browser:")
        say(url)
        try:
            open_browser(url)
        except Exception:
            pass
        deadline = time.time() + timeout
        while "code" not in result and "error" not in result and time.time() < deadline:
            time.sleep(0.1)
    finally:
        server.shutdown()
        server.server_close()
    if result.get("error"):
        raise AuthError(f"Google said: {result['error']}")
    if not result.get("code"):
        raise AuthError("No answer from Google. Run login again.")
    tokens = exchange_code(http, client, result["code"], redirect, verifier)
    if not tokens.get("refresh_token"):
        raise AuthError("Google did not give a lasting sign-in. Remove this app at myaccount.google.com/permissions and sign in again.")
    return tokens


class Tokens:
    """One channel's saved sign-in: gives a fresh access token whenever asked."""

    def __init__(self, path, client, http=api.urllib_http, now=time.time):
        self.path, self.client, self.http, self.now = path, client, http, now

    def save(self, token_response, channel_id, channel_title):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {"refresh_token": token_response["refresh_token"], "channelId": channel_id, "channelTitle": channel_title,
                "access_token": token_response.get("access_token", ""),
                "expires_at": self.now() + int(token_response.get("expires_in", 0))}
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)   # only this user can read it
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.chmod(self.path, 0o600)

    def load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            raise AuthError("Not signed in. Run: python3 -m youtube_cleaner login")

    def access_token(self):
        data = self.load()
        if data.get("access_token") and data.get("expires_at", 0) - 60 > self.now():
            return data["access_token"]
        fresh = refresh(self.http, self.client, data["refresh_token"])
        data["access_token"] = fresh["access_token"]
        data["expires_at"] = self.now() + int(fresh.get("expires_in", 3600))
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f)
        return data["access_token"]


def token_path(channel_id, root=None):
    return store.Store(channel_id, root).dir / "token.json"


def signed_in_channels(root=None):
    """[(channelId, title)] for every channel that has a saved sign-in."""
    base = (root or store.home()) / "channels"
    out = []
    if base.is_dir():
        for child in sorted(base.iterdir()):
            token = child / "token.json"
            if token.is_file():
                try:
                    with open(token, encoding="utf-8") as f:
                        out.append((child.name, json.load(f).get("channelTitle", child.name)))
                except (OSError, ValueError):
                    continue
    return out
