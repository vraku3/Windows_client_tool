"""Read-only summary of update scans and maintenance scheduling."""
from ui.glance import GlanceStrip, GlanceTile


class UpdatesOverview(GlanceStrip):
    def __init__(self, parent=None):
        self.tiles_by_key = {}
        for key, caption in (("apps", "App updates"), ("windows", "Windows updates"),
                             ("held", "Held offers"), ("restart", "Restart pending"),
                             ("last", "Last scheduled run"), ("task", "Scheduled task")):
            tile = GlanceTile(caption, tooltip=f"{caption} from the latest successful read; an unread value is shown with its reason.")
            tile.set_unreadable("Not checked yet")
            self.tiles_by_key[key] = tile
        super().__init__(self.tiles_by_key.values(), parent)

    def set_apps(self, updates):
        self.tiles_by_key["apps"].set(len(updates), "winget offers", severity="warning" if updates else "ok")

    def set_windows(self, updates, held):
        self.tiles_by_key["windows"].set(len(updates), "Windows offers", severity="warning" if updates else "ok")
        self.tiles_by_key["held"].set(len(held), "Kept on purpose", severity="info")

    def scan_failed(self, key, reason):
        self.tiles_by_key[key].set_unreadable(str(reason))
        if key == "windows":
            self.tiles_by_key["held"].set_unreadable(str(reason))

    def set_health(self, health, restart, reason=""):
        self.tiles_by_key["restart"].set(None if restart is None else "Yes" if restart else "No",
                                       reason=reason, severity="warning" if restart else "ok")
        task, last = self.tiles_by_key["task"], self.tiles_by_key["last"]
        if health.exists is None:
            task.set_unreadable(health.error or "Task query failed")
            last.set_unreadable(health.error or "Task query failed")
        elif not health.exists:
            task.set("Not scheduled", "No maintenance task", severity="info")
            last.set("Never", "No maintenance task")
        else:
            failed = health.last_run and health.last_result_code not in (None, 0, 267009, 267011)
            state = "Disabled" if health.enabled is False else "Enabled" if health.enabled else None
            task.set(state, health.last_result_text or "Result unavailable",
                     reason="Task enabled state could not be read", severity="danger" if failed else "warning" if state == "Disabled" else "ok")
            never = health.last_result_code == 267011
            last.set(health.last_run or ("Never" if never else None), "Scheduled maintenance",
                     reason="No last-run timestamp was returned", tooltip=health.last_run or "No run timestamp available")
