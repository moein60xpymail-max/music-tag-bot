# راهنمای توسعه و انتشار Music Tag Bot

این فایل برای نسخه‌های آینده حفظ شود.

## 1. ساختار پروژه

```
bot.py              # هسته اصلی ربات (منوها، هندلرها، ادیت تگ)
channel_repo.py     # مخزن کانال‌ها + SQLite متادیتا (از v8.0.0)
audio_scanner.py    # اسکنر تشابه صوتی و SQLite اسکنر
self_scan.py        # Archive Self-Scan
archive.py          # مدل‌های کمکی آرشیو
sessions.py         # پایداری نشست
dupes.py            # تشخیص تکراری
config.py           # تنظیمات پایه
requirements.txt
Dockerfile
nixpacks.toml
tests_v*.py         # تست‌های نسخه‌ها
CHANGELOG_v*.md
RELEASE_GUIDE.md    # همین فایل
```

داده پایدار در `DATA_DIR` (پیش‌فرض `data/`):

- `channel_repo.sqlite3` — متادیتای مخزن کانال
- `repo_channels.json` — تنظیمات کانال‌های Source A/B/Dest
- `audio_scanner.sqlite3`
- `auto_forward_channels.json`
- `admin_registry.json`
- `user_defaults.json`

## 2. روش افزایش شماره نسخه

در `bot.py`:

```python
VERSION = "vX.Y.Z"
VERSION_CHANGES = [
    "تغییر ۱",
    "تغییر ۲",
]
```

فقط تغییرات **همین نسخه** را در `VERSION_CHANGES` بگذار. تاریخچه قدیمی را در CHANGELOG نگه دار.

## 3. محل تنظیم تغییرات /version

دستور `/version` از `VERSION` و `VERSION_CHANGES` می‌خواند. هیچ لیست قدیمی در پاسخ نمایش داده نشود.

## 4. روش ثبت تغییرات نسخه جدید

1. فایل `CHANGELOG_vX.Y.Z.md` بساز.
2. `VERSION` و `VERSION_CHANGES` را به‌روز کن.
3. تست‌های مربوط به نسخه را با نام `tests_vXYZ.py` اضافه کن.

## 5. روش مدیریت تست‌های ده نسخه اخیر

- تست‌های ده نسخه آخر را نگه دار.
- تست‌های قدیمی‌تر غیرضروری را حذف کن؛ تست‌های حیاتی (smoke، compile) را نگه دار.
- قبل از انتشار: `python -m py_compile bot.py` و در صورت امکان `pytest`.

## 6. روش ساخت ZIP انتشار

```bash
# حذف کش و موقت
find . -type d -name __pycache__ -exec rm -rf {} +
find . -type d -name .pytest_cache -exec rm -rf {} +
rm -f *.pyc *.log

# نام پوشه و ZIP
cd ..
zip -r Music_Tag_Bot_vX.Y.Z.zip Music_Tag_Bot_vX.Y.Z/ \
  -x "*/__pycache__/*" "*/.pytest_cache/*" "*.pyc"
```

## 7. فایل‌ها و قابلیت‌های حساس

- `setchannel1` / `setchannel2` و Auto Forward — با مخزن کانال تداخل نکند.
- انتخاب نقطه شروع (`arch:start_begin` / `arch:start_pick`) — رفتار حفظ شود.
- دکمه «ورودی بررسی منابع» و خروجی TXT اسکنر.
- Single Mode / Album Mode / APIC cover.
- پنل `/admin` و عضویت اجباری کانال.

## 8. چک‌لیست تست قبل از انتشار

- [ ] `python -m py_compile bot.py channel_repo.py ...`
- [ ] Import همه ماژول‌ها بدون خطا
- [ ] `/version` فقط نسخه فعلی را نشان دهد
- [ ] منوی آرشیو شامل «مخزن کانال‌ها» باشد
- [ ] انتخاب Source A/B/Dest با picker
- [ ] دریافت پست صوتی کانال و ذخیره در SQLite
- [ ] تطبیق TXT با مخزن
- [ ] دکمه دریافت آهنگ برای ادیت (copyMessage)
- [ ] شروع از ابتدا و از آهنگ انتخابی
- [ ] Restart و بازیابی صف
- [ ] قابلیت‌های قدیمی (Single/Album/Scanner/AF) سالم باشند

## 9. توسعه سیستم مخزن کانال‌ها

- تنظیمات در `repo_channels.json` (جدا از AF).
- متادیتا در `channel_repo.sqlite3` جدول `repo_tracks`.
- request_id انتخاب‌گر: Source A=10، Source B=11، Dest=12.
- هندلر `receive_channel_repo_audio` روی `filters.ChatType.CHANNEL`.
- تطبیق: `channel_repo.match_track` (exact/high/ambiguous/none).
- کپی: `bot.copy_message` از chat_id/message_id ذخیره‌شده.
- فایل صوتی روی هاست دائمی ذخیره نشود.

## 10. دیتابیس و ساختار اطلاعات

### repo_tracks

| ستون | توضیح |
|------|--------|
| owner_id | شناسه کاربر مالک |
| source | source_a / source_b |
| chat_id, message_id | مرجع تلگرام |
| file_id, file_unique_id | شناسه فایل |
| file_name, title, performer, duration | متادیتا |
| status | received / in_progress / completed / ... |

### queue_state

JSON پایدار صف پردازش برای بازیابی پس از Restart.

### repo_channels.json

```json
{
  "123456": {
    "source_a": {"chat_id": -100..., "title": "..."},
    "source_b": {...},
    "dest": {...}
  }
}
```


## 9. اتصال به اینترنت (v8.1.0)

- برای جست‌وجوی Genius، مقدار `GENIUS_ACCESS_TOKEN` را به‌صورت Environment Variable در هاست تنظیم کن؛ توکن را در کد، ZIP یا چت قرار نده.
- توکن از بخش API Clients حساب Genius ساخته می‌شود: https://genius.com/api-clients
- Genius برای شناسایی آهنگ، کاور، هنرمندان، آلبوم و تاریخ انتشار استفاده می‌شود.
- ژانر در صورت وجود با تطبیق دقیق ضبط در MusicBrainz بررسی می‌شود؛ نبود ژانر به معنی خالی ماندن فیلد است.
- متن کامل ترانه به‌صورت خودکار دریافت یا ذخیره نمی‌شود؛ برای فیلد Lyrics از متن مجاز/ارائه‌شده توسط کاربر استفاده شود.
- درخواست‌های MusicBrainz ترتیبی و با فاصله اجرا می‌شوند.

## 10. Other Album

- پیش‌فرض‌های Album اعمال می‌شوند، اما `album`, `track`, `disc` و `albumartist` عمداً دست‌نخورده می‌مانند.
- شماره Disc در این عملیات خوانده یا تغییر داده نمی‌شود.
- `albumartist` موجود، صاحب آلبوم تلقی می‌شود و در نام فایل قبل از `Ft.` قرار می‌گیرد؛ هنرمند/هنرمندان Track پس از آن می‌آیند.
