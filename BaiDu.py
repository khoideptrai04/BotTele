import os
import requests
import pytz
from datetime import datetime, timedelta, time
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler

# --- CẤU HÌNH ---
TELEGRAM_TOKEN = '8528236957:AAHNIePz7oNObe8qvoy6bMyVfb5UVnS6tww'
FOOTBALL_API_KEY = 'de6f2a649b5b49419e4fec624319d0ef'

# Để lấy chat_id: gõ /id trong nhóm khi bot đã join
CHAT_ID_FOR_AUTO_SCHEDULE = None  # VD: -1001234567890

BASE_URL = "https://api.football-data.org/v4"

# Giải đấu FREE TIER (12 giải)
COMPETITIONS = {
    'PL': 'Premier League',       # Anh
    'PD': 'La Liga',               # Tây Ban Nha
    'BL1': 'Bundesliga',           # Đức
    'SA': 'Serie A',               # Ý
    'FL1': 'Ligue 1',              # Pháp
    'CL': 'Champions League',      # Châu Âu
    'ELC': 'Championship',         # Anh 2
    'PPL': 'Primeira Liga',        # Bồ Đào Nha
    'DED': 'Eredivisie',           # Hà Lan
    'BSA': 'Série A',              # Brazil
    'EC': 'European Championship', # Euro
    'WC': 'World Cup'              # World Cup (khi có)
}

# NOTE: Nếu muốn thêm J-League, K-League, A-League:
# - Cần nâng cấp lên TIER_TWO (~€19/tháng)
# - Hoặc dùng API-Football (cần VPN)

# Lưu trữ
tracked_matches = {}  # {match_id: match_info} - Các trận trong lịch hôm nay
announced_goals = set()  # Bàn thắng đã thông báo

# --- HÀM GỌI API ---
def call_api(endpoint):
    """Gọi API football-data.org"""
    url = f"{BASE_URL}{endpoint}"
    headers = {'X-Auth-Token': FOOTBALL_API_KEY}
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            return response.json()
        else:
            print(f"API Error {response.status_code}: {response.text}")
            return None
    except Exception as e:
        print(f"Request Error: {e}")
        return None

# --- HÀM LẤY LỊCH HÔM NAY ---
def get_today_matches():
    """Lấy lịch thi đấu hôm nay và lưu vào tracked_matches"""
    global tracked_matches
    tracked_matches.clear()  # Reset danh sách
    
    tz_vn = pytz.timezone('Asia/Ho_Chi_Minh')
    today = datetime.now(tz_vn).date()
    
    date_from = today.strftime("%Y-%m-%d")
    date_to = (today + timedelta(days=1)).strftime("%Y-%m-%d")
    
    data = call_api(f"/matches?dateFrom={date_from}&dateTo={date_to}")
    
    if not data or not data.get('matches'):
        return "📅 Hôm nay chưa có lịch thi đấu."
    
    message = f"⚽ *LỊCH THI ĐẤU HÔM NAY ({date_from})*\n\n"
    
    # Nhóm theo giải
    matches_by_comp = {}
    for match in data['matches']:
        comp_name = match['competition']['name']
        match_id = match['id']
        
        # Lưu vào danh sách theo dõi
        tracked_matches[match_id] = {
            'home': match['homeTeam']['name'],
            'away': match['awayTeam']['name'],
            'competition': comp_name,
            'utcDate': match['utcDate']
        }
        
        if comp_name not in matches_by_comp:
            matches_by_comp[comp_name] = []
        matches_by_comp[comp_name].append(match)
    
    # Hiển thị
    for comp_name, matches in matches_by_comp.items():
        message += f"🏆 *{comp_name}*\n"
        for match in matches:
            utc_time = datetime.fromisoformat(match['utcDate'].replace('Z', '+00:00'))
            vn_time = utc_time.astimezone(tz_vn).strftime("%H:%M")
            home = match['homeTeam']['name']
            away = match['awayTeam']['name']
            message += f"⏰ {vn_time} | {home} 🆚 {away}\n"
        message += "\n"
    
    message += f"📊 Tổng: *{len(tracked_matches)} trận*\n"
    message += "🔔 Bot sẽ tự động thông báo bàn thắng!"
    
    return message

# --- HÀM QUÉT BÀN THẮNG (CHỈ THEO DÕI TRẬN TRONG LỊCH) ---
async def check_goals_auto(context: ContextTypes.DEFAULT_TYPE):
    """Quét bàn thắng - CHỈ các trận trong lịch hôm nay"""
    if not context.job or not context.job.chat_id:
        return
    
    chat_id = context.job.chat_id
    
    if not tracked_matches:
        print("[AUTO] Chưa có trận nào được theo dõi")
        return
    
    # Lấy danh sách trận đang live
    data = call_api("/matches?status=IN_PLAY")
    
    if not data or not data.get('matches'):
        return
    
    for match in data['matches']:
        match_id = match['id']
        
        # CHỈ theo dõi trận có trong lịch hôm nay
        if match_id not in tracked_matches:
            continue
        
        home = match['homeTeam']['name']
        away = match['awayTeam']['name']
        score_home = match['score']['fullTime']['home'] or 0
        score_away = match['score']['fullTime']['away'] or 0
        
        goal_id = f"{match_id}_{score_home}_{score_away}"
        
        if goal_id not in announced_goals and (score_home > 0 or score_away > 0):
            # Tính phút
            utc_time = datetime.fromisoformat(match['utcDate'].replace('Z', '+00:00'))
            now = datetime.now(pytz.UTC)
            elapsed = int((now - utc_time).total_seconds() / 60)
            elapsed = min(elapsed, 95)  # Cap tối đa 95'
            
            text = f"⚽ *VÀOOOOO !!!* ({elapsed}')\n\n"
            text += f"🏆 {match['competition']['name']}\n"
            text += f"🔥 *{home} {score_home} - {score_away} {away}*"
            
            print(f"[GOAL] {home} {score_home}-{score_away} {away}")
            
            try:
                await context.bot.send_message(chat_id=chat_id, text=text, parse_mode='Markdown')
                announced_goals.add(goal_id)
            except Exception as e:
                print(f"Error sending goal: {e}")

# --- LỆNH /start ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        await update.message.reply_text(
            "👋 *Chào mừng! Bot Bóng Đá 24/7*\n\n"
            "📋 *DANH SÁCH LỆNH:*\n"
            "/lich - Xem lịch hôm nay\n"
            "/live - Xem tỉ số trực tiếp\n"
            "/watch - Bật theo dõi bàn thắng\n"
            "/stopwatch - Tắt theo dõi\n"
            "/bang PL - Xem bảng xếp hạng\n"
            "/id - Xem Chat ID (để cấu hình auto)\n"
            "/info - Thông tin API\n\n"
            "🤖 *TÍNH NĂNG TỰ ĐỘNG:*\n"
            "• Gửi lịch lúc 8h sáng mỗi ngày\n"
            "• Thông báo bàn thắng tự động",
            parse_mode='Markdown'
        )

# --- LỆNH /id (Lấy Chat ID) ---
async def get_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message and update.effective_chat:
        chat_id = update.effective_chat.id
        chat_type = update.effective_chat.type
        await update.message.reply_text(
            f"📊 *THÔNG TIN CHAT*\n\n"
            f"Chat ID: `{chat_id}`\n"
            f"Loại: {chat_type}\n\n"
            f"💡 Copy Chat ID này vào code để bật auto schedule!",
            parse_mode='Markdown'
        )

# --- LỆNH /lich ---
async def lich(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    
    await update.message.reply_text("🔄 Đang lấy lịch...")
    result = get_today_matches()
    await update.message.reply_text(result, parse_mode='Markdown')

# --- LỆNH /live ---
async def live(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    
    await update.message.reply_text("🔄 Đang lấy tỉ số...")
    
    data = call_api("/matches?status=IN_PLAY")
    
    if not data or not data.get('matches'):
        await update.message.reply_text("⚽ Hiện không có trận nào đang live!")
        return
    
    # Lọc chỉ trận trong lịch (nếu có)
    live_matches = []
    for match in data['matches']:
        if tracked_matches and match['id'] not in tracked_matches:
            continue  # Skip trận không trong lịch
        live_matches.append(match)
    
    if not live_matches:
        await update.message.reply_text("⚽ Không có trận nào trong lịch hôm nay đang live!")
        return
    
    message = "🔴 *TỈ SỐ TRỰC TIẾP*\n\n"
    
    for match in live_matches:
        comp_name = match['competition']['name']
        home = match['homeTeam']['name']
        away = match['awayTeam']['name']
        score_home = match['score']['fullTime']['home'] or 0
        score_away = match['score']['fullTime']['away'] or 0
        
        utc_time = datetime.fromisoformat(match['utcDate'].replace('Z', '+00:00'))
        now = datetime.now(pytz.UTC)
        elapsed = int((now - utc_time).total_seconds() / 60)
        elapsed = min(elapsed, 95)
        
        message += f"🏆 {comp_name}\n"
        message += f"⏱ *{elapsed}'* | {home} *{score_home} - {score_away}* {away}\n\n"
    
    await update.message.reply_text(message, parse_mode='Markdown')

# --- LỆNH /bang ---
async def bang(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    
    comp_code = context.args[0].upper() if context.args else 'PL'
    
    if comp_code not in COMPETITIONS:
        await update.message.reply_text(f"❌ Giải '{comp_code}' không hợp lệ!")
        return
    
    await update.message.reply_text(f"🔄 Đang lấy bảng xếp hạng...")
    
    data = call_api(f"/competitions/{comp_code}/standings")
    
    if not data or not data.get('standings'):
        await update.message.reply_text("❌ Không lấy được dữ liệu!")
        return
    
    standings = data['standings'][0]['table']
    comp_name = data['competition']['name']
    
    message = f"🏆 *{comp_name.upper()}*\n\n"
    
    for team in standings[:10]:
        pos = team['position']
        name = team['team']['name']
        points = team['points']
        played = team['playedGames']
        
        emoji = "🥇" if pos == 1 else "🥈" if pos == 2 else "🥉" if pos == 3 else f"{pos}."
        message += f"{emoji} {name} - *{points}* điểm ({played} trận)\n"
    
    await update.message.reply_text(message, parse_mode='Markdown')

# --- LỆNH /watch ---
async def watch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_chat or not update.message:
        return
    
    if not context.job_queue:
        await update.message.reply_text("❌ Lỗi job queue")
        return
    
    chat_id = update.effective_chat.id
    
    # Xóa job cũ
    current_jobs = context.job_queue.get_jobs_by_name(f"watch_{chat_id}")
    for job in current_jobs:
        job.schedule_removal()
    
    # Tạo job mới: quét mỗi 120s
    context.job_queue.run_repeating(
        check_goals_auto, 
        interval=120, 
        first=10, 
        chat_id=chat_id, 
        name=f"watch_{chat_id}"
    )
    
    await update.message.reply_text(
        "🔔 Đã bật theo dõi bàn thắng!\n\n"
        "💡 Bot chỉ theo dõi các trận trong lịch hôm nay.\n"
        "Dùng /lich để load danh sách trận."
    )

# --- LỆNH /stopwatch ---
async def stopwatch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_chat or not update.message:
        return
    
    if not context.job_queue:
        return
    
    chat_id = update.effective_chat.id
    current_jobs = context.job_queue.get_jobs_by_name(f"watch_{chat_id}")
    
    if not current_jobs:
        await update.message.reply_text("❌ Chưa bật theo dõi!")
        return
    
    for job in current_jobs:
        job.schedule_removal()
    
    await update.message.reply_text("⏸️ Đã tắt theo dõi bàn thắng!")

# --- LỆNH /info ---
async def info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        info_text = (
            "ℹ️ *THÔNG TIN API*\n\n"
            "🌐 API: football-data.org\n"
            "📦 Plan: Free Tier\n"
            "⚡ Limit: 10 req/phút\n"
            "🏆 Giải: 12 giải miễn phí\n\n"
            "*Giải có sẵn:*\n"
            "PL, PD, BL1, SA, FL1, CL, ELC, PPL, DED, BSA\n\n"
            "❌ *Không có:* J-League, K-League, A-League\n"
            "💰 Cần nâng cấp TIER_TWO (~€19/tháng)\n\n"
            "🌐 https://football-data.org"
        )
        await update.message.reply_text(info_text, parse_mode='Markdown')

# --- TỰ ĐỘNG GỬI LỊCH LÚC 8H SÁNG ---
async def send_daily_schedule(context: ContextTypes.DEFAULT_TYPE):
    """Gửi lịch tự động lúc 8h sáng"""
    if not context.job or not context.job.chat_id:
        return
    
    chat_id = context.job.chat_id
    
    print(f"[AUTO] Gửi lịch hàng ngày tới {chat_id}")
    
    result = get_today_matches()
    
    try:
        await context.bot.send_message(
            chat_id=chat_id, 
            text="🌅 *LỊCH THI ĐẤU HÔM NAY*\n\n" + result, 
            parse_mode='Markdown'
        )
        
        # Tự động bật watch sau khi gửi lịch
        current_jobs = context.job_queue.get_jobs_by_name(f"watch_{chat_id}")
        if not current_jobs:
            context.job_queue.run_repeating(
                check_goals_auto, 
                interval=120, 
                first=10, 
                chat_id=chat_id, 
                name=f"watch_{chat_id}"
            )
            await context.bot.send_message(
                chat_id=chat_id,
                text="🔔 Đã tự động bật theo dõi bàn thắng!"
            )
    except Exception as e:
        print(f"Error sending daily schedule: {e}")

# --- CHẠY BOT ---
if __name__ == '__main__':
    from telegram.request import HTTPXRequest
    
    # Kiểm tra config
    if FOOTBALL_API_KEY == 'YOUR_FOOTBALL_DATA_ORG_API_KEY':
        print("❌ Chưa cấu hình FOOTBALL_API_KEY!")
        print("👉 Đăng ký tại: https://www.football-data.org/client/register")
        exit(1)
    
    if TELEGRAM_TOKEN == 'YOUR_TELEGRAM_BOT_TOKEN_HERE':
        print("❌ Chưa cấu hình TELEGRAM_TOKEN!")
        exit(1)
    
    # Setup
    request = HTTPXRequest(
        connection_pool_size=8,
        connect_timeout=30.0,
        read_timeout=30.0,
        write_timeout=30.0,
        pool_timeout=30.0
    )
    
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).request(request).build()
    
    # Handlers
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('id', get_id))
    application.add_handler(CommandHandler('lich', lich))
    application.add_handler(CommandHandler('live', live))
    application.add_handler(CommandHandler('bang', bang))
    application.add_handler(CommandHandler('watch', watch))
    application.add_handler(CommandHandler('stopwatch', stopwatch))
    application.add_handler(CommandHandler('info', info))
    
    # Tự động gửi lịch lúc 8h sáng mỗi ngày
    if CHAT_ID_FOR_AUTO_SCHEDULE and application.job_queue:
        tz_vn = pytz.timezone('Asia/Ho_Chi_Minh')
        application.job_queue.run_daily(
            send_daily_schedule,
            time=time(hour=8, minute=0, tzinfo=tz_vn),
            chat_id=CHAT_ID_FOR_AUTO_SCHEDULE,
            name="daily_schedule"
        )
        print(f"✅ Đã cấu hình auto gửi lịch lúc 8h sáng cho chat {CHAT_ID_FOR_AUTO_SCHEDULE}")
    elif not CHAT_ID_FOR_AUTO_SCHEDULE:
        print("⚠️ Chưa cấu hình CHAT_ID_FOR_AUTO_SCHEDULE")
        print("💡 Gõ /id trong nhóm để lấy Chat ID")
    else:
        print("❌ Lỗi: job_queue không khả dụng")
    
    print("🚀 Bot đang khởi động...")
    print("🌐 API: football-data.org (Free Tier - 12 giải)")
    
    try:
        application.run_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True,
            poll_interval=2.0
        )
    except KeyboardInterrupt:
        print("\n⚠️ Bot đã dừng")
    except Exception as e:
        print(f"❌ Lỗi: {e}")