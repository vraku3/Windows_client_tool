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


def test_script_library_folder_selection_ui(qapp, tmp_path, monkeypatch):
    """The script library tree provides folder action items and shows folder management in detail panel."""
    from modules.scripts_hub.library_module import ScriptLibraryModule, _FOLDER_ROLE, _ACTION_ROLE

    mod = ScriptLibraryModule()

    class FakeConfig:
        def __init__(self):
            self._d = {}
        def get(self, k, default=None):
            return self._d.get(k, default)
        def set(self, k, v):
            self._d[k] = v

    class FakeApp:
        config = FakeConfig()

    mod.on_start(FakeApp())
    w = mod.create_widget()
    assert w is not None

    # Load with an empty folder
    empty_dir = tmp_path / "empty_scripts"
    empty_dir.mkdir()
    lib.set_user_scripts_dir(str(empty_dir))
    mod._reload()

    # Find My scripts branch
    tree = mod._tree
    my_scripts_item = None
    for i in range(tree.topLevelItemCount()):
        item = tree.topLevelItem(i)
        if "My scripts" in item.text(0):
            my_scripts_item = item
            break
    assert my_scripts_item is not None
    assert my_scripts_item.data(0, _FOLDER_ROLE) is True

    # Check child action items under My scripts (0)
    texts = [my_scripts_item.child(j).text(0) for j in range(my_scripts_item.childCount())]
    assert any("put .ps1" in t for t in texts)
    assert any("Choose scripts folder" in t for t in texts)
    assert any("Open folder" in t for t in texts)

    # Selecting My scripts branch shows folder info in detail panel
    tree.setCurrentItem(my_scripts_item)
    assert mod._title.text() == "My Scripts Folder"
    assert "empty" in mod._output.toPlainText().lower()
    assert not mod._folder_row_widget.isHidden()
    assert mod._script_btn_widget.isHidden()

    # Selecting the choose item triggers folder info too
    choose_child = next(my_scripts_item.child(j) for j in range(my_scripts_item.childCount())
                        if "Choose scripts folder" in my_scripts_item.child(j).text(0))
    tree.setCurrentItem(choose_child)
    assert mod._title.text() == "My Scripts Folder"

    # Simulate folder change to a populated folder
    pop_dir = tmp_path / "custom_scripts"
    pop_dir.mkdir()
    (pop_dir / "test1.ps1").write_text("<#\n.SYNOPSIS\nCustom check\n#>\nWrite-Output 1\n", encoding="utf-8")
    (pop_dir / "test2.cmd").write_text("@echo off\necho hello\n", encoding="utf-8")

    monkeypatch.setattr("PyQt6.QtWidgets.QFileDialog.getExistingDirectory", lambda *a, **k: str(pop_dir))
    mod._change_folder()

    assert lib.user_scripts_dir() == os.path.normpath(str(pop_dir))
    assert "test1.ps1" in mod._output.toPlainText()
    assert "test2.cmd" in mod._output.toPlainText()

    # Find My scripts branch again (tree was reloaded)
    my_scripts_reloaded = None
    for i in range(tree.topLevelItemCount()):
        item = tree.topLevelItem(i)
        if "My scripts" in item.text(0):
            my_scripts_reloaded = item
            break
    assert my_scripts_reloaded is not None
    assert "2" in my_scripts_reloaded.text(0)

    # Double click on a script runs/selects it
    leaf = my_scripts_reloaded.child(0)
    tree.setCurrentItem(leaf)
    assert mod._current is not None
    assert mod._current.title == "test1.ps1"
    assert not mod._script_btn_widget.isHidden()
    assert mod._folder_row_widget.isHidden()

