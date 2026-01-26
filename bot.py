import os
import re
import asyncio
import requests
from pathlib import Path
from tempfile import TemporaryDirectory
import instaloader
from dotenv import load_dotenv

try:
    import browser_cookie3
except ImportError:
    browser_cookie3 = None

from telegram import Update, InputMediaPhoto, InputMediaVideo, InputMediaDocument
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

# Telegram bot uchun real chegaralar (2025 holati)
MAX_VIDEO_SIZE = 50 * 1024 * 1024       # send_video uchun taxminan
MAX_DOCUMENT_SIZE = 2000 * 1024 * 1024  # send_document uchun (premium bo'lmasa ham ~2GB)

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
        except Exception:
            SESSION_FILE.unlink(missing_ok=True)

    if IG_USERNAME and IG_PASSWORD:
        try:
            L.login(IG_USERNAME, IG_PASSWORD)
            L.save_session_to_file(str(SESSION_FILE))
            return
        except Exception:
            pass

    if browser_cookie3:
        for fn in (browser_cookie3.chrome, browser_cookie3.firefox):
            try:
                cj = fn(domain_name="instagram.com")
                L.context._session.cookies.update(cj)
                if L.test_login():
                    L.save_session_to_file(str(SESSION_FILE))
                    return
            except Exception:
                pass


setup_session()

# ================= MANUAL DOWNLOAD =================
def download_carousel_manually(post, tmp: Path):
    def save(url, name):
        try:
            r = requests.get(url, stream=True, timeout=25)
            if r.status_code == 200:
                p = tmp / name
                with open(p, "wb") as f:
                    for chunk in r.iter_content(chunk_size=32768):
                        if chunk:
                            f.write(chunk)
        except Exception:
            pass

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
            item = {
                "path": str(f),
                "type": "video",
                "size": size
            }

            if size <= MAX_VIDEO_SIZE:
                item["send_as"] = "video"
            else:
                item["send_as"] = "document"

            # Juda katta bo'lsa ham yuborishga urinamiz (document bilan)
            if size > MAX_DOCUMENT_SIZE:
                item["send_as"] = "too_big"

            media.append(item)

        elif suf in (".jpg", ".jpeg", ".png", ".webp"):
            media.append({
                "path": str(f),
                "type": "photo",
                "send_as": "photo"
            })

    return media


# ================= SEND =================
async def send_media(update: Update, media, caption: str):
    if not media:
        await update.message.reply_text("❌ Hech qanday media topilmadi")
        return

    # Juda katta fayl borligini tekshirish
    too_big = any(m.get("send_as") == "too_big" for m in media)
    if too_big:
        await update.message.reply_text(
            "⚠️ Video(lar) juda katta (2 GB dan ortiq).\n"
            "Instagramdan o'zingiz yuklab oling yoki boshqa havola yuboring."
        )
        return

    if len(media) == 1:
        item = media[0]
        try:
            with open(item["path"], "rb") as f:
                if item["send_as"] == "document":
                    await update.message.reply_document(
                        document=f,
                        caption=caption,
                        disable_notification=True
                    )
                elif item["send_as"] == "video":
                    await update.message.reply_video(
                        video=f,
                        caption=caption,
                        supports_streaming=True,
                        disable_notification=True
                    )
                else:
                    await update.message.reply_photo(
                        photo=f,
                        caption=caption,
                        disable_notification=True
                    )
        except Exception as e:
            await update.message.reply_text(f"Yuborishda xato: {str(e)}")
        return

    # Media group (10 tagacha)
    group = []
    open_files = []

    try:
        for i, item in enumerate(media[:10]):
            fd = open(item["path"], "rb")
            open_files.append(fd)
            cap = caption if i == 0 else ""

            if item["send_as"] == "document":
                group.append(InputMediaDocument(media=fd, caption=cap))
            elif item["send_as"] == "video":
                group.append(InputMediaVideo(media=fd, caption=cap))
            else:
                group.append(InputMediaPhoto(media=fd, caption=cap))

        if group:
            await update.message.reply_media_group(
                media=group,
                disable_notification=True
            )
    except Exception as e:
        await update.message.reply_text(f"Media group yuborishda xato: {str(e)}")
    finally:
        for f in open_files:
            try:
                f.close()
            except:
                pass


# ================= HANDLER =================
DOWNLOAD_SEM = asyncio.Semaphore(1)


async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    m = re.search(r"(?:/p/|/reel/|/tv/|/stories/)([A-Za-z0-9_-]+)", text)
    if not m:
        await update.message.reply_text("Instagram post/reel/story linkini yuboring")
        return

    shortcode = m.group(1)

    async with DOWNLOAD_SEM:
        status_msg = await update.message.reply_text("⏳ Yuklanmoqda... (20-40 soniya)")
        tmpdir = TemporaryDirectory(prefix="ig_")
        tmp = Path(tmpdir.name)

        try:
            # Post, reel yoki IGTV
            if "/stories/" not in text:
                post = await asyncio.to_thread(
                    instaloader.Post.from_shortcode,
                    L.context,
                    shortcode
                )
                download_carousel_manually(post, tmp)
                caption = post.caption or "Instagram"
            else:
                # Story uchun alohida logika kerak (hozircha oddiy post sifatida)
                await update.message.reply_text("Story yuklash hali to'liq qo'llab-quvvatlanmaydi")
                return

            media = process_media(tmp)
            await status_msg.delete()
            await send_media(update, media, caption)

        except instaloader.exceptions.LoginRequiredException:
            await status_msg.edit_text("Instagram login talab qilindi. Bot egasiga xabar bering.")
        except instaloader.exceptions.PrivateProfileNotFollowedException:
            await status_msg.edit_text("Bu profil yopiq. Bot uni ko'ra olmaydi.")
        except Exception as e:
            await status_msg.edit_text(f"Xato yuz berdi: {str(e)[:200]}")
        finally:
            tmpdir.cleanup()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Salom! Instagram post, reel yoki carousel linkini yuboring.\n"
        "Misol: https://www.instagram.com/reel/ABC123xyz/"
    )


def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()