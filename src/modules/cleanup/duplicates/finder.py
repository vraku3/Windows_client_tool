"""Size -> samples -> native SHA-256, with bounded parallel I/O."""
from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
import time
from collections import defaultdict
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from threading import Lock

from modules.cleanup.cleanup_scanner import known_folders
from .enumerate import CLOUD_MASK, ScanOptions, enumerate_files, unsafe_ancestors
from .hash_cache import HashCache
from .models import DuplicateGroup, FileEntry, ScanResult

logger = logging.getLogger(__name__)
BUFFER_SIZE = 1024 * 1024
SAMPLE_SIZE = 64 * 1024
METHODS = {
    "exact": ("Exact content", "Size, samples, then full SHA-256 of every survivor."),
    "verified": ("Exact + byte verification", "Full SHA-256 and byte-by-byte verification of every group."),
    "probable": ("Fast probable", "Head, middle and tail samples; exact comparison required before removal."),
    "name_size": ("Same name + same size", "Candidates with matching names and sizes; removal requires exact bytes."),
    "name": ("Same name only", "Name candidates; removal requires exact bytes."),
    "size": ("Same size only", "Size candidates; never automatically selected."),
    "images": ("Similar images", "64-bit perceptual dHash; never automatically selected."),
}


class ScanCancelled(Exception):
    """Cooperative cancellation, caught by the Qt adapter rather than emitted as an error."""


def check_cancel(cancelled):
    if cancelled():
        raise ScanCancelled()


def safe_snapshot(entry: FileEntry) -> FileEntry:
    current = FileEntry.from_path(entry.path)
    if (current.attrs & CLOUD_MASK or known_folders.is_cloud_placeholder(entry.path)
            or unsafe_ancestors(os.path.dirname(entry.path))):
        raise OSError("Cloud placeholder or redirected path is excluded")
    if not entry.unchanged(current):
        raise OSError("File changed since enumeration")
    return current


def sample_offsets(size: int, middle: bool = False):
    if size <= SAMPLE_SIZE * 2:
        return [(0, size)]
    offsets = [0, size - SAMPLE_SIZE]
    if middle:
        offsets.append(max(0, size // 2 - SAMPLE_SIZE // 2))
    return [(offset, min(SAMPLE_SIZE, size - offset)) for offset in sorted(set(offsets))]


def hash_file(entry: FileEntry, *, kind="full", cancelled=lambda: False,
              on_bytes=lambda count: None) -> str:
    check_cancel(cancelled)
    safe_snapshot(entry)
    digest = hashlib.sha256()
    with open(entry.path, "rb", buffering=0) as stream:
        opened = os.fstat(stream.fileno())
        if (opened.st_size != entry.size or opened.st_mtime_ns != entry.mtime_ns
                or (entry.ino and (opened.st_ino, opened.st_dev) != (entry.ino, entry.dev))):
            raise OSError("File was replaced before reading")
        if kind == "full":
            buf = bytearray(BUFFER_SIZE)
            view = memoryview(buf)
            while True:
                check_cancel(cancelled)
                count = stream.readinto(buf)
                if not count:
                    break
                digest.update(view[:count])
                on_bytes(count)
        else:
            for offset, length in sample_offsets(entry.size, kind == "probable"):
                check_cancel(cancelled)
                stream.seek(offset)
                data = stream.read(length)
                if len(data) != length:
                    raise OSError("File shortened during sample read")
                digest.update(data)
                on_bytes(len(data))
        after = os.fstat(stream.fileno())
        if after.st_size != entry.size or after.st_mtime_ns != entry.mtime_ns:
            raise OSError("File changed while reading")
    check_cancel(cancelled)
    safe_snapshot(entry)
    return digest.hexdigest()


def bytes_equal(left: FileEntry, right: FileEntry, cancelled=lambda: False,
                on_bytes=lambda count: None) -> bool:
    safe_snapshot(left)
    safe_snapshot(right)
    with open(left.path, "rb", buffering=0) as a, open(right.path, "rb", buffering=0) as b:
        while True:
            check_cancel(cancelled)
            x, y = a.read(BUFFER_SIZE), b.read(BUFFER_SIZE)
            on_bytes(len(x) + len(y))
            if x != y:
                return False
            if not x:
                break
    safe_snapshot(left)
    safe_snapshot(right)
    return True


def image_support() -> tuple[bool, str]:
    try:
        from PIL import Image
        return True, f"Pillow {Image.__version__}"
    except ImportError as exc:
        return False, f"Pillow is unavailable: {exc}"


def dhash(entry: FileEntry, cancelled=lambda: False) -> str:
    from PIL import Image, ImageOps
    check_cancel(cancelled)
    safe_snapshot(entry)
    try:
        with Image.open(entry.path) as image:
            if image.width * image.height > 40_000_000:
                raise OSError("Image exceeds the 40 megapixel decoding limit")
            image.draft("RGB", (256, 256))
            small = ImageOps.exif_transpose(image).convert("L").resize((9, 8), Image.Resampling.LANCZOS)
            pixels = [small.getpixel((x, y)) for y in range(8) for x in range(9)]
    except (Image.DecompressionBombError, SyntaxError) as exc:
        raise OSError(f"Image refused: {exc}") from exc
    check_cancel(cancelled)
    safe_snapshot(entry)
    value = 0
    for row in range(8):
        for col in range(8):
            value = (value << 1) | (pixels[row * 9 + col] > pixels[row * 9 + col + 1])
    return f"{value:016x}"


class _Meter:
    def __init__(self, result, callback):
        self.result, self.callback = result, callback
        self.lock = Lock()
        self.stage = ""
        self.total = self.done = 0
        self.started = self.last = time.perf_counter()

    def begin(self, stage, total):
        self.stage, self.total, self.done = stage, total, 0
        self.started = time.perf_counter()
        self.last = 0
        self.emit()

    def add(self, count):
        with self.lock:
            self.done += count
            self.result.bytes_hashed += count
            self.emit()

    def emit(self, force=False):
        now = time.perf_counter()
        if self.callback and (force or now - self.last >= .12):
            elapsed = max(.001, now - self.started)
            rate = self.done / elapsed
            self.callback({"stage": self.stage, "files": self.result.listed,
                           "groups": self.result.candidate_groups, "done": self.done,
                           "total": self.total, "mb_s": rate / 1_000_000,
                           "eta": (max(0, self.total - self.done) / rate) if rate else None})
            self.last = now


def _group(entries, key):
    buckets = defaultdict(list)
    for entry in entries:
        buckets[key(entry)].append(entry)
    return [files for files in buckets.values() if len(files) > 1]


def _parallel_hash(files, kind, options, cache, cancelled, result, meter):
    started = time.perf_counter()
    bytes_before = result.bytes_hashed
    results, pending = {}, []
    for entry in files:
        check_cancel(cancelled)
        try:
            digest = cache.get(entry, kind) if cache else None
        except sqlite3.Error as exc:
            logger.warning("Duplicate cache read failed; continuing without it: %s", exc)
            result.errors.append(("Hash cache", str(exc)))
            cache, digest = None, None
        if digest is not None:
            results[entry.path] = digest
            result.cache_hits += 1
        else:
            pending.append(entry)
    total = sum(f.size if kind == "full" else sum(n for _, n in sample_offsets(f.size, kind == "probable"))
                for f in pending) if kind != "dhash-v1" else len(pending)
    meter.begin({"full": "SHA-256", "sample": "Head + tail", "probable": "Head + middle + tail",
                 "dhash-v1": "Image fingerprints"}[kind], total)

    def run(entry):
        if kind == "dhash-v1":
            value = dhash(entry, cancelled)
            # Image decoding isn't a sequential byte hash. Report files separately.
            with meter.lock:
                meter.done += 1
                meter.emit()
        else:
            value = hash_file(entry, kind=kind, cancelled=cancelled, on_bytes=meter.add)
        return value

    pending.sort(key=lambda f: f.size, reverse=True)
    queue = iter(pending)
    with ThreadPoolExecutor(max_workers=max(1, options.threads), thread_name_prefix="dupes-hash") as pool:
        active = {}
        exhausted = False
        while active or not exhausted:
            check_cancel(cancelled)
            while len(active) < max(1, options.threads) * 2 and not exhausted:
                entry = next(queue, None)
                if entry is None:
                    exhausted = True
                else:
                    active[pool.submit(run, entry)] = entry
            if not active:
                break
            done, _ = wait(active, return_when=FIRST_COMPLETED)
            for future in done:
                entry = active.pop(future)
                try:
                    value = future.result()
                    results[entry.path] = value
                    if cache:
                        try:
                            cache.put(entry, kind, value)
                        except sqlite3.Error as exc:
                            logger.warning("Duplicate cache write failed; continuing without it: %s", exc)
                            result.errors.append(("Hash cache", str(exc)))
                            cache = None
                except ScanCancelled:
                    raise
                except (OSError, ValueError) as exc:
                    logger.warning("Duplicate comparison skipped %s: %s", entry.path, exc)
                    result.errors.append((entry.path, str(exc)))
    result.timings[kind] = time.perf_counter() - started
    result.stage_bytes[kind] = result.bytes_hashed - bytes_before
    meter.emit(force=True)
    return results


def _image_groups(files, digests, threshold):
    # BK-tree over group representatives: bounded Hamming search, not all pairs.
    root = None
    grouped = []
    for entry in files:
        if entry.path not in digests:
            continue
        value = int(digests[entry.path], 16)
        matching, stack = None, [root] if root else []
        while stack:
            node = stack.pop()
            distance = (value ^ node[0]).bit_count()
            if distance <= threshold:
                matching = node[1]
                break
            stack.extend(child for edge, child in node[2].items()
                         if distance - threshold <= edge <= distance + threshold)
        if matching is not None:
            grouped[matching].append(entry)
            continue
        index = len(grouped)
        grouped.append([entry])
        new = (value, index, {})
        if root is None:
            root = new
        else:
            node = root
            while True:
                distance = (value ^ node[0]).bit_count()
                if distance not in node[2]:
                    node[2][distance] = new
                    break
                node = node[2][distance]
    return [g for g in grouped if len(g) > 1]


def find_duplicates(options: ScanOptions, *, cancelled=lambda: False, progress=None) -> ScanResult:
    if options.method not in METHODS:
        raise ValueError("Unknown duplicate comparison method")
    result = ScanResult()
    started = time.perf_counter()
    files, result.hardlinks, result.skipped, result.errors = enumerate_files(options, cancelled, progress)
    check_cancel(cancelled)
    result.listed = len(files)
    result.timings["enumeration"] = time.perf_counter() - started
    method, digest_map = options.method, {}
    if method == "name":
        groups = _group(files, lambda f: os.path.basename(f.path).casefold())
    elif method == "name_size":
        groups = _group(files, lambda f: (os.path.basename(f.path).casefold(), f.size))
    elif method == "images":
        supported, reason = image_support()
        if not supported:
            raise ValueError(reason)
        extensions = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tif", ".tiff"}
        groups = [[f for f in files if os.path.splitext(f.path)[1].lower() in extensions]]
    else:
        groups = _group(files, lambda f: f.size)
    result.candidate_groups = len(groups)
    cache = None
    if options.use_cache:
        try:
            cache = HashCache(options.cache_path)
        except (OSError, sqlite3.Error) as exc:
            logger.warning("Duplicate cache unavailable: %s", exc)
            result.errors.append(("Hash cache", str(exc)))
    meter = _Meter(result, progress)
    try:
        if method in ("exact", "verified", "probable", "images"):
            entries = [f for g in groups for f in g]
            kind = "dhash-v1" if method == "images" else "probable" if method == "probable" else "sample"
            digest_map = _parallel_hash(entries, kind, options, cache, cancelled, result, meter)
            if method == "images":
                groups = _image_groups(entries, digest_map, options.image_threshold)
            else:
                groups = _group([f for f in entries if f.path in digest_map],
                                lambda f: (f.size, digest_map[f.path]))
            if method in ("exact", "verified"):
                entries = [f for g in groups for f in g]
                digest_map = _parallel_hash(entries, "full", options, cache, cancelled, result, meter)
                groups = _group([f for f in entries if f.path in digest_map],
                                lambda f: (f.size, digest_map[f.path]))
            if method == "verified":
                verification_start = time.perf_counter()
                bytes_before = result.bytes_hashed
                meter.begin("Byte verification", sum(2 * g[0].size * (len(g) - 1) for g in groups))
                verified = []
                for group in groups:
                    matched = [group[0]]
                    for target in group[1:]:
                        try:
                            if bytes_equal(group[0], target, cancelled, meter.add):
                                matched.append(target)
                        except OSError as exc:
                            result.errors.append((target.path, str(exc)))
                            logger.warning("Duplicate verification skipped %s: %s", target.path, exc)
                    if len(matched) > 1:
                        verified.append(matched)
                groups = verified
                result.timings["verification"] = time.perf_counter() - verification_start
                result.stage_bytes["verification"] = result.bytes_hashed - bytes_before
                meter.emit(force=True)
    finally:
        if cache:
            try:
                cache.close()
            except sqlite3.Error as exc:
                logger.warning("Duplicate cache could not be saved: %s", exc)
                result.errors.append(("Hash cache", str(exc)))
    confidence = "exact" if method in ("exact", "verified") else "probable" if method == "probable" else "similar" if method == "images" else "candidate"
    result.groups = [DuplicateGroup(i, sorted(g, key=lambda f: f.path), method, confidence,
                                    digest_map.get(g[0].path, ""))
                     for i, g in enumerate(groups) if len(g) > 1]
    result.groups.sort(key=lambda g: g.reclaimable, reverse=True)
    result.timings["total"] = time.perf_counter() - started
    if progress:
        progress({"stage": "Complete", "files": result.listed, "groups": len(result.groups),
                  "done": result.bytes_hashed, "total": result.bytes_hashed})
    return result
