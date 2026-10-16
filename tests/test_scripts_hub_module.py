import pytest


@pytest.fixture
def module(qapp):
    from modules.scripts_hub.scripts_hub_module import ScriptsModule
    return ScriptsModule()


def test_the_hub_hosts_the_library_then_file_forensics(module):
    assert [type(c).__name__ for c in module.children] == ["ScriptLibraryModule",
                                                           "FileForensicsModule"]


def test_the_hub_builds_a_widget_with_one_tab(module, qapp):
    class FakeApp:
        backup = None
        config = None
        thread_pool = None

    module.on_start(FakeApp())
    widget = module.create_widget()
    assert widget is not None
