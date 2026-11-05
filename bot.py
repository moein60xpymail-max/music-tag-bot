import os
import asyncio
import time
import re
import json
import shutil
import tempfile
from pathlib import Path
from collections import Counter
from dupes import classify as classify_duplicate, classify_detail
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
    CopyTextButton,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
    TypeHandler,
    ApplicationHandlerStop,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest, TimedOut, NetworkError, TelegramError
from telegram.request import HTTPXRequest

from mutagen.mp3 import MP3
from mutagen.id3 import ID3, ID3NoHeaderError, TPUB, APIC
from music_tag import load_file as mt_load

# ==================== پایداری ارتباط و ارسال پیام ====================
def install_telegram_resilience():
    """Retry malformed-entity messages as plain text instead of losing them."""
    from telegram import Bot
    for method_name in ("send_message", "send_photo", "send_audio", "send_document", "edit_message_text", "edit_message_caption"):
        original = getattr(Bot, method_name)
        async def resilient(self, *args, __original=original, __name=method_name, **kwargs):
            try:
                return await __original(self, *args, **kwargs)
            except BadRequest as exc:
                msg = str(exc).lower()
                if "parse entities" not in msg and "can't find end of the entity" not in msg:
                    raise
                retry_kwargs = dict(kwargs)
                retry_kwargs["parse_mode"] = None
                retry_kwargs.pop("entities", None)
                print(f"Telegram formatting fallback: {__name}: {exc}")
                return await __original(self, *args, **retry_kwargs)
        setattr(Bot, method_name, resilient)


async def download_to_drive_retry(tg_file, custom_path, attempts=3):
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            return await tg_file.download_to_drive(custom_path)
        except (TimedOut, NetworkError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt == attempts:
                raise
            print(f"Telegram download retry {attempt}/{attempts-1}: {exc!r}")
            await asyncio.sleep(attempt * 1.5)
    raise last_error


async def download_as_bytearray_retry(tg_file, attempts=3):
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            return await tg_file.download_as_bytearray()
        except (TimedOut, NetworkError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt == attempts:
                raise
            print(f"Telegram byte download retry {attempt}/{attempts-1}: {exc!r}")
            await asyncio.sleep(attempt * 1.5)
    raise last_error


# ==================== تنظیمات ====================
TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", "0") or 0)
def _parse_admin_ids(raw: str):
    ids = set()
    for part in (raw or "").replace(" ", "").split(","):
        if not part:
            continue
        try:
            ids.add(int(part))
        except ValueError:
            pass
    return ids
ADMIN_IDS = _parse_admin_ids(os.getenv("ADMIN_IDS", ""))
MAX_SIZE = 20 * 1024 * 1024
DATA_DIR = Path(os.getenv("DATA_DIR", "data"))
DEFAULTS_FILE = DATA_DIR / "user_defaults.json"
VERSION = "v5.2.1"
VERSION_CHANGES = [
    "اصلاح نمایش دقیق معیارهای تشابه در Needs Review (از نتیجه واقعی classify_detail)",
    "محاسبه مجدد معیارها هنگام نمایش اگر در داده صف ذخیره نشده باشند",
    "حفظ کامل قابلیت‌های نسخه ۵.۲.۰",
]
UNRELEASED_COVER_FILE = DATA_DIR / "unreleased_track_cover.jpg"

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

# Values used when mode is "apply". Modes stored in defaults["_modes"][field] = apply|keep|remove
DEFAULT_SINGLE = {
    "title": "",
    "artist": "",
    "albumartist": "",
    "album": "Single Track",
    "genre": "",
    "year": "2026",
    "composer": "@nistplay",
    "publisher": "eM Nine Collection",
    "comment": "",
    "lyrics": "",
    "disc": "1",
    "track": "0",
}

DEFAULT_ALBUM = {
    "title": "",
    "artist": "",
    "albumartist": "",
    "album": "",
    "genre": "",
    "year": "2026",
    "composer": "@nistplay",
    "publisher": "eM Nine Collection",
    "comment": "",
    "lyrics": "",
    "disc": "1",
    "track": "",
}

DEFAULT_FIELD_ORDER = [
    "title", "artist", "albumartist", "album", "genre", "year",
    "composer", "publisher", "comment", "lyrics", "disc", "track",
]


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
    else:
        changed = False
        for mode, template in (("single", DEFAULT_SINGLE), ("album", DEFAULT_ALBUM)):
            if mode not in all_data[uid]:
                all_data[uid][mode] = template.copy()
                changed = True
            current = all_data[uid][mode]
            for key, default_value in template.items():
                if key not in current:
                    current[key] = default_value
                    changed = True
        if changed:
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
    template = DEFAULT_SINGLE if mode == "single" else DEFAULT_ALBUM
    current = all_data[uid].setdefault(mode, template.copy())
    for default_key, default_value in template.items():
        current.setdefault(default_key, default_value)
    current[key] = value
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


def _default_mode(defaults: Dict[str, Any], key: str) -> str:
    """Return apply|keep|remove for a field. Legacy _disabled maps to keep."""
    if not isinstance(defaults, dict):
        return "apply"
    modes = defaults.get("_modes") or {}
    if key in modes:
        mode = str(modes[key]).lower()
        if mode in ("apply", "keep", "remove"):
            return mode
    disabled = set(defaults.get("_disabled", []))
    if key in disabled:
        return "keep"
    return "apply"


def apply_defaults_to_file(
    path: str, defaults: Dict[str, Any], is_single: bool = True
):
    if not isinstance(defaults, dict):
        return
    for key in DEFAULT_FIELD_ORDER:
        if key not in defaults and key not in (defaults.get("_modes") or {}):
            # still honor remove/keep if only mode is set
            modes = defaults.get("_modes") or {}
            if key not in modes:
                continue
        mode = _default_mode(defaults, key)
        if mode == "keep":
            continue
        if mode == "remove":
            try:
                delete_tag(path, key)
            except Exception as exc:
                print("apply_defaults remove error", key, exc)
            continue
        # apply
        value = defaults.get(key, "")
        if value in (None, ""):
            continue
        if key == "albumartist" and is_single:
            continue
        set_tag(path, key, value)
    if is_single:
        # legacy behavior: single-track clears albumartist unless explicitly applied
        if _default_mode(defaults, "albumartist") == "apply" and defaults.get("albumartist") not in (None, ""):
            set_tag(path, "albumartist", defaults.get("albumartist"))
        elif _default_mode(defaults, "albumartist") != "keep":
            try:
                delete_tag(path, "albumartist")
            except Exception:
                pass


# ==================== دکمه‌ها ====================
def main_reply_keyboard() -> ReplyKeyboardMarkup:
    """دکمه‌های پایین صفحه که همیشه دیده می‌شوند"""
    keyboard = [
        [KeyboardButton("🏠 شروع")],
        [KeyboardButton("🎧 حالت آهنگ تکی"), KeyboardButton("💿 حالت آلبوم")],
        [KeyboardButton("📚 حالت آرشیو")],
        [KeyboardButton("📊 خلاصه آهنگ‌ها")],
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
    buttons.append(
        [InlineKeyboardButton("🏷️ Unreleased Track", callback_data="action:unreleased")]
    )
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


def defaults_fields_keyboard(mode: str, current: Optional[Dict[str, Any]] = None) -> InlineKeyboardMarkup:
    current = current or {}
    buttons = []
    for k in DEFAULT_FIELD_ORDER:
        mode_state = _default_mode(current, k)
        if mode_state == "remove":
            state = "🗑 Remove"
        elif mode_state == "keep":
            state = "⏸ Keep"
        else:
            val = current.get(k, "")
            state = f"✅ Apply: {val}" if val not in (None, "") else "✅ Apply"
            if len(state) > 28:
                state = state[:25] + "…"
        label = FIELDS.get(k, k.title())
        buttons.append([
            InlineKeyboardButton(f"{label} · {state}", callback_data=f"defaulttoggle:{mode}:{k}"),
            InlineKeyboardButton("✏️", callback_data=f"setdefault:{mode}:{k}"),
        ])
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
    changes = "\n".join(f"• {item}" for item in VERSION_CHANGES)
    await update.message.reply_text(
        f"📦 **نسخه فعلی ربات شما: {VERSION}**\n\n"
        f"🆕 **تغییرات این نسخه:**\n{changes}",
        parse_mode=ParseMode.MARKDOWN,
    )


async def setchannel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Legacy alias → channel 1."""
    await setchannel1_command(update, context)


async def setchannel1_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_owner(user.id if user else None):
        await update.message.reply_text("🔒 فقط مالک ربات می‌تواند کانال مقصد Auto Forward را تنظیم کند.")
        return
    context.user_data["waiting"] = "set_auto_forward_channel:1"
    await update.message.reply_text(
        "📢 تنظیم کانال اصلی (Main Channel)\n\n"
        "آیدی عددی یا یوزرنیم کانال را بفرست (مثلاً `@MyChannel` یا `-100xxxxxxxxxx`).\n"
        "ربات باید ادمین کانال باشد و اجازه ارسال پیام داشته باشد.\n"
        "هیچ پیام آزمایشی در کانال ارسال نمی‌شود."
    )


async def setchannel2_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_owner(user.id if user else None):
        await update.message.reply_text("🔒 فقط مالک ربات می‌تواند کانال مقصد Auto Forward را تنظیم کند.")
        return
    context.user_data["waiting"] = "set_auto_forward_channel:2"
    await update.message.reply_text(
        "📢 تنظیم کانال Full Archive\n\n"
        "آیدی عددی یا یوزرنیم کانال را بفرست (مثلاً `@MyChannel` یا `-100xxxxxxxxxx`).\n"
        "ربات باید ادمین کانال باشد و اجازه ارسال پیام داشته باشد.\n"
        "هیچ پیام آزمایشی در کانال ارسال نمی‌شود."
    )


async def channelstatus_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ch1 = get_af_channel(context, "1")
    ch2 = get_af_channel(context, "2")
    lines = ["📢 وضعیت کانال‌های Auto Forward\n"]
    if ch1:
        lines.append(f"۱) Main: `{ch1['chat_id']}` — {ch1.get('title') or '—'}")
    else:
        lines.append("۱) Main: تنظیم نشده (/setchannel1)")
    if ch2:
        lines.append(f"۲) Full Archive: `{ch2['chat_id']}` — {ch2.get('title') or '—'}")
    else:
        lines.append("۲) Full Archive: تنظیم نشده (/setchannel2)")
    lines.append("\nاین تنظیمات فقط در حافظه‌اند و با ریستارت پاک می‌شوند.")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def tag_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⚙️ **Tag Defaults Configuration**\n\n"
        "Choose what you want to configure.\n"
        "Defaults are saved permanently and only applied when you click **Apply Default Tags**.",
        reply_markup=tag_config_keyboard(),
        parse_mode=ParseMode.MARKDOWN,
    )


async def unreleased_cover_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    exists = UNRELEASED_COVER_FILE.exists()
    status = "✅ کاور پیش‌فرض ثبت شده است." if exists else "❌ هنوز کاوری ثبت نشده است."
    await update.message.reply_text(
        f"🖼️ مدیریت کاور Unreleased Track\n\n{status}\n"
        "برای ثبت یا جایگزینی کاور، دکمه زیر را بزن و تصویر را ارسال کن.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📤 ارسال / تغییر کاور پیش‌فرض", callback_data="unrel:upload")],
            [InlineKeyboardButton("🗑 حذف کاور پیش‌فرض", callback_data="unrel:delete")],
        ]),
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
    context.user_data.pop("album_summary_draft", None)
    context.user_data.pop("album_summary_confirmed", None)
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
        [InlineKeyboardButton("🔎 بررسی آهنگ‌های منابع", callback_data="arch:review_menu")],
        [InlineKeyboardButton("📥 ورودی بررسی منابع", callback_data="arch:input_menu")],
        [InlineKeyboardButton("❌ بستن آرشیو", callback_data="arch:close")],
    ])

def archive_source_keyboard(source):
    label = "A" if source == "source_a" else "B"
    return InlineKeyboardMarkup([[InlineKeyboardButton(f"پایان منبع {label}", callback_data=f"arch:finish_{label.lower()}")]])

def archive_track_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ این آهنگ تموم شد", callback_data="arch:finish_current")],
        [InlineKeyboardButton("⏭ آهنگ بعدی", callback_data="arch:next")],
        [InlineKeyboardButton("⏮ آهنگ قبلی", callback_data="arch:previous")],
        [InlineKeyboardButton("⏩ رد کردن این آهنگ بدون ارسال فایل", callback_data="arch:skip_current")],
    ])


def archive_report_text(queue):
    total = len(queue)
    confirmed = sum(1 for x in queue if x.get("status") == "confirmed")
    skipped = sum(1 for x in queue if x.get("status") == "skipped")
    failed = sum(1 for x in queue if x.get("status") == "failed")
    pending = sum(1 for x in queue if x.get("status") in (None, "pending", "in_progress"))
    review = sum(1 for x in queue if x.get("review"))
    percent = (confirmed / total * 100) if total else 0
    return (
        "📊 گزارش آرشیو\n\n"
        f"🎵 تعداد کل آهنگ‌ها: {total}\n"
        f"✅ ویرایش‌شده و تأییدشده: {confirmed}\n"
        f"⏭ ردشده: {skipped}\n"
        f"❌ ناموفق: {failed}\n"
        f"❗ نیازمند بررسی: {review}\n"
        f"▫️ باقی‌مانده: {pending}\n\n"
        f"📈 درصد تأیید نهایی: {percent:.1f}%"
    )


def track_display_name(item) -> str:
    """Human-readable track name for reports (title, else file stem)."""
    title = (item.get("title") or "").strip()
    if title and title != "—":
        return title
    name = item.get("name") or item.get("file_name") or item.get("original_name") or ""
    stem = Path(str(name)).stem if name else ""
    return stem or "بدون نام"


def format_review_warning(item, queue=None) -> str:
    """Detailed Needs Review warning with matched tracks and exact criteria."""
    if not item.get("review"):
        return ""
    matches = list(item.get("review_matches") or [])
    # Legacy / incomplete: only possible_match index
    if not matches and item.get("possible_match") and queue:
        try:
            pm = int(item["possible_match"]) - 1
            if 0 <= pm < len(queue):
                other = queue[pm]
                matches = [{
                    "number": other.get("number") or (pm + 1),
                    "title": track_display_name(other),
                    "criteria": list(item.get("review_criteria") or []),
                    "_queue_index": pm,
                }]
        except (TypeError, ValueError):
            pass
    # Enrich each match: recompute real criteria from queue pair when missing
    if queue:
        enriched = []
        for m in matches:
            crit = list(m.get("criteria") or [])
            other = None
            idx = m.get("_queue_index")
            if idx is None and m.get("number") is not None:
                try:
                    num = int(m["number"])
                    for i, q in enumerate(queue):
                        if int(q.get("number") or (i + 1)) == num:
                            idx = i
                            break
                except (TypeError, ValueError):
                    idx = None
            if idx is None and item.get("possible_match") and len(matches) == 1:
                try:
                    idx = int(item["possible_match"]) - 1
                except (TypeError, ValueError):
                    idx = None
            if isinstance(idx, int) and 0 <= idx < len(queue):
                other = queue[idx]
            if other is not None:
                # Always prefer live comparison result so criteria are never guessed
                _level, labels = classify_detail(other, item)
                if labels:
                    crit = list(labels)
                if not m.get("title"):
                    m = dict(m, title=track_display_name(other))
            enriched.append({
                "number": m.get("number"),
                "title": m.get("title") or (track_display_name(other) if other else "—"),
                "criteria": crit,
            })
        matches = enriched
    current = track_display_name(item)
    if not matches:
        return (
            "\n\n⚠️ این آهنگ نیازمند بررسی است.\n"
            f"🎵 آهنگ فعلی: {current}\n"
            "جزئیات تطبیق در دسترس نیست؛ شباهت را دستی بررسی کن."
        )
    parts = [
        "",
        "⚠️ این آهنگ نیازمند بررسی است.",
        f"🎵 آهنگ فعلی: {current}",
    ]
    for m in matches:
        parts.append("")
        title = m.get("title") or "—"
        if m.get("number"):
            parts.append(f"🔍 آهنگ مشابه: {title} (شماره {m['number']})")
        else:
            parts.append(f"🔍 آهنگ مشابه: {title}")
        crit = m.get("criteria") or []
        if crit:
            parts.append("📌 دلیل تشابه:")
            for c in crit:
                parts.append(f"• {c}")
        else:
            parts.append("📌 دلیل تشابه: جزئیات معیار در دسترس نیست.")
    return "\n" + "\n".join(parts)


async def archive_start(update, context):
    context.user_data["archive"] = {"phase": "menu", "sources": {"source_a": [], "source_b": []}, "queue": [], "cursor": 0}
    context.user_data["album_mode"] = False
    context.user_data["waiting"] = None
    await update.message.reply_text("📚 آرشیو موسیقی\n\nیکی از دو مرحله را انتخاب کن:", reply_markup=archive_keyboard())

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
    pending = sum(1 for x in queue if x.get("status") in (None, "pending", "in_progress"))
    lines = [f"📚 صف آرشیو: {len(queue)} ترک", f"تأییدشده: {confirmed} | ردشده: {skipped} | باقی‌مانده: {pending}", ""]
    for i, item in enumerate(queue[:40], 1):
        mark = "✅" if item.get("status")=="confirmed" else ("⏭" if item.get("status")=="skipped" else "▫️")
        dup = " ⚠️ مشابه" if item.get("duplicate") else ""
        lines.append(f"{mark} {i:03d}. {item['name']}{dup}")
    if len(queue) > 40: lines.append(f"… و {len(queue)-40} ترک دیگر")
    rows = []
    if queue:
        rows.append([InlineKeyboardButton("✏️ باز کردن ترک بعدی", callback_data="arch:open_next")])
        rows.append([InlineKeyboardButton("📊 گزارش آرشیو", callback_data="arch:report")])
    rows.append([InlineKeyboardButton("🔙 منوی آرشیو", callback_data="arch:menu")])
    await query.message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(rows))

# ==================== گزارش خطاهای دریافت فایل ====================
def explain_media_error(exc, stage):
    """Return a safe, user-friendly reason without exposing raw server details."""
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    if stage == "download":
        if any(x in name or x in msg for x in ("timedout", "network", "connection", "timeout", "temporarily", "server")):
            return "خطای ارتباط با تلگرام یا سرور هنگام دانلود؛ ممکن است اتصال قطع شده یا سرور موقتاً پاسخ نداده باشد."
        if any(x in name or x in msg for x in ("badrequest", "file_id", "file is too big", "not found")):
            return "تلگرام نتوانست فایل را با شناسه ارسالی تحویل دهد؛ ممکن است فایل در دسترس نباشد یا لینک دانلود معتبر نباشد."
        return "دانلود از تلگرام کامل نشد؛ احتمال خطای موقت شبکه یا سرور وجود دارد."
    if any(x in msg for x in ("not a mp3", "invalid frame", "header missing", "no mpeg", "file format", "unsupported")) or "mutagenerror" in name:
        return "فایل MP3 معتبر نیست، خراب است یا ساختار صوتی آن قابل‌خواندن نیست."
    if "permission" in name or "permission" in msg or "no space" in msg:
        return "خطای ذخیره‌سازی روی سرور ربات (مجوز دسترسی یا فضای ذخیره‌سازی)."
    return "فایل دانلود شد، اما هنگام بازکردن یا خواندن اطلاعات صوتی خطا رخ داد؛ ممکن است فایل خراب یا کدگذاری آن ناسازگار باشد."


def failed_media_message(label, exc, stage):
    return (f"❌ دریافت/خواندن فایل ناموفق بود.\n"
            f"🎵 فایل: {label}\n"
            f"🔎 علت احتمالی: {explain_media_error(exc, stage)}")

# ==================== داشبورد پیشرفت گروهی v4.6 ====================
PROGRESS_UPDATE_INTERVAL = 3.0
PROGRESS_IDLE_TIMEOUT = 6.0

async def _progress_idle_finish(context, operation, state):
    """Mark intake as quiet after six seconds without a newly received file."""
    try:
        await asyncio.sleep(PROGRESS_IDLE_TIMEOUT)
        current = context.user_data.get("progress_dashboard")
        if current is not state or current.get("operation") != operation:
            return
        current["idle_task"] = None
        class _MessageRef:
            chat_id = current["chat_id"]
        await update_progress_dashboard(_MessageRef(), context, operation, final=True)
    except asyncio.CancelledError:
        return
    except Exception as exc:
        print("Progress idle-finish error:", repr(exc))

async def update_progress_dashboard(message, context, operation, *, outcome=None, track_name=None, final=False, remove=False):
    """Maintain one progress message per intake session; never emit one message per file."""
    state = context.user_data.get("progress_dashboard")
    now = time.monotonic()
    if not state or state.get("operation") != operation:
        state = {"operation": operation, "message_id": None, "chat_id": message.chat_id,
                 "last_update": 0.0, "received": 0, "success": 0, "skipped": 0,
                 "failed": 0, "current": "—", "idle_task": None}
        context.user_data["progress_dashboard"] = state
    if outcome:
        state["received"] += 1
        if outcome == "success": state["success"] += 1
        elif outcome == "skipped": state["skipped"] += 1
        elif outcome == "failed": state["failed"] += 1
    if track_name:
        state["current"] = str(track_name)[:100]
    if remove:
        if state.get("message_id"):
            try:
                await context.bot.delete_message(chat_id=state["chat_id"], message_id=state["message_id"])
            except Exception:
                pass
        context.user_data.pop("progress_dashboard", None)
        return
    # Do not fabricate a percentage: during open-ended uploads Telegram gives no total count.
    title = {"album": "📥 دریافت آهنگ‌های آلبوم", "summary": "📊 دریافت آهنگ‌ها برای خلاصه", "archive_a": "📥 دریافت منبع A", "archive_b": "📥 دریافت منبع B"}.get(operation, "⚙️ پردازش فایل‌ها")
    lines = [title, "", f"📦 فایل‌های ثبت‌شده: {state['received']}",
             f"✅ موفق: {state['success']}   |   ⏭ دانلودنشده: {state['skipped']}   |   ❌ خطا: {state['failed']}",
             f"🎵 آخرین فایل: {state['current']}"]
    if final:
        lines.insert(1, "<b>عملیات پایان یافت</b>")
    text = "\n".join(lines)
    keyboard = None
    if operation == "album":
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Finish Uploading", callback_data="album:finish")]])
    elif operation == "summary":
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("✅ پایان و ساخت خلاصه", callback_data="summary:finish")]])
    elif operation in {"archive_a", "archive_b"}:
        is_a = operation == "archive_a"
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("پایان منبع A" if is_a else "پایان منبع B", callback_data="arch:finish_a" if is_a else "arch:finish_b")]])
    if not state.get("message_id"):
        sent = await context.bot.send_message(chat_id=message.chat_id, text=text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
        state["message_id"] = sent.message_id
        state["last_update"] = now
    elif final or now - state.get("last_update", 0) >= PROGRESS_UPDATE_INTERVAL:
        try:
            await context.bot.edit_message_text(chat_id=state["chat_id"], message_id=state["message_id"], text=text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
        except BadRequest as exc:
            if "message is not modified" not in str(exc).lower():
                raise
        state["last_update"] = now
    # Every newly processed file restarts the six-second quiet-period timer.
    # The timer only finalizes the dashboard display; it does not close the mode.
    if outcome and not final:
        old_task = state.get("idle_task")
        if old_task and not old_task.done():
            old_task.cancel()
        state["idle_task"] = asyncio.create_task(
            _progress_idle_finish(context, operation, state)
        )

async def finish_progress_dashboard(context, operation, *, keep=True):
    state = context.user_data.get("progress_dashboard")
    if not state or state.get("operation") != operation:
        return
    idle_task = state.get("idle_task")
    if idle_task and not idle_task.done():
        idle_task.cancel()
    if keep:
        class _MessageRef:
            chat_id = state["chat_id"]
        await update_progress_dashboard(_MessageRef(), context, operation, final=True)
    else:
        try:
            await context.bot.delete_message(chat_id=state["chat_id"], message_id=state["message_id"])
        except Exception:
            pass
    context.user_data.pop("progress_dashboard", None)

# ==================== دریافت فایل و متن ====================
async def receive_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    document = message.document or message.audio

    # Summary mode: inspect forwarded MP3 metadata without entering the editor.
    if context.user_data.get("waiting") == "summary_tracks":
        if not document:
            return
        file_name = getattr(document, "file_name", None) or "forwarded_track.mp3"
        if not file_name.lower().endswith(".mp3"):
            await message.reply_text("برای خلاصه‌سازی، فایل MP3 بفرست.")
            return
        context.user_data.setdefault("summary_session", {"tracks": []})
        if getattr(document, "file_size", 0) and document.file_size > MAX_SIZE:
            context.user_data["summary_session"]["tracks"].append({
                "title": getattr(document, "title", None) or Path(file_name).stem,
                "artist": getattr(document, "performer", None) or "—", "album": "—",
                "downloaded": False, "duration": getattr(document, "duration", None),
            })
            await update_progress_dashboard(message, context, "summary", outcome="skipped", track_name=file_name)
            return
        temp_dir = tempfile.mkdtemp(prefix="music_summary_")
        temp_path = os.path.join(temp_dir, "summary_track.mp3")
        stage = "download"
        try:
            tg_file = await context.bot.get_file(document.file_id)
            await download_to_drive_retry(tg_file, temp_path)
            stage = "read"
            tags = get_tags(temp_path)
            title = tags.get("title", "—")
            artist = tags.get("artist", "—")
            album = tags.get("album", "—")
            context.user_data["summary_session"].setdefault("tracks", []).append({
                "title": title if title != "—" else Path(file_name).stem,
                "artist": artist, "album": album, "downloaded": True,
            })
            await update_progress_dashboard(message, context, "summary", outcome="success", track_name=file_name)
        except Exception as exc:
            print("summary intake error:", repr(exc))
            label = file_name or f"آهنگ شماره {len((context.user_data.get('summary_session') or {}).get('tracks', [])) + 1}"
            await update_progress_dashboard(message, context, "summary", outcome="failed", track_name=label)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
        return

    # In archive track mode, also accept a text reply to a previously sent
    # audio/document message. The replied-to media is what gets edited.
    if not document and context.user_data.get("waiting") == "archive_track":
        replied = message.reply_to_message
        if replied:
            document = replied.document or replied.audio
            if document:
                media_message = replied
            else:
                media_message = message
        else:
            media_message = message
    else:
        media_message = message

    if not document:
        if context.user_data.get("waiting") == "archive_track" and message.reply_to_message:
            await message.reply_text("پیام ریپلای‌شده فایل صوتی یا MP3 ندارد. روی پیام حاوی فایل آهنگ Reply بزن.")
        return

    if document.mime_type and document.mime_type.startswith("image/"):
        return

    name = document.file_name or "music.mp3"
    if context.user_data.get("waiting") == "archive_manifest" and name.lower().endswith(".txt"):
        try:
            tg_file=await context.bot.get_file(document.file_id)
            raw=await download_as_bytearray_retry(tg_file)
            payload=bytes(raw).decode("utf-8-sig").split("---DATA---",1)[-1].strip()
            manifest=json.loads(payload)
            if manifest.get("format")!="archive-v1" or not isinstance(manifest.get("tracks"),list) or not manifest["tracks"]: raise ValueError()
            context.user_data["archive"]={"phase":"queue","sources":{"source_a":[],"source_b":[]},"queue":[dict(x,status="pending") for x in manifest["tracks"]],"cursor":0}
            context.user_data["waiting"]=None
            await message.reply_text(f"✅ فهرست دریافت شد: {len(manifest['tracks'])} آهنگ\nنقطه شروع را انتخاب کن:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("▶️ شروع از ابتدا", callback_data="arch:start_begin")],[InlineKeyboardButton("⏩ شروع از آهنگ انتخابی", callback_data="arch:start_pick")],[InlineKeyboardButton("❌ لغو", callback_data="arch:start_cancel")]]))
        except Exception as exc:
            await message.reply_text(f"خواندن فایل فهرست ناموفق بود: {exc}")
        return
    if not name.lower().endswith(".mp3"):
        await message.reply_text("فقط فایل MP3 یا فایل متنی فهرست آرشیو قبول می‌کنم.")
        return

    # In manifest-driven archive mode, the bot only names the requested track.
    # The user forwards that track; it then enters the normal single-track editor.
    if context.user_data.get("waiting") == "archive_track":
        archive = context.user_data.get("archive") or {}
        idx = context.user_data.get("archive_current")
        queue = archive.get("queue", [])
        if not isinstance(idx, int) or not (0 <= idx < len(queue)):
            await message.reply_text("ترک فعال آرشیو پیدا نشد. دوباره «آهنگ بعدی» را بزن.")
            return
        context.user_data["waiting"] = None

    # Archive intake: keep Telegram file references; download only when a track is opened.
    archive = context.user_data.get("archive")
    if archive and archive.get("phase") in {"source_a", "source_b"}:
        source = archive["phase"]
        bucket = archive["sources"][source]
        audio_meta = message.audio
        bucket.append({
            "file_id": document.file_id,
            "file_unique_id": document.file_unique_id,
            "name": name,
            "title": (getattr(audio_meta, "title", None) or "").strip(),
            "artist": (getattr(audio_meta, "performer", None) or "").strip(),
            "duration": getattr(audio_meta, "duration", None),
            "size": document.file_size or 0,
            "message_id": message.message_id,
        })
        op = "archive_a" if source == "source_a" else "archive_b"
        await update_progress_dashboard(message, context, op,
            outcome="skipped" if (document.file_size or 0) > MAX_SIZE else "success", track_name=name)
        return

    if document.file_size and document.file_size > MAX_SIZE and not context.user_data.get("album_mode"):
        await message.reply_text("حجم فایل نباید بیشتر از ۲۰ مگابایت باشه.")
        return

    # حالت آلبوم
    if context.user_data.get("album_mode"):
        folder = context.user_data["album_folder"]
        attempt = context.user_data.get("album_receive_attempt", 0) + 1
        context.user_data["album_receive_attempt"] = attempt
        idx = len(context.user_data["album_tracks"]) + 1
        audio_meta = message.audio
        if document.file_size and document.file_size > MAX_SIZE:
            title = (getattr(audio_meta, "title", None) or "").strip() or Path(name).stem
            artist = (getattr(audio_meta, "performer", None) or "").strip() or "—"
            duration = getattr(audio_meta, "duration", None)
            context.user_data["album_tracks"].append({
                "path": None, "original_name": name, "index": idx,
                "original_cover": None, "original_duration": duration,
                "summary_title": title, "summary_artist": artist,
                "summary_album": (getattr(audio_meta, "album", None) or "—").strip(),
                "summary_duration": duration, "downloaded": False,
                "file_size": document.file_size, "file_id": document.file_id,
            })
            await update_progress_dashboard(message, context, "album", outcome="skipped", track_name=name)
            return
        path = os.path.join(folder, f"track_{attempt:02d}.mp3")
        stage = "download"
        try:
            tg_file = await context.bot.get_file(document.file_id)
            await download_to_drive_retry(tg_file, path)
            stage = "read"
            mt_load(path)  # فقط برای اطمینان از معتبر بودن فایل
            MP3(path)
        except Exception as e:
            print("Album download/read error:", repr(e))
            try:
                if os.path.exists(path): os.remove(path)
            except OSError:
                pass
            label = name or f"فایل ارسالی شماره {attempt}"
            await update_progress_dashboard(message, context, "album", outcome="failed", track_name=label)
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
        await update_progress_dashboard(message, context, "album", outcome="success", track_name=name)
        return

    # حالت تک آهنگ
    old_folder = context.user_data.get("folder")
    if old_folder and os.path.isdir(old_folder):
        shutil.rmtree(old_folder, ignore_errors=True)

    folder = tempfile.mkdtemp(prefix="musicbot_")
    path = os.path.join(folder, "original.mp3")

    stage = "download"
    try:
        tg_file = await context.bot.get_file(document.file_id)
        await download_to_drive_retry(tg_file, path)
        stage = "read"
        mt_load(path)
        MP3(path)
    except Exception as e:
        shutil.rmtree(folder, ignore_errors=True)
        print("Audio download/read error:", repr(e))
        label = name or "نام فایل مشخص نیست"
        await message.reply_text(failed_media_message(label, e, stage))
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
    waiting = context.user_data.get("waiting")
    if waiting == "unreleased_default_cover":
        photo = update.message.photo[-1] if update.message.photo else None
        document = update.message.document
        if photo:
            tg_file = await context.bot.get_file(photo.file_id)
        elif document and document.mime_type and document.mime_type.startswith("image/"):
            tg_file = await context.bot.get_file(document.file_id)
        else:
            await update.message.reply_text("لطفاً یک تصویر معتبر بفرست.")
            return
        try:
            ensure_data_dir()
            temp_path = DATA_DIR / "unreleased_track_cover.upload"
            await download_to_drive_retry(tg_file, str(temp_path))
            image_data = normalize_cover(temp_path.read_bytes())
            UNRELEASED_COVER_FILE.write_bytes(image_data)
            temp_path.unlink(missing_ok=True)
            context.user_data["waiting"] = None
            await update.message.reply_text("✅ کاور پیش‌فرض Unreleased Track ذخیره شد.", reply_markup=main_reply_keyboard())
        except Exception as exc:
            print("Unreleased default cover error:", exc)
            await update.message.reply_text("ذخیره کاور ناموفق بود. تصویر دیگری امتحان کن.")
        return
    if waiting != "picture":
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
        await download_to_drive_retry(tg_file, image_path)
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


async def send_temporary_prompt(message, context, text, **kwargs):
    """Send an input prompt and remember it so it can be removed after the next reply."""
    old_id = context.user_data.get("active_prompt_message_id")
    old_chat = context.user_data.get("active_prompt_chat_id")
    if old_id and old_chat:
        try:
            await context.bot.delete_message(chat_id=old_chat, message_id=old_id)
        except TelegramError:
            pass
    sent = await message.reply_text(text, **kwargs)
    context.user_data["active_prompt_message_id"] = sent.message_id
    context.user_data["active_prompt_chat_id"] = sent.chat_id
    return sent


async def clear_active_prompt(update, context):
    """Delete the prompt answered by the user's latest text message."""
    message_id = context.user_data.pop("active_prompt_message_id", None)
    chat_id = context.user_data.pop("active_prompt_chat_id", None)
    if message_id and chat_id:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
        except TelegramError:
            pass


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
    if text == "📊 خلاصه آهنگ‌ها":
        context.user_data["summary_session"] = {"tracks": []}
        context.user_data["waiting"] = "summary_tracks"
        await update.message.reply_text(
            "📊 حالت خلاصه آهنگ‌ها فعال شد.\n\n"
            "حالا آهنگ‌ها را به صورت فایل صوتی یا MP3 برایم فوروارد کن. "
            "بعد از ارسال همه فایل‌ها، دکمه «پایان و ساخت خلاصه» را بزن.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ پایان و ساخت خلاصه", callback_data="summary:finish")]])
        )
        return
    if text == "❌ لغو / خروج از آلبوم":
        await cancel(update, context)
        return

    await clear_active_prompt(update, context)
    value = text
    waiting = context.user_data.get("waiting")

    # ---------- Auto Forward: set destination channel (owner only) ----------
    if isinstance(waiting, str) and waiting.startswith("set_auto_forward_channel"):
        user = update.effective_user
        if not is_owner(user.id if user else None):
            context.user_data["waiting"] = None
            await update.message.reply_text("🔒 فقط مالک ربات می‌تواند کانال مقصد را تنظیم کند.")
            return
        slot = "1"
        if ":" in waiting:
            slot = waiting.split(":")[-1] or "1"
        raw = value.strip()
        if not raw:
            await send_temporary_prompt(update.message, context, "آیدی یا یوزرنیم کانال را بفرست.")
            return
        target = raw if raw.startswith("@") or raw.lstrip("-").isdigit() else f"@{raw}"
        try:
            chat = await context.bot.get_chat(target)
        except TelegramError as e:
            await update.message.reply_text(
                f"❌ کانال پیدا نشد یا ربات به آن دسترسی ندارد.\n{e}\n\nدوباره آیدی/یوزرنیم را بفرست یا /cancel بزن."
            )
            return
        try:
            me = await context.bot.get_me()
            member = await context.bot.get_chat_member(chat.id, me.id)
            status = getattr(member, "status", None)
            if status not in ("administrator", "creator"):
                await update.message.reply_text(
                    "❌ ربات ادمین این کانال نیست. ابتدا ربات را ادمین کن و اجازه ارسال پیام بده، سپس دوباره تلاش کن."
                )
                return
            can_post = True
            if status == "administrator":
                can_post = bool(getattr(member, "can_post_messages", True))
            if not can_post:
                await update.message.reply_text(
                    "❌ ربات اجازه ارسال پیام در این کانال را ندارد. مجوز Post Messages را فعال کن."
                )
                return
        except TelegramError as e:
            await update.message.reply_text(
                f"❌ بررسی دسترسی ربات به کانال ناموفق بود.\n{e}"
            )
            return
        title = getattr(chat, "title", None) or getattr(chat, "username", "") or str(chat.id)
        set_af_channel(context, slot, chat.id, title)
        if slot == "1":
            set_af_destination(context, chat.id, title)
        context.user_data["waiting"] = None
        slot_name = "Main Channel" if slot == "1" else "Full Archive"
        await update.message.reply_text(
            f"✅ کانال {slot_name} تنظیم شد.\n"
            f"• عنوان: {title}\n"
            f"• شناسه: `{chat.id}`\n\n"
            "این تنظیم فقط در حافظه است و با ریستارت ربات پاک می‌شود.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    # ---------- Archive start-from number ----------
    if waiting == "archive_start_number":
        queue = (context.user_data.get("archive") or {}).get("queue") or []
        if not queue:
            context.user_data["waiting"] = None
            await update.message.reply_text("صف آرشیو خالی است.")
            return
        raw = value.strip()
        if not raw.isdigit():
            await send_temporary_prompt(update.message, context, f"یک عدد بین ۱ تا {len(queue)} بفرست.")
            return
        num = int(raw)
        if num < 1 or num > len(queue):
            await send_temporary_prompt(update.message, context, f"شماره خارج از محدوده است. عدد بین ۱ تا {len(queue)} بفرست.")
            return
        # Skip tracks before selected (1-based → index num-1)
        for i, item in enumerate(queue):
            if i < num - 1 and item.get("status") in (None, "pending", "in_progress"):
                item["status"] = "skipped"
        context.user_data["waiting"] = None
        context.user_data["archive_start_from"] = num
        # Jump open_next logic: find first pending from selected index
        idx = next((i for i, x in enumerate(queue) if i >= num - 1 and x.get("status") == "pending"), None)
        if idx is None:
            await update.message.reply_text("از این شماره به بعد ترک در انتظاری نیست.")
            return
        item = queue[idx]
        item["status"] = "in_progress"
        context.user_data.update({"archive_current": idx, "waiting": "archive_track", "album_mode": False, "editing_album_track": False})
        title = (item.get("title") or Path(item.get("name") or "بدون نام").stem).strip()
        artist = (item.get("artist") or "هنرمند نامشخص").strip()
        warning = format_review_warning(item, queue)
        progress = ((idx + 1) / len(queue) * 100) if queue else 0
        title_button = InlineKeyboardButton(
            text=(f"📋 {title[:55]}" if title else "📋 کپی عنوان آهنگ"),
            copy_text=CopyTextButton(text=title or ""),
        )
        await update.message.reply_text(
            f"🎵 ترک {idx+1:03d} از {len(queue)}  |  پیشرفت: {progress:.1f}%\nعنوان: {title}\nهنرمند: {artist}{warning}\n\nلطفاً فایل همین آهنگ را از منبع خودت فوروارد کن.",
            reply_markup=InlineKeyboardMarkup([
                [title_button],
                [InlineKeyboardButton("⏮ آهنگ قبلی", callback_data="arch:previous")],
                [InlineKeyboardButton("⏩ رد کردن این آهنگ بدون ارسال فایل", callback_data="arch:skip_current")],
            ]),
        )
        return

    summary_draft = context.user_data.get("album_summary_draft") or {}
    entries = summary_draft.get("entries", [])
    if waiting in {"albumsummary:add_position", "albumsummary:add_title", "albumsummary:add_duration", "albumsummary:add_artist", "albumsummary:edit_position", "albumsummary:edit_title", "albumsummary:edit_duration", "albumsummary:edit_artist", "albumsummary:delete_position"}:
        if not value:
            await send_temporary_prompt(update.message, context, "این مقدار نمی‌تواند خالی باشد. دوباره بفرست.")
            return
        if waiting.endswith("position"):
            if not value.isdigit() or int(value) < 1 or int(value) > (len(entries) + (1 if waiting == "albumsummary:add_position" else 0)):
                await send_temporary_prompt(update.message, context, "شماره معتبر نیست. دوباره شماره جایگاه را بفرست.")
                return
            pos = int(value)
            context.user_data["albumsummary_position"] = pos
            if waiting == "albumsummary:add_position":
                context.user_data["waiting"] = "albumsummary:add_title"
                await send_temporary_prompt(update.message, context, "نام آهنگ جدید را بفرست:")
            elif waiting == "albumsummary:delete_position":
                entries.pop(pos-1)
                context.user_data["waiting"] = None
                await show_album_summary_draft(update.message, context)
            else:
                context.user_data["waiting"] = "albumsummary:edit_title"
                await send_temporary_prompt(update.message, context, f"نام فعلی: {entries[pos-1]['title']}\nنام جدید را بفرست:")
            return
        if waiting == "albumsummary:add_title":
            context.user_data["albumsummary_new"] = {"title": value, "downloaded": False}
            context.user_data["waiting"] = "albumsummary:add_duration"
            await send_temporary_prompt(update.message, context, "مدت‌زمان آهنگ را به شکل دقیقه:ثانیه بفرست؛ مثال 3:21")
            return
        if waiting in {"albumsummary:add_duration", "albumsummary:edit_duration"}:
            m = re.fullmatch(r"(\d{1,3}):(\d{1,2})", value)
            if not m or int(m.group(2)) > 59:
                await send_temporary_prompt(update.message, context, "مدت‌زمان معتبر نیست. نمونه درست: 3:21")
                return
            seconds = int(m.group(1))*60 + int(m.group(2))
            if waiting == "albumsummary:add_duration":
                context.user_data["albumsummary_new"]["duration"] = seconds
                context.user_data["waiting"] = "albumsummary:add_artist"
                await send_temporary_prompt(update.message, context, "نام هنرمند یا هنرمندان را بفرست:")
            else:
                entries[context.user_data["albumsummary_position"]-1]["duration"] = seconds
                context.user_data["waiting"] = "albumsummary:edit_artist"
                await send_temporary_prompt(update.message, context, "نام هنرمندان را بفرست:")
            return
        if waiting == "albumsummary:add_artist":
            new = context.user_data.pop("albumsummary_new", {})
            new["artist"] = value
            new.setdefault("duration", 0)
            pos = context.user_data.pop("albumsummary_position")
            entries.insert(pos-1, new)
            context.user_data["waiting"] = None
            await show_album_summary_draft(update.message, context)
            return
        if waiting == "albumsummary:edit_title":
            entries[context.user_data["albumsummary_position"]-1]["title"] = value
            context.user_data["waiting"] = "albumsummary:edit_duration"
            await send_temporary_prompt(update.message, context, "مدت‌زمان جدید را به شکل دقیقه:ثانیه بفرست:")
            return
        if waiting == "albumsummary:edit_artist":
            entries[context.user_data["albumsummary_position"]-1]["artist"] = value
            context.user_data["waiting"] = None
            await show_album_summary_draft(update.message, context)
            return
    if waiting == "archive_manifest":
        import json
        try:
            payload = text.split("---DATA---", 1)[-1].strip()
            manifest = json.loads(payload)
            if manifest.get("format") != "archive-v1" or not isinstance(manifest.get("tracks"), list): raise ValueError()
            tracks = manifest["tracks"]
            if not tracks or len(tracks) > 1000: raise ValueError()
            context.user_data["archive"] = {"phase":"queue", "sources":{"source_a":[],"source_b":[]}, "queue":[dict(x, status="pending") for x in tracks], "cursor":0}
            context.user_data["waiting"] = None
            await update.message.reply_text(f"✅ فهرست دریافت شد: {len(tracks)} آهنگ\nنقطه شروع را انتخاب کن:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("▶️ شروع از ابتدا", callback_data="arch:start_begin")],[InlineKeyboardButton("⏩ شروع از آهنگ انتخابی", callback_data="arch:start_pick")],[InlineKeyboardButton("❌ لغو", callback_data="arch:start_cancel")]]))
        except Exception:
            await update.message.reply_text("متن فهرست معتبر نیست. کل متن خروجی بررسی منابع را بدون تغییر ارسال کن.")
        return
    if not waiting:
        return

    value = text
    if not value:
        await send_temporary_prompt(update.message, context, "مقدار خالی قابل ذخیره نیست.")
        return

    # Remove one exact artist name from the Artist tag across the active album.
    if waiting == "album_remove_artist_name":
        target = re.sub(r"\s+", " ", value).strip()
        if not target:
            await update.message.reply_text("نام آرتیست خالی است؛ دوباره بفرست.")
            return
        tracks = context.user_data.get("album_tracks", [])
        matches = []
        skipped = 0
        target_norm = normalize_name(target)
        for item in tracks:
            track_path = item.get("path")
            if not track_path or not os.path.isfile(track_path):
                skipped += 1
                continue
            artist_value = str(get_tags(track_path).get("artist", "") or "").strip()
            parts = split_artists(artist_value)
            if any(normalize_name(part) == target_norm for part in parts):
                matches.append({"index": item.get("index"), "path": track_path, "artist": artist_value})
        context.user_data["waiting"] = None
        if not matches:
            await update.message.reply_text(
                f"نام «{target}» در فیلد Artist هیچ‌کدام از ترک‌های قابل‌ویرایش پیدا نشد.\n"
                f"ترک‌های فاقد فایل دانلودشده: {skipped}"
            )
            return
        context.user_data["album_remove_artist_pending"] = {"name": target, "matches": matches, "skipped": skipped}
        await update.message.reply_text(
            f"🔎 نام «{target}» در {len(matches)} ترک از {len(tracks)} ترک پیدا شد.\n"
            f"در {skipped} ترک فایل قابل‌ویرایش در دسترس نیست.\n\n"
            "فقط همین نام از فیلد Artist حذف می‌شود و نام خواننده‌های دیگر باقی می‌ماند. تأیید می‌کنی؟",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ تأیید و حذف", callback_data="album:remove_artist_confirm")],
                [InlineKeyboardButton("❌ انصراف", callback_data="album:remove_artist_cancel")],
            ])
        )
        return

    # Apply-to-all album field: first receive the field name, then its value.
    if waiting == "album_selected_field":
        aliases = {"artist":"artist", "album artist":"albumartist", "albumartist":"albumartist", "album":"album", "genre":"genre", "composer":"composer", "publisher":"publisher", "year":"year", "comment":"comment", "کامنت":"comment", "توضیحات":"comment", "هنرمند":"artist", "هنرمند آلبوم":"albumartist", "آلبوم":"album", "ژانر":"genre", "آهنگساز":"composer", "ناشر":"publisher", "سال":"year"}
        key = aliases.get(value.casefold())
        allowed = {"artist", "albumartist", "album", "genre", "composer", "publisher", "year", "comment"}
        if key not in allowed:
            await send_temporary_prompt(update.message, context, "فیلدهای مجاز: artist, albumartist, album, genre, composer, publisher, year, comment")
            return
        context.user_data["album_selected_key"] = key
        context.user_data["waiting"] = "album_selected_value"
        await send_temporary_prompt(update.message, context, f"مقدار جدید برای {FIELDS.get(key, key)} را بفرست:")
        return

    if waiting == "album_selected_value":
        key = context.user_data.get("album_selected_key")
        selected = set(context.user_data.get("album_selected", []))
        tracks = [t for t in context.user_data.get("album_tracks", []) if t.get("index") in selected and t.get("path") and os.path.isfile(t["path"])]
        allowed = {"artist", "albumartist", "album", "genre", "composer", "publisher", "year", "comment"}
        if key not in allowed or not tracks:
            context.user_data["waiting"] = None
            await update.message.reply_text("انتخاب یا فیلد معتبر نیست.")
            return
        backups = []
        try:
            for item in tracks:
                track_path = item["path"]
                backup_path = track_path + ".v42bak"
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
            await update.message.reply_text(f"ویرایش گروهی انجام نشد و تغییرات برگشت داده شد: {exc}")
            return
        context.user_data["waiting"] = None
        context.user_data.pop("album_selected_key", None)
        context.user_data["album_selected"] = []
        await update.message.reply_text(f"✅ فیلد {FIELDS.get(key, key)} روی {len(tracks)} ترک انتخاب‌شده اعمال شد.")
        await show_album_overview(update, context)
        return

    if waiting == "album_all_field":
        aliases = {"artist":"artist", "album artist":"albumartist", "albumartist":"albumartist",
                   "album":"album", "genre":"genre", "composer":"composer",
                   "publisher":"publisher", "year":"year", "comment":"comment",
                   "کامنت":"comment", "توضیحات":"comment", "هنرمند":"artist",
                   "هنرمند آلبوم":"albumartist", "آلبوم":"album", "ژانر":"genre",
                   "آهنگساز":"composer", "ناشر":"publisher", "سال":"year"}
        key = aliases.get(value.casefold())
        if key not in {"artist", "albumartist", "album", "genre", "composer", "publisher", "year", "comment"}:
            await send_temporary_prompt(update.message, context, "فیلدهای مجاز: artist, albumartist, album, genre, composer, publisher, year, comment")
            return
        context.user_data["album_all_key"] = key
        context.user_data["waiting"] = "album_all_value"
        await send_temporary_prompt(update.message, context, f"مقدار جدید برای {FIELDS.get(key, key)} را بفرست:")
        return

    if waiting == "album_all_value":
        key = context.user_data.get("album_all_key")
        tracks = context.user_data.get("album_tracks", [])
        if key not in {"artist", "albumartist", "album", "genre", "composer", "publisher", "year", "comment"}:
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

    if waiting == "albumtools_disc_value":
        if not value.isdigit() or not 1 <= int(value) <= 99:
            await send_temporary_prompt(update.message, context, "شماره دیسک باید عددی بین ۱ تا ۹۹ باشد.")
            return
        tracks=context.user_data.get("album_tracks", [])
        backups=[]
        try:
            for item in tracks:
                path=item["path"]; backup=path+".v43bak"; shutil.copy2(path,backup); backups.append((path,backup)); set_tag(path,"disc",str(int(value)))
            for _,backup in backups:
                if os.path.exists(backup): os.remove(backup)
        except Exception as exc:
            for path,backup in reversed(backups):
                if os.path.exists(backup): shutil.copy2(backup,path); os.remove(backup)
            context.user_data["waiting"]=None
            await update.message.reply_text(f"تنظیم دیسک انجام نشد و تغییرات برگشت داده شد: {exc}")
            return
        context.user_data["waiting"]=None
        await update.message.reply_text(f"✅ شماره دیسک {len(tracks)} ترک روی {value} تنظیم شد.")
        await show_album_overview(update,context)
        return

    if waiting == "albumtools_renumber_start":
        if not value.isdigit() or not 1 <= int(value) <= 999:
            await send_temporary_prompt(update.message, context, "شماره شروع باید عددی بین ۱ تا ۹۹۹ باشد.")
            return
        context.user_data["albumtools_renumber_start"] = int(value)
        context.user_data["waiting"] = None
        await update.message.reply_text(
            f"شماره‌گذاری از {int(value)} برای {len(context.user_data.get('album_tracks', []))} ترک انجام شود؟",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ تأیید شماره‌گذاری", callback_data="albumtools:renumber_confirm")], [InlineKeyboardButton("❌ لغو", callback_data="album:tools")]])
        )
        return

    if waiting == "albumtools_copy_fields":
        allowed = {"title", "artist", "album", "year", "genre", "albumartist", "composer", "publisher", "lyrics", "comment"}
        keys = [x.strip().casefold() for x in value.split(",") if x.strip()]
        aliases = {"album artist":"albumartist", "هنرمند":"artist", "ژانر":"genre", "آهنگساز":"composer", "ناشر":"publisher", "سال":"year", "عنوان":"title", "آلبوم":"album"}
        keys = [aliases.get(x, x) for x in keys]
        if not keys or any(k not in allowed for k in keys):
            await send_temporary_prompt(update.message, context, "فیلدهای مجاز: title, artist, album, year, genre, albumartist, composer, publisher, lyrics, comment")
            return
        context.user_data["albumtools_copy_keys"] = keys
        context.user_data["waiting"] = None
        await send_temporary_prompt(update.message, context, "شماره ترک مبدأ را بفرست (شماره‌ای که در فهرست آلبوم نمایش داده می‌شود):")
        context.user_data["waiting"] = "albumtools_copy_source"
        return

    if waiting == "albumtools_copy_source":
        if not value.isdigit() or not _album_track_by_index(context.user_data.get("album_tracks", []), int(value)):
            await send_temporary_prompt(update.message, context, "شماره ترک معتبر نیست. دوباره بفرست.")
            return
        context.user_data["albumtools_copy_source"] = int(value)
        context.user_data["waiting"] = None
        context.user_data["album_selected"] = []
        await send_temporary_prompt(update.message, context, "حالا ترک‌های مقصد را انتخاب کن:", reply_markup=album_select_keyboard(context.user_data.get("album_tracks", []), set()))
        context.user_data["albumtools_copy_mode"] = True
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
    # Acknowledge before any potentially slow operation.
    try:
        await query.answer()
    except TelegramError as callback_error:
        print("Callback acknowledgement warning:", repr(callback_error))
    data = query.data
    path = context.user_data.get("path")

    if data == "unrel:upload":
        context.user_data["waiting"] = "unreleased_default_cover"
        await query.message.reply_text("تصویر کاور پیش‌فرض را به صورت عکس یا فایل تصویری ارسال کن.")
        return
    if data == "unrel:delete":
        UNRELEASED_COVER_FILE.unlink(missing_ok=True)
        await query.message.reply_text("کاور پیش‌فرض حذف شد.")
        return
    if data == "unrel:replace":
        if not path or not os.path.exists(path):
            await query.message.reply_text("فایل آهنگ در دسترس نیست.")
            return
        if not UNRELEASED_COVER_FILE.exists():
            await query.message.reply_text("هنوز کاور پیش‌فرضی ثبت نشده است.")
            return
        try:
            image_data = normalize_cover(UNRELEASED_COVER_FILE.read_bytes())
            set_cover(path, image_data)
            context.user_data["original_cover"] = image_data
            update_album_track_cover(context, path, image_data)
            set_tag(path, "album", "Unreleased Track")
        except Exception as exc:
            print("Unreleased cover apply error:", exc)
            await query.message.reply_text("اعمال کاور انجام نشد؛ فایل اصلی دست‌نخورده باقی نماند؟ لطفاً دوباره امتحان کن.")
            return
        await query.message.reply_text("✅ تگ Unreleased Track و کاور پیش‌فرض روی همین آهنگ اعمال شد.")
        await show_editor(update, context, is_album=context.user_data.get("editing_album_track", False))
        return
    if data == "unrel:keep":
        if not path or not os.path.exists(path):
            await query.message.reply_text("فایل آهنگ در دسترس نیست.")
            return
        set_tag(path, "album", "Unreleased Track")
        await query.message.reply_text("✅ تگ Unreleased Track اعمال شد؛ کاور فعلی بدون تغییر ماند.")
        await show_editor(update, context, is_album=context.user_data.get("editing_album_track", False))
        return
    if data == "unrel:cancel":
        await query.message.reply_text("عملیات Unreleased Track لغو شد؛ هیچ تغییری اعمال نشد.")
        return

    if data == "summary:finish":
        session = context.user_data.get("summary_session") or {}
        tracks = session.get("tracks", [])
        if not tracks:
            await query.message.reply_text("هنوز هیچ آهنگی دریافت نشده است.")
            return
        artist_counts = Counter(
            artist.strip() for track in tracks
            for artist in split_artists(track.get("artist", ""))
            if artist.strip() and artist.strip() != "—"
        )
        album_counts = Counter(
            track.get("album", "").strip() for track in tracks
            if track.get("album", "").strip() and track.get("album", "").strip() != "—"
        )
        missing_artist_tracks = []
        missing_album_tracks = []
        for track in tracks:
            disp = track_display_name(track)
            if not split_artists(track.get("artist", "")):
                missing_artist_tracks.append(disp)
            if track.get("album", "—") in ("", "—"):
                missing_album_tracks.append(disp)
        no_artist = len(missing_artist_tracks)
        no_album = len(missing_album_tracks)
        lines = [
            "📊 خلاصه مجموعه آهنگ‌ها", "",
            f"🎵 تعداد کل آهنگ‌ها: {len(tracks)}", "",
            f"👤 هنرمندان یکتا: {len(artist_counts)}",
        ]
        if artist_counts:
            lines.extend(f"• {name} ({count} آهنگ)" for name, count in sorted(artist_counts.items(), key=lambda x: x[0].casefold()))
        else:
            lines.append("• تگ هنرمند ثبت نشده")
        lines.extend(["", f"💿 آلبوم‌های یکتا: {len(album_counts)}"])
        if album_counts:
            lines.extend(f"• {name} ({count} آهنگ)" for name, count in sorted(album_counts.items(), key=lambda x: x[0].casefold()))
        else:
            lines.append("• تگ آلبوم ثبت نشده")
        lines.append("")
        lines.append(f"▫️ بدون تگ هنرمند: {no_artist}")
        if no_artist:
            lines.extend(f"• {name}" for name in missing_artist_tracks)
        lines.append(f"▫️ بدون تگ آلبوم: {no_album}")
        if no_album:
            lines.extend(f"• {name}" for name in missing_album_tracks)
        context.user_data.pop("summary_session", None)
        context.user_data["waiting"] = None
        chunks = []
        buf = []
        size = 0
        for line in lines:
            add = len(line) + 1
            if buf and size + add > 3500:
                chunks.append(chr(10).join(buf))
                buf = [line]
                size = add
            else:
                buf.append(line)
                size += add
        if buf:
            chunks.append(chr(10).join(buf))
        dashboard = context.user_data.get("progress_dashboard")
        if dashboard and dashboard.get("operation") == "summary":
            try:
                await context.bot.edit_message_text(
                    chat_id=dashboard["chat_id"],
                    message_id=dashboard["message_id"],
                    text=chunks[0],
                )
            except Exception:
                await query.message.reply_text(chunks[0], reply_markup=main_reply_keyboard() if len(chunks) == 1 else None)
            for j, extra in enumerate(chunks[1:]):
                is_last = (j == len(chunks) - 2)
                await query.message.reply_text(extra, reply_markup=main_reply_keyboard() if is_last else None)
            context.user_data.pop("progress_dashboard", None)
        else:
            for i, chunk in enumerate(chunks):
                kb = main_reply_keyboard() if i == len(chunks) - 1 else None
                await query.message.reply_text(chunk, reply_markup=kb)
        return
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
        if data == "arch:review_menu":
            a["phase"] = "menu"
            await query.message.reply_text("برای بررسی، منبع A را اضافه کن و سپس منبع B را. هر منبع را جداگانه پایان بده.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📥 افزودن منبع A", callback_data="arch:start_a")],[InlineKeyboardButton("📥 افزودن منبع B", callback_data="arch:start_b")],[InlineKeyboardButton("🧮 تحلیل و ساخت فهرست", callback_data="arch:analyze")]]))
            return
        if data == "arch:input_menu":
            context.user_data["waiting"] = "archive_manifest"
            await query.message.reply_text("متن کامل خروجی مرحله «بررسی آهنگ‌های منابع» را ارسال کن.")
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
            await finish_progress_dashboard(context, "archive_a", keep=True)
            await query.message.edit_reply_markup(reply_markup=archive_keyboard())
            return
        if data == "arch:finish_b":
            a["phase"] = "menu"
            await finish_progress_dashboard(context, "archive_b", keep=True)
            await query.message.edit_reply_markup(reply_markup=archive_keyboard())
            return
        if data == "arch:analyze":
            merged=[]; definite_count=0; review_count=0
            # Keep all A tracks in original order; B matches attach to A and do not create another queue row.
            for entry in sorted(a["sources"]["source_a"], key=lambda x:x.get("message_id",0)):
                merged.append(dict(entry, source="A", duplicate_level="independent", review=False, status="pending"))
            for entry in sorted(a["sources"]["source_b"], key=lambda x:x.get("message_id",0)):
                item=dict(entry, source="B", duplicate_level="independent", review=False, status="pending")
                definite_hit=None
                manual_hits=[]
                for i, previous in enumerate(merged):
                    result, labels = classify_detail(previous, item)
                    if result=="definite":
                        definite_hit=i
                        break
                    if result=="manual":
                        manual_hits.append({
                            "index": i,
                            "number": previous.get("number") or (i + 1),
                            "title": track_display_name(previous),
                            "criteria": list(labels),
                        })
                if definite_hit is not None:
                    merged[definite_hit].setdefault("also_in_source_b", True); definite_count+=1
                elif manual_hits:
                    item["review"]=True
                    item["duplicate_level"]="review"
                    item["possible_match"]=manual_hits[0]["index"]+1
                    item["review_matches"]=[{
                        "number": h.get("number"),
                        "title": h.get("title"),
                        "criteria": h.get("criteria") or [],
                    } for h in manual_hits]
                    if manual_hits[0].get("criteria"):
                        item["review_criteria"]=list(manual_hits[0]["criteria"])
                    merged.append(item); review_count+=1
                else:
                    merged.append(item)
            for i,x in enumerate(merged,1): x["number"]=i
            a["queue"]=merged; a["phase"]="queue"
            lines=["ARCHIVE-MANIFEST v1",f"تعداد آهنگ‌ها: {len(merged)}",f"تکراری قطعی حذف‌شده: {definite_count}",f"نیازمند بررسی: {review_count}","", "فهرست آهنگ‌ها:"]
            for x in merged:
                mark="❗ " if x.get("review") else ""
                artist=x.get("artist") or "هنرمند نامشخص"
                name=x.get("title") or Path(x.get("name") or "بدون نام").stem
                lines.append(f"{x['number']:03d}. {mark}{name} - {artist}")
                if x.get("review") and x.get("review_matches"):
                    for h in x["review_matches"]:
                        crit=", ".join(h.get("criteria") or []) or "—"
                        lines.append(f"    ↳ مشابه: {h.get('title') or '—'} | معیارها: {crit}")
            # JSON payload after readable list lets the same text be imported without losing file references.
            manifest={"format":"archive-v1","tracks":[{k:x.get(k) for k in ("number","name","title","artist","duration","size","file_id","file_unique_id","review","possible_match","review_matches","review_criteria","source")} for x in merged]}
            lines += ["", "---DATA---", json.dumps(manifest,ensure_ascii=False,separators=(",",":"))]
            report="\n".join(lines)
            preview="\n".join(lines[:min(len(lines), 24)])
            await query.message.reply_text(preview + ("\n… فهرست کامل داخل فایل متنی پیوست است." if len(lines)>24 else ""))
            report_file=BytesIO(report.encode("utf-8")); report_file.name="archive_manifest.txt"
            await query.message.reply_document(document=report_file, filename="archive_manifest.txt", caption="این فایل متنی را برای مرحله دوم به گزینه «ورودی بررسی منابع» بده.")
            await query.message.reply_text("فهرست کامل آماده شد.", reply_markup=archive_keyboard())
            return
        if data == "arch:list":
            await archive_show_list(query, context)
            return
        if data == "arch:report":
            await query.message.reply_text(
                archive_report_text(a.get("queue", [])),
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📋 نمایش فهرست آرشیو", callback_data="arch:list")]])
            )
            return
        if data == "arch:finish_current":
            queue = a.get("queue", [])
            idx = context.user_data.get("archive_current")
            if not isinstance(idx, int) or not (0 <= idx < len(queue)):
                await query.message.reply_text("ترک فعال آرشیو پیدا نشد.")
                return
            queue[idx]["status"] = "confirmed"
            await query.message.edit_reply_markup(
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ این آهنگ تکمیل شد", callback_data="arch:already_done")],
                    [InlineKeyboardButton("⏭ آهنگ بعدی", callback_data="arch:next")],
                    [InlineKeyboardButton("⏮ آهنگ قبلی", callback_data="arch:previous")],
                ])
            )
            await query.message.reply_text("این آهنگ تکمیل شد.")
            return
        if data == "arch:already_done":
            await query.message.reply_text("این آهنگ قبلاً تأیید شده است.")
            return
        if data == "arch:previous":
            queue = a.get("queue", [])
            idx = context.user_data.get("archive_current")
            if not isinstance(idx, int) or idx <= 0 or idx >= len(queue):
                await query.message.reply_text("ترک قبلی وجود ندارد.")
                return
            prev_idx = idx - 1
            item = queue[prev_idx]
            # Re-open the previous item for review/editing, even if it was previously skipped or confirmed.
            if queue[idx].get("status") == "in_progress":
                queue[idx]["status"] = "pending"
            item["status"] = "in_progress"
            context.user_data.update({"archive_current": prev_idx, "waiting": "archive_track", "album_mode": False, "editing_album_track": False})
            title = (item.get("title") or Path(item.get("name") or "بدون نام").stem).strip()
            artist = (item.get("artist") or "هنرمند نامشخص").strip()
            warning = format_review_warning(item, queue)
            progress = ((prev_idx + 1) / len(queue) * 100) if queue else 0
            title_button = InlineKeyboardButton(text=(f"📋 {title[:55]}" if title else "📋 کپی عنوان آهنگ"), copy_text=CopyTextButton(text=title or ""))
            await query.message.reply_text(
                f"🎵 ترک {prev_idx+1:03d} از {len(queue)}  |  پیشرفت: {progress:.1f}%\nعنوان: {title}\nهنرمند: {artist}{warning}\n\nلطفاً فایل همین آهنگ را از منبع خودت فوروارد کن.",
                reply_markup=InlineKeyboardMarkup([
                    [title_button],
                    [InlineKeyboardButton("⏮ آهنگ قبلی", callback_data="arch:previous")],
                    [InlineKeyboardButton("⏩ رد کردن این آهنگ بدون ارسال فایل", callback_data="arch:skip_current")],
                ])
            )
            await query.message.reply_text("به آهنگ قبلی برگشتی.")
            return
        if data == "arch:skip_current":
            queue = a.get("queue", [])
            idx = context.user_data.get("archive_current")
            if not isinstance(idx, int) or not (0 <= idx < len(queue)):
                await query.message.reply_text("ترک فعال آرشیو پیدا نشد.")
                return
            queue[idx]["status"] = "skipped"
            await query.message.reply_text("این ترک رد شد.")
            data = "arch:open_next"
        if data == "arch:next":
            queue = a.get("queue", [])
            idx = context.user_data.get("archive_current")
            if isinstance(idx, int) and 0 <= idx < len(queue):
                if queue[idx].get("status") != "confirmed":
                    await query.message.reply_text("اول دکمهٔ «این آهنگ تموم شد» را بزن.")
                    return
            data = "arch:open_next"
        if data == "arch:start_begin":
            # Mark nothing skipped; open first pending from start
            context.user_data.pop("archive_start_from", None)
            data = "arch:open_next"
        if data == "arch:start_pick":
            queue = a.get("queue", [])
            if not queue:
                await query.message.reply_text("صف آرشیو خالی است.")
                return
            context.user_data["waiting"] = "archive_start_number"
            await query.message.reply_text(
                f"شماره آهنگ شروع را بفرست (۱ تا {len(queue)}).\n"
                "آهنگ‌های قبل از این شماره در این اجرا رد می‌شوند."
            )
            return
        if data == "arch:start_cancel":
            context.user_data["waiting"] = None
            await query.message.reply_text("انتخاب نقطه شروع لغو شد.", reply_markup=archive_keyboard())
            return
        if data == "arch:open_next":
            queue = a.get("queue", [])
            idx = next((i for i,x in enumerate(queue) if x.get("status")=="pending"), None)
            if idx is None:
                await query.message.reply_text(
                    "✅ همه ترک‌های در انتظار بررسی شدند.\n\n" + archive_report_text(queue),
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("📊 گزارش آرشیو", callback_data="arch:report")],
                        [InlineKeyboardButton("📋 فهرست آرشیو", callback_data="arch:list")],
                    ])
                )
                return
            item = queue[idx]
            item["status"] = "in_progress"
            context.user_data.update({"archive_current":idx,"waiting":"archive_track","album_mode":False,"editing_album_track":False})
            title = (item.get("title") or Path(item.get("name") or "بدون نام").stem).strip()
            artist = (item.get("artist") or "هنرمند نامشخص").strip()
            warning = format_review_warning(item, queue)
            progress = ((idx + 1) / len(queue) * 100) if queue else 0
            title_button = InlineKeyboardButton(
                text=(f"📋 {title[:55]}" if title else "📋 کپی عنوان آهنگ"),
                copy_text=CopyTextButton(text=title or ""),
            )
            await query.message.reply_text(
                f"🎵 ترک {idx+1:03d} از {len(queue)}  |  پیشرفت: {progress:.1f}%\nعنوان: {title}\nهنرمند: {artist}{warning}\n\nلطفاً فایل همین آهنگ را از منبع خودت فوروارد کن.",
                reply_markup=InlineKeyboardMarkup([
                    [title_button],
                    [InlineKeyboardButton("⏮ آهنگ قبلی", callback_data="arch:previous")],
                    [InlineKeyboardButton("⏩ رد کردن این آهنگ بدون ارسال فایل", callback_data="arch:skip_current")],
                ]),
            )
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
        for k in DEFAULT_FIELD_ORDER:
            mstate = _default_mode(current, k)
            if mstate == "remove":
                state = "🗑 Remove"
            elif mstate == "keep":
                state = "⏸ Keep"
            else:
                state = f"✅ Apply `{current.get(k, '')}`"
            text += f"• {FIELDS.get(k, k)}: {state}\n"
        text += "\nدکمه وضعیت را بزن تا بین Apply / Keep / Remove جابه‌جا شوی. دکمه ✏️ برای ویرایش مقدار Apply است."
        await query.message.edit_text(
            text,
            reply_markup=defaults_fields_keyboard(mode, current),
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if data.startswith("defaulttoggle:"):
        _, mode, key = data.split(":", 2)
        all_data = load_all_defaults()
        uid = str(query.from_user.id)
        if uid not in all_data:
            all_data[uid] = {"single": DEFAULT_SINGLE.copy(), "album": DEFAULT_ALBUM.copy()}
        current = all_data[uid].setdefault(mode, (DEFAULT_SINGLE if mode == "single" else DEFAULT_ALBUM).copy())
        modes = dict(current.get("_modes") or {})
        # migrate legacy disabled once
        disabled = set(current.get("_disabled", []))
        for dk in disabled:
            modes.setdefault(dk, "keep")
        cur = modes.get(key) or ("keep" if key in disabled else "apply")
        nxt = {"apply": "keep", "keep": "remove", "remove": "apply"}.get(cur, "apply")
        modes[key] = nxt
        current["_modes"] = modes
        current["_disabled"] = sorted([k for k, v in modes.items() if v == "keep"])
        save_all_defaults(all_data)
        label = {"apply": "✅ Apply", "keep": "⏸ Keep", "remove": "🗑 Remove"}[nxt]
        await query.message.reply_text(f"{FIELDS.get(key, key)} → {label}")
        lines = []
        for k in DEFAULT_FIELD_ORDER:
            mstate = _default_mode(current, k)
            if mstate == "remove":
                state = "🗑 Remove"
            elif mstate == "keep":
                state = "⏸ Keep"
            else:
                state = f"✅ Apply `{current.get(k, '')}`"
            lines.append(f"• {FIELDS.get(k, k)}: {state}")
        await query.message.edit_text(
            "⚙️ تنظیم تگ‌های پیش‌فرض\n\n" + "\n".join(lines) +
            "\n\nدکمه وضعیت را بزن تا بین Apply / Keep / Remove جابه‌جا شوی. دکمه ✏️ برای ویرایش مقدار Apply است.",
            reply_markup=defaults_fields_keyboard(mode, current),
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

    # ---------- ابزارهای آلبوم v4.3 ----------
    if data == "album:tools":
        await query.message.reply_text("🧰 **Album Tools**\nابزارهای پیشرفته مدیریت آلبوم:", reply_markup=album_tools_keyboard(), parse_mode=ParseMode.MARKDOWN)
        return

    if data == "albumtools:renumber":
        context.user_data["waiting"] = "albumtools_renumber_start"
        await query.message.reply_text("شماره شروع را بفرست (مثلاً 1). شماره ترک‌ها به ترتیب فعلی فهرست بازنویسی می‌شوند؛ این کار فقط پس از تأیید انجام می‌شود.")
        return

    if data == "albumtools:renumber_confirm":
        tracks = context.user_data.get("album_tracks", [])
        start = int(context.user_data.pop("albumtools_renumber_start", 1))
        backups = []
        try:
            for pos, item in enumerate(tracks):
                path = item["path"]
                backup = path + ".v43bak"
                shutil.copy2(path, backup); backups.append((path, backup))
                set_tag(path, "track", str(start + pos))
            for _, backup in backups:
                if os.path.exists(backup): os.remove(backup)
        except Exception as exc:
            for path, backup in reversed(backups):
                if os.path.exists(backup): shutil.copy2(backup, path); os.remove(backup)
            await query.message.reply_text(f"شماره‌گذاری انجام نشد و تغییرات برگشت داده شد: {exc}")
            return
        await query.message.reply_text(f"✅ شماره {len(tracks)} ترک از {start} به‌ترتیب فعلی تنظیم شد.")
        await show_album_overview(query, context)
        return

    if data == "albumtools:disc":
        context.user_data["waiting"] = "albumtools_disc_value"
        await query.message.reply_text("شماره دیسک را بفرست (مثلاً 1 یا 2):")
        return

    if data == "albumtools:reorder":
        rows = []
        tracks = context.user_data.get("album_tracks", [])
        for pos, item in enumerate(tracks):
            tags = get_tags(item["path"])
            title = tags.get("title", "—")
            controls = []
            if pos > 0: controls.append(InlineKeyboardButton("⬆️", callback_data=f"albumtools:move:{item['index']}:up"))
            if pos < len(tracks)-1: controls.append(InlineKeyboardButton("⬇️", callback_data=f"albumtools:move:{item['index']}:down"))
            rows.append([InlineKeyboardButton(f"{pos+1}. {title}"[:48], callback_data="albumtools:noop")] + controls)
        rows.append([InlineKeyboardButton("🔙 ابزارهای آلبوم", callback_data="album:tools")])
        await query.message.reply_text("برای تغییر ترتیب از فلش‌ها استفاده کن:", reply_markup=InlineKeyboardMarkup(rows))
        return

    if data.startswith("albumtools:move:"):
        _, _, raw_idx, direction = data.split(":")
        tracks = context.user_data.get("album_tracks", [])
        pos = next((i for i,t in enumerate(tracks) if int(t.get("index",-1)) == int(raw_idx)), None)
        if pos is not None:
            target = pos - 1 if direction == "up" else pos + 1
            if 0 <= target < len(tracks): tracks[pos], tracks[target] = tracks[target], tracks[pos]
        # refresh the reorder controls
        rows=[]
        for i,item in enumerate(tracks):
            title=get_tags(item["path"]).get("title","—"); controls=[]
            if i>0: controls.append(InlineKeyboardButton("⬆️",callback_data=f"albumtools:move:{item['index']}:up"))
            if i<len(tracks)-1: controls.append(InlineKeyboardButton("⬇️",callback_data=f"albumtools:move:{item['index']}:down"))
            rows.append([InlineKeyboardButton(f"{i+1}. {title}"[:48],callback_data="albumtools:noop")]+controls)
        rows.append([InlineKeyboardButton("🔙 ابزارهای آلبوم",callback_data="album:tools")])
        await query.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(rows))
        return

    if data == "albumtools:filters":
        tracks=context.user_data.get("album_tracks",[])
        missing,duplicates=_album_missing_and_duplicates(tracks)
        lines=["🔎 **بررسی آلبوم**",f"تعداد ترک‌ها: {len(tracks)}",f"بدون شماره ترک: {len(missing)}",f"شماره تکراری: {len(duplicates)}"]
        if missing: lines.append("\nبدون شماره: "+", ".join(str(t['index']) for t in missing))
        if duplicates: lines.append("\nتکراری: "+", ".join(f"{a['index']} و {b['index']} (دیسک {k[0]}، ترک {k[1]})" for a,b,k in duplicates))
        await query.message.reply_text("\n".join(lines), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 ابزارهای آلبوم",callback_data="album:tools")]]), parse_mode=ParseMode.MARKDOWN)
        return

    if data == "albumtools:copy":
        context.user_data["waiting"]="albumtools_copy_fields"
        await query.message.reply_text("نام فیلدهایی که می‌خواهی کپی شوند را با کاما بفرست. مثال: genre, composer, albumartist")
        return

    if data == "albumtools:noop":
        await query.message.reply_text()
        return

    # ---------- حذف یک آرتیست از فیلد Artist تمام ترک‌های آلبوم ----------
    if data == "album:remove_artist":
        tracks = context.user_data.get("album_tracks", [])
        if not tracks:
            await query.message.reply_text("آلبوم فعالی برای ویرایش وجود ندارد.")
            return
        context.user_data["waiting"] = "album_remove_artist_name"
        context.user_data.pop("album_remove_artist_pending", None)
        await query.message.reply_text(
            "نام دقیق آرتیست یا نام تبلیغاتی را که باید از فیلد Artist حذف شود بفرست.\n\n"
            "مثال: Promo Channel\n"
            "فقط تطبیق کامل نام حذف می‌شود؛ نام خواننده‌های دیگر دست‌نخورده می‌ماند."
        )
        return

    if data == "album:remove_artist_cancel":
        context.user_data.pop("album_remove_artist_pending", None)
        await query.message.reply_text("عملیات حذف آرتیست لغو شد.")
        return

    if data == "album:remove_artist_confirm":
        pending = context.user_data.pop("album_remove_artist_pending", None)
        if not pending:
            await query.message.reply_text("درخواست حذف منقضی شده؛ دوباره از منوی آلبوم شروع کن.")
            return
        target_norm = normalize_name(pending["name"])
        backups = []
        changed = []
        try:
            for item in pending["matches"]:
                track_path = item["path"]
                if not os.path.isfile(track_path):
                    raise RuntimeError(f"فایل ترک {item.get('index')} پیدا نشد.")
                backup_path = track_path + ".removeartist.bak"
                shutil.copy2(track_path, backup_path)
                backups.append((track_path, backup_path))
                current = str(get_tags(track_path).get("artist", "") or "").strip()
                remaining = [part for part in split_artists(current) if normalize_name(part) != target_norm]
                new_artist = " & ".join(remaining) if remaining else ""
                # Write directly so failures trigger rollback instead of being swallowed by set_tag.
                audio_file = mt_load(track_path)
                mt_key = MT_KEYS.get("artist", "artist")
                if new_artist:
                    audio_file[mt_key] = new_artist
                else:
                    try:
                        audio_file.remove_tag(mt_key)
                    except Exception:
                        pass
                audio_file.save()
                changed.append(item.get("index"))
            for _, backup_path in backups:
                if os.path.exists(backup_path):
                    os.remove(backup_path)
        except Exception as exc:
            for track_path, backup_path in reversed(backups):
                if os.path.exists(backup_path):
                    shutil.copy2(backup_path, track_path)
                    os.remove(backup_path)
            await query.message.reply_text(f"حذف انجام نشد؛ تغییرات برگشت داده شد.\nجزئیات: {exc}")
            return
        await query.message.reply_text(
            f"✅ نام «{pending['name']}» از فیلد Artist تعداد {len(changed)} ترک حذف شد.\n"
            f"ترک‌های بدون فایل قابل‌ویرایش و ردشده: {pending['skipped']}"
        )
        await show_album_overview(query, context)
        return

    # ---------- حالت آلبوم ----------
    if data == "album:finish":
        tracks = context.user_data.get("album_tracks", [])
        if not tracks:
            await query.message.reply_text("هنوز هیچ ترکی دریافت نشده.")
            return
        main_artists = context.user_data.get("main_artists", [])
        sort_album_tracks(tracks)
        for t in tracks:
            p = t.get("path")
            if not p or not os.path.isfile(p):
                continue
            tags = get_tags(p)
            artist = tags.get("artist", "—")
            set_tag(p, "albumartist", " , ".join(main_artists) if main_artists else artist)
        # دیگر فایل جدید به آلبوم اضافه نشود
        context.user_data["album_mode"] = False
        await finish_progress_dashboard(context, "album", keep=False)
        await show_album_overview(query, context)
        return

    if data == "album:select_menu":
        tracks = context.user_data.get("album_tracks", [])
        context.user_data["album_selected"] = []
        await query.message.reply_text("ترک‌هایی را که می‌خواهی ویرایش گروهی شوند انتخاب کن:", reply_markup=album_select_keyboard(tracks, set()))
        return

    if data.startswith("album:select:"):
        idx = int(data.split(":")[2])
        selected = set(context.user_data.get("album_selected", []))
        if idx in selected: selected.remove(idx)
        else: selected.add(idx)
        context.user_data["album_selected"] = sorted(selected)
        await query.message.edit_reply_markup(reply_markup=album_select_keyboard(context.user_data.get("album_tracks", []), selected))
        return

    if data == "album:select_done":
        selected = context.user_data.get("album_selected", [])
        if not selected:
            await query.message.reply_text("حداقل یک ترک را انتخاب کن.")
            return
        if context.user_data.pop("albumtools_copy_mode", False):
            source = _album_track_by_index(context.user_data.get("album_tracks", []), context.user_data.get("albumtools_copy_source"))
            targets = [t for t in context.user_data.get("album_tracks", []) if t.get("index") in set(selected) and t is not source]
            keys = context.user_data.get("albumtools_copy_keys", [])
            backups=[]
            try:
                src_tags=get_tags(source["path"])
                for item in targets:
                    path=item["path"]; backup=path+".v43bak"; shutil.copy2(path,backup); backups.append((path,backup))
                    for key in keys:
                        val=src_tags.get(key,"—")
                        if val not in (None,"","—"): set_tag(path,key,val)
            except Exception as exc:
                for path,backup in reversed(backups):
                    if os.path.exists(backup): shutil.copy2(backup,path); os.remove(backup)
                await query.message.reply_text(f"کپی تگ انجام نشد و تغییرات برگشت داده شد: {exc}")
                return
            for _,backup in backups:
                if os.path.exists(backup): os.remove(backup)
            context.user_data["album_selected"]=[]
            await query.message.reply_text(f"✅ {len(targets)} ترک با فیلدهای انتخاب‌شده به‌روزرسانی شد.")
            await show_album_overview(query,context)
            return
        context.user_data["waiting"] = "album_selected_field"
        await query.message.reply_text(f"✅ {len(selected)} ترک انتخاب شد. نام فیلدی را که می‌خواهی تغییر کند بفرست: artist, albumartist, album, genre, composer, publisher, year, comment")
        return

    if data == "album:back":
        try:
            await query.message.delete()
        except TelegramError:
            pass
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

    if data == "action:unreleased":
        if not path or not os.path.exists(path):
            await query.message.reply_text("اول یک آهنگ را باز کن.")
            return
        has_cover = UNRELEASED_COVER_FILE.exists()
        rows = []
        if has_cover:
            rows.append([InlineKeyboardButton("🖼️ جایگزینی کاور با کاور پیش‌فرض", callback_data="unrel:replace")])
        else:
            rows.append([InlineKeyboardButton("🖼️ کاور پیش‌فرض ثبت نشده", callback_data="unrel:no_cover")])
        rows.extend([
            [InlineKeyboardButton("⏭ حفظ کاور فعلی و فقط تغییر تگ", callback_data="unrel:keep")],
            [InlineKeyboardButton("❌ لغو عملیات", callback_data="unrel:cancel")],
        ])
        await query.message.reply_text(
            "🏷️ **Unreleased Track**\n\nیکی از گزینه‌ها را انتخاب کن:",
            reply_markup=InlineKeyboardMarkup(rows), parse_mode=ParseMode.MARKDOWN,
        )
        return
    if data == "unrel:no_cover":
        await query.message.reply_text("ابتدا از منوی مدیریت کاور، یک تصویر پیش‌فرض ثبت کن.")
        return

    if data == "album:all":
        context.user_data["waiting"] = "album_all_field"
        await send_temporary_prompt(query.message, context,
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
            p = t.get("path")
            if not p or not os.path.isfile(p):
                continue
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

    if data.startswith("albumsummary:"):
        draft = context.user_data.get("album_summary_draft")
        if not draft:
            await query.message.reply_text("خلاصه فعالی برای ویرایش وجود ندارد. دوباره Album Summary را باز کن.")
            return
        action = data.split(":", 1)[1]
        entries = draft.get("entries", [])
        if action == "menu":
            draft_text = build_album_summary_text(entries, context.user_data.get("main_artists", []), draft.get("album_name", ""))
            await query.message.edit_text(draft_text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ افزودن آهنگ", callback_data="albumsummary:add")],
                [InlineKeyboardButton("✏️ ویرایش آهنگ موجود", callback_data="albumsummary:edit")],
                [InlineKeyboardButton("🗑 حذف آهنگ", callback_data="albumsummary:delete")],
                [InlineKeyboardButton("✅ تأیید و ثبت خلاصه", callback_data="albumsummary:confirm")],
                [InlineKeyboardButton("↩️ لغو ویرایش", callback_data="albumsummary:cancel")],
            ]))
        elif action == "add":
            try:
                await query.message.delete()
            except TelegramError:
                pass
            context.user_data["waiting"] = "albumsummary:add_position"
            await send_temporary_prompt(query.message, context, f"جایگاه آهنگ جدید را مشخص کن (۱ تا {len(entries)+1}):")
        elif action == "edit":
            try:
                await query.message.delete()
            except TelegramError:
                pass
            if not entries:
                await query.message.reply_text("فهرست آهنگ خالی است.")
            else:
                context.user_data["waiting"] = "albumsummary:edit_position"
                await send_temporary_prompt(query.message, context, f"شماره آهنگی را که می‌خواهی ویرایش کنی بفرست (۱ تا {len(entries)}):")
        elif action == "delete":
            try:
                await query.message.delete()
            except TelegramError:
                pass
            if not entries:
                await query.message.reply_text("فهرست آهنگ خالی است.")
            else:
                context.user_data["waiting"] = "albumsummary:delete_position"
                await send_temporary_prompt(query.message, context, f"شماره آهنگی را که می‌خواهی حذف کنی بفرست (۱ تا {len(entries)}):")
        elif action == "confirm":
            try:
                await query.message.delete()
            except TelegramError:
                pass
            # Store only the edited summary text/data; never mutate MP3 tags or files.
            context.user_data["album_summary_confirmed"] = dict(draft)
            context.user_data["album_summary_draft"] = None
            context.user_data["waiting"] = None
            final_text = build_album_summary_text(entries, context.user_data.get("main_artists", []), draft.get("album_name", ""))
            await query.message.reply_text("✅ خلاصه آلبوم نهایی شد:\n\n" + final_text, parse_mode=ParseMode.MARKDOWN)
        elif action == "cancel":
            try:
                await query.message.delete()
            except TelegramError:
                pass
            context.user_data["album_summary_draft"] = None
            context.user_data["waiting"] = None
            await query.message.reply_text("ویرایش خلاصه لغو شد؛ اطلاعات اصلی آلبوم تغییری نکرد.")
        return

    if data == "album:save_all":
        await save_album_all(query, context)
        return

    # ---------- Auto Forward callbacks ----------
    if isinstance(data, str) and data.startswith("autofw:"):
        uid = query.from_user.id if query.from_user else None
        if not can_auto_forward(uid):
            await query.message.reply_text(
                "⛔ شما اجازه استفاده از قابلیت Auto Forward را ندارید. این قابلیت فقط برای مالک و ادمین‌های ربات فعال است."
            )
            return

        # Single / Archive: autofw:single:1 or autofw:archive:2 (legacy autofw:single → slot 1)
        if data.startswith("autofw:single") or data.startswith("autofw:archive"):
            parts = data.split(":")
            slot = parts[2] if len(parts) >= 3 else "1"
            ch = get_af_channel(context, slot)
            if not ch:
                await query.message.reply_text(
                    f"کانال مقصد {slot} تنظیم نشده است.\nمالک ربات باید با /setchannel{slot} آن را تنظیم کند."
                )
                return
            info = context.user_data.get("af_last_output")
            if not info or not info.get("output_path"):
                await query.message.reply_text("فایل خروجی برای Auto Forward در دسترس نیست. دوباره Save کن.")
                return
            st = _status_for_slot(info, slot)
            if st == "sending":
                await query.message.reply_text("در حال ارسال است؛ لطفاً صبر کن.")
                return
            if st == "sent":
                await query.message.reply_text("این آهنگ قبلاً با موفقیت به این کانال ارسال شده است.")
                return
            _set_status_for_slot(info, slot, "sending")
            status, detail = await send_processed_to_channel(
                context.bot,
                ch["chat_id"],
                info["output_path"],
                filename=info.get("filename") or "track.mp3",
                title=info.get("title"),
                performer=info.get("performer"),
                duration=info.get("duration"),
                cover=info.get("cover"),
            )
            _set_status_for_slot(info, slot, status)
            label = af_channel_label(context, slot)
            if status == "sent":
                extra = " (به‌صورت Document)" if detail == "document" else ""
                await query.message.reply_text(f"✅ با موفقیت به «{label}» ارسال شد{extra}.")
            elif status == "unknown":
                await query.message.reply_text(
                    f"⚠️ وضعیت ارسال به «{label}» نامشخص است.\n{detail}\n"
                    "لطفاً کانال را بررسی کن. برای جلوگیری از ارسال تکراری، دوباره ارسال خودکار انجام نشد."
                )
            else:
                await query.message.reply_text(f"❌ ارسال به «{label}» ناموفق بود.\n{detail}\nمی‌تونی دوباره دکمه را بزنی.")
            return

        if data.startswith("autofw:album_toggle:"):
            # autofw:album_toggle:SLOT:IDX
            parts = data.split(":")
            try:
                slot = parts[2]
                idx = int(parts[3])
            except (IndexError, ValueError):
                return
            tracks = context.user_data.get("af_album_tracks") or []
            for t in tracks:
                if t.get("index") == idx and _status_for_slot(t, slot) in ("pending", "failed"):
                    sel = t.setdefault("selected_slots", {})
                    sel[str(slot)] = not sel.get(str(slot), False)
                    break
            try:
                await query.message.edit_reply_markup(reply_markup=af_album_keyboard(tracks, slot))
            except TelegramError:
                pass
            return

        if data.startswith("autofw:album_noop:"):
            await query.answer("این ترک قابل انتخاب مجدد نیست.", show_alert=False)
            return

        if data.startswith("autofw:album_all:"):
            slot = data.split(":")[2]
            tracks = context.user_data.get("af_album_tracks") or []
            for t in tracks:
                if _status_for_slot(t, slot) in ("pending", "failed"):
                    t.setdefault("selected_slots", {})[str(slot)] = True
            try:
                await query.message.edit_reply_markup(reply_markup=af_album_keyboard(tracks, slot))
            except TelegramError:
                pass
            return

        if data.startswith("autofw:album_none:"):
            slot = data.split(":")[2]
            tracks = context.user_data.get("af_album_tracks") or []
            for t in tracks:
                t.setdefault("selected_slots", {})[str(slot)] = False
            try:
                await query.message.edit_reply_markup(reply_markup=af_album_keyboard(tracks, slot))
            except TelegramError:
                pass
            return

        if data.startswith("autofw:album_slot:"):
            slot = data.split(":")[2]
            if not get_af_channel(context, slot):
                await query.message.reply_text(
                    f"کانال {slot} تنظیم نشده. مالک ربات باید /setchannel{slot} را اجرا کند."
                )
                return
            tracks = context.user_data.get("af_album_tracks") or []
            context.user_data["af_album_slot"] = slot
            try:
                await query.message.edit_text(
                    f"انتخاب ترک برای ارسال به «{af_channel_label(context, slot)}»:",
                    reply_markup=af_album_keyboard(tracks, slot),
                )
            except TelegramError:
                try:
                    await query.message.edit_reply_markup(reply_markup=af_album_keyboard(tracks, slot))
                except TelegramError:
                    pass
            return

        if data == "autofw:album_skip":
            context.user_data["album_mode"] = False
            context.user_data["album_tracks"] = []
            context.user_data.pop("af_album_tracks", None)
            context.user_data["af_album_busy"] = False
            await query.message.reply_text(
                "Auto Forward رد شد. از حالت آلبوم خارج شدی.",
                reply_markup=main_reply_keyboard(),
            )
            return

        if data.startswith("autofw:album_send:") or data.startswith("autofw:album_retry:"):
            slot = data.split(":")[2]
            ch = get_af_channel(context, slot)
            if not ch:
                await query.message.reply_text(
                    f"کانال مقصد {slot} تنظیم نشده است.\nمالک ربات باید با /setchannel{slot} آن را تنظیم کند."
                )
                return
            tracks = context.user_data.get("af_album_tracks") or []
            if context.user_data.get("af_album_busy"):
                await query.message.reply_text("در حال ارسال است؛ لطفاً صبر کن.")
                return
            if data.startswith("autofw:album_retry:"):
                targets = [t for t in tracks if _status_for_slot(t, slot) == "failed"]
                for t in targets:
                    t.setdefault("selected_slots", {})[str(slot)] = True
            else:
                targets = [
                    t for t in tracks
                    if (t.get("selected_slots") or {}).get(str(slot))
                    and _status_for_slot(t, slot) in ("pending", "failed")
                ]
            if not targets:
                await query.message.reply_text("هیچ ترک مجازی برای ارسال انتخاب نشده است.")
                return
            context.user_data["af_album_busy"] = True
            sent_n = failed_n = unknown_n = 0
            for t in targets:
                if _status_for_slot(t, slot) == "sent":
                    continue
                _set_status_for_slot(t, slot, "sending")
                try:
                    await query.message.edit_reply_markup(reply_markup=af_album_keyboard(tracks, slot))
                except TelegramError:
                    pass
                status, detail = await send_processed_to_channel(
                    context.bot,
                    ch["chat_id"],
                    t.get("output_path"),
                    filename=t.get("filename") or "track.mp3",
                    title=t.get("title"),
                    performer=t.get("artist"),
                    duration=t.get("duration"),
                    cover=t.get("cover"),
                )
                _set_status_for_slot(t, slot, status)
                t.setdefault("selected_slots", {})[str(slot)] = False
                if status == "sent":
                    sent_n += 1
                elif status == "unknown":
                    unknown_n += 1
                else:
                    failed_n += 1
            context.user_data["af_album_busy"] = False
            try:
                await query.message.edit_reply_markup(reply_markup=af_album_keyboard(tracks, slot))
            except TelegramError:
                pass
            label = af_channel_label(context, slot)
            parts = [f"✅ موفق: {sent_n}", f"❌ ناموفق: {failed_n}"]
            if unknown_n:
                parts.append(f"❓ نامشخص: {unknown_n}")
            msg = f"گزارش Auto Forward آلبوم → {label}:\n" + " | ".join(parts)
            if failed_n:
                msg += "\nترک‌های ناموفق را می‌توانی دوباره انتخاب یا از «تلاش مجدد ناموفق‌ها» استفاده کنی."
            if unknown_n:
                msg += "\nبرای موارد نامشخص کانال را بررسی کن؛ ارسال خودکار مجدد انجام نشد."
            await query.message.reply_text(msg)
            return

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
        try:
            await query.message.delete()
        except TelegramError:
            pass
        await send_temporary_prompt(query.message, context,
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

        # Keep output available for Auto Forward until user finishes or session ends
        context.user_data["af_last_output"] = {
            "output_path": output_path,
            "filename": filename + ".mp3",
            "title": tags.get("title") if tags.get("title") != "—" else filename,
            "performer": tags.get("artist") if tags.get("artist") != "—" else None,
            "duration": duration,
            "cover": cover,
            "status": "pending",
            "statuses": {"1": "pending", "2": "pending"},
        }

        in_archive = bool(
            context.user_data.get("archive")
            and isinstance(context.user_data.get("archive_current"), int)
        )
        uid = query.from_user.id if query.from_user else None
        reply_kb = (
            af_archive_keyboard(context, uid)
            if in_archive
            else af_single_keyboard(context, uid)
        )

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
                    reply_markup=reply_kb,
                )
        except Exception as e:
            print("reply_audio error, falling back to document:", e)
            with open(output_path, "rb") as f:
                await query.message.reply_document(
                    document=f,
                    filename=filename + ".mp3",
                    caption="تگ‌های آهنگ ذخیره شدند! 🎵",
                    reply_markup=reply_kb,
                )
        # Archive tracks are marked complete only after the explicit button press.
        # Single-track and album workflows remain unchanged.
        return


def _parse_track_disc(value):
    """Parse ID3-style values such as 03, 3/12, or 2/3 into sortable numbers."""
    value = str(value or "").strip()
    m = re.match(r"^(\d+)(?:\s*/\s*(\d+))?", value)
    return (int(m.group(1)), int(m.group(2)) if m and m.group(2) else None) if m else (None, None)


def sort_album_tracks(tracks):
    """Sort by disc number, then track number; unknown numbers remain at the end."""
    def key(item):
        if not item.get("path") or not os.path.isfile(item.get("path", "")):
            return (1, 1, 10**9, item.get("index", 10**9))
        tags = get_tags(item["path"])
        track_no, _ = _parse_track_disc(tags.get("track"))
        disc_no, _ = _parse_track_disc(tags.get("disc"))
        return (0 if track_no is not None else 1,
                disc_no if disc_no is not None else 1,
                track_no if track_no is not None else 10**9,
                item.get("index", 10**9))
    tracks.sort(key=key)
    return tracks


def album_select_keyboard(tracks, selected):
    rows = []
    for t in tracks:
        idx = t["index"]
        tags = get_tags(t["path"]) if t.get("path") and os.path.isfile(t["path"]) else {}
        mark = "☑️" if idx in selected else "⬜"
        title = tags.get("title", t.get("summary_title", "—"))
        album = (tags.get("album", "—") if tags else t.get("summary_album", "—")) or "—"
        rows.append([InlineKeyboardButton(f"{mark} {idx}. {title} · {album}"[:60], callback_data=f"album:select:{idx}")])
    rows.append([InlineKeyboardButton("✅ ادامه با انتخاب‌ها", callback_data="album:select_done")])
    rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="album:back")])
    return InlineKeyboardMarkup(rows)


def album_tools_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔢 شماره‌گذاری مجدد ترک‌ها", callback_data="albumtools:renumber")],
        [InlineKeyboardButton("💿 تنظیم شماره دیسک", callback_data="albumtools:disc")],
        [InlineKeyboardButton("↕️ جابه‌جایی دستی ترک‌ها", callback_data="albumtools:reorder")],
        [InlineKeyboardButton("🔎 فیلتر و بررسی ترک‌ها", callback_data="albumtools:filters")],
        [InlineKeyboardButton("📋 کپی تگ از یک ترک", callback_data="albumtools:copy")],
        [InlineKeyboardButton("🔙 بازگشت به آلبوم", callback_data="album:back")],
    ])


def _album_track_by_index(tracks, idx):
    return next((t for t in tracks if int(t.get("index", -1)) == int(idx)), None)


def _album_missing_and_duplicates(tracks):
    missing, seen, duplicates = [], {}, []
    for t in tracks:
        if not t.get("path") or not os.path.isfile(t["path"]):
            missing.append(t)
            continue
        tags = get_tags(t["path"])
        n, _ = _parse_track_disc(tags.get("track"))
        d, _ = _parse_track_disc(tags.get("disc"))
        if n is None:
            missing.append(t)
        else:
            key = (d or 1, n)
            if key in seen:
                duplicates.append((seen[key], t, key))
            else:
                seen[key] = t
    return missing, duplicates


async def show_album_overview(query, context):
    tracks = context.user_data.get("album_tracks", [])
    main_artists = context.user_data.get("main_artists", [])
    text = "💿 **Album Tracks**\n\n"
    text += f"Main Artists: {' , '.join(main_artists) if main_artists else '—'}\n"
    text += f"Total tracks: {len(tracks)}\n\n"
    for position, t in enumerate(tracks, 1):
        if t.get("path") and os.path.isfile(t["path"]):
            tags = get_tags(t["path"])
            title, artist = tags.get("title", "—"), tags.get("artist", "—")
            album = (tags.get("album", "—") or "—").strip()
            fname = generate_filename(artist, title, main_artists)
            extra = f"   💿 Album: {album}\n   📁 {fname}\n"
        else:
            title = t.get("summary_title") or Path(t.get("original_name", "track")).stem
            artist = t.get("summary_artist", "—")
            album = t.get("summary_album", "—") or "—"
            extra = f"   💿 Album: {album}\n   ⚠️ فقط اطلاعات تلگرام؛ فایل دانلود نشده\n"
        text += f"{position}. {title} — {artist}\n" + extra

    buttons = []
    for position, t in enumerate(tracks, 1):
        if t.get("path") and os.path.isfile(t["path"]):
            buttons.append([InlineKeyboardButton(f"✏️ Edit Track {position}", callback_data=f"album:edit:{t['index']}")])
    buttons.append(
        [InlineKeyboardButton("📌 Apply Default Tags (Album)", callback_data="album:defaults")]
    )
    buttons.append(
        [InlineKeyboardButton("📝 Apply to All Tracks", callback_data="album:all")]
    )
    buttons.append(
        [InlineKeyboardButton("🎯 Select Tracks for Group Edit", callback_data="album:select_menu")]
    )
    buttons.append([InlineKeyboardButton("🧰 Album Tools", callback_data="album:tools")])
    buttons.append([InlineKeyboardButton("🧹 حذف آرتیست از همه ترک‌ها", callback_data="album:remove_artist")])
    buttons.append(
        [InlineKeyboardButton("📊 Album Summary", callback_data="album:summary")]
    )
    buttons.append(
        [InlineKeyboardButton("📩 Save All & Send", callback_data="album:save_all")]
    )

    await query.message.reply_text(
        text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode=ParseMode.MARKDOWN
    )


def build_album_summary_text(entries, main_artists, album_name):
    all_artists = set()
    total_duration = 0
    lines = []
    for i, item in enumerate(entries, 1):
        title = str(item.get("title") or "—").strip()
        artist = str(item.get("artist") or "—").strip()
        duration = int(item.get("duration") or 0)
        total_duration += duration
        for artist_name in split_artists(artist):
            if artist_name and artist_name != "—": all_artists.add(artist_name)
        dtext = f"{duration // 60}:{duration % 60:02d}" if duration else "unavailable"
        suffix = " ⚠️" if item.get("downloaded") is False else ""
        lines.append(f"{i}. {title} ({dtext}) — {artist}{suffix}")
    return (
        "📊 **Album Summary**\n\n"
        f"Album Artist(s): {', '.join(main_artists) if main_artists else 'unavailable'}\n"
        f"Album Name: {album_name or 'unavailable'}\n"
        f"Number of main album artists: {len(main_artists)}\n"
        f"Number of unique artists across all tracks: {len(all_artists)}\n"
        f"Total number of tracks: {len(entries)}\n"
        f"Total album duration: {total_duration // 60}:{total_duration % 60:02d}\n\n"
        "Track list:\n" + "\n".join(lines)
    )


def album_summary_entries(context):
    entries = []
    for t in context.user_data.get("album_tracks", []):
        if t.get("path") and os.path.isfile(t["path"]):
            tags = get_tags(t["path"])
            try: duration = int(MP3(t["path"]).info.length)
            except Exception: duration = int(t.get("original_duration") or 0)
            entries.append({"title": tags.get("title", "—"), "artist": tags.get("artist", "—"), "duration": duration, "downloaded": True})
        else:
            entries.append({"title": t.get("summary_title") or Path(t.get("original_name", "track")).stem, "artist": t.get("summary_artist", "—"), "duration": int(t.get("summary_duration") or 0), "downloaded": False})
    return entries


async def send_album_summary(query, context):
    tracks = context.user_data.get("album_tracks", [])
    if not tracks:
        await query.message.reply_text("هنوز ترکی برای خلاصه آلبوم وجود ندارد.")
        return
    confirmed = context.user_data.get("album_summary_confirmed")
    if confirmed:
        entries = [dict(x) for x in confirmed.get("entries", [])]
        album_name = confirmed.get("album_name", "")
    else:
        entries = album_summary_entries(context)
        album_names = []
        for t in tracks:
            if t.get("path") and os.path.isfile(t["path"]):
                name = (get_tags(t["path"]).get("album", "") or "").strip()
                if name and name != "—" and name not in album_names: album_names.append(name)
        album_name = " / ".join(album_names)
    context.user_data["album_summary_draft"] = {"entries": entries, "album_name": album_name}
    text = build_album_summary_text(entries, context.user_data.get("main_artists", []), album_name)
    await query.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ ویرایش خلاصه آلبوم", callback_data="albumsummary:menu")],
        [InlineKeyboardButton("🔙 بازگشت به آلبوم", callback_data="album:back")],
    ]))


async def show_album_summary_draft(message, context):
    draft = context.user_data.get("album_summary_draft") or {}
    entries = draft.get("entries", [])
    text = build_album_summary_text(entries, context.user_data.get("main_artists", []), draft.get("album_name", ""))
    await message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ افزودن آهنگ", callback_data="albumsummary:add")],
        [InlineKeyboardButton("✏️ ویرایش آهنگ موجود", callback_data="albumsummary:edit")],
        [InlineKeyboardButton("🗑 حذف آهنگ", callback_data="albumsummary:delete")],
        [InlineKeyboardButton("✅ تأیید و ثبت خلاصه", callback_data="albumsummary:confirm")],
        [InlineKeyboardButton("↩️ لغو ویرایش", callback_data="albumsummary:cancel")],
    ]))


async def save_album_all(query, context):
    tracks = context.user_data.get("album_tracks", [])
    main_artists = context.user_data.get("main_artists", [])
    for t in tracks:
        path = t.get("path")
        if not path or not os.path.isfile(path):
            continue
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
    # Build Auto Forward list from real output files (keep files until AF finishes)
    af_tracks = []
    for idx, t in enumerate(tracks):
        path = t.get("path")
        if not path or not os.path.isfile(path):
            continue
        tags = get_tags(path)
        filename = generate_filename(
            tags.get("artist", ""), tags.get("title", ""), main_artists
        )
        output_path = os.path.join(
            context.user_data["album_folder"], filename + ".mp3"
        )
        if not os.path.isfile(output_path):
            continue
        af_tracks.append({
            "index": idx,
            "title": tags.get("title") if tags.get("title") != "—" else filename,
            "artist": tags.get("artist") if tags.get("artist") != "—" else "",
            "filename": filename + ".mp3",
            "output_path": output_path,
            "duration": t.get("original_duration"),
            "cover": t.get("original_cover"),
            "status": "pending",
            "statuses": {"1": "pending", "2": "pending"},
            "selected": False,
            "selected_slots": {"1": False, "2": False},
        })
    context.user_data["af_album_tracks"] = af_tracks
    context.user_data["af_album_busy"] = False
    context.user_data["af_album_slot"] = "1"
    # Keep album_folder and tracks until user finishes or skips Auto Forward
    context.user_data["editing_album_track"] = False
    uid = query.from_user.id if query.from_user else None
    if af_tracks and can_auto_forward(uid):
        slot = "1" if get_af_channel(context, "1") else ("2" if get_af_channel(context, "2") else None)
        if slot:
            await query.message.reply_text(
                f"✅ همه ترک‌های آلبوم ارسال شدند ({len(af_tracks)} ترک).\n"
                f"ترک‌های موردنظر را برای «{af_channel_label(context, slot)}» انتخاب کن:",
                reply_markup=af_album_keyboard(af_tracks, slot),
            )
        else:
            await query.message.reply_text(
                f"✅ همه ترک‌های آلبوم ارسال شدند ({len(af_tracks)} ترک).\n"
                "کانال مقصدی تنظیم نشده؛ مالک می‌تواند /setchannel1 و /setchannel2 را اجرا کند.",
                reply_markup=main_reply_keyboard(),
            )
            context.user_data["album_mode"] = False
            context.user_data["album_tracks"] = []
    elif af_tracks:
        await query.message.reply_text(
            f"✅ همه ترک‌های آلبوم ارسال شدند ({len(af_tracks)} ترک).\nاز حالت آلبوم خارج شدی.",
            reply_markup=main_reply_keyboard(),
        )
        context.user_data["album_mode"] = False
        context.user_data["album_tracks"] = []
    else:
        context.user_data["album_mode"] = False
        context.user_data["album_tracks"] = []
        await query.message.reply_text(
            "✅ همه ترک‌های آلبوم ارسال شدند.\nاز حالت آلبوم خارج شدی.",
            reply_markup=main_reply_keyboard(),
        )


# ==================== Auto Forward ====================
def is_owner(user_id: Optional[int]) -> bool:
    return bool(OWNER_ID and user_id and int(user_id) == int(OWNER_ID))


def can_auto_forward(user_id: Optional[int]) -> bool:
    """Owner always; admins listed in ADMIN_IDS; otherwise denied."""
    if not user_id:
        return False
    uid = int(user_id)
    if OWNER_ID and uid == int(OWNER_ID):
        return True
    return uid in ADMIN_IDS


def _af_channels(context: ContextTypes.DEFAULT_TYPE) -> dict:
    return context.application.bot_data.setdefault("auto_forward_channels", {})


def get_af_channel(context: ContextTypes.DEFAULT_TYPE, slot: str):
    """slot is '1' (main) or '2' (full archive). Returns dict or None."""
    ch = _af_channels(context).get(str(slot))
    return ch if ch and ch.get("chat_id") else None


def set_af_channel(context: ContextTypes.DEFAULT_TYPE, slot: str, chat_id, title: str = ""):
    channels = _af_channels(context)
    channels[str(slot)] = {"chat_id": chat_id, "title": title or str(chat_id)}


def af_channel_label(context: ContextTypes.DEFAULT_TYPE, slot: str) -> str:
    ch = get_af_channel(context, slot)
    if not ch:
        return "Main Channel" if str(slot) == "1" else "Full Archive"
    return ch.get("title") or ("Main Channel" if str(slot) == "1" else "Full Archive")


# Backward-compat helpers used by older call sites
def get_af_chat_id(context: ContextTypes.DEFAULT_TYPE):
    ch = get_af_channel(context, "1")
    return ch["chat_id"] if ch else context.application.bot_data.get("auto_forward_chat_id")


def get_af_chat_title(context: ContextTypes.DEFAULT_TYPE) -> str:
    ch = get_af_channel(context, "1")
    if ch:
        return ch.get("title") or ""
    return context.application.bot_data.get("auto_forward_chat_title") or ""


def set_af_destination(context: ContextTypes.DEFAULT_TYPE, chat_id, title: str = ""):
    set_af_channel(context, "1", chat_id, title)
    context.application.bot_data["auto_forward_chat_id"] = chat_id
    context.application.bot_data["auto_forward_chat_title"] = title or str(chat_id)


def _is_audio_media_error(exc: BaseException) -> bool:
    """True only for errors that clearly relate to sending as Telegram Audio."""
    msg = str(exc).lower()
    keywords = (
        "audio", "voice", "thumbnail", "thumb", "duration", "title",
        "performer", "wrong file", "file must be", "media_empty",
        "wrong type", "unsupported", "too big for audio",
    )
    return any(k in msg for k in keywords)


async def send_processed_to_channel(
    bot,
    chat_id,
    output_path: str,
    *,
    filename: str,
    title: Optional[str] = None,
    performer: Optional[str] = None,
    duration: Optional[int] = None,
    cover: Optional[bytes] = None,
) -> tuple:
    """
    Send processed file to destination channel.
    Returns (status, detail) where status is 'sent', 'failed', or 'unknown'.
    Uses send_audio first; falls back to send_document only on clear audio-media errors.
    Network/timeout/permission errors do NOT auto-fallback (avoids possible duplicates).
    """
    if not output_path or not os.path.isfile(output_path):
        return "failed", "فایل خروجی در دسترس نیست."

    thumb = None
    try:
        if cover:
            thumb = make_telegram_thumbnail(cover)
    except Exception:
        thumb = None

    try:
        with open(output_path, "rb") as f:
            await bot.send_audio(
                chat_id=chat_id,
                audio=f,
                filename=filename,
                title=title or None,
                performer=performer or None,
                duration=duration,
                thumbnail=thumb,
                caption=None,
            )
        return "sent", "audio"
    except (TimedOut, NetworkError) as e:
        print("AF send_audio network/timeout:", repr(e))
        return "unknown", f"وضعیت ارسال نامشخص (شبکه/تایم‌اوت): {e}"
    except TelegramError as e:
        if not _is_audio_media_error(e):
            print("AF send_audio non-media error:", repr(e))
            return "failed", str(e)
        print("AF send_audio media error, trying document:", repr(e))
    except Exception as e:
        print("AF send_audio unexpected:", repr(e))
        return "failed", str(e)

    try:
        with open(output_path, "rb") as f:
            await bot.send_document(
                chat_id=chat_id,
                document=f,
                filename=filename,
                caption=None,
            )
        return "sent", "document"
    except (TimedOut, NetworkError) as e:
        print("AF send_document network/timeout:", repr(e))
        return "unknown", f"وضعیت ارسال نامشخص (شبکه/تایم‌اوت): {e}"
    except Exception as e:
        print("AF send_document error:", repr(e))
        return "failed", str(e)


def _status_for_slot(info: dict, slot: str) -> str:
    statuses = info.get("statuses") or {}
    if str(slot) in statuses:
        return statuses[str(slot)]
    # legacy single status
    if str(slot) == "1" and info.get("status"):
        return info.get("status")
    return "pending"


def _set_status_for_slot(info: dict, slot: str, status: str):
    statuses = info.setdefault("statuses", {})
    statuses[str(slot)] = status
    if str(slot) == "1":
        info["status"] = status


def af_single_keyboard(context: ContextTypes.DEFAULT_TYPE, user_id: Optional[int] = None) -> Optional[InlineKeyboardMarkup]:
    if user_id is not None and not can_auto_forward(user_id):
        return None
    rows = []
    ch1 = get_af_channel(context, "1")
    ch2 = get_af_channel(context, "2")
    if ch1:
        rows.append([InlineKeyboardButton(
            f"🚀 AF — {af_channel_label(context, '1')[:40]}",
            callback_data="autofw:single:1",
        )])
    if ch2:
        rows.append([InlineKeyboardButton(
            f"🚀 AF — {af_channel_label(context, '2')[:40]}",
            callback_data="autofw:single:2",
        )])
    if not rows:
        return None
    return InlineKeyboardMarkup(rows)


def af_archive_keyboard(context: ContextTypes.DEFAULT_TYPE, user_id: Optional[int] = None) -> InlineKeyboardMarkup:
    rows = []
    if user_id is None or can_auto_forward(user_id):
        ch1 = get_af_channel(context, "1")
        ch2 = get_af_channel(context, "2")
        if ch1:
            rows.append([InlineKeyboardButton(
                f"🚀 AF — {af_channel_label(context, '1')[:40]}",
                callback_data="autofw:archive:1",
            )])
        if ch2:
            rows.append([InlineKeyboardButton(
                f"🚀 AF — {af_channel_label(context, '2')[:40]}",
                callback_data="autofw:archive:2",
            )])
    rows.extend([
        [InlineKeyboardButton("✅ این آهنگ تموم شد", callback_data="arch:finish_current")],
        [InlineKeyboardButton("⏭ آهنگ بعدی", callback_data="arch:next")],
        [InlineKeyboardButton("⏮ آهنگ قبلی", callback_data="arch:previous")],
        [InlineKeyboardButton("⏩ رد کردن این آهنگ بدون ارسال فایل", callback_data="arch:skip_current")],
    ])
    return InlineKeyboardMarkup(rows)


def af_album_keyboard(tracks: List[dict], slot: str = "1") -> InlineKeyboardMarkup:
    """Album selection keyboard for a specific destination slot."""
    rows = []
    for t in tracks:
        st = _status_for_slot(t, slot)
        sel = (t.get("selected_slots") or {}).get(str(slot), False)
        idx = t.get("index", 0)
        title = (t.get("title") or "—")[:36]
        if st == "sent":
            prefix = "✅"
        elif st == "sending":
            prefix = "⏳"
        elif st == "failed":
            prefix = "❌" if not sel else "☑️"
        elif st == "unknown":
            prefix = "❓"
        else:
            prefix = "☑️" if sel else "☐"
        if st in ("pending", "failed"):
            cb = f"autofw:album_toggle:{slot}:{idx}"
        else:
            cb = f"autofw:album_noop:{slot}:{idx}"
        rows.append([InlineKeyboardButton(f"{prefix} {idx+1:02d}. {title}", callback_data=cb)])
    rows.append([
        InlineKeyboardButton("✅ انتخاب همه", callback_data=f"autofw:album_all:{slot}"),
        InlineKeyboardButton("⬜ لغو همه", callback_data=f"autofw:album_none:{slot}"),
    ])
    rows.append([InlineKeyboardButton("🚀 ارسال انتخاب‌شده‌ها", callback_data=f"autofw:album_send:{slot}")])
    rows.append([InlineKeyboardButton("🔄 تلاش مجدد ناموفق‌ها", callback_data=f"autofw:album_retry:{slot}")])
    rows.append([
        InlineKeyboardButton("📢 کانال ۱", callback_data="autofw:album_slot:1"),
        InlineKeyboardButton("📢 کانال ۲", callback_data="autofw:album_slot:2"),
    ])
    rows.append([InlineKeyboardButton("⏭️ رد کردن Auto Forward", callback_data="autofw:album_skip")])
    return InlineKeyboardMarkup(rows)


# ==================== عضویت اجباری کانال ====================
REQUIRED_CHANNEL = "@Nistplay"
REQUIRED_CHANNEL_URL = "https://t.me/Nistplay"


def membership_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 عضویت در کانال", url=REQUIRED_CHANNEL_URL)],
        [InlineKeyboardButton("✅ عضو شدم", callback_data="membership:check")],
    ])


async def membership_gate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Block every user-facing update until the user joins the required channel."""
    user = update.effective_user
    if user is None:
        return

    query = update.callback_query
    is_member = False
    check_error = None
    try:
        member = await context.bot.get_chat_member(
            chat_id=REQUIRED_CHANNEL,
            user_id=user.id,
        )
        status = getattr(member, "status", "")
        is_member = status in ("member", "administrator", "creator") or (
            status == "restricted" and bool(getattr(member, "is_member", False))
        )
    except TelegramError as exc:
        check_error = exc
        print(f"Required-channel membership check failed for {user.id}: {exc!r}")

    if is_member:
        if query and query.data == "membership:check":
            await query.answer("عضویت شما تأیید شد ✅")
            if query.message:
                await query.message.reply_text(
                    "✅ عضویت شما تأیید شد. حالا می‌تونی از ربات استفاده کنی.",
                    reply_markup=main_reply_keyboard(),
                )
            raise ApplicationHandlerStop
        return

    if query:
        await query.answer(
            "ابتدا باید عضو کانال شوید." if not check_error
            else "بررسی عضویت فعلاً ممکن نیست؛ کمی بعد دوباره تلاش کن.",
            show_alert=True,
        )
        if query.message:
            await query.message.reply_text(
                "🔒 برای استفاده از ربات، ابتدا عضو کانال زیر شو و سپس «عضو شدم» را بزن.\n\n"
                + ("⚠️ در حال حاضر بررسی عضویت با خطا مواجه شده؛ بعداً دوباره تلاش کن."
                   if check_error else ""),
                reply_markup=membership_keyboard(),
            )
    elif update.effective_message:
        message = (
            "🔒 برای استفاده از ربات، ابتدا عضو کانال زیر شو و سپس «عضو شدم» را بزن."
            if not check_error else
            "⚠️ در حال حاضر امکان بررسی عضویت وجود ندارد. کمی بعد دوباره تلاش کن."
        )
        await update.effective_message.reply_text(
            message,
            reply_markup=membership_keyboard(),
        )

    # Stop processing this update so no command, callback, or file handler can run.
    raise ApplicationHandlerStop


async def error_handler(update, context):
    import traceback
    print("BOT ERROR:", repr(context.error))
    traceback.print_exception(type(context.error), context.error, context.error.__traceback__)


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
            BotCommand("unreleasedcover", "🖼️ مدیریت کاور Unreleased Track"),
            BotCommand("setchannel1", "📢 تنظیم کانال اصلی AF (فقط مالک)"),
            BotCommand("setchannel2", "📢 تنظیم کانال Full Archive AF (فقط مالک)"),
            BotCommand("channelstatus", "ℹ️ وضعیت کانال‌های Auto Forward"),
            BotCommand("cancel", "❌ لغو عملیات فعلی"),
        ])

    request = HTTPXRequest(
        connection_pool_size=20, connect_timeout=30, read_timeout=90,
        write_timeout=90, pool_timeout=30,
    )
    updates_request = HTTPXRequest(
        connection_pool_size=8, connect_timeout=30, read_timeout=60,
        write_timeout=60, pool_timeout=30,
    )
    install_telegram_resilience()
    app = (Application.builder().token(TOKEN).request(request)
           .get_updates_request(updates_request).post_init(post_init).build())

    app.add_handler(TypeHandler(Update, membership_gate), group=-1)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("single", single_mode_command))
    app.add_handler(CommandHandler("archive", archive_start))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CommandHandler("tag", tag_command))
    app.add_handler(CommandHandler("album", album_command))
    app.add_handler(CommandHandler("version", version_command))
    app.add_handler(CommandHandler("unreleasedcover", unreleased_cover_command))
    app.add_handler(CommandHandler("setchannel", setchannel_command))
    app.add_handler(CommandHandler("setchannel1", setchannel1_command))
    app.add_handler(CommandHandler("setchannel2", setchannel2_command))
    app.add_handler(CommandHandler("channelstatus", channelstatus_command))

    app.add_handler(CallbackQueryHandler(button_handler))

    app.add_handler(
        MessageHandler(
            filters.AUDIO | filters.Document.ALL, receive_audio
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
