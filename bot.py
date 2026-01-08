import os
import re
import uuid
import shutil
import asyncio
import yt_dlp
import instaloader
from instaloader import Post
from dotenv import load_dotenv

from telegram import (
    Update,
    InputMediaPhoto,
    InputMediaVideo
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
)

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN yo‘q (.env ni tekshir)")

# ================= INSTALOADER =================
L = instaloader.Instaloader(
    download_video_thumbnails=False,
    download_comments=False,
    save_metadata=False,
    compress_json=False,
    post_metadata_txt_pattern="",
)

if os.path.exists("session-file"):
    try:
        L.load_session_from_file("session-file")
    except:
        pass

# ================= HELPERS =================
def extract_shortcode(url: str):
    for p in (
        r"instagram\.com/p/([^/?]+)",
        r"instagram\.com/reel/([^/?]+)",
        r"instagram\.com/tv/([^/?]+)",
    ):
        m = re.search(p, url)
        if m:
            return m.group(1)
    return None


def is_youtube_shorts(url: str) -> bool:
    return "youtube.com/shorts/" in url or "youtu.be/" in url


def safe_cleanup(path: str):
    if os.path.exists(path):
        shutil.rmtree(path, ignore_errors=True)


# ================= COMMAND =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Yo 👋\n\n"
        "IG + YouTube Shorts downloader.\n"
        "Link tashla. Qolganini men qilaman 😎"
    )


# ================= YOUTUBE =================
def yt_download(url: str, outdir: str):
    ydl_opts = {
        "outtmpl": f"{outdir}/%(id)s.%(ext)s",
        "format": "mp4",
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "cookiefile": "cookies.txt",
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])


async def download_youtube_shorts(update: Update, url: str):
    status = await update.message.reply_text("⏳ YouTube Shorts yuklanmoqda...")
    temp_dir = f"yt_{uuid.uuid4().hex}"
    os.makedirs(temp_dir, exist_ok=True)

    try:
        await asyncio.to_thread(yt_download, url, temp_dir)

        video = next(
            (os.path.join(temp_dir, f) for f in os.listdir(temp_dir) if f.endswith(".mp4")),
            None
        )

        if not video:
            await status.edit_text("❌ Video topilmadi")
            return

        if os.path.getsize(video) > 50 * 1024 * 1024:
            await status.edit_text("❌ Video juda katta (50MB limit)")
            return

        with open(video, "rb") as v:
            await update.message.reply_video(v, caption="🎬 YouTube Shorts")

        await status.delete()

    except Exception as e:
        await status.edit_text(f"❌ YT error: {e}")

    finally:
        safe_cleanup(temp_dir)


# ================= INSTAGRAM =================
def ig_download(shortcode: str, outdir: str):
    post = Post.from_shortcode(L.context, shortcode)
    L.download_post(post, target=outdir)
    return post


async def download_instagram(update: Update, url: str):
    shortcode = extract_shortcode(url)
    if not shortcode:
        await update.message.reply_text("❌ Noto‘g‘ri IG link")
        return

    status = await update.message.reply_text("⏳ Instagram yuklanmoqda...")
    temp_dir = f"ig_{uuid.uuid4().hex}"
    os.makedirs(temp_dir, exist_ok=True)

    try:
        post = await asyncio.to_thread(ig_download, shortcode, temp_dir)

        caption = "📸 Instagram"
        if post.caption:
            caption += f"\n\n{post.caption[:200]}"

        files = [
            os.path.join(temp_dir, f)
            for f in os.listdir(temp_dir)
            if f.endswith((".jpg", ".png", ".mp4"))
        ]

        if not files:
            await status.edit_text("❌ Media topilmadi")
            return

        if post.typename == "GraphSidecar":
            media = []
            for i, f in enumerate(files[:10]):
                if os.path.getsize(f) > 50 * 1024 * 1024:
                    continue
                media.append(
                    InputMediaVideo(open(f, "rb"), caption=caption if i == 0 else None)
                    if f.endswith(".mp4")
                    else InputMediaPhoto(open(f, "rb"), caption=caption if i == 0 else None)
                )
            await update.message.reply_media_group(media)
            for m in media:
                m.media.close()

        elif post.is_video:
            video = next(f for f in files if f.endswith(".mp4"))
            with open(video, "rb") as v:
                await update.message.reply_video(v, caption=caption)

        else:
            photo = next(f for f in files if f.endswith((".jpg", ".png")))
            with open(photo, "rb") as p:
                await update.message.reply_photo(p, caption=caption)

        await status.delete()

    except Exception as e:
        await status.edit_text(f"❌ IG error: {e}")

    finally:
        safe_cleanup(temp_dir)


# ================= ROUTER =================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()

    if "instagram.com" in url:
        await download_instagram(update, url)
    elif is_youtube_shorts(url):
        await download_youtube_shorts(update, url)
    else:
        await update.message.reply_text("❌ Bu IG ham YT Shorts ham emas")


# ================= MAIN =================
def main():
    print("🤖 Bot online")
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    app.run_polling()


if __name__ == "__main__":
    main()
