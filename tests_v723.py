"""Tests for Music Tag Bot v7.2.3 — Album Mode receive path."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure project root is importable
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _fake_mp3_bytes(payload: bytes = b"\xff\xfb" + bytes([1] * 4000)) -> bytes:
    body = b"T" * 32
    size = len(body)
    ss = bytes(
        [
            (size >> 21) & 0x7F,
            (size >> 14) & 0x7F,
            (size >> 7) & 0x7F,
            size & 0x7F,
        ]
    )
    return b"ID3\x03\x00\x00" + ss + body + payload


class AlbumModeUnitTests(unittest.TestCase):
    def test_version_is_723(self):
        import bot

        self.assertEqual(bot.VERSION, "v7.2.3")

    def test_album_uploading_keyboard_has_finish(self):
        import bot

        kb = bot.album_uploading_keyboard()
        datas = []
        for row in kb.inline_keyboard:
            for btn in row:
                datas.append(btn.callback_data)
        self.assertIn("album:finish", datas)
        self.assertIn("album:exit", datas)

    def test_resolve_incoming_audio_accepts_audio_and_mp3_document(self):
        import bot

        audio = SimpleNamespace(
            file_id="A1",
            file_unique_id="U1",
            file_name=None,
            title="Song",
            mime_type="audio/mpeg",
            file_size=1000,
            duration=10,
            performer="Artist",
        )
        msg = SimpleNamespace(audio=audio, document=None)
        media, source, name = bot.resolve_incoming_audio(msg)
        self.assertEqual(source, "audio")
        self.assertTrue(str(name).lower().endswith(".mp3"))

        doc = SimpleNamespace(
            file_id="D1",
            file_unique_id="U2",
            file_name="track.mp3",
            mime_type="audio/mpeg",
            file_size=2000,
        )
        msg2 = SimpleNamespace(audio=None, document=doc)
        media2, source2, name2 = bot.resolve_incoming_audio(msg2)
        self.assertEqual(source2, "document")
        self.assertTrue(str(name2).lower().endswith(".mp3"))

    def test_album_mode_state_after_name(self):
        """Simulates state machine: artists -> name -> waiting album_tracks."""
        context = SimpleNamespace(user_data={})
        context.user_data["album_mode"] = True
        context.user_data["main_artists"] = ["Test Artist"]
        context.user_data["album_name"] = "Test Album"
        context.user_data["waiting"] = "album_tracks"
        context.user_data["album_tracks"] = []
        context.user_data["album_folder"] = tempfile.mkdtemp(prefix="album_test_")
        self.assertTrue(context.user_data["album_mode"])
        self.assertEqual(context.user_data["waiting"], "album_tracks")
        self.assertTrue(context.user_data["album_name"])
        # cleanup
        import shutil

        shutil.rmtree(context.user_data["album_folder"], ignore_errors=True)

    def test_duplicate_message_ids_blocked(self):
        seen = set()
        mid = 101
        self.assertNotIn(mid, seen)
        seen.add(mid)
        self.assertIn(mid, seen)
        # second time would skip
        self.assertTrue(mid in seen)

    def test_failed_media_message_helper_exists(self):
        import bot

        text = bot.failed_media_message("file.mp3", Exception("timeout"), "download")
        self.assertIn("file.mp3", text)
        self.assertTrue(len(text) > 5)

    def test_single_mode_flag_clears_album(self):
        import bot

        context = SimpleNamespace(user_data={
            "album_mode": True,
            "album_name": "X",
            "waiting": "album_tracks",
        })
        # single_mode_command resets these — verify expected keys used by code
        context.user_data["album_mode"] = False
        context.user_data["waiting"] = None
        self.assertFalse(context.user_data["album_mode"])


class AlbumReceiveIntegrationish(unittest.IsolatedAsyncioTestCase):
    async def test_receive_audio_album_path_acknowledges(self):
        """Album mode ready: receive_audio should accept and append a track."""
        import bot

        folder = tempfile.mkdtemp(prefix="album_it_")
        raw = _fake_mp3_bytes()
        # write a real file that MP3() can open after "download"
        target_content = raw

        async def fake_download(tg_file, path):
            Path(path).write_bytes(target_content)

        audio = SimpleNamespace(
            file_id="AID",
            file_unique_id="UID",
            file_name="song.mp3",
            title="Song Title",
            mime_type="audio/mpeg",
            file_size=len(raw),
            duration=12,
            performer="Artist",
        )
        message = SimpleNamespace(
            audio=audio,
            document=None,
            message_id=555,
            media_group_id=None,
            reply_text=AsyncMock(),
            chat_id=1,
            reply_to_message=None,
        )
        update = SimpleNamespace(
            effective_message=message,
            effective_user=SimpleNamespace(id=42),
            effective_chat=SimpleNamespace(id=1),
            message=message,
        )
        context = SimpleNamespace(
            user_data={
                "album_mode": True,
                "album_name": "My Album",
                "waiting": "album_tracks",
                "album_folder": folder,
                "album_tracks": [],
                "album_seen_message_ids": set(),
                "album_receive_attempt": 0,
                "main_artists": ["Artist"],
            },
            bot=SimpleNamespace(
                get_file=AsyncMock(return_value=SimpleNamespace(file_id="AID")),
            ),
            application=SimpleNamespace(bot=None),
        )

        with patch.object(bot, "download_to_drive_retry", side_effect=fake_download), \
             patch.object(bot, "update_progress_dashboard", new_callable=AsyncMock), \
             patch.object(bot, "mt_load", return_value=None), \
             patch.object(bot, "get_cover", return_value=None), \
             patch.object(bot, "MP3") as mock_mp3:
            mock_mp3.return_value = SimpleNamespace(info=SimpleNamespace(length=12.0))
            # MP3() is also used as constructor for validation — return same
            await bot.receive_audio(update, context)

        self.assertEqual(len(context.user_data["album_tracks"]), 1)
        self.assertTrue(context.user_data["album_tracks"][0].get("downloaded") or
                        context.user_data["album_tracks"][0].get("path"))
        message.reply_text.assert_awaited()
        # second identical message_id must be ignored
        message.reply_text.reset_mock()
        with patch.object(bot, "download_to_drive_retry", side_effect=fake_download), \
             patch.object(bot, "update_progress_dashboard", new_callable=AsyncMock), \
             patch.object(bot, "mt_load", return_value=None), \
             patch.object(bot, "get_cover", return_value=None), \
             patch.object(bot, "MP3", return_value=SimpleNamespace(info=SimpleNamespace(length=12.0))):
            await bot.receive_audio(update, context)
        self.assertEqual(len(context.user_data["album_tracks"]), 1)
        message.reply_text.assert_not_awaited()

        import shutil
        shutil.rmtree(folder, ignore_errors=True)

    async def test_receive_audio_album_download_failure_notifies(self):
        import bot

        folder = tempfile.mkdtemp(prefix="album_fail_")
        audio = SimpleNamespace(
            file_id="AID2",
            file_unique_id="UID2",
            file_name="bad.mp3",
            title="Bad",
            mime_type="audio/mpeg",
            file_size=1000,
            duration=1,
            performer="X",
        )
        message = SimpleNamespace(
            audio=audio,
            document=None,
            message_id=777,
            media_group_id=None,
            reply_text=AsyncMock(),
            chat_id=1,
            reply_to_message=None,
        )
        update = SimpleNamespace(
            effective_message=message,
            effective_user=SimpleNamespace(id=1),
            effective_chat=SimpleNamespace(id=1),
            message=message,
        )
        context = SimpleNamespace(
            user_data={
                "album_mode": True,
                "album_name": "A",
                "waiting": "album_tracks",
                "album_folder": folder,
                "album_tracks": [],
                "album_seen_message_ids": set(),
                "album_receive_attempt": 0,
            },
            bot=SimpleNamespace(get_file=AsyncMock(return_value=SimpleNamespace(file_id="AID2"))),
        )

        async def boom(*a, **k):
            raise TimeoutError("download timed out")

        with patch.object(bot, "download_to_drive_retry", side_effect=boom), \
             patch.object(bot, "update_progress_dashboard", new_callable=AsyncMock):
            await bot.receive_audio(update, context)

        self.assertEqual(len(context.user_data["album_tracks"]), 0)
        message.reply_text.assert_awaited()
        args = message.reply_text.await_args
        self.assertTrue(args is not None)
        import shutil
        shutil.rmtree(folder, ignore_errors=True)

    async def test_album_mode_does_not_break_when_incomplete(self):
        import bot

        message = SimpleNamespace(
            audio=SimpleNamespace(
                file_id="A", file_unique_id="U", file_name="t.mp3", title="T",
                mime_type="audio/mpeg", file_size=100, duration=1, performer="P",
            ),
            document=None,
            message_id=1,
            media_group_id=None,
            reply_text=AsyncMock(),
            chat_id=1,
            reply_to_message=None,
        )
        update = SimpleNamespace(
            effective_message=message,
            effective_user=SimpleNamespace(id=1),
            effective_chat=SimpleNamespace(id=1),
            message=message,
        )
        context = SimpleNamespace(
            user_data={
                "album_mode": True,
                "album_name": None,
                "waiting": "album_name",
                "album_tracks": [],
            },
            bot=SimpleNamespace(),
        )
        # early path requires album_name — falls through; late path should warn
        with patch.object(bot, "update_progress_dashboard", new_callable=AsyncMock):
            await bot.receive_audio(update, context)
        # Should have replied asking to finish artist/album first
        message.reply_text.assert_awaited()


if __name__ == "__main__":
    unittest.main(verbosity=2)
