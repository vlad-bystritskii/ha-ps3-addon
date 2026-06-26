"""PS Vita trophy reader (pull over FTP).

The Vita's trophy UNLOCK STATE is sealed per-console (F00D/sealedkey), so it
can't be read off the memory card directly. An on-console homebrew ("Vita
Trophy Dump": the trophydump VPK + trophymount kernel plugin) mounts the
encrypted trophy data folder so the OS decrypts it, parses TRPTITLE.DAT, and
writes the decrypted unlock state to ux0:/data/VitaPlaytime/trophies.json, one
set per line:

    {"npcommid": "NPWR03895_00", "count": 50,
     "unlocked": [{"id": 1, "t": 1772526625}, ...]}    # t = unix seconds

The trophy DEFINITIONS (title, names, grades, hidden) are NOT encrypted: they
live in ur0:/user/00/trophy/conf/<npcommid>/TROP.SFM as plain XML, the SAME
schema as PS3's TROPCONF.SFM, so we reuse trophies.parse_tropconf /
summary_dict / detail_items unchanged. Both files are pulled over the Vita's
FTP server (ftpeverywhere autostart, or VitaShell's SELECT FTP) and stored
under platform "psvita", account = VITA_ACCOUNT — mirroring the PS3 path so the
dashboard renders Vita trophies identically and they attach to a game by the
same platform+title match.
"""
import asyncio
import ftplib
import io
import json
import logging
from datetime import datetime, timezone

from . import config, db
from .trophies import detail_items, parse_tropconf, summary_dict

log = logging.getLogger("playtime.vita_trophy")

TROPHIES_JSON = "ux0:/data/VitaPlaytime/trophies.json"
CONF_DIR = "ur0:/user/00/trophy/conf"


def _connect():
    ftp = ftplib.FTP()
    ftp.connect(config.VITA_HOST, config.VITA_PORT, timeout=10)
    ftp.login()  # ftpeverywhere / VitaShell FTP are anonymous
    return ftp


def _retr(ftp, path):
    buf = io.BytesIO()
    ftp.retrbinary("RETR " + path, buf.write)
    return buf.getvalue()


def _state_from_dump(unlocked):
    """Turn trophies.json's `unlocked` list into the {trophy_id: {unlocked,
    earned_at}} state dict that the shared PS3 summary/detail helpers expect.
    Trophies absent from the list are locked (state.get returns nothing)."""
    state = {}
    for u in unlocked:
        try:
            tid = int(u["id"])
        except (KeyError, ValueError, TypeError):
            continue
        ts = u.get("t")
        earned_at = (datetime.fromtimestamp(ts, timezone.utc).isoformat()
                     if ts else None)
        state[tid] = {"unlocked": True, "earned_at": earned_at}
    return state


def _sync_once():
    """Pull trophies.json + each set's TROP.SFM and upsert into the DB.

    Returns (reachable, sets_ingested). Idempotent: upserts mean re-running on
    an unchanged dump is a no-op, and a fresh dump (new bubble run) overwrites.
    """
    try:
        ftp = _connect()
    except ftplib.all_errors:
        return (False, 0)
    account = config.VITA_ACCOUNT
    ingested = 0
    try:
        try:
            raw = _retr(ftp, TROPHIES_JSON)
        except ftplib.all_errors:
            log.info("vita: no trophies.json yet — run the Trophy Dump bubble")
            return (True, 0)
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            npcommid = rec.get("npcommid")
            if not npcommid:
                continue
            # Trophy definitions: plaintext XML, identical schema to PS3.
            try:
                conf = _retr(ftp, CONF_DIR + "/" + npcommid + "/TROP.SFM")
            except ftplib.all_errors:
                log.warning("vita trophy: TROP.SFM missing for %s", npcommid)
                continue
            _, title, defs = parse_tropconf(conf)
            if not defs:
                continue
            state = _state_from_dump(rec.get("unlocked", []))
            summary = summary_dict(npcommid, title, defs, state)
            items = detail_items(defs, state)
            db.upsert_trophies("psvita", account, summary)
            db.upsert_trophy_items("psvita", account, npcommid, items)
            ingested += 1
        return (True, ingested)
    finally:
        try:
            ftp.quit()
        except ftplib.all_errors:
            pass


async def vita_trophy_loop():
    log.info("vita trophy sync every %ss (ftp %s:%s, account %s)",
             config.VITA_TROPHY_INTERVAL, config.VITA_HOST, config.VITA_PORT,
             config.VITA_ACCOUNT)
    while True:
        try:
            reachable, ingested = await asyncio.to_thread(_sync_once)
            if reachable:
                db.set_meta("vita_trophies_refreshed_at", db.now_iso())
            if ingested:
                log.info("ingested %d vita trophy set(s)", ingested)
        except Exception:  # never let the loop die
            log.exception("vita trophy sync failed")
        await asyncio.sleep(config.VITA_TROPHY_INTERVAL)
