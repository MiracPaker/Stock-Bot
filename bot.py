"""
Ana giriş noktası.

Render'ın ücretsiz "Web Service" katmanı bir PORT'a bağlanmayı ve düzenli
istek almayı bekliyor; aksi halde 15 dakika hareketsizlikte servisi uykuya
alıyor (ki bizim long-polling döngümüz de o an duruyor demektir).

Bunu çözmek için:
1. Ayrı bir arka plan thread'inde minik bir HTTP sunucusu çalıştırıyoruz
   (sadece "OK" döner) - Render bunu "servis ayakta" olarak görüyor.
2. Dışarıdan ücretsiz bir servis (örn. UptimeRobot) bu adrese her birkaç
   dakikada bir istek atarak servisi uyanık tutuyor (kurulum rehberine bak).
3. Ana thread'de Telegram'ın long-polling mekanizmasıyla botu çalıştırıyoruz.
"""

import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import database
from handlers import commands as cmd_handlers
from handlers import meals as meal_handlers

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("bot")


# ---------------------------------------------------------------------------
# Render keep-alive HTTP sunucusu
# ---------------------------------------------------------------------------

class _KeepAliveHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK - kalori-takip botu calisiyor")

    def do_HEAD(self):
        # UptimeRobot ve benzeri izleme servisleri bazen GET yerine HEAD
        # kullanır - bunu da desteklemezsek "down" olarak işaretlenebilir.
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()

    def log_message(self, format, *args):
        pass  # HTTP erişim loglarıyla asıl bot loglarını kirletmeyelim


def _start_keepalive_server():
    port = int(os.environ.get("PORT", "8080"))
    server = ThreadingHTTPServer(("0.0.0.0", port), _KeepAliveHandler)
    log.info(f"Keep-alive HTTP sunucusu {port} portunda başlatıldı.")
    server.serve_forever()


# ---------------------------------------------------------------------------
# Hata yönetimi - bot hiçbir durumda tamamen çökmemeli
# ---------------------------------------------------------------------------

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    log.error("Beklenmeyen hata:", exc_info=context.error)
    try:
        if isinstance(update, Update) and update.effective_message:
            await update.effective_message.reply_text(
                "⚠️ Beklenmedik bir hata oluştu. Tekrar dener misin?"
            )
    except Exception:
        pass  # hata mesajı gönderirken bile hata olursa sessizce geç


def main():
    telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not telegram_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN ortam değişkeni tanımlı değil.")

    log.info("Veritabanı şeması kontrol ediliyor...")
    database.init_db()

    keepalive_thread = threading.Thread(target=_start_keepalive_server, daemon=True)
    keepalive_thread.start()

    application = Application.builder().token(telegram_token).build()

    application.add_handler(CommandHandler("start", cmd_handlers.start_command))
    application.add_handler(CommandHandler("yardim", cmd_handlers.help_command))
    application.add_handler(CommandHandler("bugun", cmd_handlers.today_command))

    application.add_handler(MessageHandler(filters.PHOTO, meal_handlers.photo_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, meal_handlers.text_handler))
    application.add_handler(CallbackQueryHandler(meal_handlers.meal_callback_handler, pattern="^meal_"))

    application.add_error_handler(error_handler)

    log.info("Bot polling ile başlatılıyor...")
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
