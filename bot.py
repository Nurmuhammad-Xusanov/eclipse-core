import os
import re
import uuid
import shutil
import asyncio
import json
from datetime import date
from pathlib import Path

import instaloader
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
    raise RuntimeError("BOT_TOKEN yo‘q")

IG_USER = os.getenv("INSTAGRAM_USERNAME", "")
IG_PASS = os.getenv("INSTAGRAM_PASSWORD", "")

# ================= PATHS =================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SESSION_FILE = os.path.join(BASE_DIR, "insta_session")
STATS_FILE = "stats.json"

# ================= GLOBALS =================
ACTIVE_USERS = set()

# ================= INSTALOADER =================
L = instaloader.Instaloader(
    download_video_thumbnails=False,
    download_geotags=False,
    download_comments=False,
    save_metadata=False,
    compress_json=False,
    post_metadata_txt_pattern="",
    quiet=True,
)

L.context.raise_all_errors = True

def ig_login():
    try:
        if IG_USER and os.path.exists(SESSION_FILE):
            L.load_session_from_file(IG_USER, SESSION_FILE)
            print("✅ IG session loaded")
            return

        if IG_USER and IG_PASS:
            L.login(IG_USER, IG_PASS)
            L.save_session_to_file(SESSION_FILE)
            print("✅ IG logged in")
            return

        print("⚠️ IG login yo‘q — faqat public")
    except Exception as e:
        print("⚠️ IG login error:", e)

ig_login()

# ================= STATS =================
def load_stats():
    if not os.path.exists(STATS_FILE):
        return {"total": 0, "today": 0, "date": str(date.today())}
    with open(STATS_FILE, "r") as f:
        return json.load(f)

def save_stats(s):
    with open(STATS_FILE, "w") as f:
        json.dump(s, f)

def inc_stats():
    s = load_stats()
    if s["date"] != str(date.today()):
        s["date"] = str(date.today())
        s["today"] = 0
    s["today"] += 1
    s["total"] += 1
    save_stats(s)

# ================= HELPERS =================
def safe_cleanup(p):
    shutil.rmtree(p, ignore_errors=True)

async def safe_edit(msg, text):
    try:
        await msg.edit_text(text)
    except:
        pass

def is_instagram(url):
    return "instagram.com" in url

def extract_shortcode(url):
    m = re.search(r'instagram\.com/(?:p|reel|tv)/([A-Za-z0-9_-]+)', url)
    return m.group(1) if m else None

def clean_caption(text):
    if not text:
        return "📥 Instagram"
    text = re.sub(r"#\w+", "", text).strip()
    return text[:1000] or "📥 Instagram"

def get_media_files(directory):
    media = []
    for f in Path(directory).rglob("*"):
        if f.suffix.lower() in (".mp4", ".jpg", ".jpeg", ".png", ".webp"):
            media.append({
                "path": str(f),
                "type": "video" if f.suffix.lower() == ".mp4" else "photo"
            })
    return sorted(media, key=lambda x: x["path"])

# ================= DOWNLOAD =================
def download_post(shortcode, temp_dir):
    post = instaloader.Post.from_shortcode(L.context, shortcode)
    L.download_post(post, target=temp_dir)
    return post.caption or ""

# ================= SEND =================
async def send_media(update, media, caption):
    if len(media) == 1:
        with open(media[0]["path"], "rb") as f:
            if media[0]["type"] == "video":
                await update.message.reply_video(video=f, caption=caption)
            else:
                await update.message.reply_photo(photo=f, caption=caption)
        return

    group = []
    files = []
    for i, m in enumerate(media[:10]):
        f = open(m["path"], "rb")
        files.append(f)
        if m["type"] == "video":
            group.append(InputMediaVideo(f, caption=caption if i == 0 else None))
        else:
            group.append(InputMediaPhoto(f, caption=caption if i == 0 else None))

    await update.message.reply_media_group(group)

    for f in files:
        f.close()

# ================= CORE =================
async def handle_instagram(update):
    url = update.message.text.strip()
    temp_dir = f"ig_{uuid.uuid4().hex}"
    os.makedirs(temp_dir, exist_ok=True)

    status = await update.message.reply_text("⏳ Yuklanmoqda...")

    try:
        shortcode = extract_shortcode(url)
        if not shortcode:
            await safe_edit(status, "❌ Noto‘g‘ri Instagram link")
            return

        caption = await asyncio.to_thread(download_post, shortcode, temp_dir)

        media = get_media_files(temp_dir)
        if not media:
            await safe_edit(status, "❌ Media topilmadi")
            return

        await status.delete()
        await send_media(update, media, clean_caption(caption))
        inc_stats()

    except json.JSONDecodeError:
        await safe_edit(status, "❌ Instagram vaqtincha javob bermadi")
    except Exception as e:
        await safe_edit(status, f"❌ Xato: {str(e)[:120]}")
    finally:
        safe_cleanup(temp_dir)

# ================= MESSAGE =================
async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    if cid in ACTIVE_USERS:
        await update.message.reply_text("⏳ Kut...")
        return

    ACTIVE_USERS.add(cid)
    try:
        if is_instagram(update.message.text):
            await handle_instagram(update)
        else:
            await update.message.reply_text("❌ Faqat Instagram link")
    finally:
        ACTIVE_USERS.discard(cid)

# ================= MAIN =================
def main():
    print("🤖 Eclipse Core ONLINE")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", lambda u, c: u.message.reply_text("📥 Instagram downloader")))
    app.add_handler(CommandHandler("stats", lambda u, c: u.message.reply_text(str(load_stats()))))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))

    app.run_polling()

if __name__ == "__main__":
    main()