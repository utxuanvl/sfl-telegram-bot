import asyncio
import logging
import os
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from starlette.routing import Route
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
import cloudscraper
from bs4 import BeautifulSoup

# --- Cấu hình logging ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# --- Lấy biến môi trường (Render sẽ cung cấp) ---
URL = os.environ.get("RENDER_EXTERNAL_URL")
PORT = int(os.environ.get("PORT", 8000))
TOKEN = os.environ.get("BOT_TOKEN")

if not TOKEN:
    raise ValueError("BOT_TOKEN environment variable is required")

USE_WEBHOOK = URL is not None
logger.info("Bot đang chạy ở chế độ: %s", "webhook" if USE_WEBHOOK else "polling")

# --- Hàm lấy giá từ web (dùng cloudscraper) ---
def fetch_prices():
    url = "https://sfl.world/util/prices"
    # Tạo scraper với user-agent của Chrome để tránh bị phát hiện
    scraper = cloudscraper.create_scraper(
        browser={
            'browser': 'chrome',
            'platform': 'windows',
            'desktop': True
        }
    )
    
    try:
        response = scraper.get(url, timeout=15)
        if response.status_code != 200:
            logger.error(f"Lỗi HTTP {response.status_code}")
            return f"⚠️ Lỗi kết nối: HTTP {response.status_code}"
        
        soup = BeautifulSoup(response.text, 'html.parser')
        table = soup.find('table')
        
        if not table:
            logger.warning("Không tìm thấy bảng giá")
            return "⚠️ Không tìm thấy bảng giá. Trang có thể đã đổi cấu trúc."

        rows = table.find_all('tr')
        if len(rows) < 2:
            return "⚠️ Bảng giá không có dữ liệu."
        
        # Xác định thứ tự cột từ header
        header_row = rows[0]
        headers_cols = [th.get_text(strip=True).lower() for th in header_row.find_all(['th', 'td'])]
        col_map = {}
        for idx, name in enumerate(headers_cols):
            if 'item' in name or 'tên' in name or 'product' in name:
                col_map['item'] = idx
            elif 'p2p' in name:
                col_map['p2p'] = idx
            elif 'seq' in name:
                col_map['seq'] = idx
            elif 'betty' in name or 'cửa hàng' in name:
                col_map['betty'] = idx
        
        # Mặc định nếu không tìm thấy (cột: 0-item, 1-p2p, 2-seq, 3-betty)
        if not col_map:
            col_map = {'item': 0, 'p2p': 1, 'seq': 2, 'betty': 3}
        
        lines = ["📊 *Giá Sunflower Land hôm nay* 📊\n"]
        for row in rows[1:]:
            cells = row.find_all(['td', 'th'])
            if len(cells) < 4:
                continue
            item = cells[col_map['item']].get_text(strip=True) if col_map['item'] < len(cells) else "?"
            p2p = cells[col_map['p2p']].get_text(strip=True) if col_map['p2p'] < len(cells) else "?"
            seq = cells[col_map['seq']].get_text(strip=True) if col_map['seq'] < len(cells) else "?"
            betty = cells[col_map['betty']].get_text(strip=True) if col_map['betty'] < len(cells) else "?"
            
            # Bỏ qua các dòng không có dữ liệu hoặc là header phụ
            if item in ['Item', 'Greenhouse', 'Bakery', 'Deli', 'Tools', 'Resources', 'Crops']:
                continue
            if item and p2p != '' and seq != '':
                lines.append(f"🔹 *{item}*")
                lines.append(f"   P2P: `{p2p}` | Seq: `{seq}` | Betty: `{betty}`")
                lines.append("")
        
        if len(lines) == 1:
            return "⚠️ Không đọc được dữ liệu từ bảng."
        return "\n".join(lines)
        
    except Exception as e:
        logger.error(f"Lỗi fetch: {e}")
        return f"⚠️ Lỗi kết nối: {str(e)}"

# --- Các lệnh bot ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Chào bạn! Tôi là bot giá Sunflower Land.\n"
        "Gửi /check để xem bảng giá mới nhất."
    )

async def check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Đang lấy giá, vui lòng chờ...")
    info = fetch_prices()
    if len(info) > 4096:
        for i in range(0, len(info), 4096):
            await update.message.reply_text(info[i:i+4096], parse_mode='Markdown')
    else:
        await update.message.reply_text(info, parse_mode='Markdown')

# --- Khởi động bot (giữ nguyên) ---
async def main():
    application = Application.builder().token(TOKEN).updater(None).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("check", check))

    if USE_WEBHOOK:
        logger.info("Chế độ webhook, URL: %s", URL)
        await application.bot.set_webhook(url=f"{URL}/telegram")

        async def webhook(request: Request) -> Response:
            data = await request.json()
            await application.update_queue.put(Update.de_json(data, application.bot))
            return Response()

        async def health(request: Request) -> PlainTextResponse:
            return PlainTextResponse("Bot is running!")

        starlette_app = Starlette(routes=[
            Route("/telegram", webhook, methods=["POST"]),
            Route("/healthcheck", health, methods=["GET"]),
        ])
        config = uvicorn.Config(app=starlette_app, port=PORT, host="0.0.0.0")
        server = uvicorn.Server(config)

        async with application:
            await application.start()
            await server.serve()
            await application.stop()
    else:
        logger.info("Chế độ polling (chạy local)")
        async with application:
            await application.start()
            await application.updater.start_polling()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                pass
            finally:
                await application.updater.stop()
                await application.stop()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot dừng bởi người dùng")
    except Exception as e:
        logger.error("Lỗi khởi động: %s", e)
        raise