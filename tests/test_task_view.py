"""task_view + task_actions: result decoding, chips, XML facts, verified actions."""
from types import SimpleNamespace

from modules.scheduled_tasks import task_actions, task_view as v

XML = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Triggers><LogonTrigger><Enabled>true</Enabled></LogonTrigger>
    <TimeTrigger><Enabled>false</Enabled></TimeTrigger></Triggers>
  <Principals><Principal id="A"><UserId>S-1-5-18</UserId><RunLevel>HighestAvailable</RunLevel></Principal></Principals>
  <Settings><Hidden>true</Hidden></Settings>
  <Actions Context="A"><Exec><Command>C:\\definitely\\missing\\tool.exe</Command><Arguments>/x /y</Arguments></Exec></Actions>
</Task>"""


def task(**kw):
    base = dict(name="T", path="\\Vendor\\T", status="Ready", last_run="x", last_result="0",
                next_run="", author="Me", triggers="", xml=XML, enabled=True, last_result_code=0)
    base.update(kw)
    return SimpleNamespace(**base)


def test_known_codes_are_decoded_in_words():
    assert "not run yet" in v.describe_result(0x41303).lower()
    assert "terminated" in v.describe_result(0x41306).lower()
    assert "refused" in v.describe_result(-2147020576).lower()   # signed 0x800710E0
    assert v.describe_result("0x80070002").startswith("0x80070002: File not found")


def test_unknown_codes_still_say_something_honest():
    assert "exited with code 7" in v.describe_result(7)
    assert "Windows error 1234" in v.describe_result(0x800704D2)
    assert v.describe_result(None) == "Unknown result"
    assert v.describe_result("abc") == "Unknown result"


def test_failed_never_ran_classification():
    assert not v.failed(0)
    assert not v.failed(0x41303) and v.never_ran(0x41303)
    assert not v.failed(0x41301)
    assert v.failed(1) and v.failed(-2147020576) and v.failed(0x41306)
    assert not v.failed(None)   # unreadable is not "failed"


def test_never_run_dates_read_as_never():
    from modules.scheduled_tasks.tasks_reader import _fmt_date
    assert _fmt_date("1999-11-30 00:00:00+00:00") == "Never"
    assert _fmt_date("1899-12-30 00:00:00") == "Never"
    assert _fmt_date("2026-09-25 19:45:03") == "2026-09-25 19:45"


def test_well_known_sids_resolve():
    assert v.resolve_principal("S-1-5-18") == "SYSTEM"
    assert v.resolve_principal("S-1-5-4") == "Logged-on users"
    assert v.resolve_principal("DOMAIN\\bob") == "DOMAIN\\bob"


def test_xml_facts():
    d = v.parse_details(XML)
    assert d.parsed and d.run_as == "SYSTEM" and d.run_level == "HighestAvailable"
    assert d.commands == [(r"C:\definitely\missing\tool.exe", "/x /y")]
    assert d.triggers == ["At logon"]          # the disabled time trigger is not listed
    assert d.hidden
    assert v.program_missing(d) is True


def test_unparseable_xml_is_unknown_not_empty_answer():
    d = v.parse_details("<not xml")
    assert not d.parsed and v.program_missing(d) is None
    assert "could not be read" in v.detail_text(task(xml="<not xml"))


def test_chips_and_counts():
    tasks = [task(), task(name="ms", path="\\Microsoft\\Windows\\X\\ms", last_result_code=0x41303),
             task(name="off", status="Disabled", last_result_code=2)]
    counts = v.filter_counts(tasks)
    assert counts["all"] == 3 and counts["never"] == 1 and counts["failed"] == 1
    assert counts["disabled"] == 1 and counts["nonms"] == 2 and counts["system"] == 3
    assert counts["logon"] == 3 and counts["missing"] == 3
    assert [t.name for t in v.visible(tasks, "never", "")] == ["ms"]


def test_search_reaches_arguments_and_account():
    t = task()
    assert v.matches(t, "/x /y") and v.matches(t, "system") and v.matches(t, "tool.exe")
    assert not v.matches(t, "zzz")


# ---- actions with a fake scheduler ------------------------------------------------

class FakeTask:
    def __init__(self, enabled=True, obey=True):
        self._enabled, self.obey = enabled, obey
        self.LastRunTime, self.State, self.ran = "t0", 3, False

    @property
    def Enabled(self):
        return self._enabled

    @Enabled.setter
    def Enabled(self, value):
        if self.obey:
            self._enabled = value

    def Run(self, _):
        self.ran = True
        self.State = 4


class FakeSvc:
    def __init__(self, task):
        self.task, self.deleted = task, False

    def GetFolder(self, path):
        outer = self

        class F:
            def GetTask(self, name):
                if outer.deleted:
                    raise OSError("gone")
                return outer.task

            def DeleteTask(self, name, flags):
                outer.deleted = True
        return F()


def test_set_enabled_verifies_by_reading_back():
    ok, msg = task_actions.set_enabled("\\A\\T", False, connect=lambda t=FakeTask(): FakeSvc(t))
    assert ok and "disabled" in msg


def test_set_enabled_reports_a_change_windows_ignored():
    stubborn = FakeTask(enabled=True, obey=False)
    ok, msg = task_actions.set_enabled("\\A\\T", False, connect=lambda: FakeSvc(stubborn))
    assert not ok and "still enabled" in msg


def test_run_now_and_delete():
    t = FakeTask()
    svc = FakeSvc(t)
    assert task_actions.run_now("\\A\\T", connect=lambda: svc)[0] and t.ran
    ok, msg = task_actions.delete("\\A\\T", connect=lambda: svc)
    assert ok and "no longer exists" in msg


def test_split_path():
    assert task_actions.split_path("\\Microsoft\\Windows\\X\\Task") == ("\\Microsoft\\Windows\\X", "Task")
    assert task_actions.split_path("\\Task") == ("\\", "Task")


def test_real_machine_tasks_are_plausible():
    import pythoncom
    pythoncom.CoInitialize()
    from modules.scheduled_tasks.tasks_reader import ALL_FOLDERS, get_tasks_in_folder
    tasks = get_tasks_in_folder(ALL_FOLDERS)
    assert len(tasks) > 50
    counts = v.filter_counts(tasks)
    assert counts["all"] == len(tasks)
    assert counts["never"] < len(tasks)
    assert all(t.last_result_code is None or 0 <= t.last_result_code <= 0xFFFFFFFF for t in tasks)
    assert all(v.details_of(t).parsed for t in tasks)
