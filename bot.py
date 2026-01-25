import os
import re
import uuid
import time
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
    raise RuntimeError("BOT_TOKEN yo'q")

# Instagram login (opsiyonal, private contentlar uchun)
INSTAGRAM_USERNAME = os.getenv("INSTAGRAM_USERNAME", "")
INSTAGRAM_PASSWORD = os.getenv("INSTAGRAM_PASSWORD", "")

# ================= PATHS =================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SESSION_FILE = os.path.join(BASE_DIR, "insta_session")

# ================= GLOBALS =================
ACTIVE_USERS = set()
STATS_FILE = "stats.json"

# Instaloader setup
L = instaloader.Instaloader(
    download_video_thumbnails=False,
    download_geotags=False,
    download_comments=False,
    save_metadata=False,
    compress_json=False,
    post_metadata_txt_pattern="",
    quiet=True,
    user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1"
)

# Login (agar credentials berilgan bo'lsa)
def instaloader_login():
    """Instaloader'ga login qilish"""
    try:
        if os.path.exists(SESSION_FILE):
            L.load_session_from_file(INSTAGRAM_USERNAME, SESSION_FILE)
            print("✅ Session yuklandi")
            return True
        
        if INSTAGRAM_USERNAME and INSTAGRAM_PASSWORD:
            L.login(INSTAGRAM_USERNAME, INSTAGRAM_PASSWORD)
            L.save_session_to_file(SESSION_FILE)
            print("✅ Login muvaffaqiyatli")
            return True
        
        print("⚠️ Login ma'lumotlari yo'q, public content'ga cheklanadi")
        return False
    except Exception as e:
        print(f"⚠️ Login xato: {e}")
        return False

# Login qilish
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
    stats = load_stats()
    if stats["date"] != str(date.today()):
        stats["date"] = str(date.today())
        stats["today"] = 0
    stats["today"] += 1
    stats["total"] += 1
    save_stats(stats)

# ================= HELPERS =================
def safe_cleanup(path):
    if os.path.exists(path):
        try:
            shutil.rmtree(path, ignore_errors=True)
        except:
            pass

def cleanup_on_start():
    for f in os.listdir():
        if f.startswith("ig_"):
            safe_cleanup(f)

def is_instagram(url):
    return "instagram.com" in url

def clean_caption(text):
    if not text:
        return "📥 Instagram"
    text = re.sub(r"#\w+", "", text).strip()
    return text[:1000] if text else "📥 Instagram"

def extract_shortcode(url):
    """URL dan shortcode olish"""
    patterns = [
        r'instagram\.com/p/([A-Za-z0-9_-]+)',
        r'instagram\.com/reel/([A-Za-z0-9_-]+)',
        r'instagram\.com/tv/([A-Za-z0-9_-]+)',
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None

def extract_story_info(url):
    """Story URL dan username va story_id olish"""
    match = re.search(r'instagram\.com/stories/([^/]+)/(\d+)', url)
    if match:
        return match.group(1), match.group(2)
    return None, None

# ================= COMMANDS =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 Eclipse Core online\n\n"
        "📥 Instagram downloader\n"
        "✅ Post / Reel / Carousel / Story\n\n"
        "🔗 Link yuboring!"
    )

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    s = load_stats()
    await update.message.reply_text(
        f"📊 Statistika\n\n🔥 Bugun: {s['today']}\n📦 Jami: {s['total']}"
    )

# ================= DOWNLOAD FUNCTIONS =================
def download_post(shortcode, temp_dir):
    """Post/Reel/Carousel yuklash"""
    try:
        post = instaloader.Post.from_shortcode(L.context, shortcode)
        
        # Target papka
        target = temp_dir
        
        # Postni yuklash
        L.download_post(post, target=target)
        
        return {
            "caption": post.caption if post.caption else "",
            "success": True
        }
    except Exception as e:
        print(f"❌ Download post error: {e}")
        return {"success": False, "error": str(e)}

def download_story(username, story_id, temp_dir):
    """Story yuklash"""
    try:
        # Profile olish
        profile = instaloader.Profile.from_username(L.context, username)
        
        # Storylarni yuklash
        for story in L.get_stories(userids=[profile.userid]):
            for item in story.get_items():
                if str(item.mediaid) == story_id:
                    L.download_storyitem(item, target=temp_dir)
                    return {"success": True, "caption": "📥 Story"}
        
        return {"success": False, "error": "Story topilmadi"}
    except Exception as e:
        print(f"❌ Download story error: {e}")
        return {"success": False, "error": str(e)}

# ================= GET MEDIA FILES =================
def get_media_files(directory):
    """Katalogdagi barcha media fayllarni olish"""
    media = []
    
    if not os.path.exists(directory):
        return media
    
    # Barcha fayllarni ko'rib chiqish
    for file_path in Path(directory).rglob("*"):
        if not file_path.is_file():
            continue
        
        ext = file_path.suffix.lower()
        
        # Faqat media fayllar
        if ext in ['.mp4', '.mov', '.avi']:
            media.append({"path": str(file_path), "type": "video"})
        elif ext in ['.jpg', '.jpeg', '.png', '.webp']:
            media.append({"path": str(file_path), "type": "photo"})
    
    # Nom bo'yicha sort
    media.sort(key=lambda x: x["path"])
    return media

# ================= SEND FUNCTIONS =================
async def send_media_group(update, media_files, caption):
    """Media gruppani yuborish (carousel)"""
    if len(media_files) > 10:
        media_files = media_files[:10]
    
    media_group = []
    opened_files = []
    
    try:
        for i, item in enumerate(media_files):
            # Hajm tekshirish (50MB Telegram limit)
            file_size = os.path.getsize(item["path"])
            if file_size > 50 * 1024 * 1024:
                print(f"⚠️ Fayl juda katta: {item['path']} ({file_size} bytes)")
                continue
            
            file_obj = open(item["path"], "rb")
            opened_files.append(file_obj)
            
            # Faqat birinchi elementga caption
            cap = caption if i == 0 else None
            
            if item["type"] == "video":
                media_group.append(InputMediaVideo(
                    media=file_obj,
                    caption=cap,
                    supports_streaming=True
                ))
            else:
                media_group.append(InputMediaPhoto(
                    media=file_obj,
                    caption=cap
                ))
        
        if not media_group:
            return False
        
        # Yuborish
        await update.message.reply_media_group(
            media=media_group,
            read_timeout=120,
            write_timeout=120,
            connect_timeout=120
        )
        return True
        
    except Exception as e:
        print(f"❌ Media group error: {e}")
        return False
        
    finally:
        # Fayllarni yopish
        for f in opened_files:
            try:
                f.close()
            except:
                pass

async def send_single_media(update, media_file, caption):
    """Bitta media yuborish"""
    try:
        file_size = os.path.getsize(media_file["path"])
        
        # Hajm tekshirish
        if file_size > 50 * 1024 * 1024:
            await update.message.reply_text(
                f"❌ Fayl hajmi juda katta: {file_size / 1024 / 1024:.1f}MB\n"
                "Telegram limiti: 50MB"
            )
            return False
        
        with open(media_file["path"], "rb") as f:
            if media_file["type"] == "video":
                await update.message.reply_video(
                    video=f,
                    caption=caption,
                    supports_streaming=True,
                    read_timeout=120,
                    write_timeout=120,
                    connect_timeout=120
                )
            else:
                await update.message.reply_photo(
                    photo=f,
                    caption=caption,
                    read_timeout=120,
                    write_timeout=120,
                    connect_timeout=120
                )
        
        return True
        
    except Exception as e:
        print(f"❌ Send media error: {e}")
        return False

# ================= MAIN HANDLER =================
async def handle_instagram(update, url):
    """Instagram yuklab olish va yuborish"""
    temp_dir = f"ig_{uuid.uuid4().hex}"
    os.makedirs(temp_dir, exist_ok=True)

    status_msg = await update.message.reply_text("⏳ Yuklanmoqda...")

    try:
        # Story yoki Post?
        username, story_id = extract_story_info(url)
        
        if username and story_id:
            # STORY
            result = await asyncio.to_thread(download_story, username, story_id, temp_dir)
            caption = result.get("caption", "📥 Story")
        else:
            # POST / REEL / CAROUSEL
            shortcode = extract_shortcode(url)
            if not shortcode:
                await status_msg.edit_text("❌ Noto'g'ri Instagram link")
                return
            
            result = await asyncio.to_thread(download_post, shortcode, temp_dir)
            caption = clean_caption(result.get("caption", ""))
        
        if not result.get("success", False):
            error = result.get("error", "Noma'lum xato")
            await status_msg.edit_text(f"❌ Yuklanmadi: {error}")
            return

        # Media fayllarni olish
        await asyncio.sleep(1)  # Fayllar yozilishini kutish
        media_files = get_media_files(temp_dir)
        
        if not media_files:
            await status_msg.edit_text("❌ Media fayllar topilmadi")
            return

        await status_msg.edit_text(f"📤 Yuborilmoqda... ({len(media_files)} fayl)")

        # Yuborish
        success = False
        
        if len(media_files) == 1:
            # Bitta fayl
            success = await send_single_media(update, media_files[0], caption)
        else:
            # Carousel (ko'p fayl)
            success = await send_media_group(update, media_files, caption)

        if success:
            inc_stats()
            await asyncio.sleep(0.5)
            
            # Status xabarni o'chirish
            try:
                await status_msg.delete()
            except:
                try:
                    await status_msg.edit_text("✅ Yuklandi!")
                except:
                    pass
        else:
            await status_msg.edit_text("❌ Yuborishda xatolik yuz berdi")

    except Exception as e:
        print(f"❌ Handle Instagram error: {e}")
        try:
            await status_msg.edit_text(f"❌ Xatolik: {str(e)[:200]}")
        except:
            pass

    finally:
        # Tozalash
        await asyncio.sleep(2)
        safe_cleanup(temp_dir)

# ================= MESSAGE HANDLER =================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xabarlarni qayta ishlash"""
    chat_id = update.effective_chat.id
    
    # User allaqachon ishlayotgan bo'lsa
    if chat_id in ACTIVE_USERS:
        await update.message.reply_text("⏳ Kutib turing, oldingi yuklash tugashini...")
        return

    ACTIVE_USERS.add(chat_id)
    
    try:
        url = update.message.text.strip()
        
        if is_instagram(url):
            await handle_instagram(update, url)
        else:
            await update.message.reply_text(
                "❌ Faqat Instagram havolalari\n\n"
                "✅ Qo'llab-quvvatlanadi:\n"
                "• Post (rasm/video)\n"
                "• Reel\n"
                "• Carousel\n"
                "• Story"
            )
    
    finally:
        ACTIVE_USERS.discard(chat_id)

# ================= MAIN =================
def main():
    """Botni ishga tushirish"""
    cleanup_on_start()
    print("🤖 Eclipse Core online")
    print("📥 Instagram downloader ready")

    while True:
        try:
            app = Application.builder().token(BOT_TOKEN).build()
            
            # Handlerlar
            app.add_handler(CommandHandler("start", start))
            app.add_handler(CommandHandler("stats", stats_cmd))
            app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
            
            # Polling
            print("🚀 Bot ishga tushdi")
            app.run_polling(
                allowed_updates=Update.ALL_TYPES,
                timeout=30,
                close_loop=False
            )
            
        except KeyboardInterrupt:
            print("\n👋 Bot to'xtatildi")
            break
        except Exception as e:
            print(f"⚠️ Xatolik: {e}")
            print("🔄 5 soniyadan keyin qayta ishga tushiriladi...")
            time.sleep(5)

if __name__ == "__main__":
    main()