
import os
import re
import asyncio
import tempfile
from pathlib import Path

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)
from mutagen.mp3 import MP3
from mutagen.id3 import (
    ID3, TIT2, TPE1, TALB, TDRC, TRCK, TCON,
    USLT, COMM, TPE2, TCOM, TPOS, TPUB, APIC
)

TOKEN = os.getenv("BOT_TOKEN")

FIELDS = {
    "filename": "File Name",
    "title": "Title",
    "artist": "Artists",
    "album": "Album",
    "year": "Year",
    "track": "Track Number",
    "genre": "Genre",
    "lyrics": "Lyrics",
    "comment": "Comment",
    "albumartist": "Album Artist",
    "composer": "Composer",
    "disc": "Disc Number",
    "publisher": "Publisher",
}

TAG_CLASSES = {
    "title": TIT2,
    "artist": TPE1,
    "album": TALB,
    "year": TDRC,
    "track": TRCK,
    "genre": TCON,
    "albumartist": TPE2,
    "composer": TCOM,
    "disc": TPOS,
    "publisher": TPUB,
}

def get_tags(path):
    tags = ID3(path)
    result = {}

    for key, frame in {
        "title": "TIT2", "artist": "TPE1",
        "album": "TALB", "year": "TDRC",
        "track": "TRCK", "genre": "TCON",
        "albumartist": "TPE2", "composer": "TCOM",
        "disc": "TPOS", "publisher": "TPUB",
    }.items():
        values = tags.getall(frame)
        result[key] = str(values[0].text[0]) if values else "—"

    result["lyrics"] = str(tags.getall("USLT")[0].text) if tags.getall("USLT") else "—"
    result["comment"] = str(tags.getall("COMM")[0].text[0]) if tags.getall("COMM") else "—"
    return result

def keyboard():
    buttons = [
        [("📁 File Name", "filename"), ("🎼 Title", "title")],
        [("👤 Artists", "artist"), ("🖼️ Picture", "picture")],
        [("📂 Album", "album"), ("📅 Year", "year")],
        [("💿 Track N", "track"), ("🎙️ Genre", "genre")],
        [("✍️ Lyrics", "lyrics"), ("📝 Comment", "comment")],
        [("👥 Album Artist", "albumartist"), ("👨‍🎤 Composer", "composer")],
        [("💿 Disc N", "disc"), ("👤 Publisher", "publisher")],
        [("🎤 Show Lyrics", "showlyrics"), ("❌ Remove All", "remove")],
        [("📩 Save", "save")],
        [("📌 Apply default tags", "defaults")],
    ]
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(text, callback_data=data) for text, data in row]
        for row in buttons
    ])

async def show_editor(update, context):
    path = context.user_data.get("path")
    if not path:
        return

    tags = get_tags(path)
    audio = MP3(path)
    size = os.path.getsize(path) / 1024

    info = (
        "🎵 **Music Tag Editor**\n\n"
        f"Title: {tags['title']}\n"
        f"Artist: {tags['artist']}\n"
        f"Album: {tags['album']}\n"
        f"Year: {tags['year']}\n"
        f"Genre: {tags['genre']}\n"
        f"Composer: {tags['composer']}\n"
        f"Publisher: {tags['publisher']}\n"
        f"Disc N: {tags['disc']}\n"
        "----------------------\n"
        f"Bitrate: {audio.info.bitrate // 1000} kbps\n"
        f"Sample Rate: {audio.info.sample_rate} Hz\n"
        f"Duration: {int(audio.info.length)} seconds\n"
        f"Size: {size:.1f} KiB"
    )

    if update.callback_query:
        await update.callback_query.message.reply_text(
            info, reply_markup=keyboard(), parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            info, reply_markup=keyboard(), parse_mode="Markdown"
        )

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "سلام! 🎵\n\n"
        "به Music Tag Editor خوش اومدی.\n"
        "یک فایل MP3 بفرست تا ویرایش تگ‌ها رو شروع کنیم."
    )

async def receive_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    document = message.document or message.audio

    if not document:
        return

    name = document.file_name or "music.mp3"

    if not name.lower().endswith(".mp3"):
        await message.reply_text(
            "فعلاً فقط فایل MP3 قبول می‌کنم. قابلیت تبدیل رو بعداً اضافه می‌کنیم."
        )
        return

    if document.file_size and document.file_size > 20 * 1024 * 1024:
        await message.reply_text("حجم فایل فعلاً نباید بیشتر از 20 مگابایت باشه.")
        return

    file = await context.bot.get_file(document.file_id)

    folder = tempfile.mkdtemp(prefix="musicbot_")
    path = os.path.join(folder, "original.mp3")

    await file.download_to_drive(path)

    try:
        ID3(path)
    except Exception:
        ID3().save(path)

    context.user_data["path"] = path
    context.user_data["filename"] = Path(name).stem
    context.user_data["folder"] = folder

    await message.reply_text("فایل دریافت شد! 🎧")
    await show_editor(update, context)

async def edit_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    path = context.user_data.get("path")
    if not path:
        await query.message.reply_text("اول یک آهنگ MP3 بفرست.")
        return

    action = query.data

    if action == "showlyrics":
        tags = ID3(path)
        lyrics = tags.getall("USLT")
        await query.message.reply_text(
            str(lyrics[0].text) if lyrics else "متنی برای این آهنگ ثبت نشده."
        )
        return

    if action == "defaults":
        await query.message.reply_text(
            "هنوز تگ‌های پیش‌فرض تنظیم نشده‌اند. در نسخه بعدی این قابلیت را اضافه می‌کنیم."
        )
        return

    if action == "remove":
        tags = ID3(path)
        tags.clear()
        tags.save(path, v2_version=3)
        await query.message.reply_text("تمام تگ‌ها حذف شدند. ✅")
        await show_editor(update, context)
        return

    if action == "save":
        output_name = context.user_data.get("filename", "music")
        output_name = re.sub(r'[\\/:*?"<>|]', "_", output_name).strip()
        if not output_name:
            output_name = "music"

        output_path = os.path.join(
            context.user_data["folder"], output_name + ".mp3"
        )

        with open(path, "rb") as source:
            with open(output_path, "wb") as target:
                target.write(source.read())

        await query.message.reply_document(
            document=open(output_path, "rb"),
            filename=output_name + ".mp3",
            caption="تگ‌های آهنگ ذخیره شدند! 🎵"
        )
        return

    if action == "picture":
        context.user_data["waiting"] = "picture"
        await query.message.reply_text(
            "عکس کاور رو به صورت عکس یا فایل برام بفرست."
        )
        return

    context.user_data["waiting"] = action
    await query.message.reply_text(
        f"مقدار جدید {FIELDS[action]} رو بفرست:"
    )

async def receive_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    waiting = context.user_data.get("waiting")
    path = context.user_data.get("path")

    if not waiting or not path:
        return

    value = update.message.text.strip()

    if waiting == "filename":
        context.user_data["filename"] = value
    elif waiting in TAG_CLASSES:
        tags = ID3(path)
        tags.delall({
            "title": "TIT2", "artist": "TPE1",
            "album": "TALB", "year": "TDRC",
            "track": "TRCK", "genre": "TCON",
            "albumartist": "TPE2", "composer": "TCOM",
            "disc": "TPOS", "publisher": "TPUB",
        }[waiting])
        tags.add(TAG_CLASSES[waiting](encoding=3, text=value))
        tags.save(path, v2_version=3)
    elif waiting == "lyrics":
        tags = ID3(path)
        tags.delall("USLT")
        tags.add(USLT(encoding=3, lang="eng", desc="", text=value))
        tags.save(path, v2_version=3)
    elif waiting == "comment":
        tags = ID3(path)
        tags.delall("COMM")
        tags.add(COMM(encoding=3, lang="eng", desc="", text=value))
        tags.save(path, v2_version=3)

    context.user_data["waiting"] = None
    await update.message.reply_text("ذخیره شد! ✅")
    await show_editor(update, context)

async def receive_picture(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("waiting") != "picture":
        return

    path = context.user_data.get("path")
    if not path:
        return

    photo = update.message.photo[-1] if update.message.photo else None
    document = update.message.document

    if photo:
        file = await context.bot.get_file(photo.file_id)
        mime = "image/jpeg"
    elif document and document.mime_type and document.mime_type.startswith("image/"):
        file = await context.bot.get_file(document.file_id)
        mime = document.mime_type
    else:
        await update.message.reply_text("لطفاً یک عکس معتبر بفرست.")
        return

    image_path = os.path.join(context.user_data["folder"], "cover")
    await file.download_to_drive(image_path)

    with open(image_path, "rb") as image_file:
        image_data = image_file.read()

    tags = ID3(path)
    tags.delall("APIC")
    tags.add(APIC(
        encoding=3,
        mime=mime,
        type=3,
        desc="Cover",
        data=image_data
    ))
    tags.save(path, v2_version=3)

    context.user_data["waiting"] = None
    await update.message.reply_text("کاور آلبوم اضافه شد! 🖼️")
    await show_editor(update, context)

async def main():
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable is missing!")

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(edit_button))
    app.add_handler(MessageHandler(
        filters.Document.ALL | filters.AUDIO, receive_audio
    ))
    app.add_handler(MessageHandler(
        filters.PHOTO | filters.Document.IMAGE, receive_picture
    ))
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND, receive_text
    ))

    print("Music Tag Bot is running...")
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
  
