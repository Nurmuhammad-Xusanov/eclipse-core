import os
import re
import yt_dlp
import instaloader
from instaloader import Post
from dotenv import load_dotenv

from telegram import Update, InputMediaPhoto, InputMediaVideo
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
    raise ValueError("BOT_TOKEN topilmadi (.env ni tekshir)")

# ================= INSTALOADER =================
L = instaloader.Instaloader(
    download_video_thumbnails=False,
    compress_json=False,
    download_comments=False,
    save_metadata=False,
    post_metadata_txt_pattern="",
    filename_pattern="{date_utc}_UTC"
)

if os.path.exists("session-file"):
    try:
        L.load_session_from_file("session-file")
    except:
        pass

# ================= HELPERS =================
def extract_shortcode(url: str):
    patterns = [
        r'instagram\.com/p/([^/\?]+)',
        r'instagram\.com/reel/([^/\?]+)',
        r'instagram\.com/tv/([^/\?]+)',
    ]
    for p in patterns:
        m = re.search(p, url)
        if m:
            return m.group(1)
    return None


def is_youtube_shorts(url: str) -> bool:
    return "youtube.com/shorts/" in url or "youtu.be/" in url


# ================= COMMANDS =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Yo 👋\n\n"
        "Instagram + YouTube Shorts downloader botman.\n\n"
        "👉 IG post / reel / carousel link\n"
        "👉 YouTube Shorts link\n\n"
        "Link tashla, qolganini menga qo‘y 😎"
    )


# ================= YOUTUBE SHORTS =================
async def download_youtube_shorts(update: Update, url: str):
    status = await update.message.reply_text("⏳ YouTube Shorts yuklanmoqda...")

    temp_dir = f"yt_{update.effective_user.id}"
    os.makedirs(temp_dir, exist_ok=True)

    ydl_opts = {
        "outtmpl": f"{temp_dir}/%(id)s.%(ext)s",
        "format": "mp4",
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "cookiesfrombrowser": ("firefox",),
    }
    
    

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        video_path = None
        for f in os.listdir(temp_dir):
            if f.endswith(".mp4"):
                video_path = os.path.join(temp_dir, f)
                break

        if not video_path:
            await status.edit_text("❌ Video topilmadi")
            return

        with open(video_path, "rb") as v:
            await update.message.reply_video(
                video=v,
                caption="🎬 YouTube Shorts"
            )

        await status.delete()

    except Exception as e:
        await status.edit_text(f"❌ YouTube error: {e}")

    finally:
        for f in os.listdir(temp_dir):
            os.remove(os.path.join(temp_dir, f))
        os.rmdir(temp_dir)


# ================= INSTAGRAM =================
async def download_instagram(update: Update, url: str):
    shortcode = extract_shortcode(url)
    if not shortcode:
        await update.message.reply_text("❌ IG link noto‘g‘ri")
        return

    status = await update.message.reply_text("⏳ Instagram yuklanmoqda...")
    temp_dir = f"ig_{update.effective_user.id}"
    os.makedirs(temp_dir, exist_ok=True)

    try:
        post = Post.from_shortcode(L.context, shortcode)
        caption = "📸 Instagram Media"
        if post.caption:
            caption += f"\n\n{post.caption[:200]}"

        L.download_post(post, target=temp_dir)
        files = sorted(
            f for f in os.listdir(temp_dir)
            if f.endswith((".jpg", ".png", ".mp4"))
        )

        if not files:
            await status.edit_text("❌ Media topilmadi")
            return

        # Carousel
        if post.typename == "GraphSidecar":
            media = []
            for i, f in enumerate(files[:10]):
                path = os.path.join(temp_dir, f)
                if f.endswith(".mp4"):
                    media.append(
                        InputMediaVideo(
                            media=open(path, "rb"),
                            caption=caption if i == 0 else None
                        )
                    )
                else:
                    media.append(
                        InputMediaPhoto(
                            media=open(path, "rb"),
                            caption=caption if i == 0 else None
                        )
                    )

            await update.message.reply_media_group(media)
            for m in media:
                m.media.close()

        # Video
        elif post.is_video:
            for f in files:
                if f.endswith(".mp4"):
                    with open(os.path.join(temp_dir, f), "rb") as v:
                        await update.message.reply_video(v, caption=caption)
                    break

        # Photo
        else:
            for f in files:
                if f.endswith((".jpg", ".png")):
                    with open(os.path.join(temp_dir, f), "rb") as p:
                        await update.message.reply_photo(p, caption=caption)
                    break

        await status.delete()

    except Exception as e:
        await status.edit_text(f"❌ Instagram error: {e}")

    finally:
        for f in os.listdir(temp_dir):
            os.remove(os.path.join(temp_dir, f))
        os.rmdir(temp_dir)


# ================= ROUTER =================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()

    if "instagram.com" in url:
        await download_instagram(update, url)
        return

    if is_youtube_shorts(url):
        await download_youtube_shorts(update, url)
        return

    await update.message.reply_text("❌ Bu IG ham YT Shorts ham emas")


# ================= MAIN =================
def main():
    print("🤖 Bot ishga tushdi")
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    app.run_polling()


if __name__ == "__main__":
    main()
