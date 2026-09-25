r"""Registry Explorer's key tree started ~400px down a mostly empty page.

Seen 2026-09-25: the search row's status label is vertically flexible and a
horizontal QSplitter is only Preferred vertically, so with no stretch on the
splitter Qt split the leftover height between the search row and the splitter.
"""
from PyQt6.QtWidgets import QSplitter

from modules.registry_explorer.registry_module import RegistryExplorerModule


def test_the_splitter_sits_right_under_the_search_row_and_takes_the_height(qapp):
    module = RegistryExplorerModule()
    module.app = type("App", (), {"thread_pool": None})()
    root = module.create_widget()
    root.resize(1200, 800)
    root.show()
    qapp.processEvents()
    splitter = root.findChild(QSplitter)
    assert splitter.y() < 120, splitter.y()            # not pushed down the page
    assert splitter.height() > 600, splitter.height()  # and it gets the space
