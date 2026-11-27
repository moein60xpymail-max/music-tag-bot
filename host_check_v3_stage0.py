#!/usr/bin/env python3
"""
Music Tag Bot v3 - Stage 0 host diagnostic.

Run manually as: python host_check.py --once
This script does NOT import the bot, read BOT_TOKEN, or touch bot data files.
It only creates/updates its own _host_check directory under existing locations.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import platform
import shutil
import socket
import sys
import time
from pathlib import Path

TEST_NAME = "_host_check"
MARKER = ".host_check_owned"
SIGNATURE = "music-tag-bot-host-check-v2"
PAYLOAD_NAME = "payload.bin"
HISTORY_NAME = "history.jsonl"
PAYLOAD_SIZE = 1024 * 1024
MAX_PROBE_MB = 100

def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()

def read_text(path):
    try:
        return Path(path).read_text(errors="replace").strip()
    except Exception:
        return None

def human(n):
    if n is None:
        return "unknown"
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    x = float(n)
    for unit in units:
        if x < 1024 or unit == units[-1]:
            return f"{x:.1f} {unit}"
        x /= 1024

def boot_id():
    return read_text("/proc/sys/kernel/random/boot_id") or "unavailable"

def make_payload():
    out = bytearray()
    block = b"music-tag-bot-host-check-v2"
    while len(out) < PAYLOAD_SIZE:
        block = hashlib.sha256(block).digest()
        out.extend(block)
    return bytes(out[:PAYLOAD_SIZE])

def safe_test_dir(base):
    base = Path(base)
    if not base.is_dir():
        return None, "MISSING (base path does not exist)"
    target = base / TEST_NAME
    try:
        if target.is_symlink():
            return None, "REFUSED (test directory is a symlink)"
        if target.exists():
            if not target.is_dir():
                return None, "REFUSED (test path is not a directory)"
            marker = target / MARKER
            if marker.is_symlink() or not marker.is_file():
                if any(target.iterdir()):
                    return None, "REFUSED (directory exists but is not owned by this script)"
            elif marker.read_text(errors="replace").strip() != SIGNATURE:
                return None, "REFUSED (ownership marker does not match)"
        else:
            target.mkdir()
        marker = target / MARKER
        if not marker.exists():
            marker.write_text(SIGNATURE + "\n")
        return target, None
    except Exception as exc:
        return None, f"NOT WRITABLE ({type(exc).__name__}: {exc})"

def read_history(path):
    entries = []
    try:
        for line in path.read_text(errors="replace").splitlines():
            try:
                entries.append(json.loads(line))
            except Exception:
                continue
    except Exception:
        pass
    return entries

def mount_for(path):
    try:
        target = os.path.realpath(path)
        best = None
        with open("/proc/mounts", "r", errors="replace") as f:
            for line in f:
                p = line.split()
                if len(p) < 3:
                    continue
                mountpoint = p[1].replace("\\040", " ")
                real_mount = os.path.realpath(mountpoint)
                if target == real_mount or target.startswith(real_mount.rstrip("/") + "/"):
                    if best is None or len(real_mount) > len(best[0]):
                        best = (real_mount, p[0], p[2])
        return best
    except Exception:
        return None

def probe(base, testdir, probe_mb):
    result = {}
    try:
        times = []
        p = testdir / "probe.tmp"
        with p.open("wb") as f:
            for _ in range(10):
                t0 = time.perf_counter()
                f.write(os.urandom(4096))
                f.flush()
                os.fsync(f.fileno())
                times.append((time.perf_counter() - t0) * 1000)
        result["fsync_avg_ms"] = round(sum(times) / len(times), 2)
        if probe_mb:
            free = shutil.disk_usage(base).free
            need = probe_mb * 1024 * 1024
            if free < need + 100 * 1024 * 1024:
                result["bulk"] = f"SKIPPED (free {human(free)}, reserve 100 MiB)"
            else:
                chunk = os.urandom(1024 * 1024)
                t0 = time.perf_counter()
                with p.open("wb") as f:
                    for _ in range(probe_mb):
                        f.write(chunk)
                    f.flush()
                    os.fsync(f.fileno())
                elapsed = max(time.perf_counter() - t0, 0.001)
                result["bulk"] = f"{probe_mb} MiB in {elapsed:.2f}s ({probe_mb/elapsed:.1f} MiB/s)"
        else:
            result["bulk"] = "DISABLED"
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            (testdir / "probe.tmp").unlink()
        except Exception:
            pass
    return result

def inspect_location(label, base, probe_mb):
    base = os.path.abspath(os.path.expanduser(base))
    out = [f"\n[{label}]", f"path={base}"]
    try:
        usage = shutil.disk_usage(base)
        out.append(f"disk_total={human(usage.total)} used={human(usage.used)} free={human(usage.free)}")
    except Exception as exc:
        out.append(f"disk=unavailable ({type(exc).__name__}: {exc})")
    mount = mount_for(base)
    if mount:
        out.append(f"mount={mount[0]} source={mount[1]} fstype={mount[2]}")
    else:
        out.append("mount=unavailable")
    testdir, error = safe_test_dir(base)
    if error:
        out.append(f"storage_test={error}")
        return "\n".join(out)
    marker_path = testdir / "marker.json"
    history_path = testdir / HISTORY_NAME
    old_history = read_history(history_path)
    old_marker = None
    try:
        old_marker = json.loads(marker_path.read_text()) if marker_path.exists() else None
    except Exception:
        out.append("marker=CORRUPT")
    payload_path = testdir / PAYLOAD_NAME
    expected_hash = hashlib.sha256(make_payload()).hexdigest()
    if payload_path.is_file() and not payload_path.is_symlink():
        actual_hash = hashlib.sha256(payload_path.read_bytes()).hexdigest()
        payload_state = "OK" if actual_hash == expected_hash else "CORRUPT"
    else:
        payload_state = "MISSING"
    current = {
        "time": now(),
        "hostname": socket.gethostname(),
        "boot_id": boot_id(),
        "pid": os.getpid(),
        "cwd": os.getcwd(),
    }
    if not old_history:
        verdict = "NEW_OR_HISTORY_UNAVAILABLE"
    elif payload_state == "CORRUPT":
        verdict = "INDETERMINATE_PAYLOAD_CORRUPT"
    elif payload_state == "MISSING":
        verdict = "INDETERMINATE_PAYLOAD_MISSING"
    elif old_marker and old_marker.get("boot_id") == current["boot_id"] and old_marker.get("hostname") == current["hostname"]:
        verdict = "SAME_CONTAINER_OR_BOOT"
    else:
        verdict = "PERSISTED_ACROSS_DIFFERENT_BOOT_OR_INSTANCE"
    out.append(f"previous_valid_history_records={len(old_history)}")
    out.append(f"previous_payload={payload_state}")
    out.append(f"verdict={verdict}")
    io = probe(base, testdir, probe_mb)
    out.append(f"io_probe={json.dumps(io, ensure_ascii=False)}")
    current["run_count"] = len(old_history) + 1
    current["payload_sha256"] = expected_hash
    temp = marker_path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(current, ensure_ascii=False, indent=2))
    with temp.open("rb") as f:
        os.fsync(f.fileno())
    os.replace(temp, marker_path)
    with history_path.open("a") as f:
        f.write(json.dumps(current, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())
    if not payload_path.exists():
        payload_path.write_bytes(make_payload())
        with payload_path.open("rb") as f:
            os.fsync(f.fileno())
    return "\n".join(out)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="run once and exit")
    parser.add_argument("--probe-mb", type=int, default=10, help="temporary write probe, max 100 MiB; 0 disables")
    args = parser.parse_args()
    probe_mb = max(0, min(args.probe_mb, MAX_PROBE_MB))
    data_dir = os.environ.get("DATA_DIR", "data")
    print("=== MUSIC TAG BOT v3 / HOST CHECK ===")
    print(f"time_utc={now()}")
    print(f"cwd={os.getcwd()}")
    print(f"python={sys.version.split()[0]} os={platform.platform()} uid={getattr(os, 'getuid', lambda: 'n/a')()}")
    print(f"DATA_DIR={data_dir}")
    print(f"cgroup_memory_limit={read_text('/sys/fs/cgroup/memory.max') or read_text('/sys/fs/cgroup/memory/memory.limit_in_bytes') or 'unknown'}")
    print("\n/proc/mounts (relevant entries):")
    try:
        with open("/proc/mounts", "r", errors="replace") as f:
            for line in f:
                if any(x in line for x in (" / ", " /tmp ", " /app ", " /workspace ", " /data ")):
                    p = line.split()
                    if len(p) >= 3:
                        print(f"  mount={p[1]} source={p[0]} fstype={p[2]}")
    except Exception as exc:
        print(f"  unavailable: {type(exc).__name__}")
    locations = [("cwd", os.getcwd()), ("DATA_DIR", data_dir), ("/tmp", "/tmp")]
    seen = set()
    for label, path in locations:
        real = os.path.realpath(os.path.abspath(os.path.expanduser(path)))
        if real in seen:
            continue
        seen.add(real)
        print(inspect_location(label, path, probe_mb), flush=True)
    print("\n=== END HOST CHECK ===", flush=True)

if __name__ == "__main__":
    main()
