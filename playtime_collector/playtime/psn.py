"""PSN trophy rarity via the official PSN API (PSNAWP).

Rarity (the % of players who earned a trophy, and the rare tier) is a global PSN
statistic — it is NOT stored on the console. Given an NPSSO token from any PSN
account we fetch it per npCommunicationId (NPWR…) — the same id the console uses —
including PS3 legacy titles (PlatformType.PS3).

The earn rate / rare tier only comes back together with a *progress* endpoint
(include_progress=True), which is per-account: PSN only returns it for a title
that account actually owns. The configured NPSSO account rarely owns the games on
a jailbroken console, so we can also query through a configurable list of "donor"
PSN accounts (high-completion trophy hunters who collectively own ~every game).
Authenticate once with PSN_NPSSO (any valid account, just for API access), then
per title try each donor until one of them owns it and yields earn rates.

PSNAWP is synchronous (requests-based), so callers should run these in a thread.
Imports are done lazily so the module loads even if psnawp isn't installed.
"""
import logging

log = logging.getLogger("playtime.psn")

# Cached PSNAWP root client (PSNAWP(npsso)) keyed by the npsso it was built with.
_root = None
_root_npsso = None
# Cached "me()" user for the authenticated account (back-compat default).
_me = None
# Cached donor descriptor -> PSNAWP user objects, so we resolve each online_id /
# account_id once rather than for every title. Cleared if the npsso changes.
_donor_cache = {}


def _root_for(npsso):
    """Return a cached PSNAWP root client for this npsso (rebuilt if npsso changes)."""
    global _root, _root_npsso, _me, _donor_cache
    if _root is None or _root_npsso != npsso:
        from psnawp_api import PSNAWP
        _root = PSNAWP(npsso)
        _root_npsso = npsso
        _me = None
        _donor_cache = {}
    return _root


def _me_for(npsso):
    """Return the cached `me()` user for the authenticated account."""
    global _me
    if _me is None:
        _me = _root_for(npsso).me()
    return _me


def _donor_user(npsso, donor):
    """Resolve a donor descriptor to a PSNAWP user object (cached).

    A donor that is all digits is treated as a numeric PSN account_id; otherwise
    it is treated as an online_id (PSN username). Returns None if the descriptor
    can't be resolved to a user (e.g. the online_id doesn't exist).
    """
    key = str(donor).strip()
    if not key:
        return None
    if key in _donor_cache:
        return _donor_cache[key]
    try:
        from psnawp_api.core.psnawp_exceptions import PSNAWPNotFoundError
    except Exception:
        PSNAWPNotFoundError = ()
    root = _root_for(npsso)
    user = None
    try:
        if key.isdigit():
            user = root.user(account_id=key)
        else:
            user = root.user(online_id=key)
    except PSNAWPNotFoundError:
        log.warning("PSN donor %r not found — skipping", key)
        user = None
    except Exception:
        log.exception("PSN donor %r could not be resolved", key)
        user = None
    _donor_cache[key] = user
    return user


def fetch_title_rarity(npsso, npcommid, donor=None):
    """Return {trophy_id: {"earned_rate": float|None, "rare": str|None}} for a PS3 title.

    Empty dict means "no rarity available" — the account we queried through (the
    authenticated `me()` account when donor is None, otherwise the donor) doesn't
    have this title in its trophy list, so PSN has nothing to give us. The caller
    can then try the next donor.

    donor=None preserves the original behaviour (query the authenticated account).
    A donor is an online_id (PSN username) or a numeric account_id; rarity is
    fetched through that user instead.

    Note: PSN only returns the global earn rate / rare tier together with the
    per-account *progress* endpoint (include_progress=True). For a PS3 title the
    queried account doesn't own, that endpoint 404s — hence the empty result. We
    take earned/unearned from the console, so we only want the rates here.
    """
    from psnawp_api.models.trophies import PlatformType
    try:
        from psnawp_api.core.psnawp_exceptions import PSNAWPNotFoundError
    except Exception:
        PSNAWPNotFoundError = ()

    if donor is None:
        user = _me_for(npsso)
    else:
        user = _donor_user(npsso, donor)
        if user is None:
            return {}  # donor couldn't be resolved — nothing to fetch

    rarity = {}
    try:
        for trophy in user.trophies(npcommid, PlatformType.PS3, include_progress=True):
            trophy_id = getattr(trophy, "trophy_id", None)
            if trophy_id is None:
                continue
            rate = getattr(trophy, "trophy_earn_rate", None)
            rare = getattr(trophy, "trophy_rarity", None)
            if rare is not None and hasattr(rare, "name"):
                rare = rare.name  # enum -> readable tier (e.g. ULTRA_RARE)
            if rate is None and rare is None:
                continue  # nothing useful for this trophy; don't store empty rows
            rarity[int(trophy_id)] = {
                "earned_rate": float(rate) if rate is not None else None,
                "rare": str(rare) if rare is not None else None,
            }
    except PSNAWPNotFoundError:
        return {}  # title not in this account's trophy list — skip quietly
    return rarity
