
import os
import re
import shutil
import tempfile
from pathlib import Path

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

from mutagen.mp3 import MP3
from mutagen.id3 import (
    ID3,
    ID3NoHeaderError,
    TIT2, TPE1, TALB, TDRC, TRCK, TCON,
    USLT, COMM, TPE2, TCOM, TPOS, TPUB, APIC,
)

TOKEN = os.getenv("BOT_TOKEN")
MAX_SIZE = 20 * 1024 * 1024

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


def load_tags(path):
    try:
        return ID3(path)
    except ID3NoHeaderError:
        tags = ID3()
        tags.save(path, v2_version=3)
        return ID3(path)


def get_tags(path):
    tags = load_tags(path)
    result = {}

    for key, frame in TAG_CLASSES.items():
        values = tags.getall(frame.__name__)
        if values and values[0].text:
            result[key] = str(values[0].text[0])
        else:
            result[key] = "—"

    lyrics = tags.getall("USLT")
    comments = tags.getall("COMM")

    result["lyrics"] = str(lyrics[0].text) if lyrics else "—"
    result["comment"] = (
        str(comments[0].text[0])
        if comments and comments[0].text
        else "—"
    )

    return result


def get_cover(path):
    tags = load_tags(path)
    covers = tags.getall("APIC")

    if not covers:
        return None

    cover = next(
        (item for item in covers if item.type == 3),
        covers[0],
    )
    return cover.data


def keyboard():
    buttons = [
        [
            InlineKeyboardButton("📁 File Name", callback_data="filename"),
            InlineKeyboardButton("🎼 Title", callback_data="title"),
        ],
        [
            InlineKeyboardButton("👤 Artists", callback_data="artist"),
            InlineKeyboardButton("🖼️ Picture", callback_data="picture"),
        ],
        [
            InlineKeyboardButton("📂 Album", callback_data="album"),
            InlineKeyboardButton("📅 Year", callback_data="year"),
        ],
        [
            InlineKeyboardButton("💿 Track N", callback_data="track"),
            InlineKeyboardButton("🎙️ Genre", callback_data="genre"),
        ],
        [
            InlineKeyboardButton("✍️ Lyrics", callback_data="lyrics"),
            InlineKeyboardButton("📝 Comment", callback_data="comment"),
        ],
        [
            InlineKeyboardButton("👥 Album Artist", callback_data="albumartist"),
            InlineKeyboardButton("👨‍🎤 Composer", callback_data="composer"),
        ],
        [
            InlineKeyboardButton("💿 Disc N", callback_data="disc"),
            InlineKeyboardButton("👤 Publisher", callback_data="publisher"),
        ],
        [
            InlineKeyboardButton("🎤 Show Lyrics", callback_data="showlyrics"),
            InlineKeyboardButton("❌ Remove All", callback_data="remove"),
        ],
        [
            InlineKeyboardButton("📩 Save", callback_data="save"),
        ],
        [
            InlineKeyboardButton("📌 Apply default tags", callback_data="defaults"),
        ],
    ]

    return InlineKeyboardMarkup(buttons)


def editor_text(path):
    tags = get_tags(path)
    audio = MP3(path)
    size = os.path.getsize(path) / 1024

    return (
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


async def show_editor(update, context):
    path = context.user_data.get("path")

    if not path or not os.path.exists(path):
        return

    info = editor_text(path)

    message = (
        update.callback_query.message
        if update.callback_query
        else update.message
    )

    cover_data = get_cover(path)

    if cover_data:
        try:
            await message.reply_photo(
                photo=cover_data,
                caption=info,
                reply_markup=keyboard(),
                parse_mode="Markdown",
            )
            return
        except Exception as error:
            print("Cover display error:", error)

    await message.reply_text(
        info + "\n\n" + (
            "🖼️ کاور داخلی وجود دارد، اما تلگرام نتوانست آن را نمایش دهد."
            if cover_data
            else "🖼️ این فایل کاور داخلی ندارد."
        ),
        reply_markup=keyboard(),
        parse_mode="Markdown",
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "سلام! 🎵\n\n"
        "به Music Tag Editor خوش اومدی.\n"
        "یک فایل MP3 بفرست تا ویرایش تگ‌ها رو شروع کنیم."
    )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["waiting"] = None
    await update.message.reply_text(
        "عملیات لغو شد. اطلاعات قبلی آهنگ حفظ شدند."
    )


async def receive_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    document = message.document or message.audio

    if not document:
        return

    name = document.file_name or "music.mp3"

    if not name.lower().endswith(".mp3"):
        await message.reply_text("فعلاً فقط فایل MP3 قبول می‌کنم.")
        return

    if document.file_size and document.file_size > MAX_SIZE:
        await message.reply_text("حجم فایل نباید بیشتر از 20 مگابایت باشه.")
        return

    old_folder = context.user_data.get("folder")
    if old_folder and os.path.isdir(old_folder):
        shutil.rmtree(old_folder, ignore_errors=True)

    folder = tempfile.mkdtemp(prefix="musicbot_")
    path = os.path.join(folder, "original.mp3")

    try:
        telegram_file = await context.bot.get_file(document.file_id)
        await telegram_file.download_to_drive(path)

        load_tags(path)
        MP3(path)

    except Exception as error:
        shutil.rmtree(folder, ignore_errors=True)
        print("Audio download error:", error)
        await message.reply_text("دریافت یا خواندن فایل MP3 ناموفق بود.")
        return

    context.user_data["path"] = path
    context.user_data["filename"] = Path(name).stem
    context.user_data["folder"] = folder
    context.user_data["waiting"] = None

    await message.reply_text("فایل دریافت شد! 🎧")
    await show_editor(update, context)


async def edit_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    path = context.user_data.get("path")

    if not path or not os.path.exists(path):
        await query.message.reply_text("اول یک آهنگ MP3 بفرست.")
        return

    action = query.data

    if action == "showlyrics":
        lyrics = load_tags(path).getall("USLT")
        await query.message.reply_text(
            str(lyrics[0].text) if lyrics else "متنی برای این آهنگ ثبت نشده."
        )
        return

    if action == "defaults":
        await query.message.reply_text(
            "هنوز تگ‌های پیش‌فرض تنظیم نشده‌اند."
        )
        return

    if action == "remove":
        tags = load_tags(path)
        tags.clear()
        tags.save(path, v2_version=3)
        await query.message.reply_text(
            "تمام تگ‌ها، از جمله کاور، حذف شدند. ✅"
        )
        await show_editor(update, context)
        return

    if action == "save":
        output_name = context.user_data.get("filename", "music")
        output_name = re.sub(
            r'[\\/:*?"<>|]', "_", output_name
        ).strip() or "music"

        output_path = os.path.join(
            context.user_data["folder"],
            output_name + ".mp3",
        )

        shutil.copy2(path, output_path)

        with open(output_path, "rb") as output_file:
            await query.message.reply_document(
                document=output_file,
                filename=output_name + ".mp3",
                caption="تگ‌های آهنگ ذخیره شدند! 🎵",
            )
        return

    if action == "picture":
        context.user_data["waiting"] = "picture"
        await query.message.reply_text(
            "عکس جدید کاور رو به صورت عکس یا فایل بفرست.\n"
            "اگر منصرف شدی /cancel رو بفرست."
        )
        return

    if action not in FIELDS:
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

    if not value:
        await update.message.reply_text("مقدار خالی قابل ذخیره نیست.")
        return

    if waiting == "filename":
        context.user_data["filename"] = value

    elif waiting in TAG_CLASSES:
        tags = load_tags(path)
        frame_id = TAG_CLASSES[waiting].__name__
        tags.delall(frame_id)
        tags.add(TAG_CLASSES[waiting](encoding=3, text=value))
        tags.save(path, v2_version=3)

    elif waiting == "lyrics":
        tags = load_tags(path)
        tags.delall("USLT")
        tags.add(USLT(encoding=3, lang="eng", desc="", text=value))
        tags.save(path, v2_version=3)

    elif waiting == "comment":
        tags = load_tags(path)
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
    if not path or not os.path.exists(path):
        return

    photo = update.message.photo[-1] if update.message.photo else None
    document = update.message.document

    if photo:
        telegram_file = await context.bot.get_file(photo.file_id)
        mime = "image/jpeg"

    elif (
        document
        and document.mime_type
        and document.mime_type.startswith("image/")
    ):
        telegram_file = await context.bot.get_file(document.file_id)
        mime = document.mime_type

    else:
        await update.message.reply_text("لطفاً یک عکس معتبر بفرست.")
        return

    image_path = os.path.join(
        context.user_data["folder"], "new_cover"
    )

    try:
        await telegram_file.download_to_drive(image_path)

        with open(image_path, "rb") as image_file:
            image_data = image_file.read()

        tags = load_tags(path)
        tags.delall("APIC")
        tags.add(
            APIC(
                encoding=3,
                mime=mime,
                type=3,
                desc="Cover",
                data=image_data,
            )
        )
        tags.save(path, v2_version=3)

    except Exception as error:
        print("Picture error:", error)
        await update.message.reply_text("تعویض کاور ناموفق بود.")
        return

    context.user_data["waiting"] = None
    await update.message.reply_text("کاور جدید جایگزین شد! 🖼️")
    await show_editor(update, context)


async def error_handler(update, context):
    print("BOT ERROR:", context.error)


def main():
    if not TOKEN:
        raise RuntimeError(
            "BOT_TOKEN environment variable is missing!"
        )

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cancel", cancel))

    app.add_handler(CallbackQueryHandler(edit_button))

    app.add_handler(
        MessageHandler(
            filters.AUDIO | filters.Document.ALL,
            receive_audio,
        )
    )

    app.add_handler(
        MessageHandler(
            filters.PHOTO | filters.Document.IMAGE,
            receive_picture,
        )
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            receive_text,
        )
    )

    app.add_error_handler(error_handler)

    print("Music Tag Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
