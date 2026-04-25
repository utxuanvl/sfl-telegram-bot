import requests
from bs4 import BeautifulSoup
import time
import json
import os
from telegram import Bot
from telegram.error import TelegramError
import schedule

# ===== CẤU HÌNH =====
TELEGRAM_TOKEN = "YOUR_BOT_TOKEN"
CHAT_ID = "YOUR_CHAT_ID"
URL = "https://sfl.world/util/prices"
DATA_FILE = "last_prices.json"   # lưu giá cũ để so sánh

bot = Bot(token=TELEGRAM_TOKEN)

def fetch_prices():
    """Lấy dữ liệu giá từ web, trả về dict {item: {p2p, seq, betty}}"""
    response = requests.get(URL, timeout=10)
    soup = BeautifulSoup(response.text, 'html.parser')
    table = soup.find('table')
    rows = table.find_all('tr')[1:]  # bỏ qua hàng tiêu đề
    prices = {}
    for row in rows:
        cols = row.find_all('td')
        if len(cols) < 5:
            continue
        item = cols[0].get_text(strip=True)
        # Chuyển đổi chuỗi giá (vd "0.00031") thành float
        p2p = float(cols[1].get_text(strip=True))
        seq = float(cols[2].get_text(strip=True))
        betty = float(cols[3].get_text(strip=True))
        prices[item] = {"P2P": p2p, "Seq": seq, "Betty": betty}
    return prices

def load_old_prices():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r') as f:
            return json.load(f)
    return {}

def save_prices(prices):
    with open(DATA_FILE, 'w') as f:
        json.dump(prices, f, indent=2)

def format_message(old, new):
    """Tạo tin nhắn thông báo thay đổi giá"""
    changes = []
    for item in new:
        if item not in old:
            changes.append(f"➕ {item} xuất hiện")
            continue
        for market in ["P2P", "Seq", "Betty"]:
            old_val = old[item][market]
            new_val = new[item][market]
            if old_val != new_val:
                changes.append(f"🔄 {item} - {market}: {old_val:.6f} → {new_val:.6f}")
    if not changes:
        return None
    return "📊 *Cập nhật giá Sunflower Land*\n" + "\n".join(changes)

def check_and_notify():
    print(f"[{time.ctime()}] Đang kiểm tra giá...")
    try:
        new_prices = fetch_prices()
        old_prices = load_old_prices()
        msg = format_message(old_prices, new_prices)
        if msg:
            bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='Markdown')
            print("Đã gửi thông báo.")
        save_prices(new_prices)
    except Exception as e:
        error_msg = f"❌ Lỗi khi lấy giá: {str(e)}"
        print(error_msg)
        bot.send_message(chat_id=CHAT_ID, text=error_msg)

# Lên lịch kiểm tra mỗi 15 phút
schedule.every(15).minutes.do(check_and_notify)

# Chạy lần đầu ngay khi khởi động
check_and_notify()

# Vòng lặp chính
while True:
    schedule.run_pending()
    time.sleep(1)