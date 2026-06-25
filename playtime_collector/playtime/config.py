"""Configuration.

Reads Home Assistant add-on options (/data/options.json) if present, otherwise
environment variables, otherwise defaults. The same image therefore runs as a
HAOS add-on and as a plain script (Docker or local).
"""
import json
import os
from pathlib import Path

OPTIONS_FILE = Path("/data/options.json")


def load_options():
    if OPTIONS_FILE.exists():
        try:
            return json.loads(OPTIONS_FILE.read_text())
        except (ValueError, OSError):
            return {}
    return {}


options = load_options()


def get(key, env, default):
    value = options.get(key)
    if value not in (None, ""):
        return value
    return os.environ.get(env, default)


# Platform identity stamped on rows written by this collector's built-in PS3
# poller (sessions/trophies). It is NOT a global read filter: the API derives a
# row's platform from the data itself, and push-based platforms supply their own
# `platform` via POST /ingest. Configurable so the same image can front another
# console, but it defaults to "ps3" to preserve existing behaviour.
PLATFORM = get("platform", "PLATFORM", "ps3")

# IP of the PS3 running webMAN MOD. No default: set it per install.
PS3_HOST = get("ps3_host", "PS3_HOST", "")

# How often to poll the console, in seconds.
POLL_INTERVAL = int(get("poll_interval", "POLL_INTERVAL", 30))

# How often to refresh trophies, in seconds (changes slowly; keep it gentle).
TROPHY_INTERVAL = int(get("trophy_interval", "TROPHY_INTERVAL", 1800))

# Fallback player label when the active PS3 profile can't be resolved.
ACCOUNT = get("account", "ACCOUNT", "ps3")

# Profiles to NOT track (e.g. a technical account). List, or comma-separated string.
_ignore_raw = get("ignore_accounts", "IGNORE_ACCOUNTS", "Vlad")
if isinstance(_ignore_raw, list):
    IGNORE_ACCOUNTS = [str(a).strip() for a in _ignore_raw if str(a).strip()]
else:
    IGNORE_ACCOUNTS = [a.strip() for a in str(_ignore_raw).split(",") if a.strip()]

# Shared token required in the X-Auth-Token header to read the API.
# Empty = no auth (fine on a trusted LAN; set one before exposing publicly).
AUTH_TOKEN = get("auth_token", "AUTH_TOKEN", "")

HTTP_PORT = int(get("http_port", "HTTP_PORT", 3301))

# /data is provided by HAOS add-ons; otherwise store next to the code.
data_dir = Path("/data") if Path("/data").is_dir() else Path(__file__).resolve().parent.parent / "data"

# The /data volume is keyed by the add-on *slug*, so reinstalling — or switching
# between a local and a GitHub-repository install — would start from an empty
# database. To keep playtime history across that, store under the shared,
# slug-independent /share/playtime when it is mapped (map: share:rw), migrating an
# existing /data database over exactly once (see migrate_to_share, run at startup).
share_dir = Path("/share/playtime")
_use_share = Path("/share").is_dir()
store_dir = share_dir if _use_share else data_dir

DB_PATH = get("db_path", "DB_PATH", str(store_dir / "playtime.db"))
ICON_DIR = get("icon_dir", "ICON_DIR", str(store_dir / "icons"))


def migrate_to_share():
    """One-time copy of a private /data store into shared /share/playtime so the
    history survives reinstalling / changing the add-on install type. No-op when
    /share isn't mapped, or the shared DB already exists (already migrated)."""
    if not _use_share:
        return
    import shutil
    share_dir.mkdir(parents=True, exist_ok=True)
    src_db, dst_db = data_dir / "playtime.db", share_dir / "playtime.db"
    if src_db.exists() and not dst_db.exists():
        shutil.copy2(src_db, dst_db)
        src_icons, dst_icons = data_dir / "icons", share_dir / "icons"
        if src_icons.is_dir() and not dst_icons.exists():
            shutil.copytree(src_icons, dst_icons)

# Optional PSN NPSSO token to enrich trophies with global rarity (% of players).
# Rarity is a PSN-server stat, not on the console; leave empty to disable.
PSN_NPSSO = get("psn_npsso", "PSN_NPSSO", "")
# How often to refresh PSN rarity, in seconds (changes slowly).
RARITY_INTERVAL = int(get("rarity_interval", "RARITY_INTERVAL", 86400))

# "Donor" PSN accounts to fetch trophy rarity through. The configured PSN_NPSSO
# account rarely owns the games on a jailbroken console, so PSN returns no earn
# rates for them. High-completion trophy hunters (from psnprofiles leaderboards)
# collectively own ~every game, so we query rarity through them. Each entry is a
# PSN online-id (username, e.g. "ginko765") or a numeric account-id. List, or
# comma-separated string. Empty = keep the legacy me()-only behaviour.
_rarity_accounts_raw = get("rarity_accounts", "RARITY_ACCOUNTS", [])
if isinstance(_rarity_accounts_raw, list):
    RARITY_ACCOUNTS = [str(a).strip() for a in _rarity_accounts_raw if str(a).strip()]
else:
    RARITY_ACCOUNTS = [a.strip() for a in str(_rarity_accounts_raw).split(",") if a.strip()]

# How often to log a "last 24h" activity summary, in seconds.
SUMMARY_INTERVAL = int(get("summary_interval", "SUMMARY_INTERVAL", 86400))

# How often to pull the on-console plugin log (sessions.jsonl / current.json), in
# seconds. The PS3 playtime plugin writes these; when present it is the source of
# truth for sessions (the LAN poller then only reports, to avoid double counting).
PLUGIN_SYNC_INTERVAL = int(get("plugin_sync_interval", "PLUGIN_SYNC_INTERVAL", 60))

# Where playtime comes from. Each install can pick whichever it has:
#   "auto"   - use the on-console plugin when its log is present, else LAN polling
#   "webman" - LAN polling of cpursx only (no on-console plugin needed)
#   "plugin" - only the on-console plugin log (sessions.jsonl / current.json)
# Trophy and rarity collection run regardless of this setting.
PLAYTIME_SOURCE = str(get("playtime_source", "PLAYTIME_SOURCE", "auto")).strip().lower()
if PLAYTIME_SOURCE not in ("auto", "webman", "plugin"):
    PLAYTIME_SOURCE = "auto"

# Game-title overrides, maintained by the user in the add-on options. Each entry is
# "<match>=<replacement>"; <match> is a title id (BCES01585) or an exact title string
# ("KILLZONE®"). Used to drop trademark glyphs / promo tags the games bake into their
# own metadata (the XMB hides these too). See titles.fix_title.
_overrides_raw = get("title_overrides", "TITLE_OVERRIDES", [])
if isinstance(_overrides_raw, str):
    _overrides_raw = _overrides_raw.split(";")
TITLE_OVERRIDES = {}
for _item in _overrides_raw or []:
    _text = str(_item)
    if "=" in _text:
        _match, _replacement = _text.split("=", 1)
        _match = _match.strip()
        if _match:
            TITLE_OVERRIDES[_match] = _replacement.strip()


# --- PS Vita (pull over FTP) -------------------------------------------------
# The Vita can't reliably push, so HA pulls its session queue over FTP, the same
# way it polls the PS3. Needs an always-on FTP server on the Vita (the
# `ftpeverywhere` plugin autostarts one on port 1337) plus the on-Vita kernel
# plugin writing ux0:data/VitaPlaytime/pending.jsonl. Empty host = poller off.
VITA_HOST = get("vita_host", "VITA_HOST", "")
VITA_PORT = int(get("vita_port", "VITA_PORT", 1337))
# Player label stamped on Vita sessions (the Vita is single-user). Set it to a
# PS3 account name to merge both consoles under one person via the People mapping.
VITA_ACCOUNT = get("vita_account", "VITA_ACCOUNT", "vita")
# How often to pull the Vita queue over FTP, in seconds.
VITA_SYNC_INTERVAL = int(get("vita_sync_interval", "VITA_SYNC_INTERVAL", 60))
# Vita titleIds to never record (homebrew / system apps the kernel can't filter,
# e.g. VitaShell). List, or comma-separated string. NPXS* are always skipped.
_vita_ignore_raw = get("vita_ignore_titles", "VITA_IGNORE_TITLES", "VITASHELL")
if isinstance(_vita_ignore_raw, list):
    VITA_IGNORE_TITLES = [str(t).strip() for t in _vita_ignore_raw if str(t).strip()]
else:
    VITA_IGNORE_TITLES = [t.strip() for t in str(_vita_ignore_raw).split(",") if t.strip()]
