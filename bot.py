import os
import re
import uuid
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
    raise RuntimeError("BOT_TOKEN yo‘q")

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
    return "youtube.com/shorts/" in url or "youtu.be/" in url

def clean_caption(text):
    if not text:
        return "📥 Downloaded"
    return re.sub(r"#\w+", "", text).strip()[:500]

# ================= COMMANDS =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 Eclipse Core online\n\n"
        "IG + YouTube Shorts downloader\n\n"
        "YT link tashla → sifatni tanlaysan 👇"
    )

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    s = load_stats()
    await update.message.reply_text(
        f"📊 Stats\n\n🔥 Bugun: {s['today']}\n📦 Jami: {s['total']}"
    )

# ================= YOUTUBE CORE =================
def yt_download(url, outdir, height):
    ydl_opts = {
        "outtmpl": f"{outdir}/%(id)s.%(ext)s",
        "quiet": True,
        "no_warnings": True,
        "cookiefile": "cookies.txt",
        "format": f"bv*[height<={height}][fps<=30]/bv*+ba/b",
        "merge_output_format": "mp4",
        "postprocessors": [
            {
                "key": "FFmpegVideoConvertor",
                "preferedformat": "mp4",
            }
        ],
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

# ================= CALLBACK =================
async def yt_quality_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat_id
    url = PENDING_YT.pop(chat_id, None)

    if not url:
        await query.edit_message_text("❌ Session yo‘q, qayta link tashla")
        return

    height = 720 if query.data == "yt_720" else 1080
    temp = f"yt_{uuid.uuid4().hex}"
    os.makedirs(temp, exist_ok=True)

    await query.edit_message_text(f"⏳ YouTube {height}p yuklanmoqda...")

    try:
        await asyncio.to_thread(yt_download, url, temp, height)
        file = next(os.path.join(temp, f) for f in os.listdir(temp))

        if os.path.getsize(file) > 50 * 1024 * 1024:
            await query.edit_message_text("❌ Video 50MB dan katta")
            return

        with open(file, "rb") as v:
            await query.message.reply_video(v, caption=f"🎬 YouTube {height}p")

        inc_stats()

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
        await update.message.reply_text("❌ Instagram link noto‘g‘ri")
        return

    temp = f"ig_{uuid.uuid4().hex}"
    os.makedirs(temp, exist_ok=True)

    status = await update.message.reply_text("⏳ Instagram’dan yuklanmoqda...")

    try:
        post = await asyncio.to_thread(ig_download, shortcode, temp)
        caption = clean_caption(post.caption)

        files = [
            os.path.join(temp, f)
            for f in os.listdir(temp)
            if f.endswith((".mp4", ".jpg", ".png"))
        ]

        if not files:
            await status.edit_text("❌ Media topilmadi")
            return

        await status.edit_text("📤 Telegram’ga yuborilmoqda...")

        if post.typename == "GraphSidecar":
            media = []
            for i, f in enumerate(files[:10]):
                if os.path.getsize(f) > 50 * 1024 * 1024:
                    continue

                if f.endswith(".mp4"):
                    media.append(
                        InputMediaVideo(
                            open(f, "rb"),
                            caption=caption if i == 0 else None
                        )
                    )
                else:
                    media.append(
                        InputMediaPhoto(
                            open(f, "rb"),
                            caption=caption if i == 0 else None
                        )
                    )

            if media:
                await update.message.reply_media_group(media)
                for m in media:
                    m.media.close()
            else:
                await status.edit_text("❌ Fayllar juda katta")

        elif post.is_video:
            with open(next(f for f in files if f.endswith(".mp4")), "rb") as v:
                await update.message.reply_video(v, caption=caption)
        else:
            with open(files[0], "rb") as p:
                await update.message.reply_photo(p, caption=caption)

        inc_stats()
        await status.delete()

    except Exception as e:
        await status.edit_text("❌ Instagram yuklashda xatolik")
        print("IG ERROR:", e)

    finally:
        safe_cleanup(temp)

    shortcode = extract_shortcode(url)
    if not shortcode:
        await update.message.reply_text("❌ IG link noto‘g‘ri")
        return

    temp = f"ig_{uuid.uuid4().hex}"
    os.makedirs(temp, exist_ok=True)

    try:
        post = await asyncio.to_thread(ig_download, shortcode, temp)
        caption = clean_caption(post.caption)

        files = [
            os.path.join(temp, f)
            for f in os.listdir(temp)
            if f.endswith((".mp4", ".jpg", ".png"))
        ]

        if post.is_video:
            with open(next(f for f in files if f.endswith(".mp4")), "rb") as v:
                await update.message.reply_video(v, caption=caption)
        else:
            with open(files[0], "rb") as p:
                await update.message.reply_photo(p, caption=caption)

        inc_stats()

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
            await update.message.reply_text("❌ Qo‘llab-quvvatlanmaydi")

    finally:
        ACTIVE_USERS.discard(chat_id)

# ================= MAIN =================
def main():
    cleanup_on_start()
    print("🤖 Eclipse Core online")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CallbackQueryHandler(yt_quality_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    app.run_polling()

if __name__ == "__main__":
    main()
