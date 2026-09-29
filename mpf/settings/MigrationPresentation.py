"""Shared presentation of settings-migration outcomes; no screen dependencies."""

def migration_banner_text(record):
    """The dialog banner's copy (the UX spec): the rollback recipe is
    the message — the file name, the folder route and the
    reinstall-previous-version steps."""
    backup = str(record.get("backupName") or "")
    if record.get("backupWritten") and backup:
        return ("Your Moonraker settings did not carry over from the previous version, so the plugin is using defaults. "
                "Cura's configuration was saved as %s. Open it from Help > Show Configuration Folder. "
                "To roll back: close Cura, reinstall the previous version of the plugin, and copy that file over cura.cfg.") % backup
    return ("Your Moonraker settings did not carry over from the previous version, so the plugin is using defaults. "
            "Nothing was removed — your existing Cura configuration is untouched.")


def migration_diagnostics_text(record):
    """The permanent diagnostics row's copy (after dismissal): the
    recipe is demoted, never deleted."""
    backup = str(record.get("backupName") or "")
    if record.get("backupWritten") and backup:
        return "Settings migration failed. The previous configuration is saved as %s." % backup
    return "Settings migration failed. Nothing was removed."
