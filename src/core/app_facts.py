"""Session-wide installed-app facts. No Qt and no inferred purpose for unknown apps."""
from __future__ import annotations

import logging
import ntpath
from html import escape
from time import monotonic
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from threading import Lock

logger = logging.getLogger(__name__)

REGISTRY_DATE = ("installer-reported date (registry InstallDate; MSI and many installers rewrite it "
                 "on update/repair, so it can be the last servicing)")


def date_value(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(str(value or ""), fmt)
        except ValueError:
            logger.debug('App facts: date %r does not match %s', value, fmt)
            continue
    return None


@dataclass(frozen=True)
class AppKey:
    name: str = ""
    publisher: str = ""
    install_location: str = ""
    display_icon: str = ""
    package_name: str = ""
    family: str = ""
    full_name: str = ""
    winget_id: str = ""
    registry_key: str = ""
    installed: datetime | None = None
    installed_source: str = ""

    @classmethod
    def from_entry(cls, entry):
        def get(*names):
            for name in names:
                value = entry.get(name) if isinstance(entry, dict) else getattr(entry, name, None)
                if value:
                    return value
            return ""
        registry_key = get('registry_key')
        kind = get('type_')
        if registry_key and kind and not registry_key.upper().startswith(('HKLM\\', 'HKCU\\')):
            registry_key = ('HKCU' if kind == 'User' else 'HKLM') + '\\' + registry_key
        return cls(name=get("name", "display", "display_name", "Name"),
                   publisher=get("publisher", "Publisher"),
                   install_location=get("install_location", "location", "InstallLocation", "root"),
                   display_icon=get("display_icon", "icon_path"),
                   package_name=get("package_name", "Name"), family=get("family", "PackageFamilyName"),
                   full_name=get("full_name", "PackageFullName"), winget_id=get("winget_id", "app_id"),
                   registry_key=registry_key, installed=date_value(get("installed", "install_date")),
                   installed_source=get("installed_source") or (REGISTRY_DATE if registry_key or get("install_date") else ""))


@dataclass(frozen=True)
class AppFacts:
    what: str
    does: str
    installed: datetime | None = None
    installed_source: str = "not recorded by the installer"
    last_used: datetime | None = None
    last_used_source: str = "Unknown -- no matching usage evidence found"
    verdict: str = "Unknown"
    breaks: str = "Removal consequences are not known."
    confidence: str = "from the installer only"
    collected: datetime | None = None


def _norm(text):
    return ntpath.normpath(str(text).strip().strip('"')).casefold() if text else ""


def _family(key):
    if key.family:
        return key.family.casefold()
    parts = key.full_name.split("_")
    return (parts[0] + "_" + parts[-1]).casefold() if len(parts) >= 5 else ""


def _protected(key):
    from modules.game_mode.engine.app_usage import protected
    texts = (key.name, key.publisher, key.install_location, key.package_name, key.family,
             key.full_name, key.winget_id, key.registry_key, key.display_icon)
    if protected(*texts) or any(word in " | ".join(texts).casefold()
                               for word in ("sysmon", "amd3dvcache", "msteams", "microsoftteams")):
        # Metadata can justify caution, but cannot authenticate an installed product.
        from modules.app_buster.engine.windows_apps import KNOWN_PUBLISHER_IDS
        family = _family(key)
        verified_package = bool(family and family.rsplit('_', 1)[-1] in KNOWN_PUBLISHER_IDS
                                and protected(family))
        verified_desktop = (key.registry_key.upper().startswith('HKLM\\')
                            and bool(key.publisher) and protected(key.publisher))
        if not (verified_package or verified_desktop):
            return "Keep (name matches a protected app -- not verified)."
        return "Keep: on the protected list (verified package identity or machine publisher)."
    return ""


def _fallback(key):
    from modules.app_buster.engine.autodesk_info import entry
    researched = entry(key.name, key.publisher, key.registry_key)
    if researched is not None:
        verdict, what, does, breaks = researched
        return AppFacts(what, does, key.installed,
                        (key.installed_source or "installer-reported date; source not recorded") if key.installed else "not recorded by the installer",
                        verdict=verdict, breaks=breaks, confidence="researched")
    publisher = f" Publisher: {key.publisher}." if key.publisher else ""
    location = f" Install folder: {key.install_location}." if key.install_location else ""
    reason = _protected(key)
    return AppFacts(f"{key.name or key.package_name}: not in the catalog.{publisher}",
                    "The installer provides no researched description of its purpose." + location,
                    key.installed, (key.installed_source or "installer-reported date; source not recorded") if key.installed else "not recorded by the installer",
                    verdict="Keep" if reason else "Unknown", breaks=reason or "Removal consequences are not known.")


def read_usage():
    from modules.game_mode.engine import app_usage as usage
    backend, evidence, errors = usage.RegistryBackend(), [], []
    reads = (("UserAssist", lambda: usage.userassist_evidence(backend.userassist())),
             ("BAM", lambda: usage.bam_evidence(backend.bam(), backend.devices())),
             ("Prefetch", lambda: usage.prefetch_evidence(backend.prefetch())))
    for label, read in reads:
        try:
            evidence.extend(read())
        except Exception as error:
            logger.warning("App facts: %s could not read: %s", label, error)
            errors.append(f"{label}: {error}")
    return evidence, errors


def _usage(key, evidence, errors):
    from modules.game_mode.engine import app_usage as usage
    family, folder = _family(key), usage._usable_folder(key.install_location)
    exe = usage._icon_exe(key.display_icon)
    found = [e for e in evidence if
             (family and e.key == "family:" + family) or
             (exe and e.key == "path:" + exe) or
             (folder and e.key.startswith("path:" + folder + "\\"))]
    if found:
        best = max(found, key=lambda e: e.when)
        detail = best.source + ("; some sources could not read: " + "; ".join(errors) if errors else "")
        return best.when, detail
    possible = [e for e in evidence if exe and e.key == "exe:" + ntpath.basename(exe)]
    if possible:
        best = max(possible, key=lambda e: e.when)
        detail = (f"possibly used {best.when:%Y-%m-%d} (Prefetch: an executable named "
                  f"{ntpath.basename(exe)} ran -- not proof it was this install)")
        if errors:
            detail += "; could not read: " + "; ".join(errors)
        return None, detail
    if errors:
        return None, "could not read: " + "; ".join(errors)
    if not (family or folder or exe):
        return None, "cannot be matched: the installer records no program path"
    return None, "Unknown -- no matching usage evidence found"


class AppFactsIndex:
    _lock = Lock()
    _build_lock = Lock()
    _generation = 0
    _cached = None
    _failure = None
    _failed_at = float("-inf")

    def __init__(self, records=(), evidence=(), errors=(), problems=()):
        self.collected = datetime.now().astimezone()
        self.records = list(records)
        self.evidence, self.errors, self.problems = list(evidence), list(errors), list(problems)
        self.maps = [{} for _ in range(6)]
        for record in self.records:
            for mapping, token in zip(self.maps, self._tokens(AppKey.from_entry(record))):
                if token:
                    mapping.setdefault(token, []).append(record)

    @classmethod
    def invalidate(cls):
        """Discard successful scans; failure retries remain limited to once per 30 seconds."""
        with cls._lock:
            cls._cached = None
            cls._generation += 1

    @classmethod
    def build(cls):
        """Call on a Worker; simultaneous screens share one scan and evidence read."""
        with cls._build_lock:
            with cls._lock:
                if cls._failure is not None and monotonic() - cls._failed_at < 30:
                    raise RuntimeError(cls._failure)
                if cls._cached is not None:
                    return cls._cached
                generation = cls._generation
            try:
                from modules.app_buster.engine.scan import scan
                result = scan()
                evidence, errors = read_usage()
                index = cls(result.rows, evidence, errors, result.problems)
            except Exception as error:
                logger.warning('App facts catalog could not read: %s', error)
                with cls._lock:
                    cls._failure = str(error)
                    cls._failed_at = monotonic()
                raise
            with cls._lock:
                # A refresh during the scan must not republish that stale scan.
                if generation == cls._generation:
                    cls._cached = index
                cls._failure = None
            return index

    @staticmethod
    def _tokens(key):
        name = key.name.strip().casefold()
        return (_family(key), _norm(key.registry_key), key.winget_id.casefold(),
                _norm(key.install_location), (name, key.publisher.strip().casefold()) if name else "",
                name)

    def _match(self, key):
        # Strong identifiers win before any folder/display-name heuristics.
        for mapping, token in zip(self.maps[:3], self._tokens(key)[:3]):
            matches = mapping.get(token, []) if token else []
            if len(matches) == 1:
                return matches[0]
            exact = [r for r in matches if r.name.casefold() == key.name.casefold()]
            if len(exact) == 1:
                return exact[0]
        packages = [r for r in self.records if key.package_name and
                    r.package_name.casefold() == key.package_name.casefold()]
        if len(packages) == 1:
            return packages[0]

        def compatible(record):
            candidate = AppKey.from_entry(record)
            for field in ('registry_key', 'package_name', 'name', 'publisher'):
                wanted, actual = getattr(key, field), getattr(candidate, field)
                normalize = _norm if field == 'registry_key' else lambda text: text.strip().casefold()
                if wanted and normalize(wanted) != normalize(actual):
                    return False
            return True

        for mapping, token in zip(self.maps[3:], self._tokens(key)[3:]):
            matches = [r for r in mapping.get(token, []) if compatible(r)] if token else []
            if len(matches) == 1:
                return matches[0]
        return None

    def lookup(self, key):
        record = self._match(key)
        facts = _fallback(key) if record is None else _record_facts(record)
        if key.installed is not None:
            facts = replace(facts, installed=key.installed, installed_source=key.installed_source or (facts.installed_source if facts.installed else
                            "installer-reported date; source not recorded"))
        usage_key = key if record is None else AppKey.from_entry(record)
        if record is not None:
            usage_key = replace(usage_key, **{
                field: getattr(usage_key, field) or getattr(key, field)
                for field in ('display_icon', 'install_location', 'family', 'full_name')})
        last, source = _usage(usage_key, self.evidence, self.errors)
        reason = _protected(key) or _protected(usage_key)
        if reason:
            facts = replace(facts, verdict="Cannot remove" if facts.verdict == "Cannot remove" else "Keep", breaks=facts.breaks if facts.breaks.startswith(reason) else reason + " " + facts.breaks)
        if record is None and self.problems:
            facts = replace(facts, what=facts.what + " Catalog could not read: " + "; ".join(self.problems))
        return replace(facts, last_used=last, last_used_source=source, collected=self.collected)


def _record_facts(record):
    from modules.app_buster.engine import app_info, recommend
    from modules.app_buster.engine.autodesk_info import verdict as autodesk_verdict
    info = app_info.about(record)
    curated = app_info.curated(record) is not None
    recommendation, why = recommend.recommend(record)
    verdict = {"keep": "Keep", "optional": "Optional", "remove": "Safe to remove"}[recommendation]
    if not curated:
        verdict = "Unknown"
    if not record.removable or record.type in ("System app", "Framework"):
        verdict = "Cannot remove"
    if verdict != "Cannot remove":
        verdict = autodesk_verdict(record.name, record.publisher, record.registry_key) or verdict
    source = (REGISTRY_DATE if record.registry_key or record.type in ("Desktop app", "Defect", "Orphaned")
              else record.extra.get('installed_source') or
              ("estimate: creation time of the app's data folder" if record.extra.get('data_folder')
               else "date source not recorded"))
    return AppFacts(info.what, info.does, record.installed,
                    source if record.installed else "not recorded by the installer",
                    verdict=verdict, breaks=info.remove + (" " + why if not curated else ""),
                    confidence="researched" if curated else "from the installer only")


def installed_text(facts):
    return facts.installed.strftime("%Y-%m-%d") if facts.installed else "not recorded"


def last_used_text(facts, now=None):
    if facts.last_used is None:
        return facts.last_used_source
    now = now or datetime.now(timezone.utc)
    days = max(0, (now - facts.last_used.replace(tzinfo=facts.last_used.tzinfo or timezone.utc)).days)
    return "today" if days == 0 else f"{days} days ago"


def colour_role(facts):
    return {"Keep": "warning", "Cannot remove": "warning", "Safe to remove": "success",
            "Optional": "info", "Unknown": "warning"}[facts.verdict]


def facts_text(facts):
    if facts.last_used:
        moment = facts.last_used.replace(tzinfo=facts.last_used.tzinfo or timezone.utc).astimezone()
        last = f"{moment:%Y-%m-%d %H:%M} ({last_used_text(facts)})"
    else:
        last = last_used_text(facts)
    return (f"What it is: {facts.what}\nWhat it does: {facts.does}\n"
            f"Installed: {installed_text(facts)} ({facts.installed_source})\n"
            f"Last used: {last}\nUsage evidence: {facts.last_used_source}\n"
            f"Uninstall safety: {facts.verdict}\nWhat could break: {facts.breaks}\n"
            f"Confidence: {facts.confidence}"
            + (f"\nfacts collected {facts.collected:%H:%M}" if facts.collected else ""))


def tooltip(facts):
    return "<qt>" + escape(facts_text(facts)).replace("\n", "<br>") + "</qt>"
