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
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove,
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

# ==================== تنظیمات ====================
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
}

DEFAULT_ALBUM = {
    "disc": "1",
    "year": "2026",
    "composer": "@nistplay",
    "publisher": "eM Nine Collection",
}


# ==================== ذخیره پیش‌فرض‌ها ====================
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


# ==================== کار با تگ‌ها ====================
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
    tags = load_tags(path)
    tags.save(path, v2_version=3)


def sanitize_filename(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name or "music"


def split_artists(artist_str: str) -> List[str]:
    if not artist_str or artist_str == "—":
        return []
    parts = re.split(
        r"\s*[,&+/]\s*|\s+feat\.?\s+|\s+ft\.?\s+|\s+x\s+",
        artist_str,
        flags=re.IGNORECASE,
    )
    return [p.strip() for p in parts if p.strip()]


def normalize_name(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip().lower())


def generate_filename(
    artist: str, title: str, main_artists: Optional[List[str]] = None
) -> str:
    title = title if title and title != "—" else "Unknown Title"
    artists = (
        split_artists(artist) if artist and artist != "—" else ["Unknown Artist"]
    )

    if main_artists:
        main_norm = {normalize_name(a) for a in main_artists}
        mains = []
        guests = []
        for a in artists:
            if normalize_name(a) in main_norm:
                mains.append(a)
            else:
                guests.append(a)

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


def apply_defaults_to_file(
    path: str, defaults: Dict[str, str], is_single: bool = True
):
    tags = get_tags(path)
    artist = tags.get("artist", "—")
    for key, value in defaults.items():
        if key == "albumartist" and is_single:
            continue
        set_tag(path, key, value)
    if is_single:
        set_tag(path, "album", defaults.get("album", "Single Track"))
        if artist and artist != "—":
            set_tag(path, "albumartist", artist)


# ==================== دکمه‌ها ====================
def main_reply_keyboard() -> ReplyKeyboardMarkup:
    """دکمه‌های پایین صفحه که همیشه دیده می‌شوند"""
    keyboard = [
        [KeyboardButton("🎵 شروع"), KeyboardButton("⚙️ تنظیم تگ‌ها")],
        [KeyboardButton("💿 حالت آلبوم"), KeyboardButton("❌ لغو / خروج از آلبوم")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


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
            InlineKeyboardButton(
                "📌 Apply Default Tags", callback_data="action:defaults"
            ),
        ],
        [
            InlineKeyboardButton("📩 Save", callback_data="action:save"),
        ],
    ]
    if is_album:
        buttons.append(
            [InlineKeyboardButton("🔙 Back to Album", callback_data="album:back")]
        )
    return InlineKeyboardMarkup(buttons)


def field_action_keyboard(field: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✏️ Edit", callback_data=f"edit:{field}"),
                InlineKeyboardButton("🗑️ Delete", callback_data=f"delete:{field}"),
            ],
            [
                InlineKeyboardButton(
                    "📋 Copy to Clipboard", callback_data=f"copy:{field}"
                ),
                InlineKeyboardButton("❌ Cancel", callback_data="action:cancel_field"),
            ],
        ]
    )


def confirm_remove_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Yes", callback_data="confirm:remove_yes"),
                InlineKeyboardButton("❌ No", callback_data="confirm:remove_no"),
            ]
        ]
    )


def tag_config_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🎵 Configure Single Track", callback_data="tag:single"
                )
            ],
            [InlineKeyboardButton("💿 Configure Album", callback_data="tag:album")],
            [InlineKeyboardButton("❌ Close", callback_data="tag:close")],
        ]
    )


def defaults_fields_keyboard(mode: str) -> InlineKeyboardMarkup:
    keys = (
        list(DEFAULT_SINGLE.keys()) if mode == "single" else list(DEFAULT_ALBUM.keys())
    )
    buttons = []
    row = []
    for i, k in enumerate(keys):
        row.append(
            InlineKeyboardButton(
                FIELDS.get(k, k.title()), callback_data=f"setdefault:{mode}:{k}"
            )
        )
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data="tag:menu")])
    return InlineKeyboardMarkup(buttons)


# ==================== نمایش ویرایشگر ====================
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


async def show_editor(
    update: Update, context: ContextTypes.DEFAULT_TYPE, is_album: bool = False
):
    path = context.user_data.get("path")
    if not path or not os.path.exists(path):
        return

    info = editor_text(path)
    message = (
        update.callback_query.message if update.callback_query else update.message
    )
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
        info
        + "\n\n"
        + (
            "🖼️ کاور داخلی وجود دارد، اما تلگرام نتوانست آن را نمایش دهد."
            if cover_data
            else "🖼️ این فایل کاور داخلی ندارد."
        ),
        reply_markup=main_editor_keyboard(is_album),
        parse_mode=ParseMode.MARKDOWN,
    )


# ==================== دستورات ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # مطمئن شو که از حالت آلبوم خارج شده
    context.user_data["album_mode"] = False
    context.user_data["editing_album_track"] = False

    await update.message.reply_text(
        "سلام! 🎵\n\n"
        "به **Music Tag Editor** خوش اومدی.\n\n"
        "• یک فایل MP3 بفرست تا ویرایش تگ‌ها شروع بشه.\n"
        "• از دکمه‌های پایین صفحه هم می‌تونی استفاده کنی.",
        reply_markup=main_reply_keyboard(),
        parse_mode=ParseMode.MARKDOWN,
    )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # کامل از حالت آلبوم خارج شو
    context.user_data["waiting"] = None
    context.user_data["waiting_mode"] = None
    context.user_data["album_mode"] = False
    context.user_data["editing_album_track"] = False
    context.user_data["album_tracks"] = []
    context.user_data["main_artists"] = []

    # پوشه موقت آلبوم را پاک کن
    album_folder = context.user_data.get("album_folder")
    if album_folder and os.path.isdir(album_folder):
        shutil.rmtree(album_folder, ignore_errors=True)
    context.user_data["album_folder"] = None

    await update.message.reply_text(
        "✅ از حالت آلبوم خارج شدی.\n"
        "حالا می‌تونی آهنگ تکی بفرستی.",
        reply_markup=main_reply_keyboard(),
    )


async def tag_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⚙️ **Tag Defaults Configuration**\n\n"
        "Choose what you want to configure.\n"
        "Defaults are saved permanently and only applied when you click **Apply Default Tags**.",
        reply_markup=tag_config_keyboard(),
        parse_mode=ParseMode.MARKDOWN,
    )


async def album_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    old = context.user_data.get("album_folder")
    if old and os.path.isdir(old):
        shutil.rmtree(old, ignore_errors=True)

    folder = tempfile.mkdtemp(prefix="album_")
    context.user_data["album_mode"] = True
    context.user_data["album_folder"] = folder
    context.user_data["album_tracks"] = []
    context.user_data["main_artists"] = []
    context.user_data["waiting"] = "album_main_artists"

    await update.message.reply_text(
        "💿 **Album Mode**\n\n"
        "اول نام هنرمند(های) اصلی آلبوم را بفرست.\n"
        "اگر چند نفر هستند با کاما جدا کن.\n"
        "مثال: `Yas , Amir Tataloo`\n\n"
        "بعد فایل‌های MP3 را یکی‌یکی بفرست و در پایان دکمه **Finish Uploading** را بزن.\n\n"
        "اگر خواستی از حالت آلبوم خارج شی، دکمه «❌ لغو / خروج از آلبوم» رو بزن.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=main_reply_keyboard(),
    )


# ==================== دریافت فایل و متن ====================
async def receive_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    document = message.document or message.audio
    if not document:
        return

    if document.mime_type and document.mime_type.startswith("image/"):
        return

    name = document.file_name or "music.mp3"
    if not name.lower().endswith(".mp3"):
        await message.reply_text("فعلاً فقط فایل MP3 قبول می‌کنم.")
        return

    if document.file_size and document.file_size > MAX_SIZE:
        await message.reply_text("حجم فایل نباید بیشتر از ۲۰ مگابایت باشه.")
        return

    # حالت آلبوم
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

        context.user_data["album_tracks"].append(
            {
                "path": path,
                "original_name": name,
                "index": idx,
            }
        )
        await message.reply_text(
            f"✅ ترک {idx} دریافت شد: `{name}`\n"
            "فایل بعدی را بفرست یا **Finish Uploading** را بزن.",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "✅ Finish Uploading", callback_data="album:finish"
                        )
                    ]
                ]
            ),
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    # حالت تک آهنگ
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
    context.user_data["editing_album_track"] = False

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
        tags.add(
            APIC(encoding=3, mime=mime, type=3, desc="Cover", data=image_data)
        )
        tags.save(path, v2_version=3)
    except Exception as e:
        print("Picture error:", e)
        await update.message.reply_text("تعویض کاور ناموفق بود.")
        return

    context.user_data["waiting"] = None
    await update.message.reply_text("کاور جدید جایگزین شد! 🖼️")
    await show_editor(
        update, context, is_album=context.user_data.get("editing_album_track", False)
    )


async def receive_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip() if update.message.text else ""

    # دکمه‌های پایین صفحه
    if text == "🎵 شروع":
        await start(update, context)
        return
    if text == "⚙️ تنظیم تگ‌ها":
        await tag_command(update, context)
        return
    if text == "💿 حالت آلبوم":
        await album_command(update, context)
        return
    if text == "❌ لغو / خروج از آلبوم":
        await cancel(update, context)
        return

    waiting = context.user_data.get("waiting")
    if not waiting:
        return

    value = text
    if not value:
        await update.message.reply_text("مقدار خالی قابل ذخیره نیست.")
        return

    # هنرمندان اصلی آلبوم
    if waiting == "album_main_artists":
        artists = [a.strip() for a in re.split(r"\s*,\s*", value) if a.strip()]
        context.user_data["main_artists"] = artists
        context.user_data["waiting"] = None
        await update.message.reply_text(
            f"✅ هنرمندان اصلی آلبوم: {' , '.join(artists)}\n\n"
            "حالا فایل‌های MP3 آلبوم را یکی‌یکی بفرست.\n"
            "وقتی تمام شد دکمه **Finish Uploading** را بزن."
        )
        return

    # تنظیم مقدار پیش‌فرض
    if waiting.startswith("setdefault:"):
        _, mode, key = waiting.split(":", 2)
        user_id = update.effective_user.id
        set_user_defaults(user_id, mode, key, value)
        context.user_data["waiting"] = None
        await update.message.reply_text(
            f"✅ پیش‌فرض `{key}` برای حالت `{mode}` ذخیره شد: `{value}`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    path = context.user_data.get("path")
    if not path or not os.path.exists(path):
        context.user_data["waiting"] = None
        return

    if waiting == "filename":
        context.user_data["filename"] = value
    elif waiting in TAG_CLASSES or waiting in ("lyrics", "comment"):
        set_tag(path, waiting, value)
    else:
        context.user_data["waiting"] = None
        return

    context.user_data["waiting"] = None
    await update.message.reply_text("ذخیره شد! ✅")
    await show_editor(
        update, context, is_album=context.user_data.get("editing_album_track", False)
    )


# ==================== دکمه‌های اینلاین ====================
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    path = context.user_data.get("path")

    # ---------- تنظیمات پیش‌فرض ----------
    if data == "tag:menu" or data == "tag:close":
        if data == "tag:close":
            await query.message.edit_text("بسته شد.")
        else:
            await query.message.edit_text(
                "⚙️ **Tag Defaults Configuration**",
                reply_markup=tag_config_keyboard(),
                parse_mode=ParseMode.MARKDOWN,
            )
        return

    if data == "tag:single" or data == "tag:album":
        mode = "single" if data == "tag:single" else "album"
        user_defaults = get_user_defaults(query.from_user.id)
        current = user_defaults[mode]
        text = f"⚙️ Current **{mode}** defaults:\n\n"
        for k, v in current.items():
            text += f"• {FIELDS.get(k, k)}: `{v}`\n"
        text += "\nSelect a field to change:"
        await query.message.edit_text(
            text,
            reply_markup=defaults_fields_keyboard(mode),
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if data.startswith("setdefault:"):
        _, mode, key = data.split(":", 2)
        context.user_data["waiting"] = f"setdefault:{mode}:{key}"
        await query.message.reply_text(
            f"مقدار جدید برای **{FIELDS.get(key, key)}** ({mode}) را بفرست:"
        )
        return

    # ---------- حالت آلبوم ----------
    if data == "album:finish":
        tracks = context.user_data.get("album_tracks", [])
        if not tracks:
            await query.message.reply_text("هنوز هیچ ترکی دریافت نشده.")
            return
        main_artists = context.user_data.get("main_artists", [])
        for t in tracks:
            p = t["path"]
            tags = get_tags(p)
            artist = tags.get("artist", "—")
            set_tag(
                p,
                "albumartist",
                " , ".join(main_artists) if main_artists else artist,
            )
        # دیگر فایل جدید به آلبوم اضافه نشود
        context.user_data["album_mode"] = False
        await show_album_overview(query, context)
        return

    if data == "album:back":
        await show_album_overview(query, context)
        return

    if data.startswith("album:edit:"):
        idx = int(data.split(":")[2])
        tracks = context.user_data.get("album_tracks", [])
        track = next((t for t in tracks if t["index"] == idx), None)
        if not track:
            return
        context.user_data["path"] = track["path"]
        context.user_data["folder"] = context.user_data["album_folder"]
        context.user_data["filename"] = Path(track["original_name"]).stem
        context.user_data["editing_album_track"] = True
        await show_editor(update, context, is_album=True)
        return

    if data == "album:all":
        context.user_data["waiting"] = "album_all_field"
        await query.message.reply_text(
            "کدام فیلد را می‌خواهی روی **همه ترک‌ها** اعمال کنی؟\n"
            "یکی از این‌ها را بنویس: title, artist, album, year, track, genre, albumartist, composer, disc, publisher, comment"
        )
        return

    if data == "album:summary":
        await send_album_summary(query, context)
        return

    if data == "album:save_all":
        await save_album_all(query, context)
        return

    # ---------- عملیات روی فیلد ----------
    if data.startswith("field:"):
        field = data.split(":", 1)[1]
        if not path or not os.path.exists(path):
            await query.message.reply_text("اول یک آهنگ بفرست.")
            return
        current = (
            get_tags(path).get(field, "—")
            if field != "filename"
            else context.user_data.get("filename", "—")
        )
        await query.message.reply_text(
            f"**{FIELDS.get(field, field)}**\nCurrent: `{current}`",
            reply_markup=field_action_keyboard(field),
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if data.startswith("edit:"):
        field = data.split(":", 1)[1]
        context.user_data["waiting"] = field
        await query.message.reply_text(
            f"مقدار جدید **{FIELDS.get(field, field)}** را بفرست:"
        )
        return

    if data.startswith("delete:"):
        field = data.split(":", 1)[1]
        if field == "filename":
            await query.message.reply_text("نام فایل را نمی‌توان حذف کرد.")
            return
        if path and os.path.exists(path):
            delete_tag(path, field)
            await query.message.reply_text(
                f"✅ فیلد {FIELDS.get(field, field)} حذف شد."
            )
            await show_editor(
                update,
                context,
                is_album=context.user_data.get("editing_album_track", False),
            )
        return

    if data.startswith("copy:"):
        field = data.split(":", 1)[1]
        if field == "filename":
            val = context.user_data.get("filename", "")
        else:
            val = get_tags(path).get(field, "") if path else ""
        if val and val != "—":
            await query.message.reply_text(
                f"📋 مقدار فعلی (کپی کن):\n`{val}`", parse_mode=ParseMode.MARKDOWN
            )
        else:
            await query.message.reply_text("این فیلد خالی است.")
        return

    if data == "action:cancel_field":
        await query.message.reply_text("لغو شد.")
        return

    # ---------- کارهای اصلی ----------
    if data == "action:showlyrics":
        if not path:
            return
        lyrics = load_tags(path).getall("USLT")
        await query.message.reply_text(
            str(lyrics[0].text) if lyrics else "متنی ثبت نشده."
        )
        return

    if data == "action:picture":
        context.user_data["waiting"] = "picture"
        await query.message.reply_text(
            "عکس جدید کاور را به صورت عکس یا فایل بفرست.\nاگر منصرف شدی /cancel بزن."
        )
        return

    if data == "action:remove":
        await query.message.reply_text(
            "⚠️ آیا مطمئنی می‌خواهی **همه تگ‌ها و کاور** را حذف کنی؟",
            reply_markup=confirm_remove_keyboard(),
        )
        return

    if data == "confirm:remove_yes":
        if path and os.path.exists(path):
            tags = load_tags(path)
            tags.clear()
            tags.save(path, v2_version=3)
            await query.message.reply_text(
                "تمام تگ‌ها، از جمله کاور، حذف شدند. ✅"
            )
            await show_editor(
                update,
                context,
                is_album=context.user_data.get("editing_album_track", False),
            )
        return

    if data == "confirm:remove_no":
        await query.message.reply_text("عملیات لغو شد. هیچ تغییری اعمال نشد.")
        return

    if data == "action:defaults":
        if not path:
            return
        user_id = query.from_user.id
        defaults = get_user_defaults(user_id)
        is_single = not context.user_data.get("editing_album_track", False)
        apply_defaults_to_file(
            path,
            defaults["single"] if is_single else defaults["album"],
            is_single=is_single,
        )
        await query.message.reply_text("✅ تگ‌های پیش‌فرض اعمال شدند.")
        await show_editor(
            update,
            context,
            is_album=context.user_data.get("editing_album_track", False),
        )
        return

    if data == "action:save":
        if not path or not os.path.exists(path):
            return
        tags = get_tags(path)
        main_artists = (
            context.user_data.get("main_artists")
            if context.user_data.get("editing_album_track")
            else None
        )
        suggested = generate_filename(
            tags.get("artist", ""), tags.get("title", ""), main_artists
        )
        filename = context.user_data.get("filename") or suggested
        filename = sanitize_filename(filename)

        preserve_and_save(path)

        output_path = os.path.join(
            context.user_data.get("folder", tempfile.gettempdir()),
            filename + ".mp3",
        )
        shutil.copy2(path, output_path)

        cover = get_cover(output_path)
        try:
            with open(output_path, "rb") as f:
                await query.message.reply_audio(
                    audio=f,
                    filename=filename + ".mp3",
                    title=tags.get("title")
                    if tags.get("title") != "—"
                    else filename,
                    performer=tags.get("artist")
                    if tags.get("artist") != "—"
                    else None,
                    thumbnail=cover if cover else None,
                    caption="تگ‌های آهنگ ذخیره شدند! 🎵",
                )
        except Exception as e:
            print("reply_audio error, falling back to document:", e)
            with open(output_path, "rb") as f:
                await query.message.reply_document(
                    document=f,
                    filename=filename + ".mp3",
                    caption="تگ‌های آهنگ ذخیره شدند! 🎵",
                )
        return


async def show_album_overview(query, context):
    tracks = context.user_data.get("album_tracks", [])
    main_artists = context.user_data.get("main_artists", [])
    text = "💿 **Album Tracks**\n\n"
    text += f"Main Artists: {' , '.join(main_artists) if main_artists else '—'}\n"
    text += f"Total tracks: {len(tracks)}\n\n"
    for t in tracks:
        tags = get_tags(t["path"])
        text += f"{t['index']}. {tags.get('title', '—')} — {tags.get('artist', '—')}\n"

    buttons = []
    for t in tracks:
        buttons.append(
            [
                InlineKeyboardButton(
                    f"✏️ Edit Track {t['index']}",
                    callback_data=f"album:edit:{t['index']}",
                )
            ]
        )
    buttons.append(
        [InlineKeyboardButton("📝 Apply to All Tracks", callback_data="album:all")]
    )
    buttons.append(
        [InlineKeyboardButton("📊 Album Summary", callback_data="album:summary")]
    )
    buttons.append(
        [InlineKeyboardButton("📩 Save All & Send", callback_data="album:save_all")]
    )

    await query.message.reply_text(
        text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode=ParseMode.MARKDOWN
    )


async def send_album_summary(query, context):
    tracks = context.user_data.get("album_tracks", [])
    main_artists = context.user_data.get("main_artists", [])
    all_artists = set()
    total_duration = 0
    lines = []
    for t in tracks:
        tags = get_tags(t["path"])
        try:
            audio = MP3(t["path"])
            dur = int(audio.info.length)
            total_duration += dur
            dur_str = f"{dur // 60}:{dur % 60:02d}"
        except Exception:
            dur_str = "unavailable"
        arts = split_artists(tags.get("artist", ""))
        for a in arts:
            all_artists.add(a)
        lines.append(
            f"{t['index']}. {tags.get('title', '—')} ({dur_str}) — {tags.get('artist', '—')}"
        )

    text = (
        "📊 **Album Summary**\n\n"
        f"Album Artist(s): {' , '.join(main_artists) if main_artists else 'unavailable'}\n"
        f"Number of main album artists: {len(main_artists)}\n"
        f"Number of unique artists across all tracks: {len(all_artists)}\n"
        f"Total number of tracks: {len(tracks)}\n"
        f"Total album duration: {total_duration // 60}:{total_duration % 60:02d}\n\n"
        "Track list:\n" + "\n".join(lines)
    )
    await query.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def save_album_all(query, context):
    tracks = context.user_data.get("album_tracks", [])
    main_artists = context.user_data.get("main_artists", [])
    for t in tracks:
        path = t["path"]
        tags = get_tags(path)
        filename = generate_filename(
            tags.get("artist", ""), tags.get("title", ""), main_artists
        )
        preserve_and_save(path)
        output_path = os.path.join(
            context.user_data["album_folder"], filename + ".mp3"
        )
        shutil.copy2(path, output_path)
        cover = get_cover(output_path)
        try:
            with open(output_path, "rb") as f:
                await query.message.reply_audio(
                    audio=f,
                    filename=filename + ".mp3",
                    title=tags.get("title")
                    if tags.get("title") != "—"
                    else filename,
                    performer=tags.get("artist")
                    if tags.get("artist") != "—"
                    else None,
                    thumbnail=cover if cover else None,
                )
        except Exception:
            with open(output_path, "rb") as f:
                await query.message.reply_document(
                    document=f, filename=filename + ".mp3"
                )
    # از حالت آلبوم خارج شو
    context.user_data["album_mode"] = False
    context.user_data["editing_album_track"] = False
    context.user_data["album_tracks"] = []
    await query.message.reply_text(
        "✅ همه ترک‌های آلبوم ارسال شدند.\nاز حالت آلبوم خارج شدی.",
        reply_markup=main_reply_keyboard(),
    )


async def error_handler(update, context):
    print("BOT ERROR:", context.error)


def main():
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable is missing!")

    ensure_data_dir()

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CommandHandler("tag", tag_command))
    app.add_handler(CommandHandler("album", album_command))

    app.add_handler(CallbackQueryHandler(button_handler))

    app.add_handler(
        MessageHandler(
            filters.AUDIO | filters.Document.FileExtension("mp3"), receive_audio
        )
    )
    app.add_handler(
        MessageHandler(filters.PHOTO | filters.Document.IMAGE, receive_picture)
    )
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, receive_text)
    )

    app.add_error_handler(error_handler)

    print("Music Tag Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
