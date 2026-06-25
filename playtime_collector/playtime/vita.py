"""PS Vita playtime source — pull the session queue over FTP.

The on-Vita kernel plugin (``playtime_k.skprx``) appends finished sessions as
JSON lines ``{"titleId","seconds"}`` to ``ux0:data/VitaPlaytime/pending.jsonl``.
The Vita has no reliable way to push them itself, so — exactly like the PS3 —
Home Assistant *pulls*: this loop FTPs into the Vita, atomically claims the queue
(rename ``pending.jsonl`` -> ``sending.jsonl`` so the plugin's next write starts a
fresh ``pending`` and nothing is lost), ingests each line, then deletes the
claimed file. Game titles are resolved from each app's ``param.sfo``, read over
the same FTP and cached.

Needs an always-on FTP server on the Vita: the ``ftpeverywhere`` taiHEN plugin
(autostarts on boot, port 1337) or VitaShell's FTP. Set ``vita_host`` to enable.
"""
import asyncio
import ftplib
import io
import json
import logging
import struct
from datetime import datetime, timezone
from pathlib import Path

from . import config, db
from .titles import fix_title

log = logging.getLogger("playtime")

PT_DIR = "ux0:/data/VitaPlaytime"
PENDING = PT_DIR + "/pending.jsonl"
SENDING = PT_DIR + "/sending.jsonl"

# titleId -> resolved display title, cached so each param.sfo is read at most once.
_title_cache = {}


def _connect():
    ftp = ftplib.FTP()
    ftp.connect(config.VITA_HOST, config.VITA_PORT, timeout=10)
    ftp.login()  # ftpeverywhere / VitaShell FTP are anonymous
    return ftp


def _parse_sfo_title(blob):
    """Pull the TITLE value out of a param.sfo blob, or None."""
    if len(blob) < 20 or blob[:4] != b"\x00PSF":
        return None
    try:
        _magic, _ver, key_start, data_start, num = struct.unpack_from("<IIIII", blob, 0)
        for i in range(num):
            ko, _fmt, plen, _pmax, do = struct.unpack_from("<HHIII", blob, 20 + i * 16)
            key_end = blob.find(b"\x00", key_start + ko)
            key = blob[key_start + ko:key_end].decode("latin-1", "ignore")
            if key == "TITLE":
                val = blob[data_start + do:data_start + do + plen].split(b"\x00")[0]
                return val.decode("utf-8", "ignore") or None
    except (struct.error, ValueError):
        return None
    return None


def _resolve_title(ftp, title_id):
    if title_id in _title_cache:
        return _title_cache[title_id]
    raw = None
    buf = io.BytesIO()
    try:
        ftp.retrbinary("RETR ux0:/app/%s/sce_sys/param.sfo" % title_id, buf.write)
        raw = _parse_sfo_title(buf.getvalue())
    except ftplib.all_errors:
        pass
    title = fix_title(raw, title_id)
    _title_cache[title_id] = title
    return title


def _ignored(title_id):
    return title_id in config.VITA_IGNORE_TITLES or title_id.startswith("NPXS")


def _cache_icon(ftp, title_id):
    """Fetch the Vita app's icon0.png over FTP and cache it where /game-icon serves
    it (ICON_DIR/games/<titleId>). Cached once; skipped if already present or absent."""
    path = Path(config.ICON_DIR) / "games" / title_id
    if path.exists():
        return
    buf = io.BytesIO()
    try:
        ftp.retrbinary("RETR ux0:/app/%s/sce_sys/icon0.png" % title_id, buf.write)
    except ftplib.all_errors:
        return
    data = buf.getvalue()
    if data[:8] != b"\x89PNG\r\n\x1a\n":  # only store a real PNG
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _ended_iso(rec):
    """Real session-end time from the kernel's endedAt (Unix seconds, UTC). Falls
    back to 'now' if the field is missing or implausible (e.g. unset Vita clock)."""
    ended = int(rec.get("endedAt") or 0)
    if ended >= 1000000000:  # ~2001+, sane wall clock
        return datetime.fromtimestamp(ended, tz=timezone.utc).isoformat()
    return db.now_iso()


def _drain_once():
    """One FTP pass: claim + read + ingest + delete. Runs in a worker thread.

    Returns (reachable, inserted). `reachable` is False only when the Vita FTP
    can't be reached at all (asleep / Wi-Fi down) — harmless, the kernel keeps
    buffering and we retry next interval."""
    try:
        ftp = _connect()
    except ftplib.all_errors:
        return (False, 0)
    try:
        # Claim a fresh snapshot. If a prior run already left a sending.jsonl
        # (crashed before delete), this rename fails harmlessly and we process
        # that leftover instead.
        try:
            ftp.rename(PENDING, SENDING)
        except ftplib.all_errors:
            pass

        buf = io.BytesIO()
        try:
            ftp.retrbinary("RETR " + SENDING, buf.write)
        except ftplib.all_errors:
            return (True, 0)  # reachable, nothing queued

        inserted = 0
        for line in buf.getvalue().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            title_id = str(rec.get("titleId") or "")
            seconds = int(rec.get("seconds") or 0)
            if not title_id or seconds <= 0 or _ignored(title_id):
                continue
            title = _resolve_title(ftp, title_id)
            _cache_icon(ftp, title_id)
            db.insert_closed_session(
                "psvita", config.VITA_ACCOUNT, title_id, title, seconds, _ended_iso(rec))
            inserted += 1
            log.info("⏹ %s — %s · %ds (vita)", config.VITA_ACCOUNT, title or title_id, seconds)

        try:
            ftp.delete(SENDING)
        except ftplib.all_errors:
            pass
        return (True, inserted)
    finally:
        try:
            ftp.quit()
        except ftplib.all_errors:
            pass


async def vita_sync_loop():
    log.info("vita sync every %ss (ftp %s:%s, account %s)",
             config.VITA_SYNC_INTERVAL, config.VITA_HOST, config.VITA_PORT, config.VITA_ACCOUNT)
    while True:
        try:
            reachable, inserted = await asyncio.to_thread(_drain_once)
            if reachable:
                db.set_meta("vita_last_sync_at", db.now_iso())
            if inserted:
                log.info("ingested %d vita session(s)", inserted)
        except Exception:  # never let the loop die
            log.exception("vita sync failed")
        await asyncio.sleep(config.VITA_SYNC_INTERVAL)
