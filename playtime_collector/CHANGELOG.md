# Changelog

## 0.9.11

- **Cleaner icons everywhere** — game covers and trophy set covers now show the
  whole artwork, bare: no surrounding box, border, background tint or blurred
  backdrop, and never cropped. The header shows the real add-on logo instead of a
  placeholder diamond. Trophy icons render as the bare trophy art, falling back to
  a clean rarity-colored diamond when there's no image.
- **Distinct PS Vita badge** — PS Vita now has its own handheld glyph instead of
  sharing the PlayStation mark with PS3.
- **Real PS Vita per-trophy icons** — the on-console dumper now copies each
  trophy's decrypted icon out of the mounted trophy folder, and the add-on pulls
  them over FTP, so individual Vita trophies show their real art (not just the set
  cover). Requires the updated Vita Trophy Dump homebrew.

## 0.9.10

- **Trophy unlocks from all platforms in History** — the activity feed was
  hardcoded to PS3, so Vita trophy unlocks only appeared in the Trophies view.
  History now merges trophy unlocks from every platform (each with its own
  platform badge), alongside sessions.

## 0.9.9

- **Trophies view** — a new "Trophies" page (header nav) shows every trophy set as
  its own card with the set cover (ICON0), platform badge, progress bar (earned /
  total) and a per-grade breakdown, **independent of playtime** — so trophy-rich
  games you haven't logged sessions for (e.g. a Vita game played before tracking)
  finally show up. Click a set to see its full trophy list (locked + unlocked).
  Filter by platform. The Vita poller now also caches each set's ICON0, served by
  the new `/trophy-set-icon/{account}/{npcommid}` route.

## 0.9.8

- **PS Vita trophies** — a new FTP poller (`vita_trophy.py`) pulls the Vita's
  decrypted trophy unlock state (`ux0:data/VitaPlaytime/trophies.json`, produced
  on-console by the Vita Trophy Dump homebrew) plus each set's plaintext
  `TROP.SFM` definitions, and stores them under platform `psvita` so they render
  on the dashboard exactly like PS3 trophies. New `vita_trophy_interval` option
  (default 1800s). Trophy set definitions share the PS3 SFM parser.

## 0.9.7

- **`platform` field** added to the API contract on every Game and Trophy object
  (`ps3 | psvita | 3ds`), so clients can tell platforms apart.

## 0.9.0

- **In-app Settings page** (`/config`) — view and edit the add-on options (host,
  source, intervals, tokens, title overrides) from the web UI instead of
  hand-editing YAML.
- **People & account links** (`/people`) — create persons and link/unlink each
  console account to them, so playtime can be merged across platforms.
- **Platform-aware dashboard** — the hardcoded "PS3 PLAYTIME" wording is gone; the
  dashboard now shows a platform badge (PS3 / Vita / …) on each game and session,
  and a top nav between Dashboard / People / Settings.

## 0.8.1

- Two more default title overrides: `GTA IV` → `Grand Theft Auto IV` and
  `Wolverine Trophies` → `X-Men Origins: Wolverine` (for naming consistency).

## 0.8.0

- **Title overrides** — a new `title_overrides` option to clean up game names that
  ship with trademark glyphs or promo tags in their own metadata (e.g.
  `KILLZONE®` → `KILLZONE`, `Dante's Inferno™` → `Dante's Inferno`), the same way
  the PS3 XMB hides them. Each entry is `"<match>=<replacement>"`, where `<match>`
  is a title id (e.g. `BCES01585`) or an exact title string. Applied to new
  sessions, trophy sets and the live "now playing", and **retroactively to titles
  already stored** on start. Ships with sensible defaults you can edit.

## 0.7.2

- Add-on **icon and logo** for the Home Assistant store and add-on page.
- Add this **changelog** (shown in the add-on UI).
- Marked `stage: stable`; tidied store metadata.

## 0.7.1

- **Built-in web dashboard** at `/` — open it with one click from the add-on page
  (**Open Web UI**), no URL to remember.
- **Real PS3 profile avatars** for top players — resolved from the console registry
  and firmware gallery, so they work even for **locally-set avatars with no PSN login**.
  Cached to disk so faces stay visible while the console is off.
- **Real game icons** — the game's own `ICON0.PNG` from the console, with
  [GameTDB](https://www.gametdb.com) cover art as a fallback. Also cached.
- **Trophy activity feed** — recent unlocks with the actual trophy icons, rarity and grade.
- Interactive **player/game detail modals**, **now playing**, and a **by-day** chart.
- New open image routes: `GET /avatar/{account}`, `GET /game-icon/{titleId}`.
- Fix: avatar `<img>` URL no longer carries a stray `.png` (every lookup used to miss).

## 0.5.0

- First server-rendered web dashboard at `/` (playtime bars, now-playing, trophies).

## 0.4.0

- Dual playtime source (`auto` / `webman` / `plugin`) and on-console plugin ingest
  (`sessions.jsonl` / `current.json` over webMAN; plugin is the source of truth).

## 0.3.0

- Trophies read straight off the console (works for offline profiles); icons cached.
- Per-profile attribution via `localusername`; `ignore_accounts` option.
- Optional global PSN rarity via `psn_npsso`.

## 0.1.0

- Initial release: LAN polling of webMAN `cpursx.ps3`, playtime sessions, JSON API.
