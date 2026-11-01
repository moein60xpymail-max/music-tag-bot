import os
import re
import json
import shutil
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Any

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from telegram.constants import ParseMode

from mutagen.mp3 import MP3
from mutagen.id3 import (
    ID3,
    ID3NoHeaderError,
    TIT2, TPE1, TALB, TDRC, TRCK, TCON,
    USLT, COMM, TPE2, TCOM, TPOS, TPUB, APIC,
)

# ==================== Config ====================
TOKEN = os.getenv("BOT_TOKEN")
MAX_SIZE = 20 * 1024 * 1024
DATA_DIR = Path("data")
DEFAULTS_FILE = DATA_DIR / "user_defaults.json"

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

DEFAULT_SINGLE = {
    "disc": "1",
    "track": "0",
    "year": "2026",
    "composer": "@nistplay",
    "publisher": "eM Nine Collection",
    "album": "Single Track",
    # albumartist will be set to artist
}

DEFAULT_ALBUM = {
    "disc": "1",
    "year": "2026",
    "composer": "@nistplay",
    "publisher": "eM Nine Collection",
}

# ==================== Persistence ====================
def ensure_data_dir():
    DATA_DIR.mkdir(parents=True, exist_ok=True)

def load_all_defaults() -> Dict[str, Any]:
    ensure_data_dir()
    if not DEFAULTS_FILE.exists():
        return {}
    try:
        with open(DEFAULTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_all_defaults(data: Dict[str, Any]):
    ensure_data_dir()
    with open(DEFAULTS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_user_defaults(user_id: int) -> Dict[str, Any]:
    all_data = load_all_defaults()
    uid = str(user_id)
    if uid not in all_data:
        all_data[uid] = {
            "single": DEFAULT_SINGLE.copy(),
            "album": DEFAULT_ALBUM.copy(),
        }
        save_all_defaults(all_data)
    return all_data[uid]

def set_user_defaults(user_id: int, mode: str, key: str, value: str):
    all_data = load_all_defaults()
    uid = str(user_id)
    if uid not in all_data:
        all_data[uid] = {
            "single": DEFAULT_SINGLE.copy(),
            "album": DEFAULT_ALBUM.copy(),
        }
    all_data[uid][mode][key] = value
    save_all_defaults(all_data)

# ==================== Tag helpers ====================
def load_tags(path: str) -> ID3:
    try:
        return ID3(path)
    except ID3NoHeaderError:
        tags = ID3()
        tags.save(path, v2_version=3)
        return ID3(path)

def get_tags(path: str) -> Dict[str, str]:
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
        str(comments[0].text[0]) if comments and comments[0].text else "—"
    )
    return result

def get_cover(path: str) -> Optional[bytes]:
    tags = load_tags(path)
    covers = tags.getall("APIC")
    if not covers:
        return None
    cover = next((item for item in covers if item.type == 3), covers[0])
    return cover.data

def set_tag(path: str, key: str, value: str):
    tags = load_tags(path)
    if key in TAG_CLASSES:
        frame_id = TAG_CLASSES[key].__name__
        tags.delall(frame_id)
        if value and value != "—":
            tags.add(TAG_CLASSES[key](encoding=3, text=value))
        tags.save(path, v2_version=3)
    elif key == "lyrics":
        tags.delall("USLT")
        if value and value != "—":
            tags.add(USLT(encoding=3, lang="eng", desc="", text=value))
        tags.save(path, v2_version=3)
    elif key == "comment":
        tags.delall("COMM")
        if value and value != "—":
            tags.add(COMM(encoding=3, lang="eng", desc="", text=value))
        tags.save(path, v2_version=3)

def delete_tag(path: str, key: str):
    tags = load_tags(path)
    if key in TAG_CLASSES:
        tags.delall(TAG_CLASSES[key].__name__)
    elif key == "lyrics":
        tags.delall("USLT")
    elif key == "comment":
        tags.delall("COMM")
    tags.save(path, v2_version=3)

def preserve_and_save(path: str):
    """Force save while keeping APIC frames intact."""
    tags = load_tags(path)
    # mutagen keeps existing APIC when we don't touch them
    tags.save(path, v2_version=3)

def sanitize_filename(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name or "music"

def split_artists(artist_str: str) -> List[str]:
    if not artist_str or artist_str == "—":
        return []
    # support common separators
    parts = re.split(r"\s*[,&+/]\s*|\s+feat\.?\s+|\s+ft\.?\s+|\s+x\s+", artist_str, flags=re.IGNORECASE)
    return [p.strip() for p in parts if p.strip()]

def normalize_name(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip().lower())

def generate_filename(artist: str, title: str, main_artists: Optional[List[str]] = None) -> str:
    title = title if title and title != "—" else "Unknown Title"
    artists = split_artists(artist) if artist and artist != "—" else ["Unknown Artist"]

    if main_artists:
        main_norm = {normalize_name(a) for a in main_artists}
        mains = []
        guests = []
        for a in artists:
            if normalize_name(a) in main_norm:
                mains.append(a)
            else:
                guests.append(a)
        # keep order of main_artists preference if possible
        ordered_mains = []
        for m in main_artists:
            for a in mains:
                if normalize_name(a) == normalize_name(m) and a not in ordered_mains:
                    ordered_mains.append(a)
        for a in mains:
            if a not in ordered_mains:
                ordered_mains.append(a)

        if ordered_mains and guests:
            artist_part = " , ".join(ordered_mains) + " Ft. " + " , ".join(guests)
        elif ordered_mains:
            artist_part = " , ".join(ordered_mains)
        else:
            artist_part = " , ".join(artists)
    else:
        artist_part = " , ".join(artists)

    return sanitize_filename(f"{artist_part} - {title}")

def apply_defaults_to_file(path: str, defaults: Dict[str, str], is_single: bool = True):
    tags = get_tags(path)
    artist = tags.get("artist", "—")
    for key, value in defaults.items():
        if key == "albumartist" and is_single:
            # will be set to artist
            continue
        set_tag(path, key, value)
    if is_single:
        set_tag(path, "album", defaults.get("album", "Single Track"))
        if artist and artist != "—":
            set_tag(path, "albumartist", artist)

# ==================== Keyboards ====================
def main_editor_keyboard(is_album: bool = False) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton("📁 File Name", callback_data="field:filename"),
            InlineKeyboardButton("🎼 Title", callback_data="field:title"),
        ],
        [
            InlineKeyboardButton("👤 Artists", callback_data="field:artist"),
            InlineKeyboardButton("🖼️ Picture", callback_data="action:picture"),
        ],
        [
            InlineKeyboardButton("📂 Album", callback_data="field:album"),
            InlineKeyboardButton("📅 Year", callback_data="field:year"),
        ],
        [
            InlineKeyboardButton("💿 Track N", callback_data="field:track"),
            InlineKeyboardButton("🎙️ Genre", callback_data="field:genre"),
        ],
        [
            InlineKeyboardButton("✍️ Lyrics", callback_data="field:lyrics"),
            InlineKeyboardButton("📝 Comment", callback_data="field:comment"),
        ],
        [
            InlineKeyboardButton("👥 Album Artist", callback_data="field:albumartist"),
            InlineKeyboardButton("👨‍🎤 Composer", callback_data="field:composer"),
        ],
        [
            InlineKeyboardButton("💿 Disc N", callback_data="field:disc"),
            InlineKeyboardButton("👤 Publisher", callback_data="field:publisher"),
        ],
        [
            InlineKeyboardButton("🎤 Show Lyrics", callback_data="action:showlyrics"),
            InlineKeyboardButton("❌ Remove All", callback_data="action:remove"),
        ],
        [
            InlineKeyboardButton("📌 Apply Default Tags", callback_data="action:defaults"),
        ],
        [
            InlineKeyboardButton("📩 Save", callback_data="action:save"),
        ],
    ]
    if is_album:
        buttons.append([
            InlineKeyboardButton("🔙 Back to Album", callback_data="album:back"),
        ])
    return InlineKeyboardMarkup(buttons)

def field_action_keyboard(field: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✏️ Edit", callback_data=f"edit:{field}"),
            InlineKeyboardButton("🗑️ Delete", callback_data=f"delete:{field}"),
        ],
        [
            InlineKeyboardButton("📋 Copy to Clipboard", callback_data=f"copy:{field}"),
            InlineKeyboardButton("❌ Cancel", callback_data="action:cancel_field"),
        ],
    ])

def confirm_remove_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Yes", callback_data="confirm:remove_yes"),
            InlineKeyboardButton("❌ No", callback_data="confirm:remove_no"),
        ]
    ])

def tag_config_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎵 Configure Single Track", callback_data="tag:single")],
        [InlineKeyboardButton("💿 Configure Album", callback_data="tag:album")],
        [InlineKeyboardButton("❌ Close", callback_data="tag:close")],
    ])

def defaults_fields_keyboard(mode: str) -> InlineKeyboardMarkup:
    keys = list(DEFAULT_SINGLE.keys()) if mode == "single" else list(DEFAULT_ALBUM.keys())
    buttons = []
    row = []
    for i, k in enumerate(keys):
        row.append(InlineKeyboardButton(FIELDS.get(k, k.title()), callback_data=f"setdefault:{mode}:{k}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data="tag:menu")])
    return InlineKeyboardMarkup(buttons)

# ==================== UI helpers ====================
def editor_text(path: str) -> str:
    tags = get_tags(path)
    try:
        audio = MP3(path)
        bitrate = audio.info.bitrate // 1000 if audio.info.bitrate else "?"
        sample = audio.info.sample_rate if audio.info.sample_rate else "?"
        duration = int(audio.info.length)
        size = os.path.getsize(path) / 1024
    except Exception:
        bitrate = sample = duration = size = "?"
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
        f"Track N: {tags['track']}\n"
        f"Album Artist: {tags['albumartist']}\n"
        "----------------------\n"
        f"Bitrate: {bitrate} kbps\n"
        f"Sample Rate: {sample} Hz\n"
        f"Duration: {duration} seconds\n"
        f"Size: {size:.1f} KiB"
    )

async def show_editor(update: Update, context: ContextTypes.DEFAULT_TYPE, is_album: bool = False):
    path = context.user_data.get("path")
    if not path or not os.path.exists(path):
        return

    info = editor_text(path)
    message = update.callback_query.message if update.callback_query else update.message
    cover_data = get_cover(path)

    if cover_data:
        try:
            await message.reply_photo(
                photo=cover_data,
                caption=info,
                reply_markup=main_editor_keyboard(is_album),
                parse_mode=ParseMode.MARKDOWN,
            )
            return
        except Exception as e:
            print("Cover display error:", e)

    await message.reply_text(
        info + "\n\n" + (
            "🖼️ کاور داخلی وجود دارد، اما تلگرام نتوانست آن را نمایش دهد."
            if cover_data else "🖼️ این فایل کاور داخلی ندارد."
        ),
        reply_markup=main_editor_keyboard(is_album),
        parse_mode=ParseMode.MARKDOWN,
    )

# ==================== Handlers ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "سلام! 🎵\n\n"
        "به **Music Tag Editor** خوش اومدی.\n\n"
        "• یک فایل MP3 بفرست تا ویرایش تگ‌ها شروع بشه.\n"
        "• برای حالت آلبوم (چند فایل) از دستور /album استفاده کن.\n"
        "• برای تنظیم پیش‌فرض‌ها /tag رو بزن.\n"
        "• /cancel برای لغو عملیات جاری."
    )

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["waiting"] = None
    context.user_data["waiting_mode"] = None
    await update.message.reply_text("عملیات لغو شد.")

async def tag_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⚙️ **Tag Defaults Configuration**\n\n"
        "Choose what you want to configure.\n"
        "Defaults are saved permanently and only applied when you click **Apply Default Tags**.",
        reply_markup=tag_config_keyboard(),
        parse_mode=ParseMode.MARKDOWN,
    )

async def album_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # start album mode
    old = context.user_data.get("album_folder")
    if old and os.path.isdir(old):
        shutil.rmtree(old, ignore_errors=True)

    folder = tempfile.mkdtemp(prefix="album_")
    context.user_data["album_mode"] = True
    context.user_data["album_folder"] = folder
    context.user_data["album_tracks"] = []  # list of dicts {path, original_name, tags...}
    context.user_data["main_artists"] = []
    context.user_data["waiting"] = "album_main_artists"

    await update.message.reply_text(
        "💿 **Album Mode**\n\n"
        "اول نام هنرمند(های) اصلی آلبوم را بفرست.\n"
        "اگر چند نفر هستند با کاما جدا کن.\n"
        "مثال: `Yas , Amir Tataloo`\n\n"
        "بعد فایل‌های MP3 را یکی‌یکی بفرست و در پایان دکمه **Finish Uploading** را بزن.",
        parse_mode=ParseMode.MARKDOWN,
    )

async def receive_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    document = message.document or message.audio
    if not document:
        return

    # ignore images that might come as document
    if document.mime_type and document.mime_type.startswith("image/"):
        return

    name = document.file_name or "music.mp3"
    if not name.lower().endswith(".mp3"):
        await message.reply_text("فعلاً فقط فایل MP3 قبول می‌کنم.")
        return

    if document.file_size and document.file_size > MAX_SIZE:
        await message.reply_text("حجم فایل نباید بیشتر از ۲۰ مگابایت باشه.")
        return

    # Album mode
    if context.user_data.get("album_mode"):
        folder = context.user_data["album_folder"]
        idx = len(context.user_data["album_tracks"]) + 1
        path = os.path.join(folder, f"track_{idx:02d}.mp3")
        try:
            tg_file = await context.bot.get_file(document.file_id)
            await tg_file.download_to_drive(path)
            load_tags(path)
            MP3(path)
        except Exception as e:
            print("Album download error:", e)
            await message.reply_text("دریافت فایل ناموفق بود.")
            return

        context.user_data["album_tracks"].append({
            "path": path,
            "original_name": name,
            "index": idx,
        })
        await message.reply_text(
            f"✅ ترک {idx} دریافت شد: `{name}`\n"
            "فایل بعدی را بفرست یا **Finish Uploading** را بزن.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Finish Uploading", callback_data="album:finish")]
            ]),
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    # Single track mode
    old_folder = context.user_data.get("folder")
    if old_folder and os.path.isdir(old_folder):
        shutil.rmtree(old_folder, ignore_errors=True)

    folder = tempfile.mkdtemp(prefix="musicbot_")
    path = os.path.join(folder, "original.mp3")

    try:
        tg_file = await context.bot.get_file(document.file_id)
        await tg_file.download_to_drive(path)
        load_tags(path)
        MP3(path)
    except Exception as e:
        shutil.rmtree(folder, ignore_errors=True)
        print("Audio download error:", e)
        await message.reply_text("دریافت یا خواندن فایل MP3 ناموفق بود.")
        return

    context.user_data["path"] = path
    context.user_data["filename"] = Path(name).stem
    context.user_data["folder"] = folder
    context.user_data["waiting"] = None
    context.user_data["album_mode"] = False

    await message.reply_text("فایل دریافت شد! 🎧")
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
        tg_file = await context.bot.get_file(photo.file_id)
        mime = "image/jpeg"
    elif document and document.mime_type and document.mime_type.startswith("image/"):
        tg_file = await context.bot.get_file(document.file_id)
        mime = document.mime_type
    else:
        await update.message.reply_text("لطفاً یک عکس معتبر بفرست.")
        return

    image_path = os.path.join(context.user_data["folder"], "new_cover")
    try:
        await tg_file.download_to_drive(image_path)
        with open(image_path, "rb") as f:
            image_data = f.read()

        tags = load_tags(path)
        tags.delall("APIC")
        tags.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=image_data))
        tags.save(path, v2_version=3)
    except Exception as e:
        print("Picture error:", e)
        await update.message.reply_text("تعویض کاور ناموفق بود.")
        return

    context.user_data["waiting"] = None
    await update.message.reply_text("کاور جدید جایگزین شد! 🖼️")
    await show_editor(update, context, is_album=context.user_data.get("editing_album_track", False))

async def receive_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    waiting = context.user_data.get("waiting")
    if not waiting:
        return

    value = update.message.text.strip()
    if not value:
        await update.message.reply_text("مقدار خالی قابل ذخیره نیست.")
        return

    # Album main artists
    if waiting == "album_main_artists":
        artists = [a.strip() for a in re.split(r"\s*,\s*", value) if a.strip()]
        context.user_data["main_artists"] = artists
        context.user_data["waiting"] = None
        await update.message.reply_text(
            f"✅ ه tags.delall("USLT")
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
