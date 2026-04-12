from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime


CACHE_DIR = "reports"
CACHE_FILE = os.path.join(CACHE_DIR, ".audit_cache.json")


def _content_hash(en_html: str, ar_html: str) -> str:
    """SHA-256 of the concatenated EN + AR page content."""
    digest = hashlib.sha256()
    digest.update(en_html.encode("utf-8", errors="replace"))
    digest.update(ar_html.encode("utf-8", errors="replace"))
    return digest.hexdigest()


class AuditCache:
    """
    Thread-safe, file-backed content-hash cache.

    A single instance should be created per pipeline run and shared across
    all worker threads.  All reads and writes are protected by a lock so
    concurrent qa_agent workers never corrupt the cache file.
    """

    def __init__(self, cache_file: str = CACHE_FILE):
        self._path = cache_file
        self._lock = threading.Lock()
        self._data: dict[str, dict] = self._load()

    def get(
        self,
        psid: str,
        en_html: str,
        ar_html: str,
    ) -> dict | None:
        """
        Return cached audit result if the page content is unchanged.

        Returns a dict with key "issues" (list[dict]) on a cache hit,
        or None on a cache miss or content change.
        """
        content_hash = _content_hash(en_html, ar_html)
        with self._lock:
            entry = self._data.get(psid)
        if entry and entry.get("hash") == content_hash:
            return {"issues": entry.get("issues", []), "from_cache": True}
        return None

    def set(
        self,
        psid: str,
        en_html: str,
        ar_html: str,
        issues: list[dict],
    ) -> None:
        """Store audit results for psid keyed by page content hash."""
        content_hash = _content_hash(en_html, ar_html)
        entry = {
            "hash": content_hash,
            "audited_at": datetime.now().isoformat(timespec="seconds"),
            "issue_count": len(issues),
            "issues": issues,
        }
        with self._lock:
            self._data[psid] = entry
            self._flush()

    def invalidate(self, psid: str) -> None:
        """Force-expire the cache entry for psid (e.g. after a manual edit)."""
        with self._lock:
            if psid in self._data:
                del self._data[psid]
                self._flush()

    def stats(self) -> dict:
        """Return hit/miss counters accumulated since this instance was created."""
        with self._lock:
            return {
                "entries": len(self._data),
                "cache_file": self._path,
            }

    def clear(self) -> None:
        """Wipe the entire cache (use with caution)."""
        with self._lock:
            self._data = {}
            self._flush()

    def _load(self) -> dict:
        if not os.path.exists(self._path):
            return {}
        try:
            with open(self._path, encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            return {}

    def _flush(self) -> None:
        """Write current in-memory cache to disk. Caller must hold self._lock."""
        os.makedirs(CACHE_DIR, exist_ok=True)
        tmp = self._path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self._data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, self._path)  # atomic on POSIX; near-atomic on Windows
