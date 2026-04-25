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
import requests
from bs4 import BeautifulSoup

# --- Cấu hình logging ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# --- Lấy biến môi trường (Render sẽ cung cấp) ---
URL = os.environ.get("RENDER_EXTERNAL_URL")   # URL của web service do Render tạo
PORT = int(os.environ.get("PORT", 8000))
TOKEN = os.environ.get("BOT_TOKEN")           # Token bot bạn nhập sau

if not TOKEN:
    raise ValueError("BOT_TOKEN environment variable is required")

USE_WEBHOOK = URL is not None
logger.info("Bot đang chạy ở chế độ: %s", "webhook" if USE_WEBHOOK else "polling")

# --- Hàm lấy giá từ web ---
def fetch_prices():
    url = "https://sfl.world/util/prices"
    try:
        response = requests.get(url, timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')
        table = soup.find('table')
        if not table:
            return "⚠️ Không tìm thấy bảng giá."

        rows = table.find_all('tr')[1:]   # bỏ hàng tiêu đề
        lines = ["📊 *Giá Sunflower Land hôm nay* 📊\n"]
        for row in rows:
            cols = row.find_all('td')
            if len(cols) >= 4:
                item = cols[0].get_text(strip=True)
                p2p = cols[1].get_text(strip=True)
                seq = cols[2].get_text(strip=True)
                betty = cols[3].get_text(strip=True)
                lines.append(f"🔹 *{item}*")
                lines.append(f"   P2P: `{p2p}` | Seq: `{seq}` | Betty: `{betty}`")
                lines.append("")
        if len(lines) == 1:
            return "⚠️ Không thể đọc dữ liệu."
        return "\n".join(lines)
    except Exception as e:
        logger.error(f"Lỗi fetch: {e}")
        return "⚠️ Lỗi kết nối đến trang web."

# --- Các lệnh bot ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Chào bạn! Tôi là bot giá Sunflower Land.\n"
        "Gửi /check để xem bảng giá mới nhất."
    )

async def check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Đang lấy giá, vui lòng chờ...")
    info = fetch_prices()
    # Telegram giới hạn 4096 ký tự, nếu dài quá thì cắt
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