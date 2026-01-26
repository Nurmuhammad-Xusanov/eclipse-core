import os
import re
import asyncio
import aiohttp
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

# Telegram limits
MAX_VIDEO_SIZE = 50 * 1024 * 1024  # 50MB
MAX_PHOTO_SIZE = 10 * 1024 * 1024  # 10MB

# Compress settings
TARGET_VIDEO_SIZE = 45 * 1024 * 1024  # 45MB target (5MB xavfsizlik uchun)
DOWNLOAD_TIMEOUT = 120

# ================= INSTALOADER =================
L = instaloader.Instaloader(
    download_video_thumbnails=False,
    download_geotags=False,
    download_comments=False,
    save_metadata=False,
    compress_json=False,
    post_metadata_txt_pattern="",
    quiet=True,
    sleep=False,
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


# ================= VIDEO COMPRESSION =================
def check_ffmpeg():
    """FFmpeg o'rnatilganligini tekshirish"""
    try:
        subprocess.run(['ffmpeg', '-version'], 
                      stdout=subprocess.PIPE, 
                      stderr=subprocess.PIPE, 
                      check=True)
        return True
    except:
        return False


async def compress_video(input_path: Path, output_path: Path, target_size: int):
    """
    Videoni compress qilish (low-end laptop uchun optimallashtirilgan)
    
    Strategy:
    - 720p maksimum resolution
    - CRF (Constant Rate Factor) usuli - tezroq va sifatliroq
    - Hardware acceleration (agar mavjud bo'lsa)
    """
    try:
        # Video ma'lumotlarini olish
        probe_cmd = [
            'ffprobe', '-v', 'error',
            '-select_streams', 'v:0',
            '-show_entries', 'stream=duration,bit_rate',
            '-of', 'default=noprint_wrappers=1',
            str(input_path)
        ]
        
        probe_result = await asyncio.create_subprocess_exec(
            *probe_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        stdout, _ = await probe_result.communicate()
        
        # CRF usuli bilan compress (low-end uchun optimal)
        # CRF 28 = yaxshi sifat va yengil fayl
        compress_cmd = [
            'ffmpeg', '-i', str(input_path),
            '-c:v', 'libx264',  # H.264 codec
            '-crf', '28',  # Quality (18=yuqori, 28=yaxshi, 32=past)
            '-preset', 'veryfast',  # Low-end laptop uchun
            '-vf', 'scale=-2:720',  # 720p max
            '-c:a', 'aac',  # Audio codec
            '-b:a', '128k',  # Audio bitrate
            '-movflags', '+faststart',  # Web uchun optimizatsiya
            '-y',  # Overwrite
            str(output_path)
        ]
        
        process = await asyncio.create_subprocess_exec(
            *compress_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        await process.communicate()
        
        # Compress bo'lgan fayl hajmini tekshirish
        if output_path.exists():
            new_size = output_path.stat().st_size
            if new_size <= target_size:
                return True
            else:
                # Agar hali katta bo'lsa, CRF ni oshirish
                compress_cmd[6] = '32'  # CRF 32
                
                process = await asyncio.create_subprocess_exec(
                    *compress_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                
                await process.communicate()
                return output_path.exists()
        
        return False
        
    except Exception as e:
        print(f"Compress error: {e}")
        return False


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
async def process_media(tmp: Path, status_callback=None):
    """Yuklab olingan medialarni qayta ishlash va compress qilish"""
    media = []
    has_ffmpeg = check_ffmpeg()
    
    for f in sorted(tmp.iterdir()):
        if not f.is_file():
            continue
        
        size = f.stat().st_size
        ext = f.suffix.lower()
        
        if ext in (".mp4", ".mov"):
            # Video fayl
            if size > MAX_VIDEO_SIZE:
                if has_ffmpeg:
                    # Compress qilish
                    if status_callback:
                        await status_callback("🔄 Video compress qilinmoqda...")
                    
                    compressed_path = tmp / f"compressed_{f.name}"
                    success = await compress_video(f, compressed_path, TARGET_VIDEO_SIZE)
                    
                    if success and compressed_path.exists():
                        compressed_size = compressed_path.stat().st_size
                        if compressed_size <= MAX_VIDEO_SIZE:
                            media.append({
                                "path": str(compressed_path),
                                "type": "video",
                                "size": compressed_size,
                                "compressed": True
                            })
                            continue
                
                # Compress muvaffaqiyatsiz yoki FFmpeg yo'q
                media.append({
                    "path": str(f),
                    "type": "too_large",
                    "size": size
                })
            else:
                # Kichik video - compress kerak emas
                media.append({
                    "path": str(f),
                    "type": "video",
                    "size": size,
                    "compressed": False
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


def clean_caption(text: str, max_length: int = 200) -> str:
    """
    Captionni tozalash va qisqartirish
    - Hashtag va mentionlarni olib tashlash
    - Emoji saqlab qolish
    - Ko'p bo'sh joylarni tozalash
    """
    if not text:
        return "📥 Instagram"
    
    # Hashtag va mentionlarni olib tashlash
    text = re.sub(r'#\w+', '', text)
    text = re.sub(r'@\w+', '', text)
    
    # Ko'p bo'sh joylarni tozalash
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'\n\s*\n', '\n', text)
    text = text.strip()
    
    # Bo'sh bo'lsa default caption
    if not text or len(text) < 3:
        return "📥 Instagram"
    
    # Uzunlikni cheklash
    if len(text) > max_length:
        text = text[:max_length].rsplit(' ', 1)[0] + "..."
    
    return f"📥 {text}"


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
        
        if not check_ffmpeg():
            await update.message.reply_text(
                f"⚠️ Video juda katta ({total_size:.1f} MB)\n\n"
                f"❗️ FFmpeg o'rnatilmagan, compress qilib bo'lmadi.\n\n"
                f"Server adminiga xabar bering:\n"
                f"`sudo apt install ffmpeg -y`"
            )
        else:
            await update.message.reply_text(
                f"⚠️ Video juda katta ({total_size:.1f} MB) va compress qilib bo'lmadi.\n"
                f"Instagram'dan to'g'ridan-to'g'ri yuklab oling."
            )
        return
    
    # Captionni tozalash
    clean_cap = clean_caption(caption)
    
    # Bitta fayl bo'lsa
    if len(media) == 1:
        item = media[0]
        
        # Compress info qo'shish
        if item.get("compressed"):
            original_size = item["size"] / (1024 * 1024)
            clean_cap += f"\n\n🔄 Compressed: {original_size:.1f} MB"
        
        try:
            with open(item["path"], "rb") as f:
                if item["type"] == "video":
                    await update.message.reply_video(
                        video=f,
                        caption=clean_cap,
                        supports_streaming=True,
                        read_timeout=90,
                        write_timeout=90
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
                read_timeout=90,
                write_timeout=90
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
DOWNLOAD_SEM = asyncio.Semaphore(2)


async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Instagram linkini qayta ishlash"""
    text = update.message.text.strip()
    
    # Link formatini tekshirish
    post_match = re.search(r"(?:/p/|/reel/|/tv/)([A-Za-z0-9_-]+)", text)
    story_match = re.search(r"/stories/([A-Za-z0-9._]+)/(\d+)", text)
    
    # Agar oddiy story link bo'lsa (username/story_id)
    if not story_match:
        story_match = re.search(r"instagram\.com/([A-Za-z0-9._]+).*?story", text, re.IGNORECASE)
    
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
        
        async def update_status(text):
            try:
                await status_msg.edit_text(text)
            except:
                pass
        
        tmpdir = TemporaryDirectory(prefix="ig_")
        tmp = Path(tmpdir.name)
        
        try:
            if post_match:
                # Post yoki Reel
                shortcode = post_match.group(1)
                
                await update_status("📥 Instagram'dan yuklanmoqda...")
                
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
                story_id = story_match.group(2)
                
                await update_status("📥 Story yuklanmoqda...")
                
                try:
                    # Profile olish
                    profile = await asyncio.to_thread(
                        instaloader.Profile.from_username,
                        L.context,
                        username
                    )
                    
                    # Barcha storylarni olish
                    stories = await asyncio.to_thread(
                        lambda: list(L.get_stories([profile.userid]))
                    )
                    
                    story_item = None
                    
                    # Story ID bo'yicha qidirish
                    for user_story in stories:
                        for item in user_story.get_items():
                            if str(item.mediaid) == story_id or story_id in str(item):
                                story_item = item
                                break
                        if story_item:
                            break
                    
                    # Agar ID topilmasa, eng yangi storyni olish
                    if not story_item and stories:
                        for user_story in stories:
                            items = list(user_story.get_items())
                            if items:
                                story_item = items[0]
                                break
                    
                    if not story_item:
                        await status_msg.edit_text(
                            "❌ Story topilmadi\n\n"
                            "Sabablari:\n"
                            "• Story muddati tugagan (24 soat)\n"
                            "• Profil yopiq va siz follow qilmagansiz\n"
                            "• Instagram login kerak"
                        )
                        return
                    
                    await download_story_media(story_item, tmp)
                    caption = ""
                    
                except Exception as e:
                    await status_msg.edit_text(
                        f"❌ Story yuklanmadi\n\n"
                        f"Story uchun Instagram login talab qilinishi mumkin.\n"
                        f".env faylida INSTAGRAM_USERNAME va INSTAGRAM_PASSWORD qo'shing"
                    )
                    return
            
            # Medialarni qayta ishlash va compress qilish
            media = await process_media(tmp, update_status)
            
            await update_status("📤 Telegram'ga yuborilmoqda...")
            await status_msg.delete()
            await send_media(update, media, caption)
        
        except instaloader.exceptions.LoginRequiredException:
            await status_msg.edit_text(
                "❌ Instagram login talab qiladi\n\n"
                ".env faylida LOGIN va PASSWORD qo'shing"
            )
        
        except instaloader.exceptions.PrivateProfileNotFollowedException:
            await status_msg.edit_text("❌ Bu profil yopiq")
        
        except instaloader.exceptions.QueryReturnedNotFoundException:
            await status_msg.edit_text("❌ Post topilmadi yoki o'chirilgan")
        
        except asyncio.TimeoutError:
            await status_msg.edit_text("❌ Yuklab olish vaqti tugadi. Qayta urinib ko'ring")
        
        except Exception as e:
            error_msg = str(e)
            if "429" in error_msg or "rate limit" in error_msg.lower():
                await status_msg.edit_text("❌ Instagram chekladi. 10 daqiqa kutib qayta urinib ko'ring")
            else:
                await status_msg.edit_text(f"❌ Xato: {error_msg[:150]}")
        
        finally:
            tmpdir.cleanup()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start komandasi"""
    ffmpeg_status = "✅ O'rnatilgan" if check_ffmpeg() else "❌ O'rnatilmagan"
    
    await update.message.reply_text(
        f"👋 Salom!\n\n"
        f"Instagram post, reel yoki story linkini yuboring.\n\n"
        f"📌 Imkoniyatlar:\n"
        f"• Post va carousel\n"
        f"• Reel (50MB+ avtomatik compress)\n"
        f"• Story\n"
        f"• Toza caption (hashtag/mention siz)\n\n"
        f"🔧 FFmpeg: {ffmpeg_status}\n\n"
        f"💡 50MB+ videolar avtomatik compress qilinadi"
    )


def main():
    """Botni ishga tushirish"""
    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))
    
    print("🤖 Bot ishga tushdi...")
    print(f"🔧 FFmpeg: {'✅ Mavjud' if check_ffmpeg() else '❌ O\'rnatilmagan'}")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()