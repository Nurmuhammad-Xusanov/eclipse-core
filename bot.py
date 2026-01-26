import os
import re
import asyncio
import requests
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

import instaloader
from dotenv import load_dotenv

try:
    import browser_cookie3
except ImportError:
    browser_cookie3 = None

from telegram import Update, InputMediaPhoto, InputMediaVideo
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ================= CONFIG =================
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN missing in .env")

IG_USERNAME = os.getenv("INSTAGRAM_USERNAME", "").strip()
IG_PASSWORD = os.getenv("INSTAGRAM_PASSWORD", "").strip()

SESSION_FILE = Path("insta_session")

MAX_TELEGRAM_SIZE = 48 * 1024 * 1024

# ================= INSTALOADER =================
L = instaloader.Instaloader(
    download_video_thumbnails=False,
    download_geotags=False,
    download_comments=False,
    save_metadata=False,
    compress_json=False,
    post_metadata_txt_pattern="",
    quiet=False,
    sleep=True,
)

L.context.raise_all_errors = True


def setup_session():
    if IG_USERNAME and SESSION_FILE.exists():
        try:
            L.load_session_from_file(IG_USERNAME, str(SESSION_FILE))
            if L.test_login() == IG_USERNAME:
                return
        except:
            SESSION_FILE.unlink(missing_ok=True)

    if IG_USERNAME and IG_PASSWORD:
        try:
            L.login(IG_USERNAME, IG_PASSWORD)
            L.save_session_to_file(str(SESSION_FILE))
            return
        except:
            pass

    if browser_cookie3:
        for fn in (browser_cookie3.chrome, browser_cookie3.firefox):
            try:
                cj = fn(domain_name="instagram.com")
                L.context._session.cookies.update(cj)
                if L.test_login():
                    L.save_session_to_file(str(SESSION_FILE))
                    return
            except:
                pass

setup_session()

# ================= FAST REMUX (0 CPU) =================
def fast_remux(input_path: Path, output_path: Path) -> bool:
    cmd = [
        "ffmpeg", "-y",
        "-i", str(input_path),
        "-c", "copy",
        "-movflags", "+faststart",
        str(output_path)
    ]
    try:
        subprocess.run(cmd, check=True, timeout=120, capture_output=True)
        return output_path.stat().st_size <= MAX_TELEGRAM_SIZE
    except:
        output_path.unlink(missing_ok=True)
        return False


# ================= MANUAL DOWNLOAD =================
def download_carousel_manually(post, tmp: Path):
    def save(url, name):
        r = requests.get(url, stream=True, timeout=30)
        if r.status_code == 200:
            p = tmp / name
            with open(p, "wb") as f:
                for c in r.iter_content(32768):
                    f.write(c)

    if post.mediacount > 1:
        for i, node in enumerate(post.get_sidecar_nodes(), 1):
            if node.is_video:
                save(node.video_url, f"{i:02d}.mp4")
            else:
                save(node.display_url, f"{i:02d}.jpg")
    else:
        if post.is_video:
            save(post.video_url, "main.mp4")
        else:
            save(post.url, "main.jpg")


# ================= PROCESS MEDIA =================
def process_media(tmp: Path):
    media = []

    for f in sorted(tmp.iterdir()):
        if not f.is_file():
            continue

        size = f.stat().st_size
        suf = f.suffix.lower()

        if suf in (".mp4", ".mov"):
            if size <= MAX_TELEGRAM_SIZE:
                media.append({
                    "path": str(f),
                    "type": "video",
                    "send_as": "video"
                })
            else:
                remuxed = f.with_name(f.stem + "_fast.mp4")
                if fast_remux(f, remuxed):
                    f.unlink(missing_ok=True)
                    media.append({
                        "path": str(remuxed),
                        "type": "video",
                        "send_as": "video"
                    })
                else:
                    media.append({
                        "path": str(f),
                        "type": "video",
                        "send_as": "document"
                    })

        elif suf in (".jpg", ".jpeg", ".png", ".webp"):
            media.append({
                "path": str(f),
                "type": "photo",
                "send_as": "photo"
            })

    return media


# ================= CLEAN CAPTION =================
def clean_caption(_):
    return "📥 Instagram"


# ================= SEND =================
async def send_media(update: Update, media, caption):
    if not media:
        await update.message.reply_text("❌ Media yo‘q")
        return

    if len(media) == 1:
        item = media[0]
        with open(item["path"], "rb") as f:
            if item["send_as"] == "document":
                await update.message.reply_document(f, caption=caption)
            elif item["type"] == "video":
                await update.message.reply_video(f, caption=caption, supports_streaming=True)
            else:
                await update.message.reply_photo(f, caption=caption)
        return

    group = []
    files = []
    for i, item in enumerate(media[:10]):
        fd = open(item["path"], "rb")
        files.append(fd)
        cap = caption if i == 0 else None
        if item["type"] == "video":
            group.append(InputMediaVideo(fd, caption=cap))
        else:
            group.append(InputMediaPhoto(fd, caption=cap))

    await update.message.reply_media_group(group)

    for f in files:
        f.close()


# ================= HANDLER =================
DOWNLOAD_SEM = asyncio.Semaphore(1)

async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    m = re.search(r"(?:/p/|/reel/|/tv/)([A-Za-z0-9_-]+)", text)
    if not m:
        await update.message.reply_text("Instagram link yubor")
        return

    shortcode = m.group(1)

    async with DOWNLOAD_SEM:
        status = await update.message.reply_text("⏳ Yuklanmoqda...")
        tmpdir = TemporaryDirectory(prefix="ig_")
        tmp = Path(tmpdir.name)

        try:
            post = await asyncio.to_thread(instaloader.Post.from_shortcode, L.context, shortcode)
            download_carousel_manually(post, tmp)
            media = process_media(tmp)
            await status.delete()
            await send_media(update, media, clean_caption(post.caption))
        finally:
            tmpdir.cleanup()


# ================= START =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Instagram link yubor")

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))
    app.run_polling()

if __name__ == "__main__":
    main()