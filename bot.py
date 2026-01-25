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
        "✅ Post / Reel / Carousel / Story\n\n"
        "🔗 Link yuboring va yuklab olaman!"
    )

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    s = load_stats()
    await update.message.reply_text(
        f"📊 Statistika\n\n🔥 Bugun: {s['today']}\n📦 Jami: {s['total']}"
    )

# ================= INSTAGRAM =================
def ig_download(url, outdir):
    """Instagram'dan rasmlar, video, carousel, story yuklab olish"""
    
    ydl_opts = {
        "outtmpl": f"{outdir}/%(id)s_%(autonumber)s.%(ext)s",
        "quiet": False,
        "no_warnings": False,
        "format": "best",
        "merge_output_format": "mp4",
        
        # Headers
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                "Version/16.0 Mobile/15E148 Safari/604.1"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Referer": "https://www.instagram.com/",
            "DNT": "1",
        },
        
        # Network options
        "retries": 5,
        "fragment_retries": 5,
        "extractor_retries": 5,
        "sleep_interval": 1,
        "max_sleep_interval": 3,
        "socket_timeout": 30,
        
        # Postprocessor
        "postprocessors": [{
            "key": "FFmpegVideoConvertor",
            "preferedformat": "mp4",
        }],
        
        # Extract all media
        "writesubtitles": False,
        "writethumbnail": False,
        "extract_flat": False,
    }

    # Cookies
    if os.path.exists(COOKIES_FILE):
        ydl_opts["cookiefile"] = COOKIES_FILE

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        return info

def get_media_files(directory):
    """Katalogdagi barcha media fayllari"""
    media = []
    
    if not os.path.exists(directory):
        return media
    
    for filename in os.listdir(directory):
        filepath = os.path.join(directory, filename)
        
        # Faqat media fayllari
        if filename.endswith((".mp4", ".jpg", ".jpeg", ".png", ".webp")):
            # Faylni type bo'yicha ajratish
            if filename.endswith(".mp4"):
                media.append({"path": filepath, "type": "video"})
            else:
                media.append({"path": filepath, "type": "photo"})
    
    # Nom bo'yicha sort (tartib saqlansin)
    media.sort(key=lambda x: x["path"])
    return media

async def send_media_group(update, media_files, caption):
    """Media gruppani yuborish"""
    media_group = []
    opened_files = []
    
    try:
        for i, item in enumerate(media_files[:10]):  # Max 10ta
            # Fayl hajmini tekshirish
            if os.path.getsize(item["path"]) > 50 * 1024 * 1024:  # 50MB
                continue
            
            file_obj = open(item["path"], "rb")
            opened_files.append(file_obj)
            
            # Birinchi elementga caption
            cap = caption if i == 0 else None
            
            if item["type"] == "video":
                media_group.append(InputMediaVideo(file_obj, caption=cap))
            else:
                media_group.append(InputMediaPhoto(file_obj, caption=cap))
        
        if media_group:
            await update.message.reply_media_group(media_group)
            return True
        
        return False
        
    except Exception as e:
        print(f"❌ Media group yuborishda xato: {e}")
        return False
        
    finally:
        # Fayllarni yopish
        for f in opened_files:
            try:
                f.close()
            except:
                pass

async def send_single_media(update, media_file, caption):
    """Bitta media yuborish"""
    try:
        with open(media_file["path"], "rb") as f:
            if media_file["type"] == "video":
                await update.message.reply_video(f, caption=caption)
            else:
                await update.message.reply_photo(f, caption=caption)
        return True
    except Exception as e:
        print(f"❌ Media yuborishda xato: {e}")
        return False

async def handle_instagram(update, url):
    """Instagram kontentni yuklab olish va yuborish"""
    temp_dir = f"ig_{uuid.uuid4().hex}"
    os.makedirs(temp_dir, exist_ok=True)

    status_msg = await update.message.reply_text("⏳ Instagram'dan yuklanmoqda...")

    try:
        # Yuklab olish
        info = await asyncio.to_thread(ig_download, url, temp_dir)
        
        if not info:
            await status_msg.edit_text("❌ Yuklab bo'lmadi")
            return

        # Caption tayyorlash
        caption = clean_caption(info.get("description") or info.get("title", ""))
        
        # Media fayllarni olish
        media_files = get_media_files(temp_dir)
        
        if not media_files:
            await status_msg.edit_text("❌ Media fayl topilmadi")
            return

        await status_msg.edit_text("📤 Telegram'ga yuborilmoqda...")

        # Yuborish
        success = False
        if len(media_files) > 1:
            # Carousel yoki ko'p media
            success = await send_media_group(update, media_files, caption)
        else:
            # Bitta media
            success = await send_single_media(update, media_files[0], caption)

        if success:
            inc_stats()
            await asyncio.sleep(0.3)
            try:
                await status_msg.edit_text("✅ Yuklandi!")
            except:
                pass
        else:
            await status_msg.edit_text("❌ Yuborishda xatolik")

    except Exception as e:
        print(f"❌ Instagram xato: {e}")
        try:
            await status_msg.edit_text(f"❌ Xatolik: {str(e)[:100]}")
        except:
            pass

    finally:
        # Tozalash
        await asyncio.sleep(1)
        safe_cleanup(temp_dir)

# ================= MESSAGE HANDLER =================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Barcha xabarlarni boshqarish"""
    chat_id = update.effective_chat.id
    
    # Agar user allaqachon ishlayotgan bo'lsa
    if chat_id in ACTIVE_USERS:
        await update.message.reply_text("⏳ Kutib turing, oldingi yuklanish tugashini...")
        return

    ACTIVE_USERS.add(chat_id)
    
    try:
        url = update.message.text.strip()
        
        if is_instagram(url):
            await handle_instagram(update, url)
        else:
            await update.message.reply_text(
                "❌ Faqat Instagram havolalari qo'llab-quvvatlanadi\n\n"
                "📌 Qo'llab-quvvatlanadi:\n"
                "• Post (rasm/video)\n"
                "• Reel\n"
                "• Carousel\n"
                "• Story"
            )
    
    finally:
        ACTIVE_USERS.discard(chat_id)

# ================= MAIN =================
def main():
    """Botni ishga tushirish"""
    cleanup_on_start()
    print("🤖 Eclipse Core online")
    print("📥 Instagram downloader ishga tushdi")

    while True:
        try:
            app = Application.builder().token(BOT_TOKEN).build()
            
            # Handlerlar
            app.add_handler(CommandHandler("start", start))
            app.add_handler(CommandHandler("stats", stats_cmd))
            app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
            
            # Polling
            app.run_polling(
                allowed_updates=Update.ALL_TYPES,
                timeout=30,
                close_loop=False
            )
            
        except Exception as e:
            print(f"⚠️ Xatolik yuz berdi: {e}")
            print("🔄 3 soniyadan keyin qayta uriniladi...")
            time.sleep(3)

if __name__ == "__main__":
    main()