import os
import re
import asyncio
from telegram import Update, InputMediaPhoto, InputMediaVideo
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import instaloader
from instaloader import Post
from dotenv import load_dotenv

load_dotenv()

# Bot token'ingizni bu yerga qo'ying
BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN topilmadi! .env ni tekshir")

# Instaloader ob'ektini yaratish
L = instaloader.Instaloader(
    download_video_thumbnails=False,
    compress_json=False,
    download_comments=False,
    save_metadata=False,
    post_metadata_txt_pattern="",
    filename_pattern="{date_utc}_UTC"
)

# Session yaratish (403 error oldini olish)
try:
    # Agar session file mavjud bo'lsa, yuklab olish
    if os.path.exists("session-file"):
        L.load_session_from_file("session-file")
except:
    pass

def extract_shortcode(url):
    """Instagram URL'dan shortcode'ni ajratib olish"""
    patterns = [
        r'instagram\.com/p/([^/\?]+)',
        r'instagram\.com/reel/([^/\?]+)',
        r'instagram\.com/tv/([^/\?]+)',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start komandasi"""
    welcome_text = """
Yo 👋

Men Instagram’dan media ko‘chirib beradigan botman.
Link tashlaysan — men ishni bitiraman 😎

⚡ Qanday ishlaydi:
1️⃣ IG post / reel / carousel linkini tashla
2️⃣ Men media’ni olib beraman, gap yo‘q

📦 Qo‘llab-quvvatlanadi:
• Post (rasm / video)
• Reel (video)
• Carousel (album holida)

Boshladikmi? Linkni tashla 👇
"""
    await update.message.reply_text(welcome_text)

async def download_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Instagram media'ni yuklab olish"""
    url = update.message.text.strip()
    
    # URL tekshirish
    if 'instagram.com' not in url:
        await update.message.reply_text("❌ Bro bu Instagram link emas. To‘g‘risini tashla.")
        return
    
    # Shortcode'ni olish
    shortcode = extract_shortcode(url)
    if not shortcode:
        await update.message.reply_text("❌ Link formatida xatolik bor!")
        return
    
    status_msg = await update.message.reply_text("⏳ Yuklanmoqda...")
    
    # Temporary papka yaratish
    temp_dir = f"temp_{update.effective_user.id}"
    os.makedirs(temp_dir, exist_ok=True)
    
    try:
        # Post ob'ektini olish
        post = Post.from_shortcode(L.context, shortcode)
        
        # Post haqida ma'lumot
        caption = f"📸 Instagram Media\n"
        if post.caption:
            caption += f"\n{post.caption[:200]}{'...' if len(post.caption) > 200 else ''}"
        
        # Agar carousel bo'lsa
        if post.typename == 'GraphSidecar':
            await status_msg.edit_text("📦 Carousel yuklanmoqda...")
            
            # Avval barcha fayllarni yuklab olamiz
            L.download_post(post, target=temp_dir)
            
            # Yuklab olingan fayllarni topamiz va turkumlaymiz
            all_files = sorted([f for f in os.listdir(temp_dir) if f.endswith(('.jpg', '.mp4', '.png'))])
            
            if not all_files:
                await status_msg.edit_text("❌ Fayllar topilmadi!")
                return
            
            # Media group yaratish (maksimum 10 ta fayl)
            media_group = []
            for idx, filename in enumerate(all_files[:10]):  # Telegram 10 tagacha ruxsat beradi
                filepath = os.path.join(temp_dir, filename)
                try:
                    with open(filepath, 'rb') as f:
                        if filename.endswith('.mp4'):
                            media_group.append(
                                InputMediaVideo(
                                    media=f.read(),
                                    caption=caption if idx == 0 else None
                                )
                            )
                        else:
                            media_group.append(
                                InputMediaPhoto(
                                    media=f.read(),
                                    caption=caption if idx == 0 else None
                                )
                            )
                except Exception as e:
                    print(f"Fayl o'qishda xatolik ({filename}): {e}")
            
            # Media group'ni yuborish
            if media_group:
                await update.message.reply_media_group(media=media_group)
                await status_msg.delete()
            else:
                await status_msg.edit_text("❌ Yuborish uchun fayllar tayyorlanmadi!")
        
        # Agar bitta video bo'lsa
        elif post.is_video:
            await status_msg.edit_text("🎥 Video yuklanmoqda...")
            L.download_post(post, target=temp_dir)
            
            # Video faylni topish
            video_file = None
            for file in os.listdir(temp_dir):
                if file.endswith('.mp4'):
                    video_file = os.path.join(temp_dir, file)
                    break
            
            if video_file and os.path.exists(video_file):
                with open(video_file, 'rb') as f:
                    await update.message.reply_video(video=f, caption=caption)
                await status_msg.delete()
        
        # Agar bitta rasm bo'lsa
        else:
            await status_msg.edit_text("🖼 Rasm yuklanmoqda...")
            
            # Post'ni yuklab olish
            L.download_post(post, target=temp_dir)
            
            # Rasm faylni topish
            photo_file = None
            for file in os.listdir(temp_dir):
                if file.endswith(('.jpg', '.png', '.jpeg')):
                    photo_file = os.path.join(temp_dir, file)
                    break
            
            if photo_file and os.path.exists(photo_file):
                with open(photo_file, 'rb') as f:
                    await update.message.reply_photo(photo=f, caption=caption)
                await status_msg.delete()
            else:
                await status_msg.edit_text("❌ Rasm fayli topilmadi!")
        
    except Exception as e:
        error_text = f"❌ Xatolik yuz berdi: {str(e)}\n\n"
        error_text += "💡 Sabablari:\n"
        error_text += "• Post private bo'lishi mumkin\n"
        error_text += "• Link noto'g'ri\n"
        error_text += "• Instagram tomonidan bloklangan"
        
        await status_msg.edit_text(error_text)
    
    finally:
        # Temporary fayllarni o'chirish
        if os.path.exists(temp_dir):
            for file in os.listdir(temp_dir):
                try:
                    os.remove(os.path.join(temp_dir, file))
                except:
                    pass
            try:
                os.rmdir(temp_dir)
            except:
                pass

def main():
    """Botni ishga tushirish"""
    print("🤖 Bot ishga tushmoqda...")
    
    # Application yaratish
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Handlerlarni qo'shish
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, download_media))
    
    # Botni ishga tushirish
    print("✅ Bot ishga tushdi! Ctrl+C bilan to'xtatish mumkin.")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()