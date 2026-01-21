import os
import re
import uuid
import time
import shutil
import asyncio
import json
from datetime import date
import yt_dlp
from dotenv import load_dotenv

from telegram import (
    Update,
    InputMediaPhoto,
    InputMediaVideo,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ================= ENV =================
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN yo'q")

# ================= PATHS =================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
COOKIES_FILE = os.path.join(BASE_DIR, "cookies.txt")

# ================= GLOBALS =================
ACTIVE_USERS = set()
STATS_FILE = "stats.json"

# ================= STATS =================
def load_stats():
    if not os.path.exists(STATS_FILE):
        return {"total": 0, "today": 0, "date": str(date.today())}
    with open(STATS_FILE, "r") as f:
        return json.load(f)

def save_stats(stats):
    with open(STATS_FILE, "w") as f:
        json.dump(stats, f)

def inc_stats():
    stats = load_stats()
    if stats["date"] != str(date.today()):
        stats["date"] = str(date.today())
        stats["today"] = 0
    stats["today"] += 1
    stats["total"] += 1
    save_stats(stats)

# ================= HELPERS =================
def safe_cleanup(path):
    if os.path.exists(path):
        shutil.rmtree(path, ignore_errors=True)

def cleanup_on_start():
    for f in os.listdir():
        if f.startswith("ig_"):
            shutil.rmtree(f, ignore_errors=True)

def is_instagram(url):
    return "instagram.com" in url

def clean_caption(text):
    if not text:
        return "📥 Downloaded"
    text = re.sub(r"#\w+", "", text).strip()
    return text[:500] if text else "📥 Downloaded"

# ================= COMMANDS =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 Eclipse Core online\n\n"
        "📥 Instagram downloader\n"
        "Post / Reel / Carousel"
    )

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    s = load_stats()
    await update.message.reply_text(
        f"📊 Stats\n\n🔥 Bugun: {s['today']}\n📦 Jami: {s['total']}"
    )

# ================= INSTAGRAM =================
def ig_download(url, outdir):
    ydl_opts = {
        "outtmpl": f"{outdir}/%(id)s.%(ext)s",
        "quiet": True,
        "no_warnings": True,
        "user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "referer": "https://www.instagram.com/",
        "retries": 3,
        "fragment_retries": 3,
        "extractor_retries": 3,
        "sleep_interval": 2,
        "max_sleep_interval": 5,
    }

    if os.path.exists(COOKIES_FILE):
        ydl_opts["cookiefile"] = COOKIES_FILE

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(url, download=True)

async def handle_instagram(update, url):
    temp = f"ig_{uuid.uuid4().hex}"
    os.makedirs(temp, exist_ok=True)

    status = await update.message.reply_text("⏳ Instagram'dan yuklanmoqda...")

    try:
        info = await asyncio.to_thread(ig_download, url, temp)
        if not info:
            return

        caption = clean_caption(info.get("description", ""))

        files = [
            os.path.join(temp, f)
            for f in os.listdir(temp)
            if f.endswith((".mp4", ".jpg", ".png", ".webp"))
        ]
        if not files:
            return

        files.sort()
        await status.edit_text("📤 Telegram'ga yuborilmoqda...")

        # ===== ALBUM =====
        if len(files) > 1:
            media, opened = [], []
            for i, f in enumerate(files[:10]):
                if os.path.getsize(f) > 50 * 1024 * 1024:
                    continue
                fo = open(f, "rb")
                opened.append(fo)

                media.append(
                    InputMediaVideo(fo, caption=caption if i == 0 else None)
                    if f.endswith(".mp4")
                    else InputMediaPhoto(fo, caption=caption if i == 0 else None)
                )

            if media:
                try:
                    await update.message.reply_media_group(media)
                except Exception as e:
                    print("MEDIA GROUP ERROR:", e)

            for fo in opened:
                fo.close()

        # ===== SINGLE =====
        else:
            f = files[0]
            try:
                if f.endswith(".mp4"):
                    with open(f, "rb") as v:
                        await update.message.reply_video(v, caption=caption)
                else:
                    with open(f, "rb") as p:
                        await update.message.reply_photo(p, caption=caption)
            except Exception as e:
                print("SEND ERROR:", e)

        inc_stats()

        await asyncio.sleep(0.5)
        try:
            await status.edit_text("✅ Yuklandi!")
        except:
            pass

    except Exception as e:
        print("IG ERROR:", e)

    finally:
        safe_cleanup(temp)

# ================= ROUTER =================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id in ACTIVE_USERS:
        await update.message.reply_text("⏳ Kut, hozir ishlayapman")
        return

    ACTIVE_USERS.add(chat_id)
    try:
        url = update.message.text.strip()
        if is_instagram(url):
            await handle_instagram(update, url)
        else:
            await update.message.reply_text("❌ Faqat Instagram link")
    finally:
        ACTIVE_USERS.discard(chat_id)

# ================= MAIN =================
def main():
    cleanup_on_start()
    print("🤖 Eclipse Core online")

    while True:
        try:
            app = Application.builder().token(BOT_TOKEN).build()
            app.add_handler(CommandHandler("start", start))
            app.add_handler(CommandHandler("stats", stats_cmd))
            app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
            app.run_polling(allowed_updates=Update.ALL_TYPES, timeout=30, close_loop=False)
        except Exception as e:
            print("⚠️ Restarting:", e)
            time.sleep(3)

if __name__ == "__main__":
    main()
