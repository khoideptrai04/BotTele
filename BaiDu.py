import os
import requests
import pytz
import asyncio
import threading
from datetime import datetime, timedelta
from flask import Flask, jsonify
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler

# --- CẤU HÌNH ---
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN', '8528236957:AAHNIePz7oNObe8qvoy6bMyVfb5UVnS6tww')
FOOTBALL_API_KEY = os.getenv('FOOTBALL_API_KEY', '62ed7b2e57mshb51b494f3f15824p1afaecjsnb6a6535f10e7')
PORT = int(os.getenv('PORT', 10000)) 

BASE_URL = "https://api.football-data.org/v4"
tracked_matches = {}
announced_goals = set()

# --- Flask App ---
flask_app = Flask(__name__)

@flask_app.route('/')
def home():
    return jsonify({'status': 'online', 'bot': 'Football Bot 24/7'})

@flask_app.route('/health')
def health():
    return jsonify({'status': 'healthy', 'timestamp': datetime.now().isoformat()})

# --- Football API Functions ---
def call_api(endpoint):
    url = f"{BASE_URL}{endpoint}"
    headers = {'X-Auth-Token': FOOTBALL_API_KEY}
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            return response.json()
        return None
    except Exception as e:
        print(f"API Error: {e}")
        return None

def get_today_matches():
    global tracked_matches
    tz_vn = pytz.timezone('Asia/Ho_Chi_Minh')
    today = datetime.now(tz_vn).date()
    
    date_from = today.strftime("%Y-%m-%d")
    date_to = (today + timedelta(days=1)).strftime("%Y-%m-%d")
    
    data = call_api(f"/matches?dateFrom={date_from}&dateTo={date_to}")
    
    if not data or not data.get('matches'):
        return "📅 Hôm nay chưa có lịch thi đấu nào được ghi nhận."
    
    tracked_matches.clear()
    message = f"⚽ *LỊCH THI ĐẤU ({date_from})*\n\n"
    
    matches_by_comp = {}
    for match in data['matches']:
        comp_name = match['competition']['name']
        tracked_matches[match['id']] = {
            'home': match['homeTeam']['name'],
            'away': match['awayTeam']['name'],
            'competition': comp_name
        }
        if comp_name not in matches_by_comp:
            matches_by_comp[comp_name] = []
        matches_by_comp[comp_name].append(match)
    
    for comp_name, matches in matches_by_comp.items():
        message += f"🏆 *{comp_name}*\n"
        for m in matches:
            utc_time = datetime.fromisoformat(m['utcDate'].replace('Z', '+00:00'))
            vn_time = utc_time.astimezone(tz_vn).strftime("%H:%M")
            message += f"⏰ {vn_time} | {m['homeTeam']['name']} 🆚 {m['awayTeam']['name']}\n"
        message += "\n"
    return message

# --- Telegram Bot Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        await update.message.reply_text(
            "👋 *Bot Bóng Đá 24/7 (Football-Data.org)*\n\n"
            "📋 *DANH SÁCH LỆNH:*\n"
            "/lich - Xem lịch thi đấu\n"
            "/live - Tỉ số trực tiếp\n"
            "/watch - Bật thông báo bàn thắng\n"
            "/stopwatch - Tắt thông báo\n"
            "/status - Kiểm tra hệ thống",
            parse_mode='Markdown'
        )

async def lich(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        await update.message.reply_text("🔄 Đang lấy lịch từ football-data.org...")
        result = get_today_matches()
        await update.message.reply_text(result, parse_mode='Markdown')

async def live(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message: return
    data = call_api("/matches?status=IN_PLAY")
    if not data or not data.get('matches'):
        await update.message.reply_text("⚽ Hiện không có trận đấu nào đang diễn ra.")
        return
    
    message = "🔴 *TỈ SỐ TRỰC TIẾP*\n\n"
    for match in data['matches']:
        home = match['homeTeam']['name']
        away = match['awayTeam']['name']
        score_h = match['score']['fullTime']['home'] or 0
        score_a = match['score']['fullTime']['away'] or 0
        message += f"🏆 {match['competition']['name']}\n"
        message += f"{home} *{score_h} - {score_a}* {away}\n\n"
    await update.message.reply_text(message, parse_mode='Markdown')

async def check_goals_auto(context: ContextTypes.DEFAULT_TYPE):
    if not context.job or not context.job.chat_id: return
    chat_id = context.job.chat_id
    data = call_api("/matches?status=IN_PLAY")
    if not data or not data.get('matches'): return

    for match in data['matches']:
        m_id = match['id']
        s_h = match['score']['fullTime']['home'] or 0
        s_a = match['score']['fullTime']['away'] or 0
        goal_id = f"{m_id}_{s_h}_{s_a}"

        if goal_id not in announced_goals and (s_h > 0 or s_a > 0):
            utc_start = datetime.fromisoformat(match['utcDate'].replace('Z', '+00:00'))
            elapsed = int((datetime.now(pytz.UTC) - utc_start).total_seconds() / 60)
            elapsed = min(max(elapsed, 1), 95)

            text = f"⚽ *VÀOOOOO !!!* (~{elapsed}')\n\n"
            text += f"🏆 {match['competition']['name']}\n"
            text += f"🔥 *{match['homeTeam']['name']} {s_h} - {s_a} {match['awayTeam']['name']}*"
            
            await context.bot.send_message(chat_id=chat_id, text=text, parse_mode='Markdown')
            announced_goals.add(goal_id)

async def watch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_chat or not update.message or not context.job_queue: return
    chat_id = update.effective_chat.id
    
    current_jobs = context.job_queue.get_jobs_by_name(f"watch_{chat_id}")
    for job in current_jobs: job.schedule_removal()
    
    context.job_queue.run_repeating(check_goals_auto, interval=120, first=10, chat_id=chat_id, name=f"watch_{chat_id}")
    await update.message.reply_text("🔔 Đã bật báo bàn thắng (2 phút quét/lần).")

async def stopwatch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_chat or not update.message or not context.job_queue: return
    chat_id = update.effective_chat.id
    
    current_jobs = context.job_queue.get_jobs_by_name(f"watch_{chat_id}")
    for job in current_jobs: job.schedule_removal()
    await update.message.reply_text("⏸️ Đã tắt thông báo.")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        msg = f"📊 *TRẠNG THÁI*\n- Bot: Online ✅\n- Trận đang theo dõi: {len(tracked_matches)}\n- Goals đã báo: {len(announced_goals)}"
        await update.message.reply_text(msg, parse_mode='Markdown')

# --- Main App ---
def run_flask():
    flask_app.run(host='0.0.0.0', port=PORT)

async def main():
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('lich', lich))
    application.add_handler(CommandHandler('live', live))
    application.add_handler(CommandHandler('watch', watch))
    application.add_handler(CommandHandler('stopwatch', stopwatch))
    application.add_handler(CommandHandler('status', status))
    
    get_today_matches()
    await application.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    threading.Thread(target=run_flask, daemon=True).start()
    asyncio.run(main())