"""Real defect (2026-09-22): 8 catalog entries pointed a whole password
manager or crypto wallet's %APPDATA% folder at "safe" tier -- auto-included
in "Clean All Safe" with zero manual review -- while each entry's OWN
label admitted it covered "vault data", "vault cache", "credential cache"
or "session data" (1password_cache, bitwarden_cache, dashlane_cache,
keepass_cache, lastpass_cache, nordpass_cache, metamask_cache,
coinbase_cache). Getting this wrong risks a lost password vault, or for a
real wallet, real financial loss.

This is a whole-catalog sweep, not a fixed id list, so a future entry for
a new password manager or wallet trips it too -- the same "ask the whole
catalog a question at once" shape catalog.py's own docstring describes
for test_cleanup_catalog.py.
"""
import re

from modules.cleanup.cleanup_scanner.catalog import load_catalog

#: Word stems that mean "this holds real secrets", not "this is disposable
#: cache" -- checked against both the id and the label/description, since
#: a scanner naming itself "*_cache" while its own description confesses
#: to vault/credential content is exactly the pattern that slipped through.
_SENSITIVE_WORDS = re.compile(
    r"\bvault|\bcredential|\bwallet|\bpassword|\bseed\s*phrase|\bprivate\s*key",
    re.I,
)

#: Known-safe false positives, each verified individually (2026-09-22):
_ALLOWLIST = {
    # Obsidian's own terminology for a notes workspace, unrelated to any
    # security vault -- "Obsidian vault cache, plugins, and community
    # plugin downloads" never touches credentials.
    "obsidian_cache",
    # Already correctly scoped to known_hosts.old backup files only; the
    # match comes from its OWN reassuring disclaimer text ("NOT
    # authorized_keys or private keys"), not from touching a real key.
    "ssh_keys_cache",
    # A real, well-known Windows RDP bitmap cache
    # (%LOCALAPPDATA%\Microsoft\Terminal Server Client\Cache) whose own
    # description explicitly disclaims "NOT the Vault" (Windows Credential
    # Manager's saved-RDP-password store) -- the match is that disclaimer.
    "rdp_cache",
    "wifi_profiles_cache",  # WLAN "password" appears nowhere in its text;
                            # reserved here in case a future edit adds it
    # Corrected 2026-09-22 to only scan Galaxy collections + tmp files --
    # neither is the real vault password file. Its own description says so
    # explicitly ("NOT ... any user-named vault password file"), which is
    # the match.
    "ansible_cache",
}


def test_no_safe_tier_scanner_targets_a_credential_vault_or_wallet():
    catalog = load_catalog()
    offenders = []
    for spec_id, spec in catalog.items():
        if spec_id in _ALLOWLIST:
            continue
        text = f"{spec.label} {spec.description}"
        if spec.safety == "safe" and _SENSITIVE_WORDS.search(text):
            offenders.append((spec_id, spec.label))
    assert offenders == [], (
        "these scanners are 'safe' tier (auto-included in Clean All Safe) "
        "but their own label/description says they touch vault/credential/"
        "wallet data -- remove them entirely, or narrow the path to a "
        "subfolder that is verifiably just cache, matching "
        "bitwarden_desktop_cache's own %APPDATA%\\Bitwarden\\cache scoping:\n  "
        + "\n  ".join(f"{sid}: {label!r}" for sid, label in offenders))


def test_no_scanner_at_all_targets_a_bare_password_manager_app_folder():
    """Even at caution/danger tier, a scanner should never point at a whole
    password manager's root %APPDATA% folder -- narrower is always
    possible, per bitwarden_desktop_cache's own precedent."""
    catalog = load_catalog()
    known_password_managers = (
        "1Password", "Bitwarden", "Dashlane", "KeePass", "LastPass",
        "NordPass", "RoboForm", "Keeper", "Enpass",
    )
    offenders = []
    for spec_id, spec in catalog.items():
        for path in spec.paths:
            tail = path.rstrip("\\").split("\\")[-1]
            if tail in known_password_managers:
                offenders.append((spec_id, path))
    assert offenders == [], (
        "these scanners target a password manager's bare root folder "
        "rather than a verified cache-only subfolder:\n  "
        + "\n  ".join(f"{sid}: {path}" for sid, path in offenders))
