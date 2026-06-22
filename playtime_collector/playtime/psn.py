"""PSN trophy rarity via the official PSN API (PSNAWP).

Rarity (the % of players who earned a trophy, and the rare tier) is a global PSN
statistic — it is NOT stored on the console. Given an NPSSO token from any PSN
account we fetch it per npCommunicationId (NPWR…) — the same id the console uses —
including PS3 legacy titles (PlatformType.PS3).

PSNAWP is synchronous (requests-based), so callers should run these in a thread.
Imports are done lazily so the module loads even if psnawp isn't installed.
"""
import logging

log = logging.getLogger("playtime.psn")

_client = None
_client_npsso = None


def _client_for(npsso):
    global _client, _client_npsso
    if _client is None or _client_npsso != npsso:
        from psnawp_api import PSNAWP
        _client = PSNAWP(npsso).me()
        _client_npsso = npsso
    return _client


def fetch_title_rarity(npsso, npcommid):
    """Return {trophy_id: {"earned_rate": float|None, "rare": str|None}} for a PS3 title."""
    from psnawp_api.models.trophies import PlatformType

    client = _client_for(npsso)
    rarity = {}
    for trophy in client.trophies(npcommid, PlatformType.PS3, include_progress=True):
        trophy_id = getattr(trophy, "trophy_id", None)
        if trophy_id is None:
            continue
        rate = getattr(trophy, "trophy_earn_rate", None)
        rare = getattr(trophy, "trophy_rarity", None)
        if rare is not None and hasattr(rare, "name"):
            rare = rare.name  # enum -> readable tier (e.g. ULTRA_RARE)
        rarity[int(trophy_id)] = {
            "earned_rate": float(rate) if rate is not None else None,
            "rare": str(rare) if rare is not None else None,
        }
    return rarity
