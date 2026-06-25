"""SQLite storage.

The schema is multi-platform and multi-account from the start, so the same
store can later hold push-based platforms next to PS3. Playtime is kept as
rows in `sessions`; totals are computed on read. At most one session per
(platform, account) is open at a time.
"""
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from . import config

lock = threading.Lock()
conn = None


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def init_db():
    global conn
    Path(config.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            platform    TEXT NOT NULL,
            account     TEXT NOT NULL,
            title_id    TEXT NOT NULL,
            title       TEXT,
            started_at  TEXT NOT NULL,
            ended_at    TEXT NOT NULL,
            seconds     INTEGER NOT NULL DEFAULT 0,
            is_open     INTEGER NOT NULL DEFAULT 1
        );
        CREATE INDEX IF NOT EXISTS idx_open ON sessions (platform, account, is_open);
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS trophies (
            platform        TEXT NOT NULL,
            account         TEXT NOT NULL,
            npcommid        TEXT NOT NULL,
            title           TEXT,
            earned_json     TEXT NOT NULL,
            total_json      TEXT NOT NULL,
            earned_count    INTEGER NOT NULL,
            total_count     INTEGER NOT NULL,
            last_earned_at  TEXT,
            updated_at      TEXT NOT NULL,
            PRIMARY KEY (platform, account, npcommid)
        );
        CREATE TABLE IF NOT EXISTS trophy_items (
            platform   TEXT NOT NULL,
            account    TEXT NOT NULL,
            npcommid   TEXT NOT NULL,
            trophy_id  INTEGER NOT NULL,
            name       TEXT,
            detail     TEXT,
            grade      TEXT NOT NULL,
            hidden     INTEGER NOT NULL,
            unlocked   INTEGER NOT NULL,
            earned_at  TEXT,
            PRIMARY KEY (platform, account, npcommid, trophy_id)
        );
        CREATE TABLE IF NOT EXISTS trophy_rarity (
            npcommid    TEXT NOT NULL,
            trophy_id   INTEGER NOT NULL,
            earned_rate REAL,
            rare        TEXT,
            updated_at  TEXT NOT NULL,
            PRIMARY KEY (npcommid, trophy_id)
        );
        CREATE TABLE IF NOT EXISTS persons (
            id   INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS account_links (
            person_id INTEGER NOT NULL,
            platform  TEXT NOT NULL,
            account   TEXT NOT NULL,
            UNIQUE (platform, account)
        );
        CREATE INDEX IF NOT EXISTS idx_links_person ON account_links (person_id);
        """
    )
    conn.commit()
    set_meta_if_absent("tracked_since", now_iso())


def apply_title_overrides(mapping):
    """Retroactively rename already-stored titles to match the user's overrides.

    `mapping` is {match: replacement}; a match is a title id (rewrites sessions by
    title_id) or an exact title string (rewrites both sessions and trophy sets).
    Idempotent — safe to run on every startup.
    """
    if not mapping:
        return
    with lock:
        for match, replacement in mapping.items():
            conn.execute(
                "UPDATE sessions SET title = ? WHERE title_id = ? AND title IS NOT ?",
                (replacement, match, replacement))
            conn.execute(
                "UPDATE sessions SET title = ? WHERE title = ?", (replacement, match))
            conn.execute(
                "UPDATE trophies SET title = ? WHERE title = ?", (replacement, match))
        conn.commit()


def set_meta(key, value):
    with lock:
        conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        conn.commit()


def set_meta_if_absent(key, value):
    with lock:
        conn.execute("INSERT OR IGNORE INTO meta (key, value) VALUES (?, ?)", (key, value))
        conn.commit()


def get_meta(key):
    with lock:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def get_open_session(platform):
    """The single in-progress session for a platform (only one game runs at a time)."""
    with lock:
        return conn.execute(
            "SELECT * FROM sessions WHERE platform = ? AND is_open = 1 "
            "ORDER BY id DESC LIMIT 1",
            (platform,),
        ).fetchone()


def close_open_sessions(platform):
    with lock:
        conn.execute(
            "UPDATE sessions SET is_open = 0 WHERE platform = ? AND is_open = 1",
            (platform,),
        )
        conn.commit()


def open_session(platform, account, title_id, title, seconds, when):
    with lock:
        conn.execute(
            "UPDATE sessions SET is_open = 0 WHERE platform = ? AND is_open = 1",
            (platform,),
        )
        conn.execute(
            "INSERT INTO sessions "
            "(platform, account, title_id, title, started_at, ended_at, seconds, is_open) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 1)",
            (platform, account, title_id, title, when, when, max(seconds, 0)),
        )
        conn.commit()


def update_open_session(session_id, seconds, title, when):
    with lock:
        conn.execute(
            "UPDATE sessions SET seconds = ?, title = COALESCE(?, title), ended_at = ? "
            "WHERE id = ?",
            (max(seconds, 0), title, when, session_id),
        )
        conn.commit()


def insert_closed_session(platform, account, title_id, title, seconds, when):
    """Used by /ingest for push-based platforms."""
    with lock:
        conn.execute(
            "INSERT INTO sessions "
            "(platform, account, title_id, title, started_at, ended_at, seconds, is_open) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 0)",
            (platform, account, title_id, title, when, when, max(seconds, 0)),
        )
        conn.commit()


def set_live_session(platform, account, title_id, title, seconds, when):
    """Mirror the plugin's current.json as the single open ('currently playing')
    session. DELETE-then-insert (not close): the authoritative closed session
    arrives separately via sessions.jsonl, so this live row must never persist."""
    with lock:
        conn.execute("DELETE FROM sessions WHERE platform = ? AND is_open = 1", (platform,))
        conn.execute(
            "INSERT INTO sessions "
            "(platform, account, title_id, title, started_at, ended_at, seconds, is_open) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 1)",
            (platform, account, title_id, title, when, when, max(seconds, 0)),
        )
        conn.commit()


def clear_live_session(platform):
    with lock:
        conn.execute("DELETE FROM sessions WHERE platform = ? AND is_open = 1", (platform,))
        conn.commit()


def account_clause(accounts):
    """Build a SQL condition restricting rows to a list of (platform, account)
    pairs (used to aggregate a person across platforms/accounts).

    Returns (condition_or_None, params). `accounts=None` means "no restriction";
    an empty list means "match nothing" (a person with no linked accounts).
    """
    if accounts is None:
        return None, []
    if not accounts:
        return "0", []
    ors = " OR ".join(["(platform = ? AND account = ?)"] * len(accounts))
    params = []
    for plat, acct in accounts:
        params += [plat, acct]
    return "(" + ors + ")", params


def time_filter(platform, frm, to, accounts=None):
    """Build a WHERE clause filtering by platform, account set and start time.

    `frm`/`to` are ISO timestamps or dates (YYYY-MM-DD). Because timestamps are
    stored as UTC ISO strings, lexicographic comparison gives the right window:
    `started_at >= frm AND started_at < to` (to is exclusive). `accounts`, when
    given, restricts to a list of (platform, account) pairs.
    """
    conditions = []
    params = []
    if platform:
        conditions.append("platform = ?")
        params.append(platform)
    acc_cond, acc_params = account_clause(accounts)
    if acc_cond:
        conditions.append(acc_cond)
        params += acc_params
    if frm:
        conditions.append("started_at >= ?")
        params.append(frm)
    if to:
        conditions.append("started_at < ?")
        params.append(to)
    clause = ("WHERE " + " AND ".join(conditions) + " ") if conditions else ""
    return clause, params


def totals(platform=None, frm=None, to=None, accounts=None):
    clause, params = time_filter(platform, frm, to, accounts)
    sql = (
        "SELECT platform, account, title_id, "
        "MAX(title) AS title, "
        "SUM(seconds) AS total_seconds, "
        "COUNT(*) AS sessions, "
        "MIN(started_at) AS first_played, "
        "MAX(ended_at) AS last_played "
        "FROM sessions " + clause +
        "GROUP BY platform, account, title_id ORDER BY total_seconds DESC"
    )
    with lock:
        rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def summary(platform=None, frm=None, to=None, accounts=None):
    clause, params = time_filter(platform, frm, to, accounts)
    # "playing" reflects now: same platform/account scope, but no time window.
    pconds = ["is_open = 1"]
    pparams = []
    if platform:
        pconds.append("platform = ?")
        pparams.append(platform)
    acc_cond, acc_params = account_clause(accounts)
    if acc_cond:
        pconds.append(acc_cond)
        pparams += acc_params
    pwhere = "WHERE " + " AND ".join(pconds)
    with lock:
        row = conn.execute(
            "SELECT COALESCE(SUM(seconds), 0) AS seconds, COUNT(*) AS sessions "
            "FROM sessions " + clause,
            params,
        ).fetchone()
        playing = conn.execute(
            "SELECT COUNT(*) AS playing FROM sessions " + pwhere,
            pparams,
        ).fetchone()
    return {
        "seconds_total": row["seconds"],
        "sessions_total": row["sessions"],
        "playing_count": playing["playing"],
    }


def open_sessions(platform=None, accounts=None):
    """Sessions currently in progress (independent of any time range)."""
    conditions = ["is_open = 1"]
    params = []
    if platform:
        conditions.append("platform = ?")
        params.append(platform)
    acc_cond, acc_params = account_clause(accounts)
    if acc_cond:
        conditions.append(acc_cond)
        params += acc_params
    where = "WHERE " + " AND ".join(conditions)
    with lock:
        rows = conn.execute(
            "SELECT platform, account, title_id, title, "
            "seconds AS total_seconds, 1 AS sessions, "
            "started_at AS first_played, ended_at AS last_played "
            "FROM sessions " + where,
            params,
        ).fetchall()
    return [dict(row) for row in rows]


# ---- persons / account links ------------------------------------------------
# A "person" groups one or more platform accounts so playtime can be aggregated
# across consoles. account_links has a UNIQUE (platform, account): an account
# belongs to at most one person.

def add_person(name):
    with lock:
        cur = conn.execute("INSERT INTO persons (name) VALUES (?)", (name,))
        conn.commit()
        return cur.lastrowid


def get_person(person_id):
    with lock:
        row = conn.execute(
            "SELECT id, name FROM persons WHERE id = ?", (person_id,)
        ).fetchone()
    return dict(row) if row else None


def list_persons():
    """All persons, each with its linked accounts embedded."""
    with lock:
        prows = conn.execute("SELECT id, name FROM persons ORDER BY id").fetchall()
        lrows = conn.execute(
            "SELECT person_id, platform, account FROM account_links "
            "ORDER BY platform, account"
        ).fetchall()
    links = {}
    for r in lrows:
        links.setdefault(r["person_id"], []).append(
            {"platform": r["platform"], "account": r["account"]})
    return [
        {"id": r["id"], "name": r["name"], "links": links.get(r["id"], [])}
        for r in prows
    ]


def delete_person(person_id):
    """Remove a person and any account links pointing at them."""
    with lock:
        conn.execute("DELETE FROM account_links WHERE person_id = ?", (person_id,))
        cur = conn.execute("DELETE FROM persons WHERE id = ?", (person_id,))
        conn.commit()
        return cur.rowcount


def add_link(person_id, platform, account):
    """Link an account to a person. Returns False if (platform, account) is
    already linked (the UNIQUE constraint), True on success."""
    with lock:
        try:
            conn.execute(
                "INSERT INTO account_links (person_id, platform, account) "
                "VALUES (?, ?, ?)",
                (person_id, platform, account),
            )
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False


def list_links(person_id=None):
    where = "WHERE person_id = ? " if person_id is not None else ""
    params = (person_id,) if person_id is not None else ()
    with lock:
        rows = conn.execute(
            "SELECT person_id, platform, account FROM account_links " + where +
            "ORDER BY person_id, platform, account",
            params,
        ).fetchall()
    return [dict(r) for r in rows]


def delete_link(platform, account):
    with lock:
        cur = conn.execute(
            "DELETE FROM account_links WHERE platform = ? AND account = ?",
            (platform, account),
        )
        conn.commit()
        return cur.rowcount


def accounts_for_person(person_id):
    """[(platform, account)] linked to a person; [] if none."""
    with lock:
        rows = conn.execute(
            "SELECT platform, account FROM account_links WHERE person_id = ?",
            (person_id,),
        ).fetchall()
    return [(r["platform"], r["account"]) for r in rows]


def upsert_trophies(platform, account, summary):
    import json
    with lock:
        conn.execute(
            "INSERT INTO trophies (platform, account, npcommid, title, earned_json, "
            "total_json, earned_count, total_count, last_earned_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(platform, account, npcommid) DO UPDATE SET "
            "title=excluded.title, earned_json=excluded.earned_json, "
            "total_json=excluded.total_json, earned_count=excluded.earned_count, "
            "total_count=excluded.total_count, last_earned_at=excluded.last_earned_at, "
            "updated_at=excluded.updated_at",
            (
                platform, account, summary["npcommid"], summary["title"],
                json.dumps(summary["earned"]), json.dumps(summary["total"]),
                summary["earnedCount"], summary["totalCount"],
                summary["lastEarnedAt"], now_iso(),
            ),
        )
        conn.commit()


def query_trophies(platform=None, account=None):
    import json
    where = []
    params = []
    if platform:
        where.append("platform = ?")
        params.append(platform)
    if account:
        where.append("account = ?")
        params.append(account)
    clause = ("WHERE " + " AND ".join(where) + " ") if where else ""
    with lock:
        rows = conn.execute(
            "SELECT * FROM trophies " + clause + "ORDER BY earned_count DESC", params
        ).fetchall()
    result = []
    for row in rows:
        result.append({
            "platform": row["platform"],
            "account": row["account"],
            "npcommid": row["npcommid"],
            "title": row["title"],
            "earned": json.loads(row["earned_json"]),
            "total": json.loads(row["total_json"]),
            "earnedCount": row["earned_count"],
            "totalCount": row["total_count"],
            "lastEarnedAt": row["last_earned_at"],
            "updatedAt": row["updated_at"],
        })
    return result


def upsert_trophy_items(platform, account, npcommid, items):
    with lock:
        conn.execute(
            "DELETE FROM trophy_items WHERE platform=? AND account=? AND npcommid=?",
            (platform, account, npcommid),
        )
        conn.executemany(
            "INSERT INTO trophy_items "
            "(platform, account, npcommid, trophy_id, name, detail, grade, hidden, unlocked, earned_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (platform, account, npcommid, it["id"], it["name"], it["detail"],
                 it["grade"], int(it["hidden"]), int(it["unlocked"]), it["earnedAt"])
                for it in items
            ],
        )
        conn.commit()


def query_trophy_items(platform, account, npcommid):
    """Trophy items for a game. `platform=None` matches any platform (the API is
    platform-aware and may not know it up front)."""
    where = ["account=?", "npcommid=?"]
    params = [account, npcommid]
    if platform:
        where.append("platform=?")
        params.append(platform)
    with lock:
        rows = conn.execute(
            "SELECT * FROM trophy_items WHERE " + " AND ".join(where) +
            " ORDER BY trophy_id",
            params,
        ).fetchall()
    return [
        {
            "id": r["trophy_id"],
            "name": r["name"],
            "detail": r["detail"],
            "grade": r["grade"],
            "hidden": bool(r["hidden"]),
            "unlocked": bool(r["unlocked"]),
            "earnedAt": r["earned_at"],
        }
        for r in rows
    ]


def distinct_npcommids():
    with lock:
        rows = conn.execute("SELECT DISTINCT npcommid FROM trophies").fetchall()
    return [r["npcommid"] for r in rows]


def upsert_rarity(npcommid, rarity_map):
    """rarity_map: {trophy_id: {"earned_rate": float|None, "rare": str|None}} (global PSN)."""
    with lock:
        for trophy_id, info in rarity_map.items():
            conn.execute(
                "INSERT INTO trophy_rarity (npcommid, trophy_id, earned_rate, rare, updated_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(npcommid, trophy_id) DO UPDATE SET "
                "earned_rate=excluded.earned_rate, rare=excluded.rare, updated_at=excluded.updated_at",
                (npcommid, int(trophy_id), info.get("earned_rate"), info.get("rare"), now_iso()),
            )
        conn.commit()


def get_rarity(npcommid):
    with lock:
        rows = conn.execute(
            "SELECT trophy_id, earned_rate, rare FROM trophy_rarity WHERE npcommid = ?",
            (npcommid,),
        ).fetchall()
    return {r["trophy_id"]: {"earned_rate": r["earned_rate"], "rare": r["rare"]} for r in rows}


def trophies_earned_since(since_iso):
    """[(account, count)] of trophies unlocked at/after since_iso (for summaries)."""
    with lock:
        rows = conn.execute(
            "SELECT account, COUNT(*) AS c FROM trophy_items "
            "WHERE unlocked = 1 AND earned_at >= ? GROUP BY account ORDER BY c DESC",
            (since_iso,),
        ).fetchall()
    return [(r["account"], r["c"]) for r in rows]


def recent_trophy_unlocks(platform, limit=50):
    """Most recently unlocked trophies across all accounts, with game title +
    global rarity, for the activity feed."""
    with lock:
        rows = conn.execute(
            "SELECT ti.account, ti.npcommid, ti.trophy_id, ti.name, ti.detail, "
            "ti.grade, ti.earned_at, tr.title AS game, ra.earned_rate "
            "FROM trophy_items ti "
            "LEFT JOIN trophies tr ON tr.platform=ti.platform AND tr.account=ti.account "
            "AND tr.npcommid=ti.npcommid "
            "LEFT JOIN trophy_rarity ra ON ra.npcommid=ti.npcommid AND ra.trophy_id=ti.trophy_id "
            "WHERE ti.unlocked=1 AND ti.earned_at IS NOT NULL AND ti.platform=? "
            "ORDER BY ti.earned_at DESC LIMIT ?",
            (platform, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def delete_sessions(account):
    with lock:
        cur = conn.execute("DELETE FROM sessions WHERE account = ?", (account,))
        conn.commit()
        return cur.rowcount


def list_sessions(platform=None, frm=None, to=None, limit=500):
    """Raw session rows for arbitrary downstream aggregation."""
    clause, params = time_filter(platform, frm, to)
    with lock:
        rows = conn.execute(
            "SELECT platform, account, title_id, title, started_at, ended_at, "
            "seconds, is_open FROM sessions " + clause +
            "ORDER BY started_at DESC LIMIT ?",
            params + [limit],
        ).fetchall()
    return [dict(row) for row in rows]


# ---- dashboard feeds: chart / distribution / per-game / history -------------
# Helpers for the new dashboard. All are platform-agnostic and accept the same
# (platform, frm, to, accounts) scoping the rest of the read API uses, so they
# work for a single platform, all platforms, or one person across consoles.

def session_times(platform=None, frm=None, to=None, accounts=None):
    """Bare (started_at, seconds) rows in scope, for time-bucketing into a chart
    series in the API layer (where local-time bucket boundaries are computed)."""
    clause, params = time_filter(platform, frm, to, accounts)
    with lock:
        rows = conn.execute(
            "SELECT started_at, seconds FROM sessions " + clause, params
        ).fetchall()
    return [dict(row) for row in rows]


def platform_totals(platform=None, frm=None, to=None, accounts=None):
    """Total seconds + session count per platform over the (optionally person-
    filtered) range, descending. Used for the platform-distribution donut."""
    clause, params = time_filter(platform, frm, to, accounts)
    with lock:
        rows = conn.execute(
            "SELECT platform, COALESCE(SUM(seconds), 0) AS seconds, "
            "COUNT(*) AS sessions FROM sessions " + clause +
            "GROUP BY platform ORDER BY seconds DESC",
            params,
        ).fetchall()
    return [dict(row) for row in rows]


def game_players(platform, title_id):
    """Per-account aggregates for one game (platform + title_id), ranked by time.
    Each distinct account that played the title becomes a 'player' row."""
    with lock:
        rows = conn.execute(
            "SELECT account, MAX(title) AS title, "
            "COALESCE(SUM(seconds), 0) AS total_seconds, COUNT(*) AS sessions, "
            "MIN(started_at) AS first_played, MAX(ended_at) AS last_played "
            "FROM sessions WHERE platform = ? AND title_id = ? "
            "GROUP BY account ORDER BY total_seconds DESC",
            (platform, title_id),
        ).fetchall()
    return [dict(row) for row in rows]


def account_person_map():
    """{(platform, account): {"id", "name"}} for every linked account, so feeds
    can annotate rows with the owning person in one query."""
    with lock:
        rows = conn.execute(
            "SELECT l.platform, l.account, p.id AS id, p.name AS name "
            "FROM account_links l JOIN persons p ON p.id = l.person_id"
        ).fetchall()
    return {(r["platform"], r["account"]): {"id": r["id"], "name": r["name"]}
            for r in rows}


def session_history(platform=None, accounts=None, limit=200):
    """Most recent sessions in scope (no time window), newest first."""
    clause, params = time_filter(platform, None, None, accounts)
    with lock:
        rows = conn.execute(
            "SELECT platform, account, title_id, title, started_at, ended_at, "
            "seconds, is_open FROM sessions " + clause +
            "ORDER BY started_at DESC LIMIT ?",
            params + [limit],
        ).fetchall()
    return [dict(row) for row in rows]


def trophy_history(platform=None, accounts=None, limit=200):
    """Most recently unlocked trophies in scope, with game title + global rarity,
    newest first. `accounts` restricts to a person's (platform, account) pairs;
    platforms that store no trophies simply contribute nothing."""
    conds = ["ti.unlocked = 1", "ti.earned_at IS NOT NULL"]
    params = []
    if platform:
        conds.append("ti.platform = ?")
        params.append(platform)
    if accounts is not None:
        if not accounts:
            conds.append("0")
        else:
            ors = " OR ".join(["(ti.platform = ? AND ti.account = ?)"] * len(accounts))
            conds.append("(" + ors + ")")
            for plat, acct in accounts:
                params += [plat, acct]
    with lock:
        rows = conn.execute(
            "SELECT ti.platform, ti.account, ti.npcommid, ti.trophy_id, ti.name, "
            "ti.detail, ti.grade, ti.earned_at, tr.title AS game, ra.earned_rate "
            "FROM trophy_items ti "
            "LEFT JOIN trophies tr ON tr.platform=ti.platform AND tr.account=ti.account "
            "AND tr.npcommid=ti.npcommid "
            "LEFT JOIN trophy_rarity ra ON ra.npcommid=ti.npcommid AND ra.trophy_id=ti.trophy_id "
            "WHERE " + " AND ".join(conds) +
            " ORDER BY ti.earned_at DESC LIMIT ?",
            params + [limit],
        ).fetchall()
    return [dict(row) for row in rows]
