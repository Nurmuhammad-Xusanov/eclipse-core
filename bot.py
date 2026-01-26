import os
import re
import asyncio
import aiohttp
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

# Telegram limits
MAX_VIDEO_SIZE = 50 * 1024 * 1024  # 50MB for video
MAX_PHOTO_SIZE = 10 * 1024 * 1024  # 10MB for photo

# Download timeout
DOWNLOAD_TIMEOUT = 120  # 2 minutes

# ================= INSTALOADER =================
L = instaloader.Instaloader(
    download_video_thumbnails=False,
    download_geotags=False,
    download_comments=False,
    save_metadata=False,
    compress_json=False,
    post_metadata_txt_pattern="",
    quiet=True,
    sleep=False,  # Tezroq ishlashi uchun
)


def setup_session():
    """Instagram sessionni sozlash"""
    if IG_USERNAME and SESSION_FILE.exists():
        try:
            L.load_session_from_file(IG_USERNAME, str(SESSION_FILE))
            if L.test_login() == IG_USERNAME:
                return True
        except Exception:
            SESSION_FILE.unlink(missing_ok=True)

    if IG_USERNAME and IG_PASSWORD:
        try:
            L.login(IG_USERNAME, IG_PASSWORD)
            L.save_session_to_file(str(SESSION_FILE))
            return True
        except Exception as e:
            print(f"Login error: {e}")

    if browser_cookie3:
        for fn in (browser_cookie3.chrome, browser_cookie3.firefox, browser_cookie3.edge):
            try:
                cj = fn(domain_name="instagram.com")
                L.context._session.cookies.update(cj)
                if L.test_login():
                    L.save_session_to_file(str(SESSION_FILE))
                    return True
            except Exception:
                continue
    
    return False


setup_session()


# ================= ASYNC DOWNLOAD =================
async def download_file_async(url: str, path: Path, session: aiohttp.ClientSession):
    """Async ravishda fayl yuklash"""
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=DOWNLOAD_TIMEOUT)) as response:
            if response.status == 200:
                with open(path, 'wb') as f:
                    async for chunk in response.content.iter_chunked(65536):
                        f.write(chunk)
                return True
    except Exception as e:
        print(f"Download error for {url}: {e}")
    return False


async def download_post_media(post, tmp: Path):
    """Post medialarini async yuklab olish"""
    async with aiohttp.ClientSession() as session:
        tasks = []
        
        if post.mediacount > 1:
            # Carousel
            for i, node in enumerate(post.get_sidecar_nodes(), 1):
                if node.is_video:
                    url = node.video_url
                    filename = f"{i:02d}.mp4"
                else:
                    url = node.display_url
                    filename = f"{i:02d}.jpg"
                
                tasks.append(download_file_async(url, tmp / filename, session))
        else:
            # Single post/reel
            if post.is_video:
                url = post.video_url
                filename = "video.mp4"
            else:
                url = post.url
                filename = "photo.jpg"
            
            tasks.append(download_file_async(url, tmp / filename, session))
        
        # Barcha fayllarni parallel yuklash
        await asyncio.gather(*tasks)


async def download_story_media(story, tmp: Path):
    """Story mediasini yuklab olish"""
    async with aiohttp.ClientSession() as session:
        if story.is_video:
            url = story.video_url
            filename = "story.mp4"
        else:
            url = story.url
            filename = "story.jpg"
        
        await download_file_async(url, tmp / filename, session)


# ================= PROCESS MEDIA =================
def process_media(tmp: Path):
    """Yuklab olingan medialarni qayta ishlash"""
    media = []
    
    for f in sorted(tmp.iterdir()):
        if not f.is_file():
            continue
        
        size = f.stat().st_size
        ext = f.suffix.lower()
        
        if ext in (".mp4", ".mov"):
            # Video fayl
            if size <= MAX_VIDEO_SIZE:
                media.append({
                    "path": str(f),
                    "type": "video",
                    "size": size
                })
            else:
                # Katta videolar uchun ogohlantirish
                media.append({
                    "path": str(f),
                    "type": "too_large",
                    "size": size
                })
        
        elif ext in (".jpg", ".jpeg", ".png", ".webp"):
            # Rasm fayl
            if size <= MAX_PHOTO_SIZE:
                media.append({
                    "path": str(f),
                    "type": "photo",
                    "size": size
                })
    
    return media


def clean_caption(text: str, max_length: int = 1024) -> str:
    """Captionni tozalash va qisqartirish"""
    if not text:
        return ""
    
    # Hashtag va mentionlarni olib tashlash
    text = re.sub(r'#\w+', '', text)
    text = re.sub(r'@\w+', '', text)
    
    # Ko'p bo'sh joylarni tozalash
    text = re.sub(r'\s+', ' ', text)
    text = text.strip()
    
    # Uzunlikni cheklash
    if len(text) > max_length:
        text = text[:max_length-3] + "..."
    
    return text


# ================= SEND MEDIA =================
async def send_media(update: Update, media: list, caption: str):
    """Medialarni Telegramga yuborish"""
    if not media:
        await update.message.reply_text("❌ Media topilmadi")
        return
    
    # Juda katta fayllar borligini tekshirish
    too_large = [m for m in media if m["type"] == "too_large"]
    if too_large:
        total_size = sum(m["size"] for m in too_large) / (1024 * 1024)
        await update.message.reply_text(
            f"⚠️ Video juda katta ({total_size:.1f} MB)\n"
            f"Telegram 50 MB gacha videolarni qabul qiladi.\n"
            f"Iltimos, Instagramdan to'g'ridan-to'g'ri yuklab oling."
        )
        return
    
    # Captionni tozalash
    clean_cap = clean_caption(caption)
    
    # Bitta fayl bo'lsa
    if len(media) == 1:
        item = media[0]
        try:
            with open(item["path"], "rb") as f:
                if item["type"] == "video":
                    await update.message.reply_video(
                        video=f,
                        caption=clean_cap,
                        supports_streaming=True,
                        read_timeout=60,
                        write_timeout=60
                    )
                else:
                    await update.message.reply_photo(
                        photo=f,
                        caption=clean_cap,
                        read_timeout=60,
                        write_timeout=60
                    )
        except Exception as e:
            await update.message.reply_text(f"❌ Yuborishda xato: {str(e)[:100]}")
        return
    
    # Ko'p fayl bo'lsa (media group)
    try:
        group = []
        files_to_close = []
        
        for i, item in enumerate(media[:10]):  # Max 10 ta
            f = open(item["path"], "rb")
            files_to_close.append(f)
            
            cap = clean_cap if i == 0 else ""
            
            if item["type"] == "video":
                group.append(InputMediaVideo(media=f, caption=cap))
            else:
                group.append(InputMediaPhoto(media=f, caption=cap))
        
        if group:
            await update.message.reply_media_group(
                media=group,
                read_timeout=60,
                write_timeout=60
            )
    
    except Exception as e:
        await update.message.reply_text(f"❌ Media group yuborishda xato: {str(e)[:100]}")
    
    finally:
        for f in files_to_close:
            try:
                f.close()
            except:
                pass


# ================= HANDLERS =================
DOWNLOAD_SEM = asyncio.Semaphore(2)  # 2 ta parallel yuklab olish


async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Instagram linkini qayta ishlash"""
    text = update.message.text.strip()
    
    # Link formatini tekshirish
    post_match = re.search(r"(?:/p/|/reel/|/tv/)([A-Za-z0-9_-]+)", text)
    story_match = re.search(r"/stories/([^/]+)/(\d+)", text)
    
    if not post_match and not story_match:
        await update.message.reply_text(
            "Instagram post, reel yoki story linkini yuboring\n\n"
            "Misol:\n"
            "• Post: instagram.com/p/ABC123\n"
            "• Reel: instagram.com/reel/ABC123\n"
            "• Story: instagram.com/stories/username/123"
        )
        return
    
    async with DOWNLOAD_SEM:
        status_msg = await update.message.reply_text("⏳ Yuklanmoqda...")
        
        tmpdir = TemporaryDirectory(prefix="ig_")
        tmp = Path(tmpdir.name)
        
        try:
            if post_match:
                # Post yoki Reel
                shortcode = post_match.group(1)
                
                # Postni olish
                post = await asyncio.to_thread(
                    instaloader.Post.from_shortcode,
                    L.context,
                    shortcode
                )
                
                # Medialarni yuklash
                await download_post_media(post, tmp)
                
                # Caption
                caption = post.caption or ""
            
            elif story_match:
                # Story
                username = story_match.group(1)
                
                # Story olish
                profile = await asyncio.to_thread(
                    instaloader.Profile.from_username,
                    L.context,
                    username
                )
                
                # Eng so'nggi storyni olish
                stories = L.get_stories([profile.userid])
                story = None
                
                for user_story in stories:
                    for item in user_story.get_items():
                        story = item
                        break
                    if story:
                        break
                
                if not story:
                    await status_msg.edit_text("❌ Story topilmadi yoki muddati tugagan")
                    return
                
                await download_story_media(story, tmp)
                caption = ""
            
            # Medialarni qayta ishlash
            media = process_media(tmp)
            
            await status_msg.delete()
            await send_media(update, media, caption)
        
        except instaloader.exceptions.LoginRequiredException:
            await status_msg.edit_text("❌ Instagram login talab qiladi. .env faylida LOGIN va PASSWORD qo'shing")
        
        except instaloader.exceptions.PrivateProfileNotFollowedException:
            await status_msg.edit_text("❌ Bu profil yopiq. Botdan foydalanish uchun follow qiling")
        
        except instaloader.exceptions.QueryReturnedNotFoundException:
            await status_msg.edit_text("❌ Post topilmadi yoki o'chirilgan")
        
        except asyncio.TimeoutError:
            await status_msg.edit_text("❌ Yuklab olish vaqti tugadi. Qayta urinib ko'ring")
        
        except Exception as e:
            error_msg = str(e)
            if "429" in error_msg or "rate limit" in error_msg.lower():
                await status_msg.edit_text("❌ Instagram chekladi. Bir oz kutib qayta urinib ko'ring")
            else:
                await status_msg.edit_text(f"❌ Xato: {error_msg[:150]}")
        
        finally:
            tmpdir.cleanup()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start komandasi"""
    await update.message.reply_text(
        "👋 Salom!\n\n"
        "Instagram post, reel yoki story linkini yuboring.\n\n"
        "📌 Qo'llab-quvvatlanadigan formatlar:\n"
        "• Post (bir yoki ko'p surat/video)\n"
        "• Reel\n"
        "• Story\n"
        "• IGTV\n\n"
        "💡 Maslahat: Link to'liq bo'lishi shart emas, qisqa link ham ishlaydi"
    )


def main():
    """Botni ishga tushirish"""
    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))
    
    print("🤖 Bot ishga tushdi...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()