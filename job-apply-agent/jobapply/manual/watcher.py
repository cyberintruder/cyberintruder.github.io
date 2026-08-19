"""Watch the screenshot inbox.

Drop screenshots into `inbox/screenshots/` from your phone or desktop. Files are
deduplicated by content hash, so re-dropping the same image never produces a
second application.
"""

from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path

from sqlalchemy import select

from .. import settings as settings_mod
from ..db import session_scope
from ..models import ScreenshotItem, ScreenshotStatus

log = logging.getLogger(__name__)

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def inbox_dir(st=None) -> Path:
    st = st or settings_mod.load()
    return Path(st.config.manual.inbox_dir).expanduser()


def pending_screenshots(st=None) -> list[Path]:
    """Every image in the inbox that has not been ingested yet."""
    st = st or settings_mod.load()
    folder = inbox_dir(st)
    if not folder.exists():
        return []
    with session_scope() as sess:
        known = set(sess.scalars(select(ScreenshotItem.sha256)).all())
    out: list[Path] = []
    for path in sorted(folder.iterdir()):
        if not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        if sha256_of(path) in known:
            continue
        out.append(path)
    return out


def register_screenshot(path: Path) -> int | None:
    """Insert a screenshot row. Returns None if it was already ingested."""
    digest = sha256_of(path)
    with session_scope() as sess:
        existing = sess.scalar(select(ScreenshotItem).where(ScreenshotItem.sha256 == digest))
        if existing:
            return None
        item = ScreenshotItem(path=str(path), sha256=digest, status=ScreenshotStatus.PENDING)
        sess.add(item)
        sess.flush()
        return item.id


def archive(path: Path, st=None) -> Path | None:
    """Move a processed screenshot out of the inbox."""
    st = st or settings_mod.load()
    dest_dir = Path(st.config.manual.processed_dir).expanduser()
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / path.name
    counter = 1
    while dest.exists():
        dest = dest_dir / f"{path.stem}-{counter}{path.suffix}"
        counter += 1
    try:
        path.rename(dest)
        return dest
    except OSError as exc:
        log.warning("could not archive %s: %s", path, exc)
        return None


class ScreenshotWatcher:
    """Poll the inbox. Simple polling beats inotify here — screenshots arrive via
    cloud sync, which does not always emit filesystem events."""

    def __init__(self, interval_s: float = 15.0, archive_processed: bool = True) -> None:
        self.interval_s = interval_s
        self.archive_processed = archive_processed
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def poll_once(self) -> list[dict]:
        from ..pipeline import ingest_screenshots

        st = settings_mod.load()
        paths = pending_screenshots(st)
        if not paths:
            return []
        log.info("ingesting %d screenshot(s)", len(paths))
        results = ingest_screenshots(paths)
        if self.archive_processed:
            for path, res in zip(paths, results, strict=False):
                if res.get("status") in {"queued", "ignored"}:
                    archive(path, st)
        return results

    def run_forever(self) -> None:
        log.info("watching %s every %.0fs", inbox_dir(), self.interval_s)
        while not self._stop:
            try:
                for res in self.poll_once():
                    log.info("screenshot -> %s", res)
            except Exception:  # noqa: BLE001
                log.exception("screenshot poll failed")
            time.sleep(self.interval_s)


def ingest_directory(folder: Path) -> list[dict]:
    """One-shot ingest of an arbitrary folder (used by `jobapply ingest`)."""
    from ..pipeline import ingest_screenshots

    folder = Path(folder)
    paths = [p for p in sorted(folder.iterdir())
             if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES]
    return ingest_screenshots(paths)
