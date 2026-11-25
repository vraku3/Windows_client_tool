"""NTFS Alternate Data Streams (ADS) Scanner.

Uses native kernel32 FindFirstStreamW / FindNextStreamW via ctypes to detect:
1. Zone.Identifier streams marking files downloaded from the Internet/untrusted sources.
2. Hidden payloads or data attached to files outside the default ::$DATA stream.

Pure Python (ctypes), zero external dependencies, SAC-safe.
"""
from __future__ import annotations

import ctypes
import logging
import os
from ctypes import wintypes
from dataclasses import dataclass
from typing import List, Optional

logger = logging.getLogger(__name__)

kernel32 = ctypes.windll.kernel32

FindStreamInfoStandard = 0


class WIN32_FIND_STREAM_DATA(ctypes.Structure):
    _fields_ = [
        ("StreamSize", ctypes.c_int64),
        ("cStreamName", wintypes.WCHAR * 296),
    ]


kernel32.FindFirstStreamW.restype = wintypes.HANDLE
kernel32.FindFirstStreamW.argtypes = [
    wintypes.LPCWSTR,
    wintypes.DWORD,
    ctypes.c_void_p,
    wintypes.DWORD,
]

kernel32.FindNextStreamW.restype = wintypes.BOOL
kernel32.FindNextStreamW.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
]

kernel32.FindClose.restype = wintypes.BOOL
kernel32.FindClose.argtypes = [wintypes.HANDLE]


@dataclass
class AlternateDataStream:
    file_path: str
    stream_name: str
    size_bytes: int
    is_zone_identifier: bool = False
    zone_id: Optional[int] = None
    details: str = ""


def get_file_streams(file_path: str) -> List[AlternateDataStream]:
    """Enumerate all NTFS streams for a specific file path."""
    if not os.path.isfile(file_path):
        return []

    data = WIN32_FIND_STREAM_DATA()
    h = kernel32.FindFirstStreamW(file_path, FindStreamInfoStandard, ctypes.byref(data), 0)
    if h == wintypes.HANDLE(-1).value or h == -1 or not h:
        return []

    streams: List[AlternateDataStream] = []
    try:
        while True:
            s_name = data.cStreamName
            s_size = data.StreamSize

            # ::$DATA is the primary default stream; anything else is an ADS
            if s_name and s_name.lower() != "::$data":
                is_zone = "zone.identifier" in s_name.lower()
                zone_num = None
                if is_zone:
                    try:
                        # Attempt to read zone id from stream: path:Zone.Identifier
                        with open(f"{file_path}:Zone.Identifier", "r", encoding="utf-8", errors="ignore") as zf:
                            for zline in zf:
                                if "zoneid=" in zline.lower():
                                    zone_num = int(zline.split("=")[1].strip())
                    except Exception as e:
                        logger.debug("Could not read Zone.Identifier for %s: %s", file_path, e)

                streams.append(AlternateDataStream(
                    file_path=file_path,
                    stream_name=s_name,
                    size_bytes=s_size,
                    is_zone_identifier=is_zone,
                    zone_id=zone_num,
                    details=f"ADS {s_name} ({s_size} bytes)" + (f" [ZoneId={zone_num}]" if zone_num is not None else ""),
                ))

            if not kernel32.FindNextStreamW(h, ctypes.byref(data)):
                break
    finally:
        kernel32.FindClose(h)

    return streams


def scan_directory_for_ads(dir_path: str, max_files: int = 500) -> List[AlternateDataStream]:
    """Scan files in a directory recursively for Alternate Data Streams."""
    results: List[AlternateDataStream] = []
    if not os.path.isdir(dir_path):
        return results

    scanned = 0
    try:
        for root, _, files in os.walk(dir_path):
            for f in files:
                scanned += 1
                if scanned > max_files:
                    return results
                fp = os.path.join(root, f)
                ads = get_file_streams(fp)
                if ads:
                    results.extend(ads)
    except OSError as e:
        logger.debug("Error scanning directory for ADS: %s", e)

    return results
