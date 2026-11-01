import os
import re
import json
import shutil
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Any
from io import BytesIO

from PIL import Image

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove,
    BotCommand,
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
from mutagen.id3 import ID3, ID3NoHeaderError, TPUB, APIC
from music_tag import load_file as mt_load

# ==================== تنظیمات ====================
TOKEN = os.getenv("BOT_TOKEN")
MAX_SIZE = 20 * 1024 * 1024
DATA_DIR = Path(os.getenv("DATA_DIR", "data"))
DEFAULTS_FILE = DATA_DIR / "user_defaults.json"
VERSION = "v3.2"

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

# کلیدهای music_tag
MT_KEYS = {
    "title": "title",
    "artist": "artist",
    "album": "album",
    "year": "year",
    "track": "tracknumber",
    "genre": "genre",
    "albumartist": "albumartist",
    "composer": "composer",
    "disc": "discnumber",
    "publisher": "publisher",
    "lyrics": "lyrics",
    "comment": "comment",
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
    tmp = DEFAULTS_FILE.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    if DEFAULTS_FILE.exists():
        backup = DEFAULTS_FILE.with_suffix(".json.bak")
        shutil.copy2(DEFAULTS_FILE, backup)
    os.replace(tmp, DEFAULTS_FILE)


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


# ==================== کار با تگ‌ها (با music_tag) ====================
def _read_id3(path: str) -> ID3:
    """Load ID3 tags, including files that do not have an ID3 header yet."""
    try:
        return ID3(path)
    except ID3NoHeaderError:
        return ID3()


def get_publisher(path: str) -> str:
    try:
        tags = _read_id3(path)
        values = tags.getall("TPUB")
        if values:
            value = "; ".join(str(x) for frame in values for x in frame.text).strip()
            return value or "—"
    except Exception as e:
        print("get publisher error:", e)
    return "—"


def set_publisher(path: str, value: Optional[str]):
    tags = _read_id3(path)
    tags.delall("TPUB")
    if value and value != "—":
        tags.add(TPUB(encoding=3, text=[value]))
    tags.save(path, v2_version=3)


def get_tags(path: str) -> Dict[str, str]:
    result = {k: "—" for k in MT_KEYS}
    try:
        f = mt_load(path)
        for our_key, mt_key in MT_KEYS.items():
            if our_key == "publisher":
                continue  # Publisher is handled through the standard ID3 TPUB frame.
            try:
                val = f[mt_key]
                text_value = str(val.value) if val.value is not None else "—"
                result[our_key] = text_value if text_value.strip() else "—"
            except Exception:
                pass
    except Exception as e:
        print("get_tags error:", e)
    result["publisher"] = get_publisher(path)
    return result


def get_cover(path: str) -> Optional[bytes]:
    """Read the first embedded ID3 APIC image without rewriting the MP3."""
    try:
        tags = _read_id3(path)
        pictures = tags.getall("APIC")
        if pictures:
            # Prefer front cover (picture type 3), otherwise use the first image.
            picture = next((x for x in pictures if x.type == 3), pictures[0])
            return bytes(picture.data)
    except Exception as e:
        print("get cover error:", e)
    # Fallback for unusual files supported by music_tag.
    try:
        f = mt_load(path)
        art = f["artwork"]
        if art.value is not None:
            return bytes(art.value.data)
    except Exception:
        pass
    return None


def normalize_cover(image_data: bytes) -> bytes:
    """Convert user-supplied artwork to a broadly compatible JPEG."""
    with Image.open(BytesIO(image_data)) as image:
        image.load()
        if image.mode not in ("RGB", "L"):
            if "A" in image.getbands():
                background = Image.new("RGB", image.size, "white")
                rgba = image.convert("RGBA")
                background.paste(rgba, mask=rgba.getchannel("A"))
                image = background
            else:
                image = image.convert("RGB")
        elif image.mode == "L":
            image = image.convert("RGB")
        output = BytesIO()
        image.save(output, format="JPEG", quality=92, optimize=True)
        return output.getvalue()


def set_cover(path: str, image_data: Optional[bytes]):
    """Replace/remove the MP3 front-cover APIC frame using Mutagen directly."""
    tags = _read_id3(path)
    tags.delall("APIC")
    if image_data:
        jpeg_data = normalize_cover(image_data)
        tags.add(APIC(
            encoding=3,
            mime="image/jpeg",
            type=3,
            desc="Cover",
            data=jpeg_data,
        ))
    tags.save(path, v2_version=3)


def make_telegram_thumbnail(image_data: bytes) -> BytesIO:
    """Create Telegram-compatible JPEG thumbnail (small dimensions and size)."""
    with Image.open(BytesIO(image_data)) as source:
        image = source.convert("RGB")
        image.thumbnail((320, 320))
        quality = 82
        while True:
            output = BytesIO()
            image.save(output, format="JPEG", quality=quality, optimize=True)
            if output.tell() <= 190 * 1024:
                break
            if quality > 50:
                quality -= 10
            else:
                image.thumbnail((240, 240))
                quality = 55
                # A 240px JPEG should comfortably fit Telegram's thumbnail limit.
                output = BytesIO()
                image.save(output, format="JPEG", quality=quality, optimize=True)
                break
        output.seek(0)
        output.name = "cover.jpg"
        return output


def update_album_track_cover(context: ContextTypes.DEFAULT_TYPE, path: str, cover: Optional[bytes]):
    """Keep the album backup in sync when the currently edited track's cover changes."""
    if not context.user_data.get("editing_album_track"):
        return
    for track in context.user_data.get("album_tracks", []):
        if os.path.abspath(track.get("path", "")) == os.path.abspath(path):
            track["original_cover"] = cover
            break


def set_tag(path: str, key: str, value: str):
    try:
        if key == "publisher":
            set_publisher(path, value)
            return
        f = mt_load(path)
        mt_key = MT_KEYS.get(key, key)
        if value and value != "—":
            f[mt_key] = value
        else:
            try:
                f.remove_tag(mt_key)
            except Exception:
                pass
        f.save()
    except Exception as e:
        print("set_tag error:", e)


def delete_tag(path: str, key: str):
    try:
        if key == "publisher":
            set_publisher(path, None)
            return
        f = mt_load(path)
        mt_key = MT_KEYS.get(key, key)
        try:
            f.remove_tag(mt_key)
        except Exception:
            pass
        f.save()
    except Exception as e:
        print("delete_tag error:", e)


def preserve_and_save(path: str):
    # Tag writes are committed by set_tag/set_cover. Avoid an unnecessary
    # whole-file music_tag rewrite at export time, which can alter artwork.
    if not os.path.isfile(path):
        raise FileNotFoundError(path)


def restore_cover_from_backup(path: str, cover_data: bytes, mime: str = "image/jpeg"):
    if not cover_data:
        return
    try:
        set_cover(path, cover_data)
    except Exception as e:
        print("restore cover error:", e)


def attach_cover_with_ffmpeg(input_path: str, cover_data: bytes, output_path: str) -> bool:
    """Copy the MP3 unchanged, then write its embedded ID3 APIC cover."""
    try:
        shutil.copy2(input_path, output_path)
        if cover_data:
            set_cover(output_path, cover_data)
            return True
        # No cover requested: ensure no stale APIC image remains.
        set_cover(output_path, None)
        return False
    except Exception as e:
        print("cover export error:", e)
        try:
            if not os.path.exists(output_path):
                shutil.copy2(input_path, output_path)
        except Exception:
            pass
        return False


def remove_all_tags(path: str):
    """Remove standard text tags, Publisher and embedded artwork."""
    f = mt_load(path)
    for key, mt_key in MT_KEYS.items():
        if key == "publisher":
            continue
        try:
            f.remove_tag(mt_key)
        except Exception:
            pass
    try:
        f.save()
    except Exception as e:
        print("remove text tags error:", e)
    tags = _read_id3(path)
    tags.delall("TPUB")
    tags.delall("APIC")
    tags.save(path, v2_version=3)

def sanitize_filename(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name or "music"


def split_artists(artist_str: str) -> List[str]:
    if not artist_str or artist_str == "—":
        return []
    # & و , و + و / و feat و ft را جدا می‌کند
    parts = re.split(
        r"\s*[&,+/]\s*|\s+feat\.?\s+|\s+ft\.?\s+|\s+x\s+",
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
    for key, value in defaults.items():
        if key == "albumartist" and is_single:
            continue
        set_tag(path, key, value)
    if is_single:
        set_tag(path, "album", defaults.get("album", "Single Track"))
        # در حالت تک‌آهنگ، Album Artist را حذف کن
        delete_tag(path, "albumartist")


# ==================== دکمه‌ها ====================
def main_reply_keyboard() -> ReplyKeyboardMarkup:
    """دکمه‌های پایین صفحه که همیشه دیده می‌شوند"""
    keyboard = [
        [KeyboardButton("🏠 شروع")],
        [KeyboardButton("🎧 حالت آهنگ تکی"), KeyboardButton("💿 حالت آلبوم")],
        [KeyboardButton("📚 حالت آرشیو")],
        [KeyboardButton("⚙️ تگ‌های پیش‌فرض")],
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
def editor_text(path: str, filename: str = "—") -> str:
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
        f"📁 File Name: {filename}\n"
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

    filename = context.user_data.get("filename", "—")
    info = editor_text(path, filename)
    message = (
        update.callback_query.message if update.callback_query else update.message
    )

    # همیشه از کاور ذخیره‌شده در حافظه استفاده کن (نه از فایل)
    cover_data = context.user_data.get("original_cover")
    if not cover_data:
        # فقط اگر حافظه خالی بود از فایل بخون
        cover_data = get_cover(path)
        if cover_data:
            context.user_data["original_cover"] = cover_data

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
    context.user_data["single_mode"] = False

    await update.message.reply_text(
        "سلام! 🎵\n\n"
        "به **Music Tag Editor** خوش اومدی.\n\n"
        "• یک فایل MP3 بفرست تا ویرایش تگ‌ها شروع بشه.\n"
        "• از دکمه‌های پایین صفحه هم می‌تونی استفاده کنی.",
        reply_markup=main_reply_keyboard(),
        parse_mode=ParseMode.MARKDOWN,
    )


async def single_mode_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Switch explicitly to single-track editing mode."""
    context.user_data["album_mode"] = False
    context.user_data["editing_album_track"] = False
    context.user_data["single_mode"] = True
    context.user_data["waiting"] = None
    context.user_data["waiting_mode"] = None
    await update.message.reply_text(
        "🎧 حالت آهنگ تکی فعال شد.\n\nحالا یک فایل MP3 بفرست تا تگ‌های همان آهنگ را ویرایش کنی.",
        reply_markup=main_reply_keyboard(),
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

    path = context.user_data.get("path")
    if path and os.path.exists(path):
        await update.message.reply_text(
            "✅ عملیات لغو شد.\nبرگشتی به منوی ویرایش.",
            reply_markup=main_reply_keyboard(),
        )
        # یک آپدیت ساختگی برای show_editor
        class FakeUpdate:
            def __init__(self, message):
                self.message = message
                self.callback_query = None
        await show_editor(FakeUpdate(update.message), context)
    else:
        await update.message.reply_text(
            "✅ عملیات لغو شد.\n"
            "از حالت آلبوم هم خارج شدی.\n"
            "حالا می‌تونی آهنگ تکی بفرستی یا از دکمه‌های پایین استفاده کنی.",
            reply_markup=main_reply_keyboard(),
        )


async def version_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"نسخه فعلی ربات: **{VERSION}**", parse_mode=ParseMode.MARKDOWN)


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


# ==================== آرشیو موسیقی (نسخه پایه قابل استفاده) ====================
def archive_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📥 افزودن منبع A", callback_data="arch:start_a")],
        [InlineKeyboardButton("📥 افزودن منبع B", callback_data="arch:start_b")],
        [InlineKeyboardButton("🔎 تحلیل و ساخت صف", callback_data="arch:analyze")],
        [InlineKeyboardButton("📋 نمایش صف و آمار", callback_data="arch:list")],
        [InlineKeyboardButton("❌ بستن آرشیو", callback_data="arch:close")],
    ])

async def archive_start(update, context):
    context.user_data["archive"] = {"phase": "menu", "sources": {"source_a": [], "source_b": []}, "queue": [], "cursor": 0}
    context.user_data["album_mode"] = False
    context.user_data["waiting"] = None
    await update.message.reply_text("📚 آرشیو موسیقی\n\nابتدا منبع A و سپس منبع B را اضافه کن. فایل‌ها فعلاً فقط به‌صورت ارجاع تلگرامی ثبت می‌شوند؛ برای باز کردن هر ترک دانلود می‌شود. هر دو منبع را می‌توانی جداگانه تکمیل کنی.", reply_markup=archive_keyboard())

def archive_norm(name):
    import unicodedata
    value = unicodedata.normalize("NFKC", Path(name).stem).casefold()
    value = value.replace("ي", "ی").replace("ك", "ک")
    value = re.sub(r"[^\w\s]", " ", value)
    return " ".join(value.split())

async def archive_show_list(query, context):
    a = context.user_data.get("archive") or {}
    queue = a.get("queue", [])
    confirmed = sum(1 for x in queue if x.get("status") == "confirmed")
    skipped = sum(1 for x in queue if x.get("status") == "skipped")
    pending = sum(1 for x in queue if x.get("status") == "pending")
    lines = [f"📚 صف آرشیو: {len(queue)} ترک", f"تأییدشده: {confirmed} | ردشده: {skipped} | باقی‌مانده: {pending}", ""]
    for i, item in enumerate(queue[:40], 1):
        mark = "✅" if item.get("status")=="confirmed" else ("⏭" if item.get("status")=="skipped" else "▫️")
        dup = " ⚠️ مشابه" if item.get("duplicate") else ""
        lines.append(f"{mark} {i:03d}. {item['name']}{dup}")
    if len(queue) > 40: lines.append(f"… و {len(queue)-40} ترک دیگر")
    rows = []
    if queue:
        rows.append([InlineKeyboardButton("✏️ باز کردن ترک بعدی", callback_data="arch:open_next")])
    rows.append([InlineKeyboardButton("🔙 منوی آرشیو", callback_data="arch:menu")])
    await query.message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(rows))

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

    # Archive intake: keep Telegram file references; download only when a track is opened.
    archive = context.user_data.get("archive")
    if archive and archive.get("phase") in {"source_a", "source_b"}:
        source = archive["phase"]
        bucket = archive["sources"][source]
        bucket.append({"file_id": document.file_id, "file_unique_id": document.file_unique_id,
                       "name": name, "size": document.file_size or 0,
                       "message_id": message.message_id})
        await message.reply_text(
            f"✅ {name} به منبع {'A' if source == 'source_a' else 'B'} اضافه شد.\n"
            f"تعداد این منبع: {len(bucket)}\nفایل بعدی را بفرست یا پایان منبع را بزن.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(
                "پایان منبع A" if source == "source_a" else "پایان منبع B",
                callback_data="arch:finish_a" if source == "source_a" else "arch:finish_b")]])
        )
        return

    # حالت آلبوم
    if context.user_data.get("album_mode"):
        folder = context.user_data["album_folder"]
        idx = len(context.user_data["album_tracks"]) + 1
        path = os.path.join(folder, f"track_{idx:02d}.mp3")
        try:
            tg_file = await context.bot.get_file(document.file_id)
            await tg_file.download_to_drive(path)
            mt_load(path)  # فقط برای اطمینان از معتبر بودن فایل
            MP3(path)
        except Exception as e:
            print("Album download error:", e)
            await message.reply_text("دریافت فایل ناموفق بود.")
            return

        # کاور و زمان اصلی این ترک رو ذخیره کن
        try:
            track_cover = get_cover(path)
        except Exception:
            track_cover = None
        try:
            track_duration = int(MP3(path).info.length)
        except Exception:
            track_duration = None

        context.user_data["album_tracks"].append(
            {
                "path": path,
                "original_name": name,
                "index": idx,
                "original_cover": track_cover,
                "original_duration": track_duration,
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
        mt_load(path)
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

    # کاور و زمان اصلی فایل رو برای همیشه نگه می‌داریم
    try:
        cover_data = get_cover(path)
        context.user_data["original_cover"] = cover_data
    except Exception:
        context.user_data["original_cover"] = None

    try:
        audio_info = MP3(path)
        context.user_data["original_duration"] = int(audio_info.info.length)
    except Exception:
        context.user_data["original_duration"] = None

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

        image_data = normalize_cover(image_data)
        set_cover(path, image_data)
        # کاور جدید رو هم به عنوان original ذخیره کن
        context.user_data["original_cover"] = image_data
        update_album_track_cover(context, path, image_data)
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
    if text in {"🏠 شروع", "🎵 شروع"}:
        await start(update, context)
        return
    if text in {"🎧 حالت آهنگ تکی", "🎵 حالت آهنگ تکی"}:
        await single_mode_command(update, context)
        return
    if text in {"📚 حالت آرشیو", "📚 آرشیو موسیقی"}:
        await archive_start(update, context)
        return
    if text in {"⚙️ تگ‌های پیش‌فرض", "⚙️ تنظیم تگ‌ها"}:
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

    # Apply-to-all album field: first receive the field name, then its value.
    if waiting == "album_all_field":
        aliases = {"artist":"artist", "album artist":"albumartist", "albumartist":"albumartist",
                   "album":"album", "genre":"genre", "composer":"composer",
                   "publisher":"publisher", "year":"year", "هنرمند":"artist",
                   "هنرمند آلبوم":"albumartist", "آلبوم":"album", "ژانر":"genre",
                   "آهنگساز":"composer", "ناشر":"publisher", "سال":"year"}
        key = aliases.get(value.casefold())
        if key not in {"artist", "albumartist", "album", "genre", "composer", "publisher", "year"}:
            await update.message.reply_text("فقط یکی از این فیلدها را بفرست: artist, albumartist, album, genre, composer, publisher, year")
            return
        context.user_data["album_all_key"] = key
        context.user_data["waiting"] = "album_all_value"
        await update.message.reply_text(f"مقدار جدید برای {FIELDS.get(key, key)} را بفرست:")
        return

    if waiting == "album_all_value":
        key = context.user_data.get("album_all_key")
        tracks = context.user_data.get("album_tracks", [])
        if key not in {"artist", "albumartist", "album", "genre", "composer", "publisher", "year"}:
            context.user_data["waiting"] = None
            await update.message.reply_text("فیلد انتخاب‌شده معتبر نیست.")
            return
        backups = []
        try:
            for item in tracks:
                track_path = item.get("path")
                if not track_path or not os.path.isfile(track_path):
                    raise RuntimeError("یکی از فایل‌های آلبوم پیدا نشد.")
                backup_path = track_path + ".v3bak"
                shutil.copy2(track_path, backup_path)
                backups.append((track_path, backup_path))
                set_tag(track_path, key, value)
            for _, backup_path in backups:
                if os.path.exists(backup_path): os.remove(backup_path)
        except Exception as exc:
            for track_path, backup_path in reversed(backups):
                if os.path.exists(backup_path):
                    shutil.copy2(backup_path, track_path)
                    os.remove(backup_path)
            context.user_data["waiting"] = None
            await update.message.reply_text(f"اعمال گروهی انجام نشد و تغییرات برگشت داده شد: {exc}")
            return
        context.user_data["waiting"] = None
        context.user_data.pop("album_all_key", None)
        await update.message.reply_text(f"مقدار «{FIELDS.get(key, key)}» روی {len(tracks)} ترک اعمال شد. ✅")
        await show_album_overview(update, context)
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
    elif waiting in MT_KEYS:
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

    if data.startswith("arch:"):
        a = context.user_data.get("archive")
        if data == "arch:close":
            context.user_data.pop("archive", None)
            await query.message.edit_text("آرشیو بسته شد. فایل‌های قبلی ربات دست‌نخورده‌اند.")
            return
        if data == "arch:menu":
            if a: a["phase"] = "menu"
            await query.message.edit_text("📚 منوی آرشیو", reply_markup=archive_keyboard())
            return
        if not a:
            await query.message.reply_text("آرشیوی فعال نیست؛ از دکمه آرشیو موسیقی شروع کن.")
            return
        if data == "arch:start_a":
            a["phase"] = "source_a"
            await query.message.reply_text("📥 فایل‌های MP3 منبع A را بفرست. برای پایان، دکمه زیر را بزن.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("پایان منبع A", callback_data="arch:finish_a")]]))
            return
        if data == "arch:start_b":
            a["phase"] = "source_b"
            await query.message.reply_text("📥 فایل‌های MP3 منبع B را بفرست. برای پایان، دکمه زیر را بزن.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("پایان منبع B", callback_data="arch:finish_b")]]))
            return
        if data == "arch:finish_a":
            a["phase"] = "menu"
            await query.message.reply_text(f"منبع A ثبت شد: {len(a['sources']['source_a'])} فایل", reply_markup=archive_keyboard())
            return
        if data == "arch:finish_b":
            a["phase"] = "menu"
            await query.message.reply_text(f"منبع B ثبت شد: {len(a['sources']['source_b'])} فایل", reply_markup=archive_keyboard())
            return
        if data == "arch:analyze":
            merged = []
            seen = {}
            for src in ("source_a", "source_b"):
                for entry in sorted(a["sources"][src], key=lambda x: x["message_id"]):
                    key = archive_norm(entry["name"])
                    duplicate = bool(key and key in seen)
                    item = dict(entry, source=src, duplicate=duplicate, status="pending")
                    if duplicate:
                        item["duplicate_of"] = seen[key]
                    elif key:
                        seen[key] = len(merged) + 1
                    merged.append(item)
            a["queue"] = merged
            a["phase"] = "queue"
            await query.message.reply_text(f"تحلیل نام فایل‌ها تمام شد. {len(merged)} ترک در صف قرار گرفت؛ موارد مشابه فقط علامت‌گذاری شده‌اند و حذف نشده‌اند.", reply_markup=archive_keyboard())
            return
        if data == "arch:list":
            await archive_show_list(query, context)
            return
        if data == "arch:open_next":
            queue = a.get("queue", [])
            idx = next((i for i,x in enumerate(queue) if x.get("status")=="pending"), None)
            if idx is None:
                await query.message.reply_text("ترک در انتظار دیگری وجود ندارد.")
                return
            item = queue[idx]
            try:
                folder = tempfile.mkdtemp(prefix="archive_track_")
                dest = os.path.join(folder, "original.mp3")
                tg_file = await context.bot.get_file(item["file_id"])
                await tg_file.download_to_drive(dest)
                mt_load(dest); MP3(dest)
            except Exception as exc:
                if 'folder' in locals(): shutil.rmtree(folder, ignore_errors=True)
                item["status"] = "failed"
                await query.message.reply_text(f"دریافت ترک ناموفق بود: {exc}")
                return
            context.user_data.update({"path":dest,"folder":folder,"filename":Path(item["name"]).stem,"archive_current":idx,"archive_folder":folder,"album_mode":False,"editing_album_track":False})
            await query.message.reply_text(f"ترک {idx+1:03d} از {len(queue)} باز شد. پس از ویرایش، Save را بزن و از منوی آرشیو ترک بعدی را باز کن.")
            await show_editor(update, context)
            return
        return

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
        context.user_data["original_cover"] = track.get("original_cover")
        context.user_data["original_duration"] = track.get("original_duration")
        await show_editor(update, context, is_album=True)
        return

    if data == "album:all":
        context.user_data["waiting"] = "album_all_field"
        await query.message.reply_text(
            "کدام فیلد را می‌خواهی روی **همه ترک‌ها** اعمال کنی؟\n"
            "یکی از این‌ها را بنویس: title, artist, album, year, track, genre, albumartist, composer, disc, publisher, comment"
        )
        return

    if data == "album:defaults":
        tracks = context.user_data.get("album_tracks", [])
        if not tracks:
            await query.message.reply_text("ترکی وجود ندارد.")
            return
        user_id = query.from_user.id
        defaults = get_user_defaults(user_id)["album"]
        for t in tracks:
            p = t["path"]
            apply_defaults_to_file(p, defaults, is_single=False)
            # کاور اصلی همین ترک رو برگردون
            if t.get("original_cover"):
                restore_cover_from_backup(p, t["original_cover"])
        await query.message.reply_text("✅ تگ‌های پیش‌فرض آلبوم روی همه ترک‌ها اعمال شد.")
        await show_album_overview(query, context)
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
        await query.message.reply_text("لغو شد. برگشتی به منوی ویرایش.")
        await show_editor(
            update,
            context,
            is_album=context.user_data.get("editing_album_track", False),
        )
        return

    # ---------- کارهای اصلی ----------
    if data == "action:showlyrics":
        if not path:
            return
        tags = get_tags(path)
        lyrics = tags.get("lyrics", "—")
        await query.message.reply_text(
            lyrics if lyrics != "—" else "متنی ثبت نشده."
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
            try:
                remove_all_tags(path)
            except Exception as e:
                print("remove all error:", e)
            context.user_data["original_cover"] = None
            update_album_track_cover(context, path, None)
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
        await show_editor(
            update,
            context,
            is_album=context.user_data.get("editing_album_track", False),
        )
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

        # کاور اصلی رو دوباره با زور برگردون
        if context.user_data.get("original_cover"):
            restore_cover_from_backup(path, context.user_data["original_cover"])

        # بعد از اعمال پیش‌فرض‌ها، اسم فایل را هم از روی آرتیست و عنوان بساز
        tags = get_tags(path)
        new_filename = generate_filename(
            tags.get("artist", ""), tags.get("title", "")
        )
        context.user_data["filename"] = new_filename

        await query.message.reply_text(
            f"✅ تگ‌های پیش‌فرض اعمال شدند.\n"
            f"اسم فایل هم شد: `{new_filename}`",
            parse_mode=ParseMode.MARKDOWN,
        )
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

        # با ffmpeg کاور رو تمیز بچسبون
        cover = context.user_data.get("original_cover")
        if cover:
            attach_cover_with_ffmpeg(path, cover, output_path)
        else:
            shutil.copy2(path, output_path)

        duration = context.user_data.get("original_duration")

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
                    duration=duration,
                    thumbnail=make_telegram_thumbnail(cover) if cover else None,
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
        archive = context.user_data.get("archive")
        archive_idx = context.user_data.get("archive_current")
        if archive and isinstance(archive_idx, int) and archive_idx < len(archive.get("queue", [])):
            archive["queue"][archive_idx]["status"] = "confirmed"
        return


async def show_album_overview(query, context):
    tracks = context.user_data.get("album_tracks", [])
    main_artists = context.user_data.get("main_artists", [])
    text = "💿 **Album Tracks**\n\n"
    text += f"Main Artists: {' , '.join(main_artists) if main_artists else '—'}\n"
    text += f"Total tracks: {len(tracks)}\n\n"
    for t in tracks:
        tags = get_tags(t["path"])
        # اسم فایل رو از روی آرتیست و عنوان بساز
        fname = generate_filename(
            tags.get("artist", ""), tags.get("title", ""), main_artists
        )
        text += (
            f"{t['index']}. {tags.get('title', '—')} — {tags.get('artist', '—')}\n"
            f"   📁 {fname}\n"
        )

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
        [InlineKeyboardButton("📌 Apply Default Tags (Album)", callback_data="album:defaults")]
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
        cover = t.get("original_cover")
        if cover:
            attach_cover_with_ffmpeg(path, cover, output_path)
        else:
            shutil.copy2(path, output_path)
        duration = t.get("original_duration")
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
                    duration=duration,
                    thumbnail=make_telegram_thumbnail(cover) if cover else None,
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

    async def post_init(application):
        # Commands shown in Telegram's built-in menu button.
        await application.bot.set_my_commands([
            BotCommand("start", "🏠 شروع و نمایش منوی اصلی"),
            BotCommand("single", "🎧 فعال‌کردن حالت آهنگ تکی"),
            BotCommand("album", "💿 شروع حالت آلبوم"),
            BotCommand("archive", "📚 بازکردن آرشیو موسیقی"),
            BotCommand("tag", "⚙️ تنظیم تگ‌های پیش‌فرض"),
            BotCommand("version", "ℹ️ نمایش نسخهٔ ربات"),
            BotCommand("cancel", "❌ لغو عملیات فعلی"),
        ])

    app = Application.builder().token(TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("single", single_mode_command))
    app.add_handler(CommandHandler("archive", archive_start))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CommandHandler("tag", tag_command))
    app.add_handler(CommandHandler("album", album_command))
    app.add_handler(CommandHandler("version", version_command))

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
