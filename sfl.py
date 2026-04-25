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

# --- Hàm lấy giá từ web (dùng cloudscraper để vượt Cloudflare) ---
def fetch_prices():
    url = "https://sfl.world/util/prices"
    try:
        # Tạo scraper với cấu hình giống trình duyệt
        scraper = cloudscraper.create_scraper(
            browser={
                'browser': 'chrome',
                'platform': 'windows',
                'mobile': False
            }
        )
        response = scraper.get(url, timeout=30)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        table = soup.find('table')
        if not table:
            # Thử tìm trong div table-responsive
            table_div = soup.find('div', class_='table-responsive')
            if table_div:
                table = table_div.find('table')
        if not table:
            return "⚠️ Không tìm thấy bảng giá. Trang có thể đã đổi cấu trúc."
        
        rows = table.find_all('tr')
        if len(rows) < 2:
            return "⚠️ Bảng giá không có dữ liệu."
        
        # Dựa trên kết quả test, hàng 0 là header, các hàng tiếp theo là dữ liệu
        # Trong test, dòng 1 là ["Greenhouse"] (tên nhóm), dòng 2 là ["Grape", ...]
        # Cần xử lý các dòng không có đủ cột
        
        lines = ["📊 *Giá Sunflower Land hôm nay* 📊\n"]
        i = 1
        while i < len(rows):
            row = rows[i]
            cells = row.find_all(['td', 'th'])
            cell_texts = [c.get_text(strip=True) for c in cells]
            
            # Nếu chỉ có 1 cột, đó có thể là tên nhóm (ví dụ "Greenhouse")
            if len(cell_texts) == 1 and cell_texts[0]:
                lines.append(f"🏷️ *{cell_texts[0]}*")
                i += 1
                continue
            
            # Dòng dữ liệu bình thường có ít nhất 4 cột (item, p2p, seq, betty)
            if len(cell_texts) >= 4:
                item = cell_texts[0]
                p2p = cell_texts[1] if len(cell_texts) > 1 else "?"
                # Cột thứ 2 có thể là seq (trong test, cột 2 là rỗng, cột 3 là seq? Hãy xem lại)
                # Trong test: Dòng 2: ['Grape', '0.26086', '', '0.75000', ...]
                # Vậy cells[1] là P2P, cells[2] là rỗng (không dùng), cells[3] là Seq? Không, Betty ở cells[3]?
                # Thực tế, dựa vào header: ['Item', 'P2P-10%', 'Seq-30%', 'Betty1:320', ...]
                # Nên cells[1] = P2P, cells[2] = Seq, cells[3] = Betty
                seq = cell_texts[2] if len(cell_texts) > 2 else "?"
                betty = cell_texts[3] if len(cell_texts) > 3 else "?"
                lines.append(f"🔹 *{item}*")
                lines.append(f"   P2P: `{p2p}` | Seq: `{seq}` | Betty: `{betty}`")
                lines.append("")
            i += 1
        
        if len(lines) == 1:
            return "⚠️ Không đọc được dữ liệu từ bảng."
        return "\n".join(lines)
        
    except Exception as e:
        logger.error(f"Lỗi fetch: {e}")
        return f"⚠️ Lỗi khi lấy giá: {str(e)}"

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

# --- Khởi động bot ---
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