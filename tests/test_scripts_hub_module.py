import pytest


@pytest.fixture
def module(qapp):
    from modules.scripts_hub.scripts_hub_module import ScriptsModule
    return ScriptsModule()


def test_the_hub_has_one_child(module):
    assert len(module.children) == 1
    assert type(module.children[0]).__name__ == "FileForensicsModule"


def test_the_hub_builds_a_widget_with_one_tab(module, qapp):
    class FakeApp:
        backup = None
        config = None
        thread_pool = None

    module.on_start(FakeApp())
    widget = module.create_widget()
    assert widget is not None
