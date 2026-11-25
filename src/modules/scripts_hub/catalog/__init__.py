"""The Script Library's catalogs, curated into deep, multi-stage sysadmin & diagnostic suites. Qt-free."""
from typing import List

from ..library import Script
from . import (
    deep_ad_identity,
    deep_client_cpu_kernel,
    deep_client_gaming_power,
    deep_client_hw_storage,
    deep_client_media_gpu,
    deep_client_profile_boot_gdi,
    deep_crash_postmortem,
    deep_hardware_firmware,
    deep_network_dns,
    deep_printing,
    deep_security_persistence,
    deep_server_infra,
    deep_servicing_cbs,
    deep_storage_vss,
    deep_system_perf,
)

CATALOG: List[Script] = (
    deep_system_perf.SCRIPTS
    + deep_security_persistence.SCRIPTS
    + deep_hardware_firmware.SCRIPTS
    + deep_storage_vss.SCRIPTS
    + deep_network_dns.SCRIPTS
    + deep_servicing_cbs.SCRIPTS
    + deep_crash_postmortem.SCRIPTS
    + deep_printing.SCRIPTS
    + deep_ad_identity.SCRIPTS
    + deep_server_infra.SCRIPTS
    + deep_client_profile_boot_gdi.SCRIPTS
    + deep_client_gaming_power.SCRIPTS
    + deep_client_media_gpu.SCRIPTS
    + deep_client_cpu_kernel.SCRIPTS
    + deep_client_hw_storage.SCRIPTS
)
