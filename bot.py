import os
import re
import uuid
import time
import shutil
import asyncio
import json
from datetime import date
import yt_dlp
import instaloader
from instaloader import Post
from dotenv import load_dotenv

from telegram import (
    Update,
    InputMediaPhoto,
    InputMediaVideo,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
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
PENDING_YT = {}  # chat_id -> url
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

# ================= INSTAGRAM =================
L = instaloader.Instaloader(
    download_video_thumbnails=False,
    download_comments=False,
    save_metadata=False,
    compress_json=False,
    post_metadata_txt_pattern="",
)

# ================= HELPERS =================
def safe_cleanup(path):
    if os.path.exists(path):
        shutil.rmtree(path, ignore_errors=True)

def cleanup_on_start():
    for f in os.listdir():
        if f.startswith(("ig_", "yt_")):
            shutil.rmtree(f, ignore_errors=True)

def extract_shortcode(url):
    for p in (
        r"instagram\.com/p/([^/?]+)",
        r"instagram\.com/reel/([^/?]+)",
        r"instagram\.com/tv/([^/?]+)",
    ):
        m = re.search(p, url)
        if m:
            return m.group(1)
    return None

def is_youtube(url):
    return "youtube.com/shorts/" in url or "youtu.be/" in url or "youtube.com/watch" in url

def clean_caption(text):
    if not text:
        return "📥 Downloaded"
    # Remove hashtags and extra whitespace
    cleaned = re.sub(r"#\w+", "", text).strip()
    # Limit to 500 characters
    return cleaned[:500] if cleaned else "📥 Downloaded"

# ================= COMMANDS =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 Eclipse Core online\n\n"
        "Instagram + YouTube Shorts downloader\n\n"
        "YT link → sifatni tanlaysan 👇"
    )

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    s = load_stats()
    await update.message.reply_text(
        f"📊 Stats\n\n🔥 Bugun: {s['today']}\n📦 Jami: {s['total']}"
    )

# ================= YOUTUBE =================
def yt_download(url, outdir, height):
    ydl_opts = {
        "outtmpl": f"{outdir}/%(id)s.%(ext)s",
        "quiet": True,
        "no_warnings": True,
        "format": f"bv*[height<={height}][fps<=30]/bv*+ba/b",
        "merge_output_format": "mp4",
        "postprocessors": [
            {
                "key": "FFmpegVideoConvertor",
                "preferedformat": "mp4",
            }
        ],
    }
    
    if os.path.exists(COOKIES_FILE):
        ydl_opts["cookiefile"] = COOKIES_FILE
    else:
        print(f"⚠️ WARNING: cookies.txt not found at {COOKIES_FILE}")

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

async def yt_quality_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat_id
    url = PENDING_YT.pop(chat_id, None)

    if not url:
        await query.edit_message_text("❌ Session yo'q, qayta link tashla")
        return

    height = 720 if query.data == "yt_720" else 1080
    temp = f"yt_{uuid.uuid4().hex}"
    os.makedirs(temp, exist_ok=True)

    await query.edit_message_text(f"⏳ YouTube {height}p yuklanmoqda...")

    try:
        await asyncio.to_thread(yt_download, url, temp, height)
        
        files = [f for f in os.listdir(temp) if os.path.isfile(os.path.join(temp, f))]
        if not files:
            await query.edit_message_text("❌ Video yuklanmadi")
            return
            
        file = os.path.join(temp, files[0])

        if os.path.getsize(file) > 50 * 1024 * 1024:
            await query.edit_message_text("❌ Video 50MB dan katta")
            return

        with open(file, "rb") as v:
            await query.message.reply_video(v, caption=f"🎬 YouTube {height}p")

        inc_stats()
        await query.message.delete()

    except Exception as e:
        print("YT ERROR:", e)
        error_msg = str(e)
        if "Sign in" in error_msg or "bot" in error_msg:
            await query.edit_message_text(
                "❌ YouTube cookies eskirgan!\n"
                "cookies.txt ni yangilang."
            )
        else:
            await query.edit_message_text("❌ YouTube yuklashda xatolik")

    finally:
        safe_cleanup(temp)

# ================= INSTAGRAM =================
def ig_download(shortcode, outdir):
    post = Post.from_shortcode(L.context, shortcode)
    L.download_post(post, target=outdir)
    return post

async def handle_instagram(update, url):
    shortcode = extract_shortcode(url)
    if not shortcode:
        await update.message.reply_text("❌ Instagram link noto'g'ri")
        return

    temp = f"ig_{uuid.uuid4().hex}"
    os.makedirs(temp, exist_ok=True)
    status = await update.message.reply_text("⏳ Instagram'dan yuklanmoqda...")
    post = None

    try:
        post = await asyncio.to_thread(ig_download, shortcode, temp)
    except Exception as e:
        await status.edit_text("❌ Instagram yuklashda xatolik")
        print("IG DOWNLOAD ERROR:", e)
        safe_cleanup(temp)
        return

    try:
        caption = clean_caption(post.caption)
        all_files = [
            os.path.join(temp, f)
            for f in os.listdir(temp)
            if f.endswith((".mp4", ".jpg", ".png"))
        ]

        if post.typename == "GraphSidecar":
            files = [
                f for f in all_files if re.search(r'_\d+\.(mp4|jpg|png)$', f)
            ]
        else:
            files = all_files

        files.sort()

        if not files:
            await status.edit_text("❌ Media topilmadi")
            return

        await status.edit_text("📤 Telegram'ga yuborilmoqda...")

        if post.typename == "GraphSidecar":
            media = []
            opened_files = []
            
            for i, f in enumerate(files[:10]):
                if os.path.getsize(f) > 50 * 1024 * 1024:
                    continue
                
                file_obj = open(f, "rb")
                opened_files.append(file_obj)
                
                if f.endswith(".mp4"):
                    media.append(
                        InputMediaVideo(file_obj, caption=caption if i == 0 else None)
                    )
                else:
                    media.append(
                        InputMediaPhoto(file_obj, caption=caption if i == 0 else None)
                    )
            
            if media:
                await update.message.reply_media_group(media)
                
                # Fayllarni yuborilgandan keyin yopamiz
                for file_obj in opened_files:
                    file_obj.close()

        elif post.is_video:
            with open(files[0], "rb") as v:
                await update.message.reply_video(v, caption=caption)
        else:
            with open(files[0], "rb") as p:
                await update.message.reply_photo(p, caption=caption)

        inc_stats()
        await status.delete()

    except Exception as e:
        await status.edit_text("❌ Media yuborishda xatolik")
        print("IG SEND ERROR:", e)
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

        if "instagram.com" in url:
            await handle_instagram(update, url)

        elif is_youtube(url):
            PENDING_YT[chat_id] = url
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("🎥 720p", callback_data="yt_720"),
                    InlineKeyboardButton("🔥 1080p", callback_data="yt_1080"),
                ]
            ])
            await update.message.reply_text(
                "Qaysi sifatda yuklaymiz?",
                reply_markup=keyboard
            )
        else:
            await update.message.reply_text("❌ Qo'llab-quvvatlanmaydi")

    except Exception as e:
        print("HANDLE ERROR:", e)
        await update.message.reply_text("❌ Xatolik yuz berdi")
    finally:
        ACTIVE_USERS.discard(chat_id)

# ================= MAIN =================
def main():
    cleanup_on_start()
    
    if os.path.exists(COOKIES_FILE):
        print(f"✅ cookies.txt found at: {COOKIES_FILE}")
    else:
        print(f"⚠️ WARNING: cookies.txt NOT found at: {COOKIES_FILE}")
        print("   YouTube downloads may fail without cookies!")
    
    print("🤖 Eclipse Core online")

    while True:
        try:
            app = Application.builder().token(BOT_TOKEN).build()

            app.add_handler(CommandHandler("start", start))
            app.add_handler(CommandHandler("stats", stats_cmd))
            app.add_handler(CallbackQueryHandler(yt_quality_callback))
            app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

            app.run_polling(
                allowed_updates=Update.ALL_TYPES,
                timeout=30,
                close_loop=False,
            )

        except Exception as e:
            print("⚠️ Connection lost, retrying...")
            print(f"Error: {e}")
            time.sleep(3)

if __name__ == "__main__":
    main()