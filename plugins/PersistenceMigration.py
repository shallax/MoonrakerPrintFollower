"""The 4.5.0 one-shot migration's explicit owner (the panel's
B1/C1-C6/E5 rulings): the strict source read that distinguishes an
absent blob from a corrupt one, the backup-before-clean gate, the
verify-by-re-read interlock and the tri-state outcome record. Pure:
no Qt, no Resources, no Uranium — the caller passes the preference
writer, the paths and the document writers, so the migration tests
run without Cura (the StateStore pattern).

The control flow (the architecture panel's Q1 contract): strict
read, backup (fsynced, verified by content), the new files, the
verify, the clean LAST — gated on the backup existing when a backup
was owed — and the "ok" outcome persisted AFTER all of them, as the
commit step. Nothing else may persist an "ok" record: a candidate
write used to carry one before the verify ran, so a migration that
failed verification left a success on disk for the next boot to clean
up after. While no record exists the attempt is UNCOMMITTED, and the
next boot replays it from the same intact source. Every failure here
is also REPORTED rather than only logged — the persisted record, or
the caller's session copy of the returned outcome, is what the toast
and the banner read. Everything before the clean is idempotently
replayable, so a crash anywhere replays from the same source. The
document writers are the facade's stores, which write pretty-printed
JSON (indent + sorted keys, the ruling)."""
from __future__ import annotations

import configparser
import json
import os
from dataclasses import asdict, dataclass
from typing import Any, Callable, Dict, Optional, Tuple
from urllib.parse import quote_plus

from .PrinterConfig import PrinterConfig, PrinterConfigStore


@dataclass
class MigrationOutcome:
    """The persisted tri-state (the critic's C2): `status` is one of
    "ok"/"failed" — "pending" is implicit while no record exists —
    and `reason` stays machine-readable for the notice's flavour.
    `record_persisted` is the commit step's own verdict: the migration
    can complete while its record write fails, and the caller must
    know that the next boot has nothing to read."""

    status: str = "ok"
    reason: str = "nothing-to-do"
    backup_name: Optional[str] = None
    backup_written: bool = False
    records: int = 0
    record_persisted: bool = True


def read_source(value: Any) -> Tuple[str, Dict[str, Any]]:
    """The strict v1-blob read: absent and corrupt must be
    distinguishable before any migration decision (round-1 C1/C5).
    "empty" covers never-written and written-empty — both are healthy
    and mean there is nothing to migrate."""
    if value is None or value == "":
        return "absent", {}
    text = str(value)
    if text == "{}":
        return "empty", {}
    try:
        decoded = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return "corrupt", {}
    if not isinstance(decoded, dict):
        return "corrupt", {}
    return ("empty" if not decoded else "records"), decoded


def split_record(record: Dict[str, Any]) -> tuple:
    """One v1 record into its two homes (the panel's E3 table): the
    console transcript/store-time move to the per-machine state
    shard; everything else is settings. The legacy typed-history key
    folds into the transcript as command entries when no transcript
    exists (the 4.5.0 cleanup) — the dead consoleHistory shard key
    is never written."""
    config = PrinterConfig.from_dict(record)
    settings = {
        key: value
        for key, value in vars(config).items()
        if key not in ("console_history", "console_transcript", "console_store_time")
    }
    settings["feed_mode"] = settings["feed_mode"].value
    transcript = config.console_transcript
    if not isinstance(transcript, (list, tuple)) or not transcript:
        transcript = [
            {"kind": "command", "text": str(line), "error": False, "success": False}
            for line in (config.console_history or [])
        ]
    state = {
        "consoleTranscript": transcript,
        "consoleStoreTime": config.console_store_time,
    }
    return settings, state


def _raw_source_evidence(raw: bytes) -> bool:
    """The backup's source evidence (the 4.5.0 one-boot upgrade fix):
    the transformed v1 blob need not be on disk yet — the backup is a
    copy of the ACTUAL pre-migration source, so the genuine flat
    legacy settings or a genuine Moonraker Connection instances blob
    count too. Structured, never a broad substring: the flat check
    runs the SAME normalised comparison the legacy chain uses
    (registered defaults are never evidence), and the Connection
    check requires a real non-empty instances mapping — an arbitrary
    unrelated cura.cfg still fails closed. A TOTAL predicate:
    arbitrary cura.cfg bytes yield True or False, never a raise —
    interpolation is disabled (a legacy `%20` value is data, not a
    format string) and every structured read sits inside the same
    defensive boundary."""
    if b"printer_configs_v1" in raw:
        return True
    try:
        parsed = configparser.ConfigParser(interpolation=None)
        parsed.read_string(raw.decode("utf-8", errors="replace"))
        if parsed.has_section("moonrakerprintfollower"):
            raw_legacy = {}
            for field, pref_key in PrinterConfigStore.LEGACY_MAP.items():
                option = pref_key.rsplit("/", 1)[-1]
                if not parsed.has_option("moonrakerprintfollower", option):
                    continue
                raw_legacy[field] = parsed.get("moonrakerprintfollower", option)
            if raw_legacy:
                legacy = asdict(PrinterConfig.from_dict(raw_legacy))
                defaults = asdict(PrinterConfig())
                if any(
                    legacy.get(field) != defaults.get(field)
                    for field in raw_legacy
                ):
                    return True
        if parsed.has_section("moonraker"):
            instances = json.loads(parsed.get("moonraker", "instances", fallback="{}"))
            if isinstance(instances, dict) and instances:
                return True
    except Exception:
        return False
    return False


def write_backup(source_path: str, backup_path: str) -> bool:
    """The whole-file cura.cfg copy: fsynced, then verified by content
    against the source bytes before the clean may proceed. The copy
    must carry source evidence — existence is not durability (the
    architecture panel's C6)."""
    try:
        with open(source_path, "rb") as handle:
            raw = handle.read()
        if not raw or not _raw_source_evidence(raw):
            return False
        with open(backup_path + ".tmp", "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(backup_path + ".tmp", backup_path)
        with open(backup_path, "rb") as handle:
            if handle.read() != raw:
                return False
        return True
    except OSError:
        return False


def run_migration(
    blob_value: Any,
    cura_cfg_path: str,
    settings_path: str,
    state_dir: str,
    old_state_path: Optional[str],
    settings_write: Callable[[Dict[str, Any]], bool],
    settings_record_write: Callable[[Dict[str, Any]], bool],
    state_global_write: Callable[[Dict[str, Any]], bool],
    state_machine_write: Callable[[str, Dict[str, Any]], bool],
    set_pref: Callable[[str, Any], None],
    timestamp: str,
) -> MigrationOutcome:
    """One migration attempt, safe to re-run: everything before the
    clean is idempotently replayable and the OK record lands last —
    after the backup, the candidate files, the verify and the clean
    have all succeeded, which is the only moment the attempt becomes
    committed. Every failure returns with cura.cfg intact and nothing
    claiming success on disk, so the next boot replays it. `timestamp`
    is the caller's filesystem-safe sortable form (digits and dashes,
    the ruling)."""
    outcome = MigrationOutcome()
    source_state, records = read_source(blob_value)

    if source_state in ("absent", "empty"):
        # Side-effect free by design: schema activation is the
        # BINDING's job, not the transformer's. No source means no
        # record, no backup, no clean — the absence of a record is
        # exactly "nothing was ever migrated", and a fresh install
        # must never manufacture evidence of one.
        outcome.status = "ok"
        outcome.reason = "nothing-to-do"
        return outcome

    if source_state == "corrupt":
        # The ruling over the critic's C1 gate: a corrupt
        # blob is a migration gone wrong — flag it and start clean,
        # provided the backup landed. Nothing is silent: the record
        # and the notice both carry the failure, and the backup holds
        # the raw material to pick apart. A corrupt blob is stale
        # input: the pieces of a live v2 document that already exist
        # are carried through untouched, never replaced. The clean
        # runs ONLY after the recovery documents (with the failed
        # record) have landed — a recovery write failure must leave
        # the corrupt source in place for the next boot's replay (the
        # 4.5.0 transactional fix).
        outcome.status = "failed"
        outcome.reason = "corrupt-blob"
        backup_name = f"cura.cfg.{timestamp}"
        if write_backup(cura_cfg_path, os.path.join(os.path.dirname(cura_cfg_path), backup_name)):
            outcome.backup_name = backup_name
            outcome.backup_written = True
            if not _recover_empty_documents(
                settings_write, state_global_write, old_state_path, outcome, timestamp,
                existing=_read_settings_document(settings_path),
            ):
                outcome.reason = "write-failed"  # nothing landed: no record to read back
            else:
                _clean_preferences(set_pref)
        # A failed backup leaves cura.cfg untouched; the next launch
        # retries (the notice's flavour B has no backup to open).
        return outcome

    # Records: back up before anything moves, write the new files,
    # verify by re-read, clean, and commit the ok record LAST (Q1).
    # Nothing before the commit writes a record: an absent record is
    # exactly "this attempt has not been committed", which is what
    # makes the replay correct.
    outcome.reason = "migrated"
    outcome.records = len(records)
    backup_name = f"cura.cfg.{timestamp}"
    backup_path = os.path.join(os.path.dirname(cura_cfg_path), backup_name)
    if not write_backup(cura_cfg_path, backup_path):
        outcome.status = "failed"
        outcome.reason = "backup-failed"
        return outcome
    outcome.backup_name = backup_name
    outcome.backup_written = True

    if not _write_new_files(
        records, settings_write, state_global_write, state_machine_write,
        old_state_path, outcome, timestamp,
        existing=_read_settings_document(settings_path),
    ):
        outcome.status = "failed"
        outcome.reason = "write-failed"
        return outcome

    if not _verify_new_files(records, settings_path, state_dir):
        outcome.status = "failed"
        outcome.reason = "verify-failed"
        return outcome

    _clean_preferences(set_pref)
    _remove_old_state_file(old_state_path)
    outcome.status = "ok"
    # The commit step, and the only place an ok record is ever
    # written. If this write itself fails the migration still
    # happened — the clean has run — but the next boot has no record
    # to read: it replays against an empty source, which is a no-op,
    # and the caller logs the unpersisted verdict.
    outcome.record_persisted = bool(settings_record_write(_record(outcome, timestamp)))
    return outcome


def _recover_empty_documents(
    settings_write, state_global_write, old_state_path, outcome, timestamp, existing=None,
) -> bool:
    """The CORRUPT-blob recovery path (never first-install
    activation): new files, configVersion 2, the
    old chrome carried across where the old state file exists — and
    the old file removed once the new document is written (found
    live: no old-config trace remains). Reports whether both writes
    landed: a half-written activation is a failure the caller must
    replay, and the ok record lives inside the settings write, so a
    failed write must not be followed by an unearned success."""
    existing = existing or {}
    existing_machines = existing.get("machines")
    machines = dict(existing_machines) if isinstance(existing_machines, dict) else {}
    global_section = _global_section_of(existing)
    global_section["migration"] = _record(outcome, timestamp)
    if not state_global_write({**_read_old_chrome(old_state_path), "configVersion": 2}):
        return False
    if not settings_write({
        "configVersion": 2,
        "global": global_section,
        "machines": machines,
    }):
        return False
    _remove_old_state_file(old_state_path)
    return True


def _read_old_chrome(old_state_path: Optional[str]) -> Dict[str, Any]:
    """The 4.4.0 state file's document, for the global chrome's move:
    the old file stays on disk; the new global file wins once
    written."""
    if not old_state_path:
        return {}
    try:
        with open(old_state_path, "r", encoding="utf-8") as handle:
            decoded = json.load(handle)
        return decoded if isinstance(decoded, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_new_files(
    records, settings_write, state_global_write, state_machine_write,
    old_state_path, outcome, timestamp, existing=None,
) -> bool:
    """The candidate writes, before anything is verified or cleaned.
    NO record is written here — not even a failed one: `outcome` and
    `timestamp` stay in the signature for the callers that pass them,
    while the record is the commit step's business alone (an ok record
    that reached disk before the verify is the fault this shape
    fixes)."""
    chrome = _read_old_chrome(old_state_path)
    if not state_global_write({**chrome, "configVersion": 2}):
        return False
    machines: Dict[str, Any] = {}
    for machine_id, record in records.items():
        if not isinstance(record, dict):
            continue
        key = str(machine_id)
        settings, state = split_record(record)
        machines[key] = settings
        if not state_machine_write(key, state):
            return False
    existing = existing or {}
    existing_machines = existing.get("machines")
    if isinstance(existing_machines, dict):
        # A re-run must not clobber the live config: the existing
        # records win, the migrated records only fill gaps.
        machines = {**machines, **existing_machines}
    return settings_write({
        "configVersion": 2,
        "global": _global_section_of(existing),
        "machines": machines,
    })


def _global_section_of(document: Dict[str, Any]) -> Dict[str, Any]:
    """The document's global section as a copy: the live document's
    keys win over anything a migration is about to write into it."""
    section = document.get("global")
    return dict(section) if isinstance(section, dict) else {}


def _read_settings_document(settings_path: str) -> Dict[str, Any]:
    """The current settings document, or {} — the re-run guard's
    eyes: a document that already exists is live config and must
    never be replaced by a nothing-to-do migration."""
    try:
        with open(settings_path, "r", encoding="utf-8") as handle:
            decoded = json.load(handle)
        return decoded if isinstance(decoded, dict) else {}
    except Exception:
        return {}


def _verify_new_files(records, settings_path, state_dir) -> bool:
    """The verify-by-re-read interlock (E5): the new files must prove
    they hold the records before the clean destroys the only other
    copy."""
    try:
        with open(settings_path, "r", encoding="utf-8") as handle:
            decoded = json.load(handle)
        if not isinstance(decoded, dict):
            return False
        machines = decoded.get("machines")
        # At least the migrated records — a re-run may also carry
        # live machines the merge preserved (the lost-config guard).
        if not isinstance(machines, dict) or len(machines) < len(records):
            return False
        for machine_id in records:
            if machine_id not in machines:
                return False
            with open(os.path.join(state_dir, f"{quote_plus(machine_id)}.json"), "r", encoding="utf-8") as handle:
                shard = json.load(handle)
            if not isinstance(shard, dict):
                return False
        return True
    except (OSError, ValueError):
        return False


def _remove_old_state_file(old_state_path: Optional[str]) -> None:
    """The pre-4.5.0 sections file leaves no trace once the new state
    document holds its content (found live) — removed
    only AFTER the new document's write landed."""
    if not old_state_path:
        return
    try:
        os.remove(old_state_path)
    except OSError:
        pass  # already gone, or not ours to remove — the new document is authoritative either way


def _clean_preferences(set_pref: Callable[[str, Any], None]) -> None:
    """The in-memory return-to-default (C4): Uranium's writer omits
    values equal to their registered defaults, so this is the only
    clean that survives the next savePreferences — file surgery and
    removePreference are both wrong for this file's owner. The legacy
    flat keys reset too: their mirror would otherwise re-emit on the
    next save."""
    from .PrinterConfig import PrinterConfigStore
    set_pref(PrinterConfigStore.PREF_KEY, "{}")
    for field_name, pref_key in PrinterConfigStore.LEGACY_MAP.items():
        set_pref(pref_key, PrinterConfigStore.LEGACY_DEFAULTS[field_name])
    # The migrated flags reset to their registered defaults too: the
    # [moonrakerprintfollower] section leaves cura.cfg entirely (found
    # live). The legacy chain guards on the migration
    # record, so nothing re-runs and resurrects the blob. The bed-mesh
    # keys reset the same way — their home is the settings document's
    # global section now (the no-trace ruling).
    set_pref(PrinterConfigStore.MIGRATED_KEY, False)
    set_pref(PrinterConfigStore.MOONRAKER_CONNECTION_MIGRATED_KEY, False)
    set_pref("moonrakerprintfollower/bed_mesh_visible", True)
    set_pref("moonrakerprintfollower/bed_mesh_exaggeration", 20.0)


def _record(outcome: MigrationOutcome, timestamp: str) -> Dict[str, Any]:
    """The persisted record (the UX spec's shape): the backup FILE
    NAME, never a path — the config folder moves between Cura
    versions."""
    return {
        "status": outcome.status,
        "reason": outcome.reason,
        "attemptedAt": timestamp,
        "backupName": outcome.backup_name,
        "backupWritten": outcome.backup_written,
        "records": outcome.records,
        "toastShown": False,
        "bannerDismissed": False,
    }
