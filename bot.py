import os
import re
import json
import asyncio
import requests
import shutil
import subprocess
from datetime import date
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
from telegram.error import NetworkError, BadRequest

# ================= CONFIG =================
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN missing in .env")

IG_USERNAME = os.getenv("INSTAGRAM_USERNAME", "").strip()
IG_PASSWORD = os.getenv("INSTAGRAM_PASSWORD", "").strip()

SESSION_FILE = Path("insta_session")
STATS_FILE = Path("stats.json")

MAX_TELEGRAM_SIZE = 48 * 1024 * 1024
COMPRESSED_SUFFIX = "_compressed.mp4"

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
    max_connection_attempts=5,
    request_timeout=35,
)

L.context.raise_all_errors = True

def setup_session():
    if IG_USERNAME and SESSION_FILE.exists():
        try:
            L.load_session_from_file(IG_USERNAME, str(SESSION_FILE))
            if L.test_login() == IG_USERNAME:
                print(f"✅ Loaded session for {IG_USERNAME}")
                return True
            SESSION_FILE.unlink(missing_ok=True)
        except:
            SESSION_FILE.unlink(missing_ok=True)

    if IG_USERNAME and IG_PASSWORD:
        try:
            L.login(IG_USERNAME, IG_PASSWORD)
            L.save_session_to_file(str(SESSION_FILE))
            print("✅ Password login OK")
            return True
        except:
            pass

    if browser_cookie3:
        for fn in [browser_cookie3.firefox, browser_cookie3.chrome]:
            try:
                cj = fn(domain_name="instagram.com")
                L.context._session.cookies.update(cj)
                if IG_USERNAME:
                    L.context.username = IG_USERNAME
                if L.test_login():
                    L.save_session_to_file(str(SESSION_FILE))
                    print("✅ Browser cookies OK")
                    return True
            except:
                pass

    print("⚠️ Anonymous mode")
    return False

setup_session()


# ================= COMPRESS VIDEO =================
def compress_video(input_path: Path, output_path: Path) -> bool:
    """Video kompressiya qiladi va muvaffaqiyatli bo'lsa True qaytaradi"""
    
    # CRF 24 → yaxshi sifat
    cmd = [
        "ffmpeg", "-y", "-i", str(input_path),
        "-vcodec", "libx264", "-crf", "24", "-preset", "medium",
        "-acodec", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        str(output_path)
    ]
    try:
        subprocess.run(cmd, check=True, timeout=400, capture_output=True)
        if output_path.stat().st_size <= MAX_TELEGRAM_SIZE:
            print(f"✅ Compressed: {output_path.name} ({output_path.stat().st_size//(1024*1024)} MB)")
            return True
    except Exception as e:
        print(f"CRF 24 failed: {e}")
        output_path.unlink(missing_ok=True)

    # Agar hali katta bo'lsa → 480p + past bitrate
    cmd = [
        "ffmpeg", "-y", "-i", str(input_path),
        "-vf", "scale=854:480",
        "-vcodec", "libx264", "-crf", "28", "-b:v", "800k",
        "-acodec", "aac", "-b:a", "96k",
        "-movflags", "+faststart",
        str(output_path)
    ]
    try:
        subprocess.run(cmd, check=True, timeout=400, capture_output=True)
        if output_path.stat().st_size <= MAX_TELEGRAM_SIZE:
            print(f"✅ Compressed 480p: {output_path.name} ({output_path.stat().st_size//(1024*1024)} MB)")
            return True
    except Exception as e:
        print(f"480p compression failed: {e}")

    output_path.unlink(missing_ok=True)
    return False


# ================= MANUAL CAROUSEL YUKLASH =================
def download_carousel_manually(post, temp_path: Path) -> list[dict]:
    """Carousel postlarni manual ravishda yuklab oladi"""
    saved = []
    
    def save_url(url: str, fname: str, media_type: str):
        try:
            r = requests.get(url, timeout=30, stream=True)
            if r.status_code == 200:
                path = temp_path / fname
                with path.open("wb") as f:
                    for chunk in r.iter_content(32768):
                        f.write(chunk)
                size = path.stat().st_size
                size_mb = size / (1024 * 1024)
                print(f"📥 Saved: {fname} ({size_mb:.1f} MB)")
                saved.append({
                    "path": str(path),
                    "type": media_type,
                    "size": size,
                    "original": True
                })
                return True
        except Exception as e:
            print(f"❌ Save failed {fname}: {e}")
        return False

    try:
        # Carousel mavjudligini tekshirish
        if post.mediacount > 1:
            print(f"🎠 Carousel detected: {post.mediacount} items")
            
            # get_sidecar_nodes() ishlatish
            try:
                nodes = list(post.get_sidecar_nodes())
                print(f"Found {len(nodes)} sidecar nodes")
                
                for idx, node in enumerate(nodes, 1):
                    if node.is_video and node.video_url:
                        save_url(node.video_url, f"carousel_{idx:02d}.mp4", "video")
                    elif node.display_url:
                        save_url(node.display_url, f"carousel_{idx:02d}.jpg", "photo")
                        
            except Exception as e:
                print(f"⚠️ get_sidecar_nodes() failed: {e}")
                
                # Alternativ: edge_sidecar_to_children ishlatish
                try:
                    if hasattr(post, '_node') and 'edge_sidecar_to_children' in post._node:
                        edges = post._node['edge_sidecar_to_children']['edges']
                        print(f"Found {len(edges)} edges in _node")
                        
                        for idx, edge in enumerate(edges, 1):
                            node = edge['node']
                            if node.get('is_video') and node.get('video_url'):
                                save_url(node['video_url'], f"carousel_{idx:02d}.mp4", "video")
                            elif node.get('display_url'):
                                save_url(node['display_url'], f"carousel_{idx:02d}.jpg", "photo")
                except Exception as e2:
                    print(f"⚠️ Manual edge parsing failed: {e2}")
        
        # Bitta media bo'lsa
        else:
            if post.is_video and post.video_url:
                save_url(post.video_url, "main_video.mp4", "video")
            elif post.url:
                save_url(post.url, "main_photo.jpg", "photo")
                
    except Exception as e:
        print(f"❌ Manual download error: {e}")
    
    return saved


# ================= MEDIA TO'PLASH + COMPRESS =================
def process_media(temp_path: Path) -> list[dict]:
    """Barcha medialarni to'playdi va video kompressiya qiladi"""
    media = []
    processed_files = set()
    
    for file in sorted(temp_path.rglob("*")):
        if not file.is_file() or file.name in processed_files:
            continue
            
        # Compressed fayllarni o'tkazib yuborish
        if COMPRESSED_SUFFIX in file.name:
            continue
            
        suf = file.suffix.lower()
        size = file.stat().st_size
        
        if suf in {".mp4", ".mov"}:
            # Video kompressiya kerakmi?
            if size > MAX_TELEGRAM_SIZE:
                print(f"🔄 Compressing large video: {file.name} ({size//(1024*1024)} MB)")
                compressed_path = file.parent / f"{file.stem}_compressed{file.suffix}"
                
                if compress_video(file, compressed_path):
                    # Original faylni o'chirish
                    file.unlink()
                    processed_files.add(file.name)
                    
                    media.append({
                        "path": str(compressed_path),
                        "type": "video",
                        "size": compressed_path.stat().st_size
                    })
                    processed_files.add(compressed_path.name)
                else:
                    print(f"⚠️ Skipping {file.name} - compression failed")
            else:
                media.append({
                    "path": str(file),
                    "type": "video",
                    "size": size
                })
                processed_files.add(file.name)
                
        elif suf in {".jpg", ".jpeg", ".png", ".webp"}:
            media.append({
                "path": str(file),
                "type": "photo",
                "size": size
            })
            processed_files.add(file.name)
    
    return sorted(media, key=lambda x: x["path"])


# ================= CAPTION TOZALASH =================
def clean_caption(text: str) -> str:
    """Caption dan keraksiz belgilarni olib tashlaydi"""
    if not text:
        return ""
    # Hashtag va mention qoldirish, boshqa formatlash
    text = text.strip()
    # 1024 belgidan uzun bo'lsa kesish (Telegram limit)
    if len(text) > 1024:
        text = text[:1020] + "..."
    return text


# ================= SEND =================
async def send_media(update: Update, media: list[dict], caption: str):
    if not media:
        await update.message.reply_text("❌ Hech qanday media yuklanmadi")
        return

    # Bitta media
    if len(media) == 1:
        item = media[0]
        if item["size"] > MAX_TELEGRAM_SIZE:
            await update.message.reply_text(f"❌ Fayl juda katta ({item['size']//(1024*1024)} MB)")
            return
        with open(item["path"], "rb") as f:
            try:
                if item["type"] == "video":
                    await update.message.reply_video(f, caption=caption, supports_streaming=True)
                else:
                    await update.message.reply_photo(f, caption=caption)
            except Exception as e:
                await update.message.reply_text(f"❌ Yuborishda xato: {str(e)[:100]}")
        return

    # Media group (max 10 ta)
    group = []
    files = []
    try:
        extra = f"\n\n⚠️ Jami {len(media)} ta media (10 ta ko'rsatildi)" if len(media) > 10 else ""
        
        for i, item in enumerate(media[:10]):
            fd = open(item["path"], "rb")
            files.append(fd)
            
            cap = (caption + extra) if i == 0 else None
            
            if item["type"] == "video":
                group.append(InputMediaVideo(fd, caption=cap, supports_streaming=True))
            else:
                group.append(InputMediaPhoto(fd, caption=cap))
        
        if group:
            await update.message.reply_media_group(group)
            print(f"✅ Sent {len(group)} media items")
            
    except Exception as e:
        await update.message.reply_text(f"❌ Media group yuborishda xato: {str(e)[:100]}")
    finally:
        # Barcha ochiq fayllarni yopish
        for f in files:
            try:
                f.close()
            except:
                pass


# ================= STORY YUKLASH =================
async def download_story(update: Update, username: str, story_id: str = None):
    """Instagram Story yuklab oladi"""
    status = await update.message.reply_text(f"⏳ Story yuklanmoqda: @{username}...")
    
    temp_dir_obj = TemporaryDirectory(prefix="ig_story_")
    tmp = Path(temp_dir_obj.name)
    
    print(f"\n{'='*50}")
    print(f"📖 Story: @{username} (ID: {story_id})")
    print(f"📁 Temp dir: {tmp}")
    
    try:
        # Profile olish
        profile = await asyncio.to_thread(instaloader.Profile.from_username, L.context, username)
        
        stories = []
        story_count = 0
        
        # Barcha storylarni olish (blocking funksiya)
        def fetch_stories():
            nonlocal story_count
            try:
                for story in L.get_stories(userids=[profile.userid]):
                    for item in story.get_items():
                        story_count += 1
                        
                        # Agar ma'lum ID berilgan bo'lsa, faqat uni olish
                        if story_id and str(item.mediaid) != story_id:
                            continue
                        
                        url = item.video_url if item.is_video else item.url
                        ext = ".mp4" if item.is_video else ".jpg"
                        fname = f"story_{item.mediaid}{ext}"
                        
                        # Yuklash
                        try:
                            r = requests.get(url, timeout=30, stream=True)
                            if r.status_code == 200:
                                path = tmp / fname
                                with path.open("wb") as f:
                                    for chunk in r.iter_content(32768):
                                        f.write(chunk)
                                
                                stories.append({
                                    "path": str(path),
                                    "type": "video" if item.is_video else "photo",
                                    "size": path.stat().st_size
                                })
                                print(f"✅ Story saved: {fname}")
                        except Exception as e:
                            print(f"❌ Story download failed: {e}")
            except Exception as e:
                print(f"❌ get_stories error: {e}")
        
        await asyncio.to_thread(fetch_stories)
        
        if not stories:
            await status.edit_text(f"❌ @{username} da aktiv story topilmadi")
            return
        
        # Media qayta ishlash
        media = process_media(tmp)
        
        await status.delete()
        
        if media:
            cap = f"📖 Story: @{username}"
            await send_media(update, media, cap)
        else:
            await update.message.reply_text("❌ Story yuklanmadi")
            
    except instaloader.exceptions.ProfileNotExistsException:
        await status.edit_text(f"❌ @{username} topilmadi")
    except instaloader.exceptions.LoginRequiredException:
        await status.edit_text("❌ Story ko'rish uchun login kerak (INSTAGRAM_USERNAME va INSTAGRAM_PASSWORD .env ga qo'shing)")
    except Exception as e:
        err = str(e)[:140]
        print(f"❌ Story error: {err}")
        try:
            await status.edit_text(f"❌ Xato: {err}")
        except:
            await update.message.reply_text(f"❌ Xato: {err}")
    finally:
        try:
            temp_dir_obj.cleanup()
        except:
            pass
        if tmp.exists():
            shutil.rmtree(str(tmp), ignore_errors=True)
        print(f"🧹 Cleaned: {tmp}")
        print(f"{'='*50}\n")


# ================= ASOSIY HANDLER =================
DOWNLOAD_SEM = asyncio.Semaphore(1)

async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    
    # Story link tekshirish
    # Format: instagram.com/stories/username/story_id
    story_match = re.search(r"/stories/([A-Za-z0-9_.]+)/(\d+)", text)
    if story_match:
        username = story_match.group(1)
        story_id = story_match.group(2)
        async with DOWNLOAD_SEM:
            await download_story(update, username, story_id)
        return
    
    # Oddiy username (barcha storylarni olish)
    # Format: @username yoki instagram.com/username
    username_match = re.search(r"(?:@|instagram\.com/)([A-Za-z0-9_.]+)/?$", text)
    if username_match and "/p/" not in text and "/reel/" not in text and "/tv/" not in text and "/stories/" not in text:
        username = username_match.group(1)
        async with DOWNLOAD_SEM:
            await download_story(update, username)
        return
    
    # Post/Reel link
    shortcode = re.search(r"(?:/p/|/reel/|/tv/)([A-Za-z0-9_-]{10,})", text)
    shortcode = shortcode.group(1) if shortcode else None

    if not shortcode:
        await update.message.reply_text(
            "📎 Instagram link yuboring:\n\n"
            "✅ Post/Reel: instagram.com/p/ABC123\n"
            "✅ Story: instagram.com/stories/username/123456\n"
            "✅ Barcha storylar: @username yoki instagram.com/username"
        )
        return

    async with DOWNLOAD_SEM:
        status = await update.message.reply_text(f"⏳ Yuklanmoqda... ({shortcode})")

        temp_dir_obj = TemporaryDirectory(prefix="ig_")
        tmp = Path(temp_dir_obj.name)
        
        print(f"\n{'='*50}")
        print(f"📥 Processing: {shortcode}")
        print(f"📁 Temp dir: {tmp}")

        try:
            # Post ma'lumotlarini olish
            post = await asyncio.to_thread(instaloader.Post.from_shortcode, L.context, shortcode)
            print(f"📊 Post info: mediacount={post.mediacount}, is_video={post.is_video}")

            # Manual yuklash (carousel uchun eng ishonchli)
            media_list = download_carousel_manually(post, tmp)
            
            # Media qayta ishlash (compress va tozalash)
            media = process_media(tmp)
            
            print(f"✅ Processed {len(media)} media files")

            await status.delete()

            if not media:
                await update.message.reply_text("❌ Media topilmadi yoki yuklanmadi")
                return

            cap = clean_caption(post.caption or "")
            await send_media(update, media, cap)

        except Exception as e:
            err = str(e)[:140]
            print(f"❌ Error {shortcode}: {err}")
            try:
                await status.edit_text(f"❌ Xato: {err}")
            except:
                await update.message.reply_text(f"❌ Xato: {err}")
                
        finally:
            # MAJBURIY TOZALASH
            try:
                temp_dir_obj.cleanup()
            except Exception as e:
                print(f"⚠️ Cleanup warning: {e}")
                
            # Qo'shimcha tozalash (agar cleanup() ishlamasa)
            if tmp.exists():
                try:
                    shutil.rmtree(str(tmp), ignore_errors=True)
                except:
                    pass
                    
            print(f"🧹 Cleaned: {tmp}")
            print(f"{'='*50}\n")


# ================= COMMANDS =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Salom! Instagram link yuboring → yuklab beraman\n\n"
        "✅ Post va Reel\n"
        "✅ Carousel (ko'p rasmli)\n"
        "✅ Story (yakka yoki barcha)\n"
        "✅ Video va rasmlar\n"
        "✅ Avtomatik kompressiya\n\n"
        "📝 Qo'llanma:\n"
        "• Post/Reel: link yuboring\n"
        "• Story: instagram.com/stories/username/123\n"
        "• Barcha story: @username"
    )


def main():
    print("🤖 Bot ishga tushdi")
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()