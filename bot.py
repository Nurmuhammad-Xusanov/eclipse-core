import os
import re
import uuid
import shutil
import asyncio
import json
from datetime import date
from pathlib import Path

import instaloader
import instaloader.structures
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

# ================= INSTALOADER JSON KILL =================
instaloader.structures.Post._field_structure = {}
instaloader.structures.PostSidecarNode._field_structure = {}


# ================= ENV =================
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN yo'q")

INSTAGRAM_USERNAME = os.getenv("INSTAGRAM_USERNAME", "")
INSTAGRAM_PASSWORD = os.getenv("INSTAGRAM_PASSWORD", "")

# ================= PATHS =================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SESSION_FILE = os.path.join(BASE_DIR, "insta_session")

# ================= GLOBALS =================
ACTIVE_USERS = set()
STATS_FILE = "stats.json"

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

def instaloader_login():
    try:
        if os.path.exists(SESSION_FILE):
            L.load_session_from_file(INSTAGRAM_USERNAME, SESSION_FILE)
            return
        if INSTAGRAM_USERNAME and INSTAGRAM_PASSWORD:
            L.login(INSTAGRAM_USERNAME, INSTAGRAM_PASSWORD)
            L.save_session_to_file(SESSION_FILE)
    except:
        pass

instaloader_login()

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
    s = load_stats()
    if s["date"] != str(date.today()):
        s["date"] = str(date.today())
        s["today"] = 0
    s["today"] += 1
    s["total"] += 1
    save_stats(s)

# ================= HELPERS =================
def safe_cleanup(path):
    shutil.rmtree(path, ignore_errors=True)

def cleanup_on_start():
    for f in os.listdir():
        if f.startswith("ig_"):
            safe_cleanup(f)

def is_instagram(url):
    return "instagram.com" in url

def extract_shortcode(url):
    m = re.search(r'instagram\.com/(?:p|reel|tv)/([A-Za-z0-9_-]+)', url)
    return m.group(1) if m else None

def extract_story_or_highlight(url):
    s = re.search(r'instagram\.com/stories/([^/]+)/(\d+)', url)
    if s:
        return "story", s.group(1), s.group(2)
    h = re.search(r'instagram\.com/stories/highlights/(\d+)', url)
    if h:
        return "highlight", None, h.group(1)
    return None, None, None

# ================= DOWNLOAD =================
def download_post(shortcode, temp_dir):
    post = instaloader.Post.from_shortcode(L.context, shortcode)
    L.download_post(post, target=temp_dir)
    return {"success": True}

def download_story(username, story_id, temp_dir):
    profile = instaloader.Profile.from_username(L.context, username)
    for story in L.get_stories(userids=[profile.userid]):
        for item in story.get_items():
            if str(item.mediaid).endswith(story_id):
                L.download_storyitem(item, target=temp_dir)
                return {"success": True}
    return {"success": False, "error": "Story topilmadi"}

def download_highlight(highlight_id, temp_dir):
    if not L.context.is_logged_in:
        return {"success": False, "error": "Login kerak"}
    for profile in instaloader.Profile.from_id(L.context, L.context.user_id).get_followees():
        for h in profile.get_highlights():
            if str(h.unique_id) == str(highlight_id):
                for item in h.get_items():
                    L.download_storyitem(item, target=temp_dir)
                return {"success": True}
    return {"success": False, "error": "Highlight topilmadi"}

# ================= MEDIA =================
def get_media_files(directory):
    media = []
    for f in Path(directory).rglob("*"):
        if f.suffix.lower() in [".mp4", ".jpg", ".jpeg", ".png", ".webp"]:
            media.append({
                "path": str(f),
                "type": "video" if f.suffix.lower() == ".mp4" else "photo"
            })
    return sorted(media, key=lambda x: x["path"])

# ================= SEND =================
async def send_media(update, media):
    if len(media) == 1:
        with open(media[0]["path"], "rb") as f:
            if media[0]["type"] == "video":
                await update.message.reply_video(video=f)
            else:
                await update.message.reply_photo(photo=f)
        return

    group = []
    files = []
    for m in media[:10]:
        f = open(m["path"], "rb")
        files.append(f)
        if m["type"] == "video":
            group.append(InputMediaVideo(f))
        else:
            group.append(InputMediaPhoto(f))
    await update.message.reply_media_group(group)
    for f in files:
        f.close()

# ================= HANDLER =================
async def handle_instagram(update, url):
    temp_dir = f"ig_{uuid.uuid4().hex}"
    os.makedirs(temp_dir)

    status = await update.message.reply_text("⏳ Yuklanmoqda...")

    try:
        kind, user, cid = extract_story_or_highlight(url)

        if kind == "story":
            result = await asyncio.to_thread(download_story, user, cid, temp_dir)
        elif kind == "highlight":
            result = await asyncio.to_thread(download_highlight, cid, temp_dir)
        else:
            shortcode = extract_shortcode(url)
            if not shortcode:
                await status.edit_text("❌ Noto‘g‘ri link")
                return
            result = await asyncio.to_thread(download_post, shortcode, temp_dir)

        if not result["success"]:
            await status.edit_text(f"❌ {result['error']}")
            return

        media = get_media_files(temp_dir)
        if not media:
            await status.edit_text("❌ Media topilmadi")
            return

        await status.delete()
        await send_media(update, media)
        inc_stats()

    except Exception:
        await status.edit_text("❌ Instagram vaqtincha javob bermadi")
    finally:
        safe_cleanup(temp_dir)

# ================= MESSAGE =================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id in ACTIVE_USERS:
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
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", lambda u, c: u.message.reply_text("📥 Instagram downloader")))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling()

if __name__ == "__main__":
    main()
