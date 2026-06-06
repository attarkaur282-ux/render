# Owner: @UnknownGuy9876 | Channel: @SGCodexs
# Telegram Bot Hosting Board - Render.com Ready
# User website se vote/poll/board host kar sakta hai

import os
import json
import threading
import time
import logging
import subprocess
import sys
from datetime import datetime
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import telebot

# ---------- CONFIG ----------
PORT = int(os.environ.get("PORT", 5000))
HOST = "0.0.0.0"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BOT_STORAGE_FILE = os.path.join(BASE_DIR, "bot.txt")
UPLOAD_FOLDER = os.path.join(BASE_DIR, "user_bots")
LOG_FOLDER = os.path.join(BASE_DIR, "logs")

# Create folders
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(LOG_FOLDER, exist_ok=True)

# ---------- LOGGING ----------
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("HostingBoard")

# ---------- FLASK APP ----------
app = Flask(__name__)
CORS(app)

# ---------- DATA STORAGE ----------
# Structure: { bot_token: { name, board_chat, status, pid, script_path, started_at } }
running_bots = {}
bot_metadata = {}

def load_bots_from_file():
    global bot_metadata
    if not os.path.exists(BOT_STORAGE_FILE):
        bot_metadata = {}
        return
    try:
        with open(BOT_STORAGE_FILE, 'r', encoding='utf-8') as f:
            bot_metadata = json.load(f)
        logger.info(f"Loaded {len(bot_metadata)} bots from storage")
    except Exception as e:
        logger.error(f"Error loading bots: {e}")
        bot_metadata = {}

def save_bots_to_file():
    try:
        with open(BOT_STORAGE_FILE, 'w', encoding='utf-8') as f:
            json.dump(bot_metadata, f, indent=2)
        logger.info(f"Saved {len(bot_metadata)} bots to storage")
    except Exception as e:
        logger.error(f"Error saving bots: {e}")

# ---------- BOT RUNNER ----------
def run_bot_process(bot_token, script_content, bot_name, board_chat):
    """Run a user's bot script as a subprocess"""
    # Create unique folder for this bot
    safe_token = bot_token.replace(':', '_').replace('/', '_')
    bot_folder = os.path.join(UPLOAD_FOLDER, safe_token)
    os.makedirs(bot_folder, exist_ok=True)
    
    # Save script
    script_path = os.path.join(bot_folder, "bot.py")
    with open(script_path, 'w', encoding='utf-8') as f:
        f.write(script_content)
    
    # Create launcher with environment
    launcher_path = os.path.join(bot_folder, "launcher.py")
    launcher_code = f'''
import os
import sys
import telebot
import time

TOKEN = "{bot_token}"
BOARD_CHAT = "{board_chat}" if "{board_chat}" else None

sys.path.insert(0, "{bot_folder}")
try:
    with open("{script_path}", "r") as f:
        exec(f.read())
except Exception as e:
    print(f"Error: {{e}}")
    bot = telebot.TeleBot(TOKEN)
    @bot.message_handler(func=lambda m: True)
    def forward(m):
        if BOARD_CHAT:
            try:
                bot.forward_message(BOARD_CHAT, m.chat.id, m.message_id)
                bot.reply_to(m, "✅ Forwarded to board")
            except Exception as e:
                bot.reply_to(m, f"Error: {{e}}")
        else:
            bot.reply_to(m, "No board set. Use /setboard")
    bot.infinity_polling()
'''
    with open(launcher_path, 'w', encoding='utf-8') as f:
        f.write(launcher_code)
    
    # Log file
    log_path = os.path.join(LOG_FOLDER, f"{safe_token}.log")
    log_file = open(log_path, 'a', encoding='utf-8')
    log_file.write(f"\n--- Starting bot {bot_name} at {datetime.now()} ---\n")
    log_file.flush()
    
    # Start process
    proc = subprocess.Popen(
        [sys.executable, launcher_path],
        stdout=log_file,
        stderr=subprocess.STDOUT,
        cwd=bot_folder,
        text=True
    )
    
    return proc, script_path, launcher_path, log_file

def stop_bot(token):
    """Stop a running bot"""
    if token in running_bots:
        proc = running_bots[token].get('process')
        log_file = running_bots[token].get('log_file')
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        if log_file:
            try:
                log_file.close()
            except:
                pass
        del running_bots[token]
        if token in bot_metadata:
            bot_metadata[token]['status'] = 'stopped'
            save_bots_to_file()
        logger.info(f"Stopped bot {token[:10]}...")
        return True
    return False

def start_bot_from_metadata(token):
    """Restart a bot from saved metadata"""
    meta = bot_metadata.get(token)
    if not meta:
        return False
    script_content = meta.get('script_content')
    bot_name = meta.get('name', 'Unnamed')
    board_chat = meta.get('board_chat', '')
    if not script_content:
        return False
    try:
        proc, script_path, launcher_path, log_file = run_bot_process(token, script_content, bot_name, board_chat)
        running_bots[token] = {
            'process': proc,
            'script_path': script_path,
            'launcher_path': launcher_path,
            'log_file': log_file,
            'name': bot_name,
            'board_chat': board_chat,
            'started_at': datetime.now().isoformat()
        }
        meta['status'] = 'running'
        meta['started_at'] = datetime.now().isoformat()
        save_bots_to_file()
        logger.info(f"Started bot {token[:10]}...")
        return True
    except Exception as e:
        logger.error(f"Failed to start bot {token[:10]}: {e}")
        return False

# ---------- HEALTH CHECK THREAD ----------
def health_check():
    """Restart crashed bots"""
    while True:
        time.sleep(30)
        for token, info in list(running_bots.items()):
            proc = info.get('process')
            if proc and proc.poll() is not None:
                logger.warning(f"Bot {token[:10]} crashed. Restarting...")
                start_bot_from_metadata(token)

# Start health check thread
threading.Thread(target=health_check, daemon=True).start()

# ---------- API ROUTES ----------
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/host', methods=['POST'])
def api_host():
    """Host a new bot – user sends token and script"""
    data = request.json
    bot_token = data.get('bot_token')
    bot_name = data.get('bot_name', 'Hosted Bot')
    board_chat = data.get('board_chat', '')
    script_content = data.get('script_content')
    
    if not bot_token:
        return jsonify({'error': 'Bot token required'}), 400
    if not script_content:
        return jsonify({'error': 'Script content required'}), 400
    
    # Validate token
    try:
        test_bot = telebot.TeleBot(bot_token)
        me = test_bot.get_me()
        logger.info(f"Valid token for @{me.username}")
    except Exception as e:
        return jsonify({'error': f'Invalid token: {str(e)}'}), 400
    
    # Stop existing bot if any
    if bot_token in running_bots:
        stop_bot(bot_token)
    
    # Save metadata
    bot_metadata[bot_token] = {
        'name': bot_name,
        'board_chat': board_chat,
        'script_content': script_content,
        'status': 'running',
        'created_at': datetime.now().isoformat(),
        'username': me.username
    }
    save_bots_to_file()
    
    # Start the bot
    try:
        proc, script_path, launcher_path, log_file = run_bot_process(bot_token, script_content, bot_name, board_chat)
        running_bots[bot_token] = {
            'process': proc,
            'script_path': script_path,
            'launcher_path': launcher_path,
            'log_file': log_file,
            'name': bot_name,
            'board_chat': board_chat,
            'started_at': datetime.now().isoformat()
        }
        return jsonify({'message': f'Bot @{me.username} hosted successfully!', 'status': 'running'}), 200
    except Exception as e:
        return jsonify({'error': f'Failed to start bot: {str(e)}'}), 500

@app.route('/api/list', methods=['GET'])
def api_list():
    """List all hosted bots"""
    bots = []
    for token, meta in bot_metadata.items():
        bots.append({
            'token': token[:10] + '...',
            'full_token': token,
            'name': meta.get('name', 'Unnamed'),
            'board_chat': meta.get('board_chat', ''),
            'status': 'running' if token in running_bots else 'stopped',
            'created_at': meta.get('created_at', ''),
            'username': meta.get('username', '')
        })
    return jsonify({'bots': bots})

@app.route('/api/stop', methods=['POST'])
def api_stop():
    data = request.json
    token = data.get('bot_token')
    if not token:
        return jsonify({'error': 'Token required'}), 400
    if stop_bot(token):
        return jsonify({'message': 'Bot stopped'}), 200
    return jsonify({'message': 'Bot not running'}), 200

@app.route('/api/restart', methods=['POST'])
def api_restart():
    data = request.json
    token = data.get('bot_token')
    if not token:
        return jsonify({'error': 'Token required'}), 400
    stop_bot(token)
    if start_bot_from_metadata(token):
        return jsonify({'message': 'Bot restarted'}), 200
    return jsonify({'error': 'Failed to restart'}), 500

@app.route('/api/delete', methods=['POST'])
def api_delete():
    data = request.json
    token = data.get('bot_token')
    if not token:
        return jsonify({'error': 'Token required'}), 400
    stop_bot(token)
    if token in bot_metadata:
        del bot_metadata[token]
        save_bots_to_file()
    return jsonify({'message': 'Bot deleted'}), 200

# ---------- LOAD EXISTING BOTS ON STARTUP ----------
load_bots_from_file()
for token in list(bot_metadata.keys()):
    if bot_metadata[token].get('status') == 'running':
        logger.info(f"Auto-starting bot {token[:10]}...")
        start_bot_from_metadata(token)

# ---------- MAIN ----------
if __name__ == '__main__':
    logger.info(f"Hosting Board started on port {PORT}")
    app.run(host=HOST, port=PORT, debug=False, threaded=True)