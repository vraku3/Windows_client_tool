"""The Script Library's catalogs, one module per area. Qt-free.

`CATALOG` is every catalog script in display order. Client scripts were run
on a real Windows 11 machine, elevated and as a standard user, before they
shipped (`tools/script_library_check.py`); a script with `requires` set
needs a role or module that machine does not have, so it was syntax-checked
by PowerShell's parser only -- the pane says so on its card.
"""
from typing import List

from ..library import Script
from . import (client_logs_network, client_mgmt, client_more, client_security, client_system,
               server, server_ad, server_infra, server_ops)

CATALOG: List[Script] = (client_system.SCRIPTS + client_logs_network.SCRIPTS
                         + client_security.SCRIPTS + client_mgmt.SCRIPTS + client_more.SCRIPTS
                         + server.SCRIPTS + server_ad.SCRIPTS + server_infra.SCRIPTS
                         + server_ops.SCRIPTS)
