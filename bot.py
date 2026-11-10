import os
import asyncio
import time
import re
import json
import shutil
import tempfile
import html as html_module
from pathlib import Path
from collections import Counter
from dupes import classify as classify_duplicate, classify_detail
from audio_scanner import (
    AudioScannerStore, fpcalc_available, fingerprint_file, build_similarity_groups,
    DEFAULT_THRESHOLD, EXACT_THRESHOLD, REVIEW_THRESHOLD,
    analyze_local_file, similarity_between_analyses, content_hash_file,
)
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

# ==================== Escape helpers for Telegram parse modes ====================
def escape_md(text: Any) -> str:
    """Escape dynamic text for Telegram legacy Markdown (ParseMode.MARKDOWN)."""
    if text is None:
        return ""
    s = str(text)
    for ch in ("\\", "_", "*", "`", "["):
        s = s.replace(ch, "\\" + ch)
    return s


def escape_html(text: Any) -> str:
    """Escape dynamic text for Telegram HTML parse mode."""
    if text is None:
        return ""
    return html_module.escape(str(text), quote=False)



def ensure_mp3_filename(raw_name, title=None) -> str:
    """Build a usable .mp3 filename from Telegram Audio/Document metadata."""
    raw = (raw_name or "").strip()
    if raw.lower().endswith(".mp3"):
        return raw
    if raw:
        lower = raw.lower()
        for ext in (".m4a", ".aac", ".wav", ".ogg", ".flac", ".wma", ".mp4", ".mpeg", ".mpga"):
            if lower.endswith(ext):
                return raw[: -len(ext)] + ".mp3"
        # Titles like "01. Intro" must not be stripped by Path.stem
        return raw + ".mp3"
    if title and str(title).strip():
        return str(title).strip() + ".mp3"
    return "music.mp3"


def is_audio_mime(mime: str) -> bool:
    m = (mime or "").lower().strip()
    if not m:
        return True  # Telegram often omits mime for valid MP3
    if m.startswith("audio/"):
        return True
    if m in (
        "application/octet-stream",
        "application/mp3",
        "application/x-mp3",
        "binary/octet-stream",
    ):
        return True
    return False


def has_non_audio_extension(name: str) -> bool:
    lower = (name or "").lower()
    return any(
        lower.endswith(ext)
        for ext in (
            ".txt", ".json", ".jpg", ".jpeg", ".png", ".webp", ".gif",
            ".pdf", ".zip", ".rar", ".7z", ".doc", ".docx", ".csv",
        )
    )


def resolve_incoming_audio(message):
    """Resolve Telegram Audio or Document into processable MP3 media.

    Returns (media, source, name):
      media  – Audio or Document (or None)
      source – "audio" | "document" | None
      name   – filename ending with .mp3 when accepted as track
    """
    audio = getattr(message, "audio", None)
    document = getattr(message, "document", None)

    # 1) Native Telegram Music / Audio — always treat as valid track.
    #    file_name often lacks ".mp3"; mime may be missing. Trust the type.
    if audio is not None:
        name = ensure_mp3_filename(
            getattr(audio, "file_name", None),
            getattr(audio, "title", None),
        )
        return audio, "audio", name

    # 2) Document attachment
    if document is not None:
        mime = getattr(document, "mime_type", None) or ""
        fname = getattr(document, "file_name", None) or ""
        if mime.lower().startswith("image/"):
            return document, "document", fname or "file"
        if fname.lower().endswith(".mp3") or (
            is_audio_mime(mime) and not has_non_audio_extension(fname)
        ):
            return document, "document", ensure_mp3_filename(fname)
        # Not accepted as MP3 (e.g. manifest .txt) — caller decides
        return document, "document", fname or "file"

    return None, None, ""



# ==================== پایداری ارتباط و ارسال پیام ====================
def install_telegram_resilience():
    """Retry malformed-entity messages as plain text instead of losing them."""
    from telegram import Bot
    for method_name in (
        "send_message", "send_photo", "send_audio", "send_document",
        "edit_message_text", "edit_message_caption", "send_media_group",
    ):
        original = getattr(Bot, method_name)
        async def resilient(self, *args, __original=original, __name=method_name, **kwargs):
            try:
                return await __original(self, *args, **kwargs)
            except BadRequest as exc:
                msg = str(exc).lower()
                if (
                    "parse entities" not in msg
                    and "can't find end of the entity" not in msg
                    and "can't parse entities" not in msg
                ):
                    raise
                retry_kwargs = dict(kwargs)
                retry_kwargs["parse_mode"] = None
                retry_kwargs.pop("entities", None)
                # Captions on media group items may also need clearing
                media = retry_kwargs.get("media")
                if media:
                    cleaned = []
                    for item in media:
                        try:
                            if getattr(item, "parse_mode", None) or getattr(item, "caption", None):
                                item.parse_mode = None
                        except Exception:
                            pass
                        cleaned.append(item)
                    retry_kwargs["media"] = cleaned
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
VERSION = "v7.0.3"
VERSION_CHANGES = [
    "اصلاح callbackهای آرشیو که داخل ascan گیر کرده بودند",
    "Dockerfile با chromaprint-tools (fpcalc) برای EthioDeploy",
    "حذف خودکار Duplicate قطعی با اولویت Source A بر B",
    "Audio Scanner مرکز واحد تحلیل؛ حذف دکمه «تحلیل و ساخت فهرست»",
    "UI ساده‌شده Scanner: اسکن، نیازمند بررسی، خروجی فهرست",
    "خروجی archive-v1 سازگار با ورودی بررسی منابع",
    "اصلاح متن راهنمای منوی آرشیو",
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


_AUDIO_SCANNER_STORE = None

def get_audio_scanner_store():
    global _AUDIO_SCANNER_STORE
    if _AUDIO_SCANNER_STORE is None:
        ensure_data_dir()
        _AUDIO_SCANNER_STORE = AudioScannerStore(DATA_DIR / "audio_scanner.sqlite3")
    return _AUDIO_SCANNER_STORE


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

# ==================== Navigation / Tab lifecycle ====================
async def safe_delete_message(message) -> None:
    if not message:
        return
    try:
        await message.delete()
    except Exception:
        pass


async def safe_delete_chat_message(bot, chat_id, message_id) -> None:
    if not chat_id or not message_id:
        return
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass


def _is_message_not_modified(exc: BaseException) -> bool:
    return "message is not modified" in str(exc).lower()


async def safe_edit_message_text(message, text, **kwargs):
    """Edit message text; ignore only Telegram 'Message is not modified'."""
    if not message:
        return None
    try:
        current = (getattr(message, "text", None) or getattr(message, "caption", None) or "")
        # Skip when plain text identical and no explicit markup change forces update
        if current == text and "reply_markup" not in kwargs and "parse_mode" not in kwargs:
            return message
        return await message.edit_text(text, **kwargs)
    except BadRequest as exc:
        if _is_message_not_modified(exc):
            return message
        raise


async def safe_edit_reply_markup(message, reply_markup=None):
    """Edit reply markup; ignore only Telegram 'Message is not modified'."""
    if not message:
        return None
    try:
        return await message.edit_reply_markup(reply_markup=reply_markup)
    except BadRequest as exc:
        if _is_message_not_modified(exc):
            return message
        raise



def nav_push(context, tab: str, message_id=None, parent: str = None):
    """Track active UI tab per user for cleanup/return."""
    stack = context.user_data.setdefault("nav_stack", [])
    if parent is None and stack:
        parent = stack[-1].get("tab")
    entry = {"tab": tab, "message_id": message_id, "parent": parent}
    stack.append(entry)
    context.user_data["nav_active"] = entry
    return entry


def nav_set_message(context, message_id):
    active = context.user_data.get("nav_active")
    if active is not None:
        active["message_id"] = message_id


def nav_pop(context):
    stack = context.user_data.get("nav_stack") or []
    if not stack:
        context.user_data.pop("nav_active", None)
        return None
    stack.pop()
    context.user_data["nav_active"] = stack[-1] if stack else None
    return context.user_data.get("nav_active")


async def nav_close_active(context, bot, chat_id):
    active = context.user_data.get("nav_active")
    if active and active.get("message_id"):
        await safe_delete_chat_message(bot, chat_id, active["message_id"])


def direct_field_keyboard(field: str) -> InlineKeyboardMarkup:
    """Copy / Remove / Cancel under direct field edit prompt."""
    rows = [
        [
            InlineKeyboardButton("📋 Copy", callback_data=f"copy:{field}"),
            InlineKeyboardButton("🗑 Remove", callback_data=f"delete:{field}"),
        ],
        [InlineKeyboardButton("❌ Cancel", callback_data="action:cancel_field")],
    ]
    return InlineKeyboardMarkup(rows)


def picture_cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="action:cancel_field")]])


def lyrics_back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="action:cancel_field")]])


def field_pick_keyboard(prefix: str) -> InlineKeyboardMarkup:
    """Inline field picker for Apply-to-All / Group Edit style flows."""
    mapping = [
        ("🎵 Title", "title"),
        ("👤 Artist", "artist"),
        ("💿 Album", "album"),
        ("📅 Year", "year"),
        ("🔢 Track", "track"),
        ("🎼 Genre", "genre"),
        ("👥 Album Artist", "albumartist"),
        ("✍️ Composer", "composer"),
        ("💿 Disc", "disc"),
        ("🏢 Publisher", "publisher"),
        ("💬 Comment", "comment"),
    ]
    rows = []
    row = []
    for label, key in mapping:
        row.append(InlineKeyboardButton(label, callback_data=f"{prefix}:{key}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("❌ Cancel", callback_data=f"{prefix}:cancel")])
    return InlineKeyboardMarkup(rows)


def album_exit_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ لغو / خروج از آلبوم", callback_data="album:exit")]])


def album_track_pick_keyboard(tracks, page: int = 0, per_page: int = 8) -> InlineKeyboardMarkup:
    total = len(tracks)
    start = page * per_page
    end = min(start + per_page, total)
    rows = []
    for i in range(start, end):
        t = tracks[i]
        idx = t.get("index", i)
        title = "—"
        p = t.get("path")
        if p and os.path.isfile(p):
            try:
                title = get_tags(p).get("title") or Path(t.get("original_name", "track")).stem
            except Exception:
                title = Path(t.get("original_name", "track")).stem
        else:
            title = Path(t.get("original_name", f"track{i+1}")).stem
        label = f"Track {i+1}: {str(title)[:28]}"
        rows.append([InlineKeyboardButton(label, callback_data=f"album:edit:{idx}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"album:pickpage:{page-1}"))
    if end < total:
        nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"album:pickpage:{page+1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("⬅️ Back to Album Menu", callback_data="album:pick_back")])
    return InlineKeyboardMarkup(rows)


def main_reply_keyboard() -> ReplyKeyboardMarkup:
    """دکمه‌های پایین صفحه که همیشه دیده می‌شوند"""
    keyboard = [
        [KeyboardButton("🏠 شروع")],
        [KeyboardButton("💿 حالت آلبوم"), KeyboardButton("🎵 حالت تکی آهنگ")],
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
            [InlineKeyboardButton("📤 Export Default Tags", callback_data="tag:export")],
            [InlineKeyboardButton("📥 Import Default Tags", callback_data="tag:import")],
            [InlineKeyboardButton("❌ Close", callback_data="tag:close")],
        ]
    )


DEFAULTS_EXPORT_FORMAT = "music-tag-bot-defaults-v1"


def _sanitize_defaults_mode_block(block: dict) -> dict:
    """Keep only supported field keys + _modes/_disabled metadata."""
    if not isinstance(block, dict):
        return {}
    out = {}
    for k in DEFAULT_FIELD_ORDER:
        if k in block:
            out[k] = block[k]
    if isinstance(block.get("_modes"), dict):
        modes = {}
        for k, v in block["_modes"].items():
            if k in DEFAULT_FIELD_ORDER and str(v).lower() in ("apply", "keep", "remove"):
                modes[k] = str(v).lower()
        if modes:
            out["_modes"] = modes
    if isinstance(block.get("_disabled"), list):
        out["_disabled"] = [k for k in block["_disabled"] if k in DEFAULT_FIELD_ORDER]
    return out


def build_defaults_export_payload(user_id: int) -> dict:
    ud = get_user_defaults(user_id)
    return {
        "format": DEFAULTS_EXPORT_FORMAT,
        "version": VERSION,
        "single": _sanitize_defaults_mode_block(ud.get("single") or {}),
        "album": _sanitize_defaults_mode_block(ud.get("album") or {}),
    }


def parse_defaults_import_payload(data) -> tuple:
    """Return (single_block, album_block) or raise ValueError."""
    if not isinstance(data, dict):
        raise ValueError("ساختار فایل معتبر نیست (object نیست).")
    fmt = data.get("format")
    if fmt not in (None, DEFAULTS_EXPORT_FORMAT, "music-tag-bot-defaults-v1"):
        if not (isinstance(data.get("single"), dict) or isinstance(data.get("album"), dict)):
            raise ValueError("فرمت فایل Default Tags شناخته نشد.")
    if fmt is None and not (isinstance(data.get("single"), dict) or isinstance(data.get("album"), dict)):
        raise ValueError("فرمت فایل Default Tags شناخته نشد.")
    single = _sanitize_defaults_mode_block(data.get("single") or {})
    album = _sanitize_defaults_mode_block(data.get("album") or {})
    if not single and not album:
        raise ValueError("هیچ تنظیم Default Tags معتبری در فایل نیست.")
    return single, album


def apply_defaults_import(user_id: int, single: dict, album: dict) -> None:
    """Atomically replace owner's single+album defaults."""
    all_data = load_all_defaults()
    uid = str(user_id)
    new_single = DEFAULT_SINGLE.copy()
    new_single.update({k: v for k, v in single.items() if not str(k).startswith("_")})
    if "_modes" in single:
        new_single["_modes"] = single["_modes"]
    if "_disabled" in single:
        new_single["_disabled"] = single["_disabled"]
    new_album = DEFAULT_ALBUM.copy()
    new_album.update({k: v for k, v in album.items() if not str(k).startswith("_")})
    if "_modes" in album:
        new_album["_modes"] = album["_modes"]
    if "_disabled" in album:
        new_album["_disabled"] = album["_disabled"]
    all_data[uid] = {"single": new_single, "album": new_album}
    save_all_defaults(all_data)


def defaults_import_summary(single: dict, album: dict) -> str:
    def lines(label, block):
        modes = block.get("_modes") or {}
        disabled = set(block.get("_disabled") or [])
        parts = [f"**{label}**"]
        for k in DEFAULT_FIELD_ORDER:
            if k not in block and k not in modes and k not in disabled:
                continue
            m = modes.get(k) or ("keep" if k in disabled else "apply")
            if m == "remove":
                parts.append(f"• {FIELDS.get(k, k)}: Remove")
            elif m == "keep":
                parts.append(f"• {FIELDS.get(k, k)}: Keep")
            else:
                parts.append(f"• {FIELDS.get(k, k)}: Apply `{escape_md(block.get(k, ''))}`")
        return chr(10).join(parts) if len(parts) > 1 else f"**{label}**: (خالی)"
    return lines("Single", single) + chr(10) + chr(10) + lines("Album", album)


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
        f"📁 File Name: {escape_md(filename)}\n"
        f"Title: {escape_md(tags['title'])}\n"
        f"Artist: {escape_md(tags['artist'])}\n"
        f"Album: {escape_md(tags['album'])}\n"
        f"Year: {escape_md(tags['year'])}\n"
        f"Genre: {escape_md(tags['genre'])}\n"
        f"Composer: {escape_md(tags['composer'])}\n"
        f"Publisher: {escape_md(tags['publisher'])}\n"
        f"Disc N: {escape_md(tags['disc'])}\n"
        f"Track N: {escape_md(tags['track'])}\n"
        f"Album Artist: {escape_md(tags['albumartist'])}\n"
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
        lines.append(f"۱) Main: `{ch1['chat_id']}` — {escape_md(ch1.get('title') or '—')}")
    else:
        lines.append("۱) Main: تنظیم نشده (/setchannel1)")
    if ch2:
        lines.append(f"۲) Full Archive: `{ch2['chat_id']}` — {escape_md(ch2.get('title') or '—')}")
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

    sent = await update.message.reply_text(
        "💿 **Album Mode**\n\n"
        "اول نام هنرمند(های) اصلی آلبوم را بفرست.\n"
        "اگر چند نفر هستند با کاما جدا کن.\n"
        "مثال: `Yas , Amir Tataloo`",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=album_exit_keyboard(),
    )
    nav_push(context, "album_setup", sent.message_id, parent="main")


# ==================== آرشیو موسیقی (نسخه پایه قابل استفاده) ====================

def source_intake_stats(archive: dict, source_key: str) -> dict:
    bucket = (archive.get("sources") or {}).get(source_key) or []
    received = len(bucket)
    too_large = sum(1 for x in bucket if int(x.get("size") or 0) > MAX_SIZE)
    invalid = sum(1 for x in bucket if not x.get("file_id"))
    processable = sum(1 for x in bucket if x.get("file_id") and int(x.get("size") or 0) <= MAX_SIZE)
    failed = sum(1 for x in bucket if x.get("error") or x.get("status") == "failed")
    return {
        "received": received, "too_large": too_large, "invalid": invalid,
        "failed": failed, "processable": processable,
        "label": "A" if source_key == "source_a" else "B", "key": source_key,
    }


def format_source_intake_report(stats: dict) -> str:
    label = stats["label"]
    return (
        f"📋 گزارش دریافت Source {label}\n\n"
        f"• تعداد آهنگ‌های دریافت‌شده: {stats['received']}\n"
        f"• تعداد فایل‌های بزرگ‌تر از حد مجاز: {stats['too_large']}\n"
        f"• تعداد فایل‌های نامعتبر (بدون file_id): {stats['invalid']}\n"
        f"• تعداد خطاهای ثبت‌شده: {stats['failed']}\n"
        f"• تعداد آهنگ‌های قابل پردازش: {stats['processable']}"
    )


def source_finish_keyboard(source_key: str) -> InlineKeyboardMarkup:
    label = "A" if source_key == "source_a" else "B"
    cont = "arch:continue_a" if source_key == "source_a" else "arch:continue_b"
    conf = "arch:confirm_a" if source_key == "source_a" else "arch:confirm_b"
    export = "arch:export_a" if source_key == "source_a" else "arch:export_b"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"➕ ادامه دریافت Source {label}", callback_data=cont)],
        [InlineKeyboardButton(f"✅ اتمام Source {label} و مرحله بعد", callback_data=conf)],
        [InlineKeyboardButton(f"📄 خروجی TXT فهرست Source {label}", callback_data=export)],
        [InlineKeyboardButton("🔙 منوی آرشیو", callback_data="arch:menu")],
    ])


def export_source_txt(archive: dict, source_key: str) -> bytes:
    bucket = (archive.get("sources") or {}).get(source_key) or []
    lines = [
        f"# Music Tag Bot — Source {'A' if source_key=='source_a' else 'B'} track list",
        f"# count={len(bucket)}",
        "# Edit display names if needed. Keep the ---DATA--- section intact.",
        "",
    ]
    tracks = []
    for i, x in enumerate(bucket, 1):
        title = (x.get("title") or Path(str(x.get("name") or f"track_{i}")).stem).strip()
        artist = (x.get("artist") or "").strip()
        display = f"{title} - {artist}" if artist else title
        lines.append(display)
        tracks.append({
            "number": i, "name": x.get("name"), "title": x.get("title"),
            "artist": x.get("artist"), "duration": x.get("duration"), "size": x.get("size"),
            "file_id": x.get("file_id"), "file_unique_id": x.get("file_unique_id"),
            "message_id": x.get("message_id"),
            "source": "A" if source_key == "source_a" else "B", "display": display,
        })
    payload = {"format": "source-list-v1", "source": source_key, "tracks": tracks}
    lines += ["", "---DATA---", json.dumps(payload, ensure_ascii=False, separators=(",", ":"))]
    return ("\n".join(lines) + "\n").encode("utf-8")


def archive_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📥 دریافت Source A / B", callback_data="arch:review_menu")],
        [InlineKeyboardButton("🎧 Audio Scanner", callback_data="ascan:menu")],
        [InlineKeyboardButton("📥 ورودی بررسی منابع", callback_data="arch:input_menu")],
        [InlineKeyboardButton("❌ بستن آرشیو", callback_data="arch:close")],
    ])

def audio_scanner_keyboard():
    """Minimal scanner UI — definite dups are auto-resolved; only review needs attention."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 شروع / ادامه اسکن", callback_data="ascan:scan")],
        [InlineKeyboardButton("📊 گزارش آخرین اسکن", callback_data="ascan:results")],
        [InlineKeyboardButton("🔎 نیازمند بررسی", callback_data="ascan:review_list")],
        [InlineKeyboardButton("📄 دریافت خروجی فهرست", callback_data="ascan:export")],
        [InlineKeyboardButton("🔙 منوی آرشیو", callback_data="arch:menu")],
    ])


def scanner_user_allowed(user_id):
    # If OWNER_ID is configured, keep archive analysis owner-only.
    return (not OWNER_ID) or is_owner(user_id)


def scanner_sync_current_archive(context, user_id, chat_id):
    archive = context.user_data.get("archive") or {}
    sources = archive.get("sources") or {}
    store = get_audio_scanner_store()
    count = 0
    for source_key, label in (("source_a", "A"), ("source_b", "B")):
        for item in sources.get(source_key, []) or []:
            if not item.get("file_id") or not item.get("message_id"):
                continue
            store.register_track(
                user_id=user_id, source=label, chat_id=int(chat_id),
                message_id=int(item["message_id"]), file_id=item["file_id"],
                file_unique_id=item.get("file_unique_id"),
                name=item.get("name") or "بدون نام.mp3",
                size=int(item.get("size") or 0), duration=item.get("duration"),
            )
            count += 1
    return count



async def _fingerprint_source_item(context, entry, source_label, temp_root: Path):
    """Download one archive source item and return (item_dict, analysis_or_none)."""
    item = dict(
        entry,
        source=source_label,
        duplicate_level="independent",
        review=False,
        status="pending",
        fingerprint_score=None,
        removed=False,
        removed_reason=None,
    )
    fid = entry.get("file_id")
    if not fid:
        item["review"] = True
        item["duplicate_level"] = "unanalyzed"
        item["review_note"] = "بدون file_id"
        return item, None
    if int(entry.get("size") or 0) > MAX_SIZE:
        item["review"] = True
        item["duplicate_level"] = "unanalyzed"
        item["review_note"] = "حجم بیش از حد مجاز"
        return item, None
    tmp = temp_root / f"{source_label}_{entry.get('message_id') or id(entry)}.mp3"
    try:
        tg_file = await context.bot.get_file(fid)
        await download_to_drive_retry(tg_file, str(tmp))
        analysis = await asyncio.to_thread(analyze_local_file, tmp)
        return item, analysis
    except Exception as exc:
        item["review"] = True
        item["duplicate_level"] = "unanalyzed"
        item["review_note"] = f"خطا: {exc!r}"
        return item, None
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass


def _is_exact_match(analysis_a, analysis_b) -> tuple[bool, float]:
    if not analysis_a or not analysis_b:
        return False, 0.0
    score = similarity_between_analyses(analysis_a, analysis_b)
    return score >= EXACT_THRESHOLD, score


def _is_review_match(analysis_a, analysis_b) -> tuple[bool, float]:
    if not analysis_a or not analysis_b:
        return False, 0.0
    score = similarity_between_analyses(analysis_a, analysis_b)
    return REVIEW_THRESHOLD <= score < EXACT_THRESHOLD, score


async def resolve_archive_with_priority(context, progress_cb=None) -> dict:
    """Fingerprint Source A/B, auto-drop definite duplicates with A priority.

    Returns stats + final queue written into context.user_data['archive']['queue'].
    """
    archive = context.user_data.setdefault("archive", {"sources": {"source_a": [], "source_b": []}, "queue": []})
    sources = archive.setdefault("sources", {"source_a": [], "source_b": []})
    temp_root = Path(tempfile.mkdtemp(prefix="arch_resolve_", dir=str(DATA_DIR)))
    stats = {
        "total_received": 0,
        "from_a": 0,
        "from_b": 0,
        "kept_a": 0,
        "kept_b": 0,
        "removed_definite": 0,
        "removed_within_a": 0,
        "removed_within_b": 0,
        "removed_b_vs_a": 0,
        "review_count": 0,
        "errors": 0,
        "final_count": 0,
    }
    try:
        a_raw = list(sources.get("source_a") or [])
        b_raw = list(sources.get("source_b") or [])
        stats["from_a"] = len(a_raw)
        stats["from_b"] = len(b_raw)
        stats["total_received"] = len(a_raw) + len(b_raw)

        # --- analyze A ---
        a_items = []  # list of (item, analysis)
        for i, entry in enumerate(sorted(a_raw, key=lambda x: x.get("message_id") or 0)):
            if progress_cb:
                await progress_cb(f"تحلیل Source A: {i+1}/{len(a_raw)}")
            item, analysis = await _fingerprint_source_item(context, entry, "A", temp_root)
            if item.get("duplicate_level") == "unanalyzed":
                stats["errors"] += 1
            a_items.append((item, analysis))

        # Dedup within A (keep first occurrence of each exact cluster)
        kept_a = []  # list of (item, analysis)
        for item, analysis in a_items:
            if item.get("duplicate_level") == "unanalyzed":
                item["review"] = True
                kept_a.append((item, analysis))
                stats["review_count"] += 1
                continue
            drop = False
            for kept_item, kept_an in kept_a:
                if kept_item.get("removed"):
                    continue
                exact, score = _is_exact_match(analysis, kept_an)
                if exact:
                    item["removed"] = True
                    item["removed_reason"] = f"duplicate_within_A score={score}"
                    item["fingerprint_score"] = score
                    stats["removed_within_a"] += 1
                    stats["removed_definite"] += 1
                    drop = True
                    break
                rev, score = _is_review_match(analysis, kept_an)
                if rev:
                    item["review"] = True
                    item["duplicate_level"] = "review"
                    item["fingerprint_score"] = score
                    item["review_matches"] = [{
                        "title": track_display_name(kept_item),
                        "criteria": [f"Audio Fingerprint {score}%"],
                    }]
                    item["review_note"] = "مشابه داخل Source A — نیاز به بررسی"
                    stats["review_count"] += 1
            if not drop:
                kept_a.append((item, analysis))

        # --- analyze B ---
        b_items = []
        for i, entry in enumerate(sorted(b_raw, key=lambda x: x.get("message_id") or 0)):
            if progress_cb:
                await progress_cb(f"تحلیل Source B: {i+1}/{len(b_raw)}")
            item, analysis = await _fingerprint_source_item(context, entry, "B", temp_root)
            if item.get("duplicate_level") == "unanalyzed":
                stats["errors"] += 1
            b_items.append((item, analysis))

        kept_b = []
        for item, analysis in b_items:
            if item.get("duplicate_level") == "unanalyzed":
                item["review"] = True
                kept_b.append((item, analysis))
                stats["review_count"] += 1
                continue
            # Compare against kept A — A always wins
            drop = False
            for kept_item, kept_an in kept_a:
                exact, score = _is_exact_match(analysis, kept_an)
                if exact:
                    item["removed"] = True
                    item["removed_reason"] = f"duplicate_of_A score={score}"
                    item["fingerprint_score"] = score
                    kept_item["also_in_source_b"] = True
                    kept_item["fingerprint_score"] = score
                    stats["removed_b_vs_a"] += 1
                    stats["removed_definite"] += 1
                    drop = True
                    break
                rev, score = _is_review_match(analysis, kept_an)
                if rev:
                    item["review"] = True
                    item["duplicate_level"] = "review"
                    item["fingerprint_score"] = score
                    item["review_matches"] = [{
                        "title": track_display_name(kept_item),
                        "criteria": [f"Audio Fingerprint {score}%"],
                    }]
                    item["review_note"] = "مشابه با Source A — نیاز به بررسی (حذف خودکار نشد)"
                    stats["review_count"] += 1
            if drop:
                continue
            # Dedup within kept B
            for kb_item, kb_an in kept_b:
                exact, score = _is_exact_match(analysis, kb_an)
                if exact:
                    item["removed"] = True
                    item["removed_reason"] = f"duplicate_within_B score={score}"
                    item["fingerprint_score"] = score
                    stats["removed_within_b"] += 1
                    stats["removed_definite"] += 1
                    drop = True
                    break
            if not drop:
                kept_b.append((item, analysis))

        # Build final queue: only non-removed
        final = []
        for item, _an in kept_a:
            if item.get("removed"):
                continue
            item.pop("_analysis", None)
            final.append(item)
            stats["kept_a"] += 1
        for item, _an in kept_b:
            if item.get("removed"):
                continue
            item.pop("_analysis", None)
            final.append(item)
            stats["kept_b"] += 1

        for i, x in enumerate(final, 1):
            x["number"] = i
            x["status"] = x.get("status") or "pending"

        archive["queue"] = final
        archive["phase"] = "queue"
        archive["scan_stats"] = stats
        # Keep a side log of removed items for report (not in queue)
        archive["removed_log"] = [
            {
                "name": (it.get("name") or it.get("title")),
                "source": it.get("source"),
                "reason": it.get("removed_reason"),
                "score": it.get("fingerprint_score"),
            }
            for it, _ in (a_items + b_items)
            if it.get("removed")
        ]
        stats["final_count"] = len(final)
        return stats
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def build_archive_manifest_bytes(archive: dict) -> bytes:
    """Produce archive-v1 text+JSON payload compatible with ورودی بررسی منابع."""
    queue = archive.get("queue") or []
    stats = archive.get("scan_stats") or {}
    lines = [
        "ARCHIVE-MANIFEST v1",
        f"تعداد آهنگ‌های نهایی: {len(queue)}",
        f"حذف‌شده قطعی: {stats.get('removed_definite', 0)}",
        f"نیازمند بررسی: {sum(1 for x in queue if x.get('review'))}",
        f"از Source A: {stats.get('kept_a', 0)}",
        f"جدید از Source B: {stats.get('kept_b', 0)}",
        "",
        "فهرست آهنگ‌ها:",
    ]
    for x in queue:
        mark = "❗ " if x.get("review") else ""
        artist = x.get("artist") or "هنرمند نامشخص"
        name = x.get("title") or Path(str(x.get("name") or "بدون نام")).stem
        score = x.get("fingerprint_score")
        score_s = f" [{score}%]" if score else ""
        lines.append(f"{int(x.get('number') or 0):03d}. {mark}{name} - {artist}{score_s}")
        if x.get("review") and x.get("review_matches"):
            for h in x["review_matches"]:
                crit = ", ".join(h.get("criteria") or []) or "—"
                lines.append(f"    ↳ مشابه: {h.get('title') or '—'} | {crit}")
        if x.get("review_note"):
            lines.append(f"    ↳ {x['review_note']}")
    manifest = {
        "format": "archive-v1",
        "tracks": [{
            k: x.get(k) for k in (
                "number", "name", "title", "artist", "duration", "size",
                "file_id", "file_unique_id", "review", "possible_match",
                "review_matches", "review_criteria", "source",
                "fingerprint_score", "duplicate_level", "review_note",
            )
        } for x in queue],
        "stats": stats,
    }
    lines += ["", "---DATA---", json.dumps(manifest, ensure_ascii=False, separators=(",", ":"))]
    return ("\n".join(lines) + "\n").encode("utf-8")


async def audio_scanner_worker(context, user_id, chat_id, progress_message_id):
    """Scan Source A/B, auto-remove definite dups (A wins), build final archive queue + manifest."""
    bot = context.application.bot
    try:
        archive = context.user_data.get("archive") or {}
        sources = archive.get("sources") or {}
        n_a = len(sources.get("source_a") or [])
        n_b = len(sources.get("source_b") or [])
        if n_a + n_b == 0:
            await bot.edit_message_text(
                chat_id=chat_id, message_id=progress_message_id,
                text="آرشیوی برای اسکن نیست. ابتدا Source A و B را از «دریافت Source A/B» وارد کن.",
                reply_markup=audio_scanner_keyboard(),
            )
            return

        # Register in store for cache continuity (best-effort)
        try:
            scanner_sync_current_archive(context, user_id, chat_id)
        except Exception as exc:
            print("scanner_sync warning:", repr(exc))

        method = "chromaprint" if fpcalc_available() else "content-hash/soft-fp"
        await bot.edit_message_text(
            chat_id=chat_id, message_id=progress_message_id,
            text=(
                f"🎧 Audio Scanner\n\n"
                f"Source A: {n_a} | Source B: {n_b}\n"
                f"روش: {method}\n"
                f"اولویت: Source A بر B\n\nشروع تحلیل…"
            ),
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⏹ لغو", callback_data="ascan:cancel")]]),
        )

        async def progress_cb(msg: str):
            if context.user_data.get("audio_scan_cancel"):
                raise asyncio.CancelledError()
            try:
                await bot.edit_message_text(
                    chat_id=chat_id, message_id=progress_message_id,
                    text=f"🎧 Audio Scanner\n\n{msg}",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⏹ لغو", callback_data="ascan:cancel")]]),
                )
            except Exception:
                pass

        try:
            stats = await resolve_archive_with_priority(context, progress_cb=progress_cb)
        except asyncio.CancelledError:
            await bot.edit_message_text(
                chat_id=chat_id, message_id=progress_message_id,
                text="⏹ اسکن لغو شد. می‌توانی دوباره شروع کنی.",
                reply_markup=audio_scanner_keyboard(),
            )
            return

        # Persist last report
        context.user_data["scanner_last_report"] = stats
        archive = context.user_data.get("archive") or {}
        archive["scan_stats"] = stats

        report = (
            "📊 گزارش Audio Scanner\n\n"
            f"• کل دریافتی: {stats['total_received']} (A={stats['from_a']} + B={stats['from_b']})\n"
            f"• Duplicate قطعی حذف‌شده: {stats['removed_definite']}\n"
            f"  ↳ داخل A: {stats['removed_within_a']} | B در برابر A: {stats['removed_b_vs_a']} | داخل B: {stats['removed_within_b']}\n"
            f"• باقی‌مانده از Source A: {stats['kept_a']}\n"
            f"• جدید از Source B: {stats['kept_b']}\n"
            f"• نهایی در آرشیو: {stats['final_count']}\n"
            f"• نیازمند بررسی: {stats['review_count']}\n"
            f"• خطا / غیرقابل تحلیل: {stats['errors']}\n\n"
            "Duplicateهای قطعی از صف نهایی حذف شدند (اولویت با Source A).\n"
            "فایل خروجی با «📄 دریافت خروجی فهرست» برای ورودی بررسی منابع آماده است."
        )
        await bot.edit_message_text(
            chat_id=chat_id, message_id=progress_message_id,
            text=report,
            reply_markup=audio_scanner_keyboard(),
        )
    except Exception as exc:
        print("Audio scanner worker error:", repr(exc))
        try:
            await bot.edit_message_text(
                chat_id=chat_id, message_id=progress_message_id,
                text=f"❌ اسکن متوقف شد: {type(exc).__name__}: {str(exc)[:300]}",
                reply_markup=audio_scanner_keyboard(),
            )
        except Exception:
            pass
    finally:
        context.user_data.pop("audio_scan_task", None)
        context.user_data.pop("audio_scan_cancel", None)



async def audio_scanner_show_group(query, context, group_id):
    user_id = query.from_user.id
    store = get_audio_scanner_store()
    groups = store.groups(user_id)
    row = next((g for g in groups if int(g["id"]) == int(group_id)), None)
    if not row:
        await query.message.reply_text("این گروه پیدا نشد؛ احتمالاً نتیجه اسکن جدیدی ثبت شده است.")
        return
    data = row["data"]
    lines = [f"🎧 گروه مشابه شماره {row['group_no']}", f"کمترین شباهت ثبت‌شده در گروه: {row['score']:.2f}%", ""]
    buttons = []
    for track in data.get("tracks", []):
        tid = int(track["id"])
        lines.append(f"🎵 {track.get('name') or 'بدون نام'}")
        size_bytes = int(track.get("size") or 0)
        duration_seconds = float(track.get("duration") or 0)
        bitrate_kbps = round(size_bytes * 8 / duration_seconds / 1000) if duration_seconds > 0 and size_bytes > 0 else 0
        bitrate_text = f" | بیت‌ریت تقریبی: {bitrate_kbps} kbps" if bitrate_kbps else ""
        lines.append(f"منبع: {track.get('source') or '?'} | حجم: {size_bytes/1024/1024:.2f} MB{bitrate_text}")
        pair_scores = []
        for other in data.get("tracks", []):
            oid = int(other["id"])
            if oid == tid: continue
            key = f"{min(tid,oid)}-{max(tid,oid)}"
            if key in data.get("pair_scores", {}):
                pair_scores.append(f"با {other.get('name') or oid}: {data['pair_scores'][key]:.2f}%")
        if pair_scores:
            lines.extend("  ↳ " + x for x in pair_scores)
        buttons.append([InlineKeyboardButton(f"✅ نگه‌داشتن این نسخه", callback_data=f"ascan:keep:{row['id']}:{tid}")])
        lines.append("")
    if row["status"] == "pending":
        lines.append("یک نسخه را برای نگهداری انتخاب کن؛ بقیه فقط به قرنطینه منطقی Scanner منتقل می‌شوند.")
    else:
        lines.append("برای این گروه قبلاً انتخاب ثبت شده است.")
        buttons = []
    buttons.append([InlineKeyboardButton("🔙 گروه‌ها", callback_data="ascan:groups")])
    await query.message.reply_text("\n".join(lines)[:3900], reply_markup=InlineKeyboardMarkup(buttons))


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
    await update.message.reply_text("📚 آرشیو موسیقی\n\nیکی از گزینه‌های زیر را انتخاب کن:\n• دریافت Source A/B\n• Audio Scanner\n• ورودی بررسی منابع", reply_markup=archive_keyboard())

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
    await safe_edit_message_text(query.message, "\n".join(lines), reply_markup=InlineKeyboardMarkup(rows))

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
             f"🎵 آخرین فایل: {escape_html(state['current'])}"]
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
    message = update.effective_message
    if message is None:
        return
    # Prefer Telegram Audio (Music), then Document. Never crash on missing media.
    document, media_source, resolved_name = resolve_incoming_audio(message)

    # Default Tags import (owner only)
    if context.user_data.get("waiting") == "defaults_import_file":
        user = update.effective_user
        if not is_owner(user.id if user else None):
            context.user_data["waiting"] = None
            await message.reply_text("🔒 فقط مالک ربات می‌تواند Import کند.")
            return
        if not message.document:
            await message.reply_text("لطفاً فایل JSON را به صورت Document بفرست (نه به‌صورت متن).")
            return
        fname = (message.document.file_name or "").lower()
        if not fname.endswith(".json"):
            await message.reply_text("فایل باید با پسوند .json باشد (مثلاً default_tags.json).")
            return
        try:
            tg_file = await context.bot.get_file(message.document.file_id)
            raw = await download_as_bytearray_retry(tg_file)
            text = bytes(raw).decode("utf-8-sig")
            data = json.loads(text)
            single, album = parse_defaults_import_payload(data)
        except json.JSONDecodeError:
            await message.reply_text("❌ فایل JSON معتبر نیست (خراب یا ناقص). تنظیمات فعلی تغییر نکرد.")
            return
        except ValueError as exc:
            await message.reply_text(f"❌ فایل رد شد:\n{exc}\n\nتنظیمات فعلی تغییر نکرد.")
            return
        except Exception as exc:
            await message.reply_text(f"❌ خواندن فایل ناموفق بود:\n{exc}")
            return
        context.user_data["defaults_import_pending"] = {"single": single, "album": album}
        context.user_data["waiting"] = None
        summary = defaults_import_summary(single, album)
        await message.reply_text(
            "📥 خلاصه تنظیمات برای Import:\n\n" + summary + "\n\nآیا اعمال شود؟",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ تأیید و اعمال", callback_data="tag:import_confirm")],
                [InlineKeyboardButton("❌ لغو", callback_data="tag:import_cancel")],
            ]),
        )
        return

    # Summary mode: inspect forwarded MP3 metadata without entering the editor.
    if context.user_data.get("waiting") == "summary_tracks":
        if not document:
            return
        file_name = resolved_name or getattr(document, "file_name", None) or "forwarded_track.mp3"
        if media_source == "audio":
            file_name = ensure_mp3_filename(file_name, getattr(document, "title", None))
        elif not (file_name or "").lower().endswith(".mp3"):
            await message.reply_text("برای خلاصه‌سازی، فایل MP3 بفرست.")
            return
        else:
            file_name = ensure_mp3_filename(file_name)
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
            document, media_source, resolved_name = resolve_incoming_audio(replied)
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

    mime = (getattr(document, "mime_type", None) or "")
    if mime.startswith("image/"):
        return

    name = resolved_name or getattr(document, "file_name", None) or "music.mp3"
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
    # Accept:
    #   - any Telegram Audio/Music (media_source == "audio")
    #   - Document whose resolved name ends with .mp3
    # Reject only clearly non-MP3 documents.
    if media_source == "audio":
        name = ensure_mp3_filename(name, getattr(document, "title", None))
    elif not (name or "").lower().endswith(".mp3"):
        mime_dbg = getattr(document, "mime_type", None)
        fname_dbg = getattr(document, "file_name", None)
        print(
            f"reject non-mp3 media: source={media_source!r} "
            f"file_name={fname_dbg!r} mime={mime_dbg!r} resolved={name!r}"
        )
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
        archive_item = {
            "file_id": document.file_id,
            "file_unique_id": document.file_unique_id,
            "name": name,
            "title": (getattr(audio_meta, "title", None) or "").strip(),
            "artist": (getattr(audio_meta, "performer", None) or "").strip(),
            "duration": getattr(audio_meta, "duration", None),
            "size": document.file_size or 0,
            "message_id": message.message_id,
        }
        bucket.append(archive_item)
        try:
            user = update.effective_user
            chat = update.effective_chat
            if user and chat:
                get_audio_scanner_store().register_track(
                    user_id=user.id, source="A" if source == "source_a" else "B",
                    chat_id=chat.id, message_id=message.message_id,
                    file_id=document.file_id, file_unique_id=document.file_unique_id,
                    name=name, size=document.file_size or 0,
                    duration=getattr(audio_meta, "duration", None),
                )
        except Exception as scanner_store_error:
            print("Audio scanner catalog registration failed:", repr(scanner_store_error))
        op = "archive_a" if source == "source_a" else "archive_b"
        await update_progress_dashboard(message, context, op,
            outcome="skipped" if (document.file_size or 0) > MAX_SIZE else "success", track_name=name)
        return

    if document.file_size and document.file_size > MAX_SIZE and not context.user_data.get("album_mode"):
        await message.reply_text("حجم فایل نباید بیشتر از ۲۰ مگابایت باشه.")
        return

    # حالت آلبوم
    if context.user_data.get("album_mode"):
        if context.user_data.get("waiting") in ("album_main_artists", "album_name") or not context.user_data.get("album_name"):
            await message.reply_text(
                "اول نام هنرمندان و نام آلبوم را کامل کن؛ سپس فایل بفرست.",
                reply_markup=album_exit_keyboard(),
            )
            return
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
    if text in {"🎧 حالت آهنگ تکی", "🎵 حالت آهنگ تکی", "🎵 حالت تکی آهنگ"}:
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
        if not artists:
            await send_temporary_prompt(update.message, context, "حداقل یک نام هنرمند بفرست.")
            return
        context.user_data["main_artists"] = artists
        context.user_data["waiting"] = "album_name"
        await update.message.reply_text(
            f"✅ هنرمندان اصلی آلبوم: {' , '.join(artists)}\n\n💿 حالا نام آلبوم را وارد کن:",
            reply_markup=album_exit_keyboard(),
        )
        return

    if waiting == "album_name":
        album_name = value.strip()
        if not album_name:
            await send_temporary_prompt(update.message, context, "نام آلبوم نمی‌تواند خالی باشد. دوباره بفرست.")
            return
        context.user_data["album_name"] = album_name
        context.user_data["waiting"] = None
        await update.message.reply_text(
            f"✅ نام آلبوم: {album_name}\n\n"
            "🎵 حالا فایل‌های MP3 آلبوم را یکی‌یکی بفرست.\n"
            "وقتی تمام شد دکمه **Finish Uploading** را بزن.",
            reply_markup=album_exit_keyboard(),
        )
        return

    # تنظیم مقدار پیش‌فرض
    if waiting.startswith("setdefault:"):
        _, mode, key = waiting.split(":", 2)
        user_id = update.effective_user.id
        set_user_defaults(user_id, mode, key, value)
        context.user_data["waiting"] = None
        await update.message.reply_text(
            f"✅ پیش‌فرض `{escape_md(key)}` برای حالت `{escape_md(mode)}` ذخیره شد: `{escape_md(value)}`",
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
    if data.startswith("ascan:"):
        user_id = query.from_user.id if query.from_user else None
        if not scanner_user_allowed(user_id):
            await query.message.reply_text("🔒 Audio Similarity Scanner فقط برای مالک ربات فعال است.")
            return
        store = get_audio_scanner_store()
        chat_id = query.message.chat_id
        if data == "ascan:menu":
            await query.message.reply_text(
                "🎧 Audio Similarity Scanner\n\nاین ابزار آهنگ‌های ثبت‌شده از منابع A و B را با اثر انگشت صوتی مقایسه می‌کند. فایل‌ها یکی‌یکی دانلود و پس از تحلیل فوراً پاک می‌شوند.",
                reply_markup=audio_scanner_keyboard())
            return
        if data == "ascan:scan":
            if context.user_data.get("audio_scan_task") and not context.user_data["audio_scan_task"].done():
                await query.message.reply_text("یک اسکن در حال اجراست.")
                return
            synced = scanner_sync_current_archive(context, user_id, chat_id)
            tracks = store.tracks(user_id)
            sources = (context.user_data.get("archive") or {}).get("sources") or {}
            n_src = len(sources.get("source_a") or []) + len(sources.get("source_b") or [])
            if not tracks and n_src == 0:
                await query.message.reply_text("هنوز آهنگی از منبع A یا B ثبت نشده. ابتدا منابع را وارد کن.")
                return
            sent = await query.message.reply_text(
                f"⏳ اسکن صوتی شروع شد.\nفایل‌های فعال: {len(tracks)}\nموارد همگام‌شده از نشست فعلی: {synced}\nپردازش کاملاً مرحله‌ای است.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⏹ لغو اسکن", callback_data="ascan:cancel")]])
            )
            context.user_data["audio_scan_cancel"] = False
            task = asyncio.create_task(audio_scanner_worker(context, user_id, chat_id, sent.message_id))
            context.user_data["audio_scan_task"] = task
            return
        if data == "ascan:cancel":
            context.user_data["audio_scan_cancel"] = True
            await query.message.reply_text("درخواست توقف ثبت شد؛ پس از پایان پردازش فایل جاری، اسکن متوقف می‌شود.")
            return
        if data == "ascan:results":
            stats = context.user_data.get("scanner_last_report") or (context.user_data.get("archive") or {}).get("scan_stats")
            if not stats:
                await query.message.reply_text(
                    "هنوز اسکن کاملی ثبت نشده. «شروع اسکن» را بزن.",
                    reply_markup=audio_scanner_keyboard(),
                )
                return
            await query.message.reply_text(
                "📊 گزارش آخرین اسکن\n\n"
                f"• کل دریافتی: {stats.get('total_received', 0)}\n"
                f"• Duplicate قطعی حذف‌شده: {stats.get('removed_definite', 0)}\n"
                f"• باقی‌مانده از A: {stats.get('kept_a', 0)}\n"
                f"• جدید از B: {stats.get('kept_b', 0)}\n"
                f"• نهایی: {stats.get('final_count', 0)}\n"
                f"• نیازمند بررسی: {stats.get('review_count', 0)}\n"
                f"• خطا: {stats.get('errors', 0)}",
                reply_markup=audio_scanner_keyboard(),
            )
            return
        if data == "ascan:review_list":
            queue = (context.user_data.get("archive") or {}).get("queue") or []
            reviews = [x for x in queue if x.get("review")]
            if not reviews:
                await query.message.reply_text(
                    "مورد نیازمند بررسی‌ای در فهرست نهایی نیست.",
                    reply_markup=audio_scanner_keyboard(),
                )
                return
            lines = [f"🔎 نیازمند بررسی ({len(reviews)} مورد):\n"]
            for x in reviews[:40]:
                name = x.get("title") or Path(str(x.get("name") or "?")).stem
                src = x.get("source") or "?"
                note = x.get("review_note") or ""
                score = x.get("fingerprint_score")
                sc = f" {score}%" if score else ""
                lines.append(f"• [{src}] {name}{sc}")
                if note:
                    lines.append(f"  {note}")
            if len(reviews) > 40:
                lines.append(f"… و {len(reviews)-40} مورد دیگر")
            await query.message.reply_text("\n".join(lines)[:3900], reply_markup=audio_scanner_keyboard())
            return
        if data == "ascan:export":
            archive = context.user_data.get("archive") or {}
            queue = archive.get("queue") or []
            if not queue:
                await query.message.reply_text(
                    "فهرست نهایی خالی است. ابتدا اسکن را اجرا کن.",
                    reply_markup=audio_scanner_keyboard(),
                )
                return
            payload = build_archive_manifest_bytes(archive)
            buf = BytesIO(payload)
            buf.name = "archive_manifest.txt"
            await query.message.reply_document(
                document=buf,
                filename="archive_manifest.txt",
                caption=(
                    f"فهرست نهایی آرشیو ({len(queue)} آهنگ).\n"
                    "این فایل را به «ورودی بررسی منابع» بده."
                ),
            )
            return
        # Legacy callbacks kept as soft redirects
        if data in ("ascan:groups", "ascan:quarantine", "ascan:settings") or data.startswith("ascan:group:") or data.startswith("ascan:keep:") or data.startswith("ascan:restore:") or data.startswith("ascan:forget") or data.startswith("ascan:threshold:"):
            await query.message.reply_text(
                "این بخش ساده‌سازی شده است.\nDuplicateهای قطعی خودکار حذف می‌شوند.\nاز منوی اصلی Scanner استفاده کن.",
                reply_markup=audio_scanner_keyboard(),
            )
            return
        return  # end ascan:* handlers
    # ---------- Archive Mode callbacks ----------
    a = context.user_data.get("archive")
    if a is None and isinstance(data, str) and data.startswith("arch:"):
        # Auto-create archive session so menu buttons always work after /archive or restart
        if data != "arch:close":
            a = {"phase": "menu", "sources": {"source_a": [], "source_b": []}, "queue": [], "cursor": 0}
            context.user_data["archive"] = a

    if data == "arch:menu":
        if a: a["phase"] = "menu"
        await safe_edit_message_text(query.message, "📚 منوی آرشیو", reply_markup=archive_keyboard())
        return
    if not a:
        await query.message.reply_text("آرشیوی فعال نیست؛ از دکمه آرشیو موسیقی شروع کن.")
        return
    if data == "arch:review_menu":
        a["phase"] = "menu"
        await query.message.reply_text(
            "دریافت منابع:\n"
            "۱) Source A را بفرست و پایان را تأیید کن\n"
            "۲) Source B را بفرست و پایان را تأیید کن\n"
            "۳) Audio Scanner را اجرا کن تا تکراری‌های قطعی حذف و فهرست نهایی ساخته شود",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📥 افزودن منبع A", callback_data="arch:start_a")],
                [InlineKeyboardButton("📥 افزودن منبع B", callback_data="arch:start_b")],
                [InlineKeyboardButton("🎧 Audio Scanner", callback_data="ascan:menu")],
                [InlineKeyboardButton("🔙 منوی آرشیو", callback_data="arch:menu")],
            ]),
        )
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
        await finish_progress_dashboard(context, "archive_a", keep=True)
        a["phase"] = "source_a_review"
        stats = source_intake_stats(a, "source_a")
        await query.message.reply_text(
            format_source_intake_report(stats),
            reply_markup=source_finish_keyboard("source_a"),
        )
        return
    if data == "arch:finish_b":
        await finish_progress_dashboard(context, "archive_b", keep=True)
        a["phase"] = "source_b_review"
        stats = source_intake_stats(a, "source_b")
        await query.message.reply_text(
            format_source_intake_report(stats),
            reply_markup=source_finish_keyboard("source_b"),
        )
        return
    if data == "arch:continue_a":
        a["phase"] = "source_a"
        await query.message.reply_text(
            "📥 ادامه دریافت Source A — فایل‌های جدید را بفرست.\nاطلاعات قبلی حفظ شده‌اند.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("پایان منبع A", callback_data="arch:finish_a")]]),
        )
        return
    if data == "arch:continue_b":
        a["phase"] = "source_b"
        await query.message.reply_text(
            "📥 ادامه دریافت Source B — فایل‌های جدید را بفرست.\nاطلاعات قبلی حفظ شده‌اند.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("پایان منبع B", callback_data="arch:finish_b")]]),
        )
        return
    if data == "arch:confirm_a":
        stats = source_intake_stats(a, "source_a")
        await query.message.reply_text(
            f"در مجموع {stats['received']} آهنگ از Source A دریافت شده است.\nآیا دریافت این منبع را تأیید می‌کنی؟",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ تأیید و رفتن به Source B", callback_data="arch:confirm_a_yes")],
                [InlineKeyboardButton("↩️ بازگشت به Source A", callback_data="arch:continue_a")],
            ]),
        )
        return
    if data == "arch:confirm_a_yes":
        a["phase"] = "menu"
        a["source_a_confirmed"] = True
        await query.message.reply_text(
            f"✅ Source A نهایی شد ({len((a.get('sources') or {}).get('source_a') or [])} آهنگ).\nحالا Source B را اضافه کن.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📥 افزودن منبع B", callback_data="arch:start_b")],
                [InlineKeyboardButton("🔙 منوی آرشیو", callback_data="arch:menu")],
            ]),
        )
        return
    if data == "arch:confirm_b":
        stats = source_intake_stats(a, "source_b")
        await query.message.reply_text(
            f"در مجموع {stats['received']} آهنگ از Source B دریافت شده است.\nآیا دریافت این منبع را تأیید می‌کنی؟\n\nپس از تأیید، تحلیل Audio Fingerprint اجرا می‌شود.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ تأیید Source B", callback_data="arch:confirm_b_yes")],
                [InlineKeyboardButton("↩️ بازگشت به Source B", callback_data="arch:continue_b")],
            ]),
        )
        return
    if data == "arch:confirm_b_yes":
        a["phase"] = "menu"
        a["source_b_confirmed"] = True
        n_a = len((a.get("sources") or {}).get("source_a") or [])
        n_b = len((a.get("sources") or {}).get("source_b") or [])
        await query.message.reply_text(
            f"✅ Source B نهایی شد.\nSource A: {n_a} | Source B: {n_b}\n\n"
            "حالا Audio Scanner را باز کن تا تکراری‌های قطعی حذف شوند و فهرست نهایی ساخته شود.",
            reply_markup=audio_scanner_keyboard(),
        )
        return
    if data in ("arch:export_a", "arch:export_b"):
        key = "source_a" if data.endswith("_a") else "source_b"
        label = "A" if key == "source_a" else "B"
        payload = export_source_txt(a, key)
        buf = BytesIO(payload)
        buf.name = f"source_{label}_list.txt"
        await query.message.reply_document(
            document=buf,
            filename=f"source_{label}_list.txt",
            caption=f"فهرست Source {label} — بخش ---DATA--- شناسه‌های تلگرام را حفظ می‌کند.",
        )
        return
    if data == "arch:analyze":
        # Removed independent analyze step — Audio Scanner is the only analysis path.
        await query.message.reply_text(
            "مرحله «تحلیل و ساخت فهرست» حذف شده است.\nاز Audio Scanner استفاده کن.",
            reply_markup=audio_scanner_keyboard(),
        )
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

    if data == "tag:export":
        if not is_owner(query.from_user.id if query.from_user else None):
            await query.message.reply_text("🔒 فقط مالک ربات می‌تواند Export کند.")
            return
        payload = build_defaults_export_payload(query.from_user.id)
        raw = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        buf = BytesIO(raw)
        buf.name = "default_tags.json"
        await query.message.reply_document(
            document=buf,
            filename="default_tags.json",
            caption="📤 خروجی Default Tags (Single + Album). این فایل را برای Import بعدی نگه دار.",
        )
        return

    if data == "tag:import":
        if not is_owner(query.from_user.id if query.from_user else None):
            await query.message.reply_text("🔒 فقط مالک ربات می‌تواند Import کند.")
            return
        context.user_data["waiting"] = "defaults_import_file"
        await query.message.reply_text(
            "📥 فایل `default_tags.json` را که قبلاً Export کرده‌ای به صورت Document بفرست.\n"
            "قبل از اعمال، خلاصه تنظیمات نمایش داده می‌شود و تأیید می‌گیرم.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if data == "tag:import_confirm":
        if not is_owner(query.from_user.id if query.from_user else None):
            await query.message.reply_text("🔒 فقط مالک ربات می‌تواند Import کند.")
            return
        pending = context.user_data.pop("defaults_import_pending", None)
        context.user_data.pop("waiting", None)
        if not pending:
            await query.message.reply_text("درخواست Import منقضی شده است. دوباره از منو Import را بزن.")
            return
        try:
            apply_defaults_import(query.from_user.id, pending.get("single") or {}, pending.get("album") or {})
        except Exception as exc:
            await query.message.reply_text(f"❌ اعمال Import ناموفق بود؛ تنظیمات قبلی حفظ شد.\n{exc}")
            return
        await query.message.reply_text(
            "✅ Default Tags با موفقیت Import شد (Single و Album).",
            reply_markup=tag_config_keyboard(),
        )
        return

    if data == "tag:import_cancel":
        context.user_data.pop("defaults_import_pending", None)
        context.user_data["waiting"] = None
        await query.message.reply_text(
            "Import لغو شد. تنظیمات فعلی بدون تغییر ماند.",
            reply_markup=tag_config_keyboard(),
        )
        return

    if data == "tag:menu" or data == "tag:close":
        if data == "tag:close":
            await safe_edit_message_text(query.message, "بسته شد.")
        else:
            await safe_edit_message_text(
                query.message,
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
                state = f"✅ Apply `{escape_md(current.get(k, ''))}`"
            text += f"• {FIELDS.get(k, k)}: {state}\n"
        text += "\nدکمه وضعیت را بزن تا بین Apply / Keep / Remove جابه‌جا شوی. دکمه ✏️ برای ویرایش مقدار Apply است."
        await safe_edit_message_text(
            query.message,
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
                state = f"✅ Apply `{escape_md(current.get(k, ''))}`"
            lines.append(f"• {FIELDS.get(k, k)}: {state}")
        await safe_edit_message_text(
            query.message,
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


    if data.startswith("afield:"):
        key = data.split(":", 1)[1]
        if key == "cancel":
            await safe_delete_message(query.message)
            await show_album_overview(query, context)
            return
        context.user_data["waiting"] = "album_all_value"
        context.user_data["album_all_field"] = key
        await safe_delete_message(query.message)
        await send_temporary_prompt(
            query.message, context,
            f"مقدار جدید برای **{FIELDS.get(key, key)}** را بفرست (اعمال روی همه ترک‌ها):",
        )
        return

    if data.startswith("gfield:"):
        key = data.split(":", 1)[1]
        if key == "cancel":
            await safe_delete_message(query.message)
            await show_album_overview(query, context)
            return
        context.user_data["waiting"] = "album_selected_field"
        context.user_data["album_selected_key"] = key
        await safe_delete_message(query.message)
        await send_temporary_prompt(
            query.message, context,
            f"مقدار جدید برای **{FIELDS.get(key, key)}** را بفرست (اعمال روی ترک‌های انتخاب‌شده):",
        )
        return

    if data == "album:exit":
        # Cancel album mode session
        album_folder = context.user_data.get("album_folder")
        if album_folder and os.path.isdir(album_folder):
            shutil.rmtree(album_folder, ignore_errors=True)
        for k in ("album_mode", "editing_album_track", "album_tracks", "main_artists", "album_name",
                  "album_folder", "album_summary_draft", "album_summary_confirmed", "waiting",
                  "af_album_tracks", "album_selected"):
            context.user_data.pop(k, None)
        context.user_data["album_mode"] = False
        context.user_data["waiting"] = None
        await safe_delete_message(query.message)
        await query.message.chat.send_message(
            "✅ از حالت آلبوم خارج شدی.",
            reply_markup=main_reply_keyboard(),
        )
        return

    if data == "album:pick_tracks":
        tracks = context.user_data.get("album_tracks", [])
        await safe_delete_message(query.message)
        sent = await query.message.chat.send_message(
            "✏️ یک ترک را برای ویرایش انتخاب کن:",
            reply_markup=album_track_pick_keyboard(tracks, page=0),
        )
        nav_push(context, "album_pick", sent.message_id, parent="album_menu")
        return

    if data.startswith("album:pickpage:"):
        try:
            page = int(data.split(":")[2])
        except Exception:
            page = 0
        tracks = context.user_data.get("album_tracks", [])
        try:
            await safe_edit_reply_markup(query.message, reply_markup=album_track_pick_keyboard(tracks, page=page))
        except TelegramError:
            await query.message.chat.send_message(
                "✏️ یک ترک را برای ویرایش انتخاب کن:",
                reply_markup=album_track_pick_keyboard(tracks, page=page),
            )
        return

    if data == "album:pick_back":
        await safe_delete_message(query.message)
        await show_album_overview(query, context)
        return

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
            album_name = context.user_data.get("album_name")
            if album_name:
                set_tag(p, "album", album_name)
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
        await safe_edit_reply_markup(query.message, reply_markup=album_select_keyboard(context.user_data.get("album_tracks", []), selected))
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
        await safe_delete_message(query.message)
        sent = await query.message.chat.send_message(
            f"✅ {len(selected)} ترک انتخاب شد. فیلد موردنظر را انتخاب کن:",
            reply_markup=field_pick_keyboard("gfield"),
        )
        nav_push(context, "group_field", sent.message_id, parent="album_menu")
        return
        return

    if data == "album:back":
        # Keep current message (e.g. Album Summary) visible; open album menu again
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
        await safe_delete_message(query.message)
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
        await safe_delete_message(query.message)
        sent = await query.message.chat.send_message(
            "کدام فیلد را می‌خواهی روی **همه ترک‌ها** اعمال کنی؟",
            reply_markup=field_pick_keyboard("afield"),
            parse_mode=ParseMode.MARKDOWN,
        )
        nav_push(context, "album_all_field", sent.message_id, parent="album_menu")

    if data == "album:defaults":
        tracks = context.user_data.get("album_tracks", [])
        if not tracks:
            await query.message.reply_text("ترکی وجود ندارد.")
            return
        if context.user_data.get("op_busy"):
            await query.answer("عملیات در حال انجام است.", show_alert=False)
            return
        context.user_data["op_busy"] = True
        await safe_delete_message(query.message)
        status = await query.message.chat.send_message("⏳ در حال اعمال تگ‌های پیش‌فرض هستیم...")
        try:
            for dots in (".", "..", "..."):
                await asyncio.sleep(1)
                try:
                    await status.edit_text(f"⏳ در حال اعمال تگ‌های پیش‌فرض هستیم{dots}")
                except Exception:
                    pass
            user_id = query.from_user.id
            defaults = get_user_defaults(user_id)["album"]
            applied = 0
            for t in tracks:
                p = t.get("path")
                if not p or not os.path.isfile(p):
                    continue
                apply_defaults_to_file(p, defaults, is_single=False)
                if t.get("original_cover"):
                    restore_cover_from_backup(p, t["original_cover"])
                applied += 1
            try:
                await status.edit_text(f"✅ تگ‌های پیش‌فرض آلبوم روی {applied} ترک اعمال شد.")
            except Exception:
                await query.message.chat.send_message(f"✅ تگ‌های پیش‌فرض آلبوم روی {applied} ترک اعمال شد.")
            await show_album_overview(query, context)
        finally:
            context.user_data["op_busy"] = False
        return

    if data == "album:summary":
        await safe_delete_message(query.message)
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
            # Keep the Album Summary message; open editor UI as a new message
            draft_text = build_album_summary_text(entries, context.user_data.get("main_artists", []), draft.get("album_name", ""))
            await query.message.reply_text(
                "✏️ ویرایش خلاصه آلبوم:",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("➕ افزودن آهنگ", callback_data="albumsummary:add")],
                    [InlineKeyboardButton("✏️ ویرایش آهنگ موجود", callback_data="albumsummary:edit")],
                    [InlineKeyboardButton("🗑 حذف آهنگ", callback_data="albumsummary:delete")],
                    [InlineKeyboardButton("✅ تأیید و ثبت خلاصه", callback_data="albumsummary:confirm")],
                    [InlineKeyboardButton("↩️ لغو ویرایش", callback_data="albumsummary:cancel")],
                ]),
            )
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
                await safe_edit_message_text(query.message, 
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
            # Sequential single-message send (pre-v6.2.0 behavior): one track = one message
            targets = [t for t in targets if _status_for_slot(t, slot) != "sent"]
            targets = sorted(targets, key=lambda t: (t.get("index") is None, t.get("index", 0)))
            sent_n = failed_n = unknown_n = 0
            for i, t in enumerate(targets):
                if _status_for_slot(t, slot) == "sent":
                    continue
                _set_status_for_slot(t, slot, "sending")
                if i == 0 or i == len(targets) - 1 or i % 5 == 0:
                    try:
                        await safe_edit_reply_markup(query.message, reply_markup=af_album_keyboard(tracks, slot))
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
                # Light pacing to reduce host pressure on large albums
                if i + 1 < len(targets):
                    await asyncio.sleep(0.35)
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
        # Close editor menu (Change 2/3/8)
        await safe_delete_message(query.message)
        if field == "filename":
            current = context.user_data.get("filename", "—")
        else:
            current = get_tags(path).get(field, "—")
        if current in (None, "", "—"):
            current_disp = "ندارد"
        else:
            current_disp = current
        context.user_data["waiting"] = field
        context.user_data["waiting_mode"] = "field_edit"
        prompt = (
            f"✏️ **{FIELDS.get(field, field)}**\n"
            f"مقدار فعلی: `{escape_md(current_disp)}`\n\n"
            f"مقدار جدید را بفرست:"
        )
        sent = await query.message.chat.send_message(
            prompt,
            reply_markup=direct_field_keyboard(field),
            parse_mode=ParseMode.MARKDOWN,
        )
        nav_push(context, f"field:{field}", sent.message_id, parent="editor")
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
                f"📋 مقدار فعلی (کپی کن):\n`{escape_md(val)}`", parse_mode=ParseMode.MARKDOWN
            )
        else:
            await query.message.reply_text("این فیلد خالی است.")
        return

    if data == "action:cancel_field":
        context.user_data["waiting"] = None
        context.user_data["waiting_mode"] = None
        await safe_delete_message(query.message)
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
        await safe_delete_message(query.message)
        tags = get_tags(path)
        lyrics = tags.get("lyrics", "—")
        text = lyrics if lyrics != "—" else "متنی ثبت نشده."
        sent = await query.message.chat.send_message(
            text[:4000],
            reply_markup=lyrics_back_keyboard(),
        )
        nav_push(context, "showlyrics", sent.message_id, parent="editor")
        return

    if data == "action:picture":
        await safe_delete_message(query.message)
        context.user_data["waiting"] = "picture"
        sent = await query.message.chat.send_message(
            "🖼️ تصویر جدید کاور را به صورت عکس یا فایل بفرست.",
            reply_markup=picture_cancel_keyboard(),
        )
        nav_push(context, "picture", sent.message_id, parent="editor")
        return

    if data == "action:remove":
        await safe_delete_message(query.message)
        sent = await query.message.chat.send_message(
            "⚠️ آیا مطمئنی می‌خواهی **همه تگ‌ها و کاور** را حذف کنی؟",
            reply_markup=confirm_remove_keyboard(),
            parse_mode=ParseMode.MARKDOWN,
        )
        nav_push(context, "remove_confirm", sent.message_id, parent="editor")
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
        if context.user_data.get("op_busy"):
            await query.answer("عملیات در حال انجام است.", show_alert=False)
            return
        context.user_data["op_busy"] = True
        await safe_delete_message(query.message)
        status = await query.message.chat.send_message("⏳ در حال اعمال تگ‌های پیش‌فرض هستیم...")
        try:
            for dots in (".", "..", "..."):
                await asyncio.sleep(1)
                try:
                    await status.edit_text(f"⏳ در حال اعمال تگ‌های پیش‌فرض هستیم{dots}")
                except Exception:
                    pass
            user_id = query.from_user.id
            defaults = get_user_defaults(user_id)
            is_single = not context.user_data.get("editing_album_track", False)
            apply_defaults_to_file(
                path,
                defaults["single"] if is_single else defaults["album"],
                is_single=is_single,
            )
            if context.user_data.get("original_cover"):
                restore_cover_from_backup(path, context.user_data["original_cover"])
            tags = get_tags(path)
            new_filename = generate_filename(
                tags.get("artist", ""), tags.get("title", "")
            )
            context.user_data["filename"] = new_filename
            try:
                await status.edit_text(
                    f"✅ تگ‌های پیش‌فرض اعمال شدند.\nاسم فایل: `{escape_md(new_filename)}`",
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception:
                await query.message.chat.send_message(
                    f"✅ تگ‌های پیش‌فرض اعمال شدند.\nاسم فایل: `{escape_md(new_filename)}`",
                    parse_mode=ParseMode.MARKDOWN,
                )
            await show_editor(
                update,
                context,
                is_album=context.user_data.get("editing_album_track", False),
            )
        finally:
            context.user_data["op_busy"] = False
        return

    if data == "action:save":
        if not path or not os.path.exists(path):
            return
        if context.user_data.get("op_busy"):
            await query.answer("عملیات در حال انجام است.", show_alert=False)
            return
        context.user_data["op_busy"] = True
        await safe_delete_message(query.message)
        status = await query.message.chat.send_message("💾 در حال ذخیره آهنگ هستیم...")
        try:
            for dots in (".", "..", "..."):
                await asyncio.sleep(1)
                try:
                    await status.edit_text(f"💾 در حال ذخیره آهنگ هستیم{dots}")
                except Exception:
                    pass
        except Exception:
            pass
        try:
            await status.delete()
        except Exception:
            pass
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
        finally:
            context.user_data["op_busy"] = False
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

    buttons = [
        [InlineKeyboardButton("✏️ Edit Selected Track", callback_data="album:pick_tracks")],
        [InlineKeyboardButton("📌 Apply Default Tags (Album)", callback_data="album:defaults")],
        [InlineKeyboardButton("📝 Apply to All Tracks", callback_data="album:all")],
        [InlineKeyboardButton("🎯 Select Tracks for Group Edit", callback_data="album:select_menu")],
        [InlineKeyboardButton("🧰 Album Tools", callback_data="album:tools")],
        [InlineKeyboardButton("🗑 Remove Artist from All Tracks", callback_data="album:remove_artist")],
        [InlineKeyboardButton("📊 Album Summary", callback_data="album:summary")],
        [InlineKeyboardButton("📩 Save All & Send", callback_data="album:save_all")],
    ]

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
    if context.user_data.get("op_busy"):
        try:
            await query.answer("عملیات در حال انجام است.", show_alert=False)
        except Exception:
            pass
        return
    context.user_data["op_busy"] = True
    await safe_delete_message(query.message)
    status_msg = await query.message.chat.send_message("⏳ در حال آماده‌سازی و ارسال آهنگ‌های آلبوم هستیم...")
    try:
        for dots in (".", "..", "..."):
            await asyncio.sleep(1)
            try:
                await status_msg.edit_text(f"⏳ در حال آماده‌سازی و ارسال آهنگ‌های آلبوم هستیم{dots}")
            except Exception:
                pass
        try:
            await status_msg.delete()
        except Exception:
            pass
    except Exception:
        pass
    try:
        await _save_album_all_body(query, context, tracks, main_artists)
    finally:
        context.user_data["op_busy"] = False


async def _save_album_all_body(query, context, tracks, main_artists):
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



async def send_album_tracks_as_media_groups(bot, chat_id, targets: list) -> tuple:
    """Backward-compatible name: sequential single sends (no Media Group)."""
    sent_n = failed_n = unknown_n = 0
    ordered = sorted(
        [t for t in targets if t.get("_last_send_status") != "sent"],
        key=lambda t: (t.get("index") is None, t.get("index", 0)),
    )
    for i, t in enumerate(ordered):
        status, detail = await send_processed_to_channel(
            bot,
            chat_id,
            t.get("output_path"),
            filename=t.get("filename") or "track.mp3",
            title=t.get("title"),
            performer=t.get("artist"),
            duration=t.get("duration"),
            cover=t.get("cover"),
        )
        t["_last_send_status"] = status
        if status == "sent":
            sent_n += 1
        elif status == "unknown":
            unknown_n += 1
        else:
            failed_n += 1
        if i + 1 < len(ordered):
            await asyncio.sleep(0.35)
    return sent_n, failed_n, unknown_n


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
    err = context.error
    # Temporary network/gateway failures are retried by the updater; log briefly.
    if isinstance(err, (NetworkError, TimedOut)):
        print(f"BOT TRANSIENT NETWORK ERROR (will retry): {err!r}")
        return
    print("BOT ERROR:", repr(err))
    traceback.print_exception(type(err), err, err.__traceback__)


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

    # Separate HTTP clients: long read timeout for getUpdates long-polling,
    # and resilient timeouts for regular Bot API calls.
    request = HTTPXRequest(
        connection_pool_size=20, connect_timeout=30, read_timeout=90,
        write_timeout=90, pool_timeout=30,
    )
    updates_request = HTTPXRequest(
        connection_pool_size=8, connect_timeout=30, read_timeout=90,
        write_timeout=60, pool_timeout=30,
    )
    install_telegram_resilience()
    app = (
        Application.builder()
        .token(TOKEN)
        .request(request)
        .get_updates_request(updates_request)
        .post_init(post_init)
        .concurrent_updates(False)  # avoid overlapping update processing
        .build()
    )

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
    # drop_pending_updates=False keeps backlog; bootstrap_retries=-1 retries
    # temporary network failures (e.g. Bad Gateway) during startup forever.
    # PTB's updater already applies backoff on NetworkError during getUpdates.
    app.run_polling(
        drop_pending_updates=False,
        bootstrap_retries=-1,
        poll_interval=1.0,
        timeout=30,
        allowed_updates=None,
    )


if __name__ == "__main__":
    main()
