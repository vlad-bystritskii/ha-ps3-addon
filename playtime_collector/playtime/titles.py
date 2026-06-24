"""Hand-curated game-title overrides.

Some PS3 games bake trademark glyphs / promo tags into their own metadata
(PARAM.SFO TITLE, TROPCONF <title-name>). The XMB hides these at render time;
we mirror that with an explicit map the user maintains in the add-on options
(`title_overrides`) — no fuzzy stripping, so nothing legitimate is ever mangled.

Each override is `"<match>=<replacement>"`, where `<match>` is either a title id
(e.g. BCES01585) or an exact title string (e.g. "KILLZONE®"). Title-id matches
win over title-string matches.
"""
from . import config


def fix_title(title, title_id=None):
    """Return the curated name for this title, or the title unchanged."""
    overrides = config.TITLE_OVERRIDES
    if not overrides:
        return title
    if title_id and title_id in overrides:
        return overrides[title_id]
    if title is not None and title in overrides:
        return overrides[title]
    return title
