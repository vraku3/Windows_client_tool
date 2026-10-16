"""The scripts folder can be chosen, scripts see it, and the output pane shows
an ISE-style prompt."""
import io
import os
import socket

import pytest

from modules.scripts_hub import library as lib
from modules.scripts_hub import output_style as st


@pytest.fixture
def folder(tmp_path):
    lib.set_user_scripts_dir(str(tmp_path))
    yield tmp_path
    lib.set_user_scripts_dir(None)


def test_the_folder_defaults_and_can_be_changed_and_reset(folder):
    assert lib.user_scripts_dir() == os.path.normpath(str(folder))
    lib.set_user_scripts_dir(None)
    assert lib.user_scripts_dir() == lib.default_user_scripts_dir()
    assert lib.default_user_scripts_dir().endswith(os.path.join("WindowsTweaker", "scripts"))


def test_user_scripts_and_new_scripts_follow_the_chosen_folder(folder):
    path = lib.new_script("Mine")
    assert os.path.dirname(path) == os.path.normpath(str(folder))
    assert [s.title for s in lib.load_user_scripts()] == ["Mine.ps1"]


class FakeProc:
    def __init__(self):
        self.stdout = io.BytesIO(b"x\r\n")
        self.returncode = 0
        self.pid = 99999999

    def wait(self, timeout=None):
        return 0


def test_scripts_get_the_folder_in_their_environment_and_built_ins_start_in_home(folder):
    seen = {}

    def popen(argv, **kw):
        seen.update(kw)
        return FakeProc()

    lib.run(lib.BUILT_IN[0], lambda _l: None, popen=popen)
    assert seen["env"][lib.FOLDER_ENV] == os.path.normpath(str(folder))
    assert seen["cwd"] == os.path.expanduser("~")


def test_a_fleet_script_reads_servers_txt_from_the_chosen_folder(folder):
    """Real run: servers.txt in the chosen folder names this computer, so the
    'no servers.txt' notice must not appear and this computer must be checked."""
    (folder / "servers.txt").write_text(f"# test\n{socket.gethostname()}\n", encoding="utf-8")
    s = next(x for x in lib.built_in() if x.id == "fl_uptime")
    r = lib.run(s, lambda _l: None)
    text = "\n".join(r.lines)
    assert r.returncode == 0 and "checking this computer only" not in text
    assert socket.gethostname().upper() in text.upper()


def test_the_prompt_line_looks_like_ise():
    builtin = lib.BUILT_IN[0]
    assert lib.prompt_line(builtin) == f"PS {os.path.expanduser('~')}> .\\{builtin.id}.ps1"
    mine = lib.Script("user:a.ps1", "a.ps1", lib.USER_CATEGORY, "", "", path=r"C:\s\a.ps1")
    assert lib.prompt_line(mine) == r"PS C:\s> & 'C:\s\a.ps1'"
    cmd = next(x for x in lib.built_in() if x.id == "ipconfig")
    assert lib.prompt_line(cmd).endswith("> ipconfig /all")


def test_a_table_row_with_an_ipv6_address_is_not_a_key():
    assert st.spans("UDP   ::                 4500  7144 svchost")[0][1] == st.PLAIN
    assert st.spans("Computer             : VRAKU")[0] == ("Computer", st.KEY)
