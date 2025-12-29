"""
Bot Telegram + Flask Web Server
Dành cho Render.com Free Tier & Local Run
"""

import os
import requests
import pytz
import asyncio
import threading
import logging
from datetime import datetime, timedelta, time
from flask import Flask, jsonify
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler

# --- CẤU HÌNH LOGGING (Để xem lỗi rõ hơn) ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# --- CẤU HÌNH TOKEN & API ---
# Ưu tiên lấy từ biến môi trường, nếu không có thì dùng key cứng (để test local)
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN', '8528236957:AAHNIePz7oNObe8qvoy6bMyVfb5UVnS6tww')
FOOTBALL_API_KEY = os.getenv('FOOTBALL_API_KEY', 'de6f2a649b5b49419e4fec624319d0ef')

# Xử lý CHAT_ID: Ép kiểu sang int an toàn để tránh lỗi Pylance
raw_chat_id = os.getenv('CHAT_ID', "-1003621081160")
try:
    CHAT_ID_FOR_AUTO_SCHEDULE = int(raw_chat_id)
except (ValueError, TypeError):
    CHAT_ID_FOR_AUTO_SCHEDULE = -1003621081160

PORT = int(os.getenv('PORT', 10000))  # Render dùng PORT từ ENV

BASE_URL = "https://api.football-data.org/v4"

# Danh sách giải đấu muốn theo dõi (theo mã của football-data.org)
COMPETITIONS = {
    'PL': 'Premier League',
    'PD': 'La Liga',
    'BL1': 'Bundesliga',
    'SA': 'Serie A',
    'FL1': 'Ligue 1',
    'CL': 'Champions League'
}

# Biến toàn cục lưu trạng thái
tracked_matches = {}
announced_goals = set()

# --- Flask App (Để Render không sleep) ---
flask_app = Flask(__name__)

@flask_app.route('/')
def home():
    return jsonify({
        'status': 'online',
        'bot': 'Football Bot',
        'uptime': 'running'
    })

@flask_app.route('/health')
def health():
    return jsonify({'status': 'healthy', 'tracked_matches': len(tracked_matches)})

# --- Football API Functions ---
def call_api(endpoint):
    """Gọi API football-data.org"""
    url = f"{BASE_URL}{endpoint}"
    headers = {'X-Auth-Token': FOOTBALL_API_KEY}
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            return response.json()
        print(f"API Error {response.status_code}: {response.text}")
        return None
    except Exception as e:
        print(f"Exception calling API: {e}")
        return None

def get_today_matches():
    """Lấy lịch hôm nay và lưu vào tracked_matches"""
    global tracked_matches
    # Xóa cache cũ
    tracked_matches.clear()
    
    tz_vn = pytz.timezone('Asia/Ho_Chi_Minh')
    today = datetime.now(tz_vn).date()
    
    date_from = today.strftime("%Y-%m-%d")
    date_to = (today + timedelta(days=1)).strftime("%Y-%m-%d")
    
    # Gọi API lấy tất cả trận đấu
    data = call_api(f"/matches?dateFrom={date_from}&dateTo={date_to}")
    
    if not data or not data.get('matches'):
        return f"📅 Hôm nay ({date_from}) không có dữ liệu lịch thi đấu."
    
    message = f"⚽ *LỊCH THI ĐẤU ({date_from})*\n\n"
    has_match = False
    
    # Nhóm trận đấu theo giải
    matches_by_comp = {}
    
    for match in data['matches']:
        comp_code = match['competition']['code']
        # Chỉ lấy các giải HOT
        if comp_code in COMPETITIONS:
            has_match = True
            comp_name = match['competition']['name']
            match_id = match['id']
            
            # Lưu vào bộ nhớ để theo dõi Live Score
            tracked_matches[match_id] = {
                'home': match['homeTeam']['name'],
                'away': match['awayTeam']['name'],
                'competition': comp_name
            }
            
            if comp_name not in matches_by_comp:
                matches_by_comp[comp_name] = []
            matches_by_comp[comp_name].append(match)
    
    if not has_match:
        return "📅 Hôm nay không có trận nào thuộc các giải lớn (NHA, C1, La Liga...)."

    for comp_name, matches in matches_by_comp.items():
        message += f"🏆 *{comp_name}*\n"
        for match in matches:
            utc_time = datetime.fromisoformat(match['utcDate'].replace('Z', '+00:00'))
            vn_time = utc_time.astimezone(tz_vn).strftime("%H:%M")
            home = match['homeTeam']['name']
            away = match['awayTeam']['name']
            message += f"⏰ {vn_time} | {home} 🆚 {away}\n"
        message += "\n"
    
    message += f"📊 Đã thêm *{len(tracked_matches)} trận* vào danh sách theo dõi Live."
    return message

# --- Telegram Bot Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        await update.message.reply_text(
            "👋 *Bot Bóng Đá Ready*\n"
            "/lich - Xem lịch & Nạp dữ liệu trận đấu\n"
            "/watch - Bật thông báo bàn thắng\n"
            "/id - Lấy ID nhóm",
            parse_mode='Markdown'
        )

async def get_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message and update.effective_chat:
        chat_id = update.effective_chat.id
        await update.message.reply_text(f"🆔 Chat ID: `{chat_id}`", parse_mode='Markdown')

async def lich(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message: return
    await update.message.reply_text("🔄 Đang tải lịch thi đấu...")
    result = get_today_matches()
    await update.message.reply_text(result, parse_mode='Markdown')

async def check_goals_auto(context: ContextTypes.DEFAULT_TYPE):
    """Hàm chạy ngầm quét bàn thắng"""
    if not context.job or not context.job.chat_id: return
    
    chat_id = context.job.chat_id
    
    # Nếu chưa có danh sách trận đấu, thử lấy lại
    if not tracked_matches:
        get_today_matches()
        if not tracked_matches: return # Vẫn không có thì thôi

    # Lấy các trận đang đá (IN_PLAY) hoặc Tạm dừng (PAUSED)
    data = call_api("/matches?status=IN_PLAY") # Có thể thêm ,PAUSED nếu muốn
    
    if not data or not data.get('matches'): return
    
    for match in data['matches']:
        match_id = match['id']
        
        # Chỉ báo tin nếu trận đấu nằm trong danh sách theo dõi
        if match_id in tracked_matches:
            home_name = match['homeTeam']['name']
            away_name = match['awayTeam']['name']
            score_home = match['score']['fullTime']['home']
            score_away = match['score']['fullTime']['away']
            
            # Xử lý trường hợp API trả về None
            if score_home is None: score_home = 0
            if score_away is None: score_away = 0
            
            # Tạo ID bàn thắng: IDTran_BanThangNha_BanThangKhach
            goal_id = f"{match_id}_{score_home}_{score_away}"
            
            # Logic: Tỉ số thay đổi -> Báo tin
            if goal_id not in announced_goals:
                # Không báo 0-0
                if score_home == 0 and score_away == 0:
                    continue
                
                comp_name = match['competition']['name']
                minute = "Live" # API Free đôi khi không trả về phút chính xác
                
                text = (
                    f"⚽ *VÀOOOOO !!!*\n\n"
                    f"🏆 {comp_name}\n"
                    f"🔥 *{home_name} {score_home} - {score_away} {away_name}*"
                )
                
                try:
                    await context.bot.send_message(chat_id=chat_id, text=text, parse_mode='Markdown')
                    announced_goals.add(goal_id)
                except Exception as e:
                    print(f"Lỗi gửi tin nhắn: {e}")

async def watch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_chat or not update.message: return
    
    if not context.job_queue:
        await update.message.reply_text("❌ Lỗi JobQueue không hoạt động.")
        return
    
    chat_id = update.effective_chat.id
    job_name = f"watch_{chat_id}"
    
    # Xóa job cũ để tránh trùng lặp
    current_jobs = context.job_queue.get_jobs_by_name(job_name)
    for job in current_jobs: job.schedule_removal()
    
    # Chạy 120s (2 phút) một lần để tiết kiệm API
    context.job_queue.run_repeating(
        check_goals_auto, 
        interval=120, 
        first=10, 
        chat_id=chat_id, 
        name=job_name
    )
    
    await update.message.reply_text(f"🔔 Đã bật theo dõi tỉ số cho nhóm `{chat_id}` (Quét 2 phút/lần).", parse_mode='Markdown')

async def send_daily_schedule(context: ContextTypes.DEFAULT_TYPE):
    """Gửi lịch tự động lúc 8h sáng"""
    if not context.job or not context.job.chat_id: return
    chat_id = context.job.chat_id
    
    msg = get_today_matches()
    try:
        await context.bot.send_message(chat_id=chat_id, text=msg, parse_mode='Markdown')
        # Tự động bật watch sau khi gửi lịch
        if context.job_queue:
            job_name = f"watch_{chat_id}"
            jobs = context.job_queue.get_jobs_by_name(job_name)
            if not jobs:
                context.job_queue.run_repeating(check_goals_auto, interval=120, first=60, chat_id=chat_id, name=job_name)
    except Exception as e:
        print(f"Lỗi gửi lịch tự động: {e}")

# --- Main Run ---
async def main():
    print(f"✅ Đang khởi động Bot...")
    
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    
    # Thêm Handlers
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('id', get_id))
    application.add_handler(CommandHandler('lich', lich))
    application.add_handler(CommandHandler('watch', watch))
    
    # Auto Schedule (Chỉ chạy nếu có CHAT_ID hợp lệ)
    if CHAT_ID_FOR_AUTO_SCHEDULE and application.job_queue:
        tz_vn = pytz.timezone('Asia/Ho_Chi_Minh')
        # Gửi vào lúc 8h00 sáng mỗi ngày
        application.job_queue.run_daily(
            send_daily_schedule,
            time=time(hour=8, minute=0, tzinfo=tz_vn),
            chat_id=CHAT_ID_FOR_AUTO_SCHEDULE,
            name="daily_schedule"
        )
        print(f"📅 Đã đặt lịch gửi tự động cho ID: {CHAT_ID_FOR_AUTO_SCHEDULE}")
    
    print("🚀 Bot đang chạy polling...")
    # Sửa lỗi 'None is not awaitable' bằng cách gọi đúng hàm async
    await application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

def run_flask():
    """Chạy Flask server"""
    flask_app.run(host='0.0.0.0', port=PORT, use_reloader=False)

if __name__ == '__main__':
    # 1. Chạy Flask ở luồng riêng (Daemon Thread)
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    # 2. Chạy Bot Telegram (Main Thread)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("🛑 Đã dừng Bot.")