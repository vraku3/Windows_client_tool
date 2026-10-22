# src/modules/process_explorer/virustotal_client.py
from __future__ import annotations
import hashlib
import logging
from dataclasses import dataclass
from typing import Dict, Optional

import requests

logger = logging.getLogger(__name__)

_VT_API_BASE = "https://www.virustotal.com/api/v3"


@dataclass
class VTResult:
    found: bool
    sha256: str
    malicious: int = 0
    total: int = 0
    score: Optional[str] = None       # e.g. "3/72"
    details: Optional[dict] = None    # full last_analysis_results
    error: Optional[str] = None       # None for a confirmed response, including 404


def compute_sha256(path: str) -> Optional[str]:
    """Compute SHA256 of a file. Returns None on error."""
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception as e:
        logger.warning("SHA256 failed for %s: %s", path, e)
        return None


def check_hash(sha256: str, api_key: str) -> VTResult:
    """Query VT for a known hash. Returns VTResult(found=False) on 404."""
    try:
        resp = requests.get(
            f"{_VT_API_BASE}/files/{sha256}",
            headers={"x-apikey": api_key},
            timeout=10,
        )
        if resp.status_code == 404:
            return VTResult(found=False, sha256=sha256)
        resp.raise_for_status()
        attrs = resp.json()["data"]["attributes"]
        stats = attrs.get("last_analysis_stats", {})
        malicious = stats.get("malicious", 0)
        total = sum(stats.values())
        return VTResult(
            found=True, sha256=sha256,
            malicious=malicious, total=total,
            score=f"{malicious}/{total}",
            details=attrs.get("last_analysis_results"),
        )
    except (requests.RequestException, ValueError, KeyError, TypeError) as e:
        logger.error("VT hash check failed: %s", e)
        return VTResult(found=False, sha256=sha256, error=str(e) or type(e).__name__)


class VTClient:
    """Session-scoped VirusTotal client with in-memory cache."""

    def __init__(self, api_key: str):
        self._api_key = api_key
        self._cache: Dict[str, VTResult] = {}
        self._pending: Dict[str, str] = {}   # analysis_id → sha256

    def check(self, sha256: str) -> VTResult:
        if sha256 in self._cache:
            return self._cache[sha256]
        result = check_hash(sha256, self._api_key)
        if not result.error:
            self._cache[sha256] = result
        return result

    def submit_file(self, path: str, sha256: str = "") -> Optional[str]:
        """Upload file to VT for analysis. Returns analysis ID or None.

        Pass ``sha256`` so the result can later be cached under the correct key
        when ``poll_analysis`` completes.
        """
        try:
            with open(path, "rb") as f:
                file_data = f.read()
            resp = requests.post(
                f"{_VT_API_BASE}/files",
                headers={"x-apikey": self._api_key},
                files={"file": (path, file_data)},
                timeout=60,
            )
            resp.raise_for_status()
            analysis_id: str = resp.json()["data"]["id"]
            if sha256:
                self._pending[analysis_id] = sha256
            return analysis_id
        except (requests.RequestException, OSError) as e:
            logger.error("VT file submission failed: %s", e)
            return None

    def poll_analysis(self, analysis_id: str) -> Optional[VTResult]:
        """Poll for analysis result. Returns None if still pending."""
        try:
            resp = requests.get(
                f"{_VT_API_BASE}/analyses/{analysis_id}",
                headers={"x-apikey": self._api_key},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()["data"]
            if data["attributes"]["status"] != "completed":
                return None
            stats = data["attributes"]["stats"]
            malicious = stats.get("malicious", 0)
            total = sum(stats.values())
            # Try to recover sha256 from the response or from the pending map
            sha256 = (data.get("meta", {}).get("file_info", {}).get("sha256", "")
                      or self._pending.pop(analysis_id, ""))
            result = VTResult(found=True, sha256=sha256, malicious=malicious,
                              total=total, score=f"{malicious}/{total}")
            if sha256:
                self._cache[sha256] = result
            return result
        except requests.RequestException as e:
            logger.error("VT poll failed: %s", e)
            return None
