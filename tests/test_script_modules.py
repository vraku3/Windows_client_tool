"""Server scripts know the Microsoft module they need, the app knows whether
this PC has it, and offers Windows' own installer -- never a bundled copy."""
from modules.scripts_hub import library as lib
from modules.scripts_hub import modules_catalog as mc


def _by_id(sid):
    return next(s for s in lib.built_in() if s.id == sid)


def test_scripts_map_to_the_module_their_guard_checks():
    assert mc.requirement_for(_by_id("ad_fsmo")).key == "ad"
    assert mc.requirement_for(_by_id("dns_zones")).key == "dns"
    assert mc.requirement_for(_by_id("hv_vms")).key == "hyperv"
    assert mc.requirement_for(_by_id("gpo_list")).key == "gpo"
    assert mc.requirement_for(_by_id("uptime")) is None          # needs nothing extra


def test_every_guarded_server_script_that_needs_a_module_is_mapped():
    """A guard on an RSAT/feature cmdlet must resolve to a requirement, or the
    pane could not offer to install it."""
    rsat_prefixes = ("Get-AD", "Get-GP", "Get-DnsServer", "Get-Dhcp", "Get-Cluster", "Get-Fsrm",
                     "Get-Dfsn", "Get-Dfsr", "Get-VM", "Get-Website", "Get-RD", "Get-Wsus")
    for s in lib.built_in():
        guards = mc._GUARD.findall(s.body)
        if any(g.startswith(rsat_prefixes) for g in guards):
            assert mc.requirement_for(s) is not None, s.id


def test_detect_parses_the_answer_and_never_invents_a_value():
    def run(argv):
        return 0, "ad=1\ndns=0\nserver=0\nnoise line\nhyperv=maybe\n"
    status, server = mc.detect(run)
    assert status == {"ad": True, "dns": False} and server is False   # hyperv: unknown, not False


def test_detect_survives_powershell_not_starting():
    def run(argv):
        raise OSError("refused")
    assert mc.detect(run) == ({}, False)


def test_install_commands_use_windows_own_mechanisms():
    ad = mc.REQUIREMENTS["ad"]
    assert mc.install_command(ad, server=False) == (
        "Add-WindowsCapability -Online -Name 'Rsat.ActiveDirectory.DS-LDS.Tools~~~~0.0.1.0'")
    assert mc.install_command(ad, server=True) == (
        "Install-WindowsFeature -Name 'RSAT-AD-PowerShell', 'RSAT-ADDS-Tools' -IncludeManagementTools")
    hv = mc.install_command(mc.REQUIREMENTS["hyperv"], server=False)
    assert hv.startswith("Enable-WindowsOptionalFeature -Online") and "-NoRestart" in hv


def test_server_roles_are_not_offered_on_a_client():
    dedup = mc.REQUIREMENTS["dedup"]
    assert mc.install_command(dedup, server=False) is None
    assert "server role" in mc.why_not_installable(dedup, server=False)
    assert "FS-Data-Deduplication" in mc.install_command(dedup, server=True)


def test_capability_names_have_the_fod_suffix():
    for r in mc.REQUIREMENTS.values():
        if r.client_kind == mc.CAPABILITY:
            assert all(n.startswith("Rsat.") and n.endswith("~~~~0.0.1.0") for n in r.client_names)
