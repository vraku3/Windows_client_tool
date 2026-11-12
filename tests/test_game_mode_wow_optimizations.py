"""Tests for World of Warcraft & max performance game mode enhancements.

Covers:
- Battle.net & World of Warcraft auto-discovery
- Game disk I/O priority boost (IoPriorityHigh) & restoration
- Background app disk I/O demotion (IoPriorityVeryLow) & restoration
- High-resolution 0.5ms timer management
- Network low-latency tuning (TcpAckFrequency & TCPNoDelay)
- MMCSS GPU and Games task priority tuning
- Real-machine WinSystem timer resolution and network adapter enumeration
"""
import os
from pathlib import Path
from unittest.mock import Mock

import pytest

from modules.game_mode.engine import catalog, game_config, game_library, session
from modules.game_mode.engine.journal import Journal
from modules.game_mode.engine.system import (
    HIGH,
    IO_PRIORITY_HIGH,
    IO_PRIORITY_NORMAL,
    IO_PRIORITY_VERY_LOW,
    NORMAL,
    Proc,
    WinSystem,
)


class WowFakeSystem:
    def __init__(self, admin=True):
        self.admin = admin
        self.plan = session.HIGH_PERFORMANCE
        self.plan_names = {session.HIGH_PERFORMANCE: "High performance"}
        self.svc = {}
        self.reg = {}
        self.broadcasts = []
        self.anim = True
        self.fullscreen = set()
        self.wifi = False
        self.procs = [
            Proc(100, 1, "Wow.exe", 8000, 1, 1000),
            Proc(101, 1, "chrome.exe", 4000, 1, 1001),
            Proc(102, 1, "discord.exe", 2500, 1, 1002),
        ]
        self.prio = {100: NORMAL, 101: NORMAL, 102: NORMAL}
        self.io_prio = {100: IO_PRIORITY_NORMAL, 101: IO_PRIORITY_NORMAL, 102: IO_PRIORITY_NORMAL}
        self.paths = {
            100: r"C:\Program Files (x86)\World of Warcraft\_forever_\Wow.exe",
            101: r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            102: r"C:\Users\User\AppData\Local\Discord\app-1.0.9000\Discord.exe",
        }
        self.timer_res = 1.0
        self.adapters = ["{89aee31e-e7d6-4110-8e95-f0b2d9a62194}"]
        self.trimmed_pids = []
        self.affinities = {100: 0xFFFFFFFF, 101: 0xFFFFFFFF, 102: 0xFFFFFFFF}

    def is_admin(self): return self.admin
    def cpu_name(self): return getattr(self, "_cpu", "AMD Ryzen 7 7800X3D 8-Core Processor")
    def get_affinity(self, pid): return self.affinities.get(pid, 0xFFFFFFFF)
    def set_affinity(self, pid, mask):
        self.affinities[pid] = mask
        return None
    def plans(self): return dict(self.plan_names)
    def active_plan(self): return self.plan
    def set_plan(self, guid): self.plan = guid; return None

    def service(self, name): return None
    def service_names(self, pattern): return []
    def running_dependents(self, name): return []
    def stop_service(self, name): return None
    def start_service(self, name): return None

    def processes(self): return list(self.procs)
    def details(self, pid):
        p = self.paths.get(pid)
        return p, (f'"{p}"' if p else None), False

    def aumid(self, pid): return None
    def priority(self, pid): return self.prio.get(pid)
    def set_priority(self, pid, value):
        self.prio[pid] = value
        return None

    def io_priority(self, pid): return self.io_prio.get(pid, IO_PRIORITY_NORMAL)
    def set_io_priority(self, pid, value):
        self.io_prio[pid] = value
        return None

    def trim_working_set(self, pid):
        self.trimmed_pids.append(pid)
        return True

    def timer_resolution(self): return self.timer_res
    def set_timer_resolution(self, period_100ns=5000):
        self.timer_res = period_100ns / 10000.0
        return None

    def cancel_timer_resolution(self, period_100ns=5000):
        self.timer_res = 1.0
        return None

    def active_network_interfaces(self): return list(self.adapters)

    def close(self, pids, exe_path):
        self.procs = [p for p in self.procs if (p.pid, p.create_time) not in pids]
        return None

    def close_many(self, jobs, grace=8.0, force=True):
        return [self.close(pids, path) for pids, path in jobs]

    def fullscreen_pids(self): return set(self.fullscreen)
    def wifi_connected(self): return self.wifi
    def alive(self, pid, ct): return any(p.pid == pid and p.create_time == ct for p in self.procs)

    def launch(self, program, args="", cwd=""): return None
    def launch_aumid(self, aumid): return None

    def reg_get(self, path, name, hive="HKCU"):
        key = (hive, path, name)
        if key in self.reg:
            val, kind = self.reg[key]
            return True, val, kind
        return False, None, 0

    def reg_set(self, path, name, value, kind, hive="HKCU"):
        self.reg[(hive, path, name)] = (value, kind)
        return None

    def reg_delete(self, path, name, hive="HKCU"):
        self.reg.pop((hive, path, name), None)
        return None

    def broadcast_setting_change(self, area):
        self.broadcasts.append(area)

    def animations(self): return self.anim
    def set_animations(self, on): self.anim = on; return None
    def tasks(self): return []


def test_battlenet_games_detection_finds_wow(tmp_path):
    """Test discovery of World of Warcraft from directories."""
    wow_dir = tmp_path / "World of Warcraft"
    forever = wow_dir / "_forever_"
    forever.mkdir(parents=True)
    fake_wow_exe = forever / "Wow.exe"
    fake_wow_exe.write_bytes(b"MZ\x00\x00")

    # Pass nonexistent db and custom extra_paths to scan
    games = game_library.scan_games(extra_paths=[str(fake_wow_exe)])
    assert any("wow" in g.exe.casefold() for g in games)


def test_game_io_priority_boost_and_restore(tmp_path):
    """Game Mode ON sets bound game's I/O priority to High, and OFF restores it."""
    sys_ = WowFakeSystem()
    journal = Journal(str(tmp_path))
    choices = session.Choices(
        bound_game="Wow.exe",
        priority_boost="high",
        game_io_priority="high",
        lower_apps_io_priority=True,
        high_resolution_timer=True,
        low_latency_network=True,
        max_mmcss=True,
        trim_background_memory=True,
        suppress_game_dvr=True,
        prefer_discrete_gpu=True,
        services=set(),
        close=set(),
    )
    session.turn_on(sys_, choices, journal, lambda *a: None, own_pid=999, session=1)
    # Wow.exe should have High I/O priority
    assert sys_.io_prio[100] == IO_PRIORITY_HIGH
    # Background apps should have Very Low I/O priority
    assert sys_.io_prio[101] == IO_PRIORITY_VERY_LOW
    assert sys_.io_prio[102] == IO_PRIORITY_VERY_LOW
    # Background apps should be trimmed to free RAM, bound game NOT trimmed
    assert 101 in sys_.trimmed_pids
    assert 102 in sys_.trimmed_pids
    assert 100 not in sys_.trimmed_pids
    # Game DVR should be suppressed & DirectFlip enabled
    dvr_fse = ("HKCU", r"System\GameConfigStore", "GameDVR_FSEBehaviorMode")
    assert sys_.reg.get(dvr_fse) == (2, 4)
    dvr_app = ("HKCU", r"Software\Microsoft\Windows\CurrentVersion\GameDVR", "AppCaptureEnabled")
    assert sys_.reg.get(dvr_app) == (0, 4)
    # Discrete GPU preference should be set in DirectX
    gpu_key = ("HKCU", r"Software\Microsoft\DirectX\UserGpuPreferences", r"C:\Program Files (x86)\World of Warcraft\_forever_\Wow.exe")
    assert sys_.reg.get(gpu_key) == ("GpuPreference=2;", 1)
    # Timer should be 0.5ms
    assert sys_.timer_res == 0.5
    # Network low latency registry should be set
    tcp_key = ("HKLM", r"SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\Interfaces\{89aee31e-e7d6-4110-8e95-f0b2d9a62194}", "TcpAckFrequency")
    assert sys_.reg.get(tcp_key) == (1, 4)
    qos_key = ("HKLM", r"SOFTWARE\Policies\Microsoft\Windows\Psched", "NonBestEffortLimit")
    assert sys_.reg.get(qos_key) == (0, 4)
    # MMCSS should be set
    mmcss_sched_key = ("HKLM", r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile\Tasks\Games", "Scheduling Category")
    assert sys_.reg.get(mmcss_sched_key) == ("High", 1)

    # Now turn off
    report = session.turn_off(sys_, journal, lambda *a: None, settle=0.1)
    assert not [r for r in report if r["level"] == session.BAD]
    # Wow.exe and background apps back to Normal
    assert sys_.io_prio[100] == IO_PRIORITY_NORMAL
    assert sys_.io_prio[101] == IO_PRIORITY_NORMAL
    assert sys_.io_prio[102] == IO_PRIORITY_NORMAL
    # Timer released
    assert sys_.timer_res == 1.0
    # Network, MMCSS, Game DVR and GPU preferences deleted/restored
    assert tcp_key not in sys_.reg
    assert qos_key not in sys_.reg
    assert mmcss_sched_key not in sys_.reg
    assert dvr_fse not in sys_.reg
    assert dvr_app not in sys_.reg
    assert gpu_key not in sys_.reg


def test_dual_ccd_x3d_isolation_and_auto_game_detect(tmp_path, monkeypatch):
    """Ryzen 9 9950X3D / 7950X3D: auto-detects Wow.exe, pins game to CCD0 (V-Cache) and background to CCD1."""
    sys_ = WowFakeSystem()
    sys_._cpu = "AMD Ryzen 9 9950X3D 16-Core Processor"
    monkeypatch.setattr(session.os, "cpu_count", lambda: 32)
    journal = Journal(str(tmp_path))

    # choices with bound_game="" and affinity_mode="off" -- auto-detection should trigger!
    choices = session.Choices(
        bound_game="",
        priority_boost="off",
        affinity_mode="off",
        game_io_priority="off",
        lower_apps_io_priority=True,
        high_resolution_timer=False,
        low_latency_network=False,
        max_mmcss=False,
        trim_background_memory=False,
        suppress_game_dvr=False,
        prefer_discrete_gpu=False,
        services=set(),
        close=set(),
    )
    session.turn_on(sys_, choices, journal, lambda *a: None, own_pid=999, session=1)

    # 32 threads: half is 16.
    # CCD0 (low 16 threads): (1 << 16) - 1 = 0x0000FFFF (65535)
    # CCD1 (high 16 threads): 0xFFFF0000 (4294901760)
    expected_ccd0 = 0x0000FFFF
    expected_ccd1 = 0xFFFF0000

    # Game (pid 100 Wow.exe) should be pinned to CCD0
    assert sys_.affinities[100] == expected_ccd0
    # Background apps (pid 101 chrome, pid 102 discord) should be isolated to CCD1
    assert sys_.affinities[101] == expected_ccd1
    assert sys_.affinities[102] == expected_ccd1

    # Wow.exe should have High CPU and High I/O priority
    assert sys_.prio[100] == HIGH
    assert sys_.io_prio[100] == IO_PRIORITY_HIGH

    # Turn off: everything must be restored cleanly!
    report = session.turn_off(sys_, journal, lambda *a: None, settle=0.1)
    assert not [r for r in report if r["level"] == session.BAD]
    assert sys_.affinities[100] == 0xFFFFFFFF
    assert sys_.affinities[101] == 0xFFFFFFFF
    assert sys_.affinities[102] == 0xFFFFFFFF
    assert sys_.prio[100] == NORMAL
    assert sys_.io_prio[100] == IO_PRIORITY_NORMAL



def test_real_system_timer_resolution_and_network_unelevated():
    """Verify real WinSystem timer resolution, memory trim and network adapter APIs work."""
    sys_ = WinSystem()
    current_res = sys_.timer_resolution()
    assert current_res is not None
    assert 0.4 <= current_res <= 20.0

    # Setting 0.5ms (5000 100ns units) succeeds even unelevated
    err = sys_.set_timer_resolution(5000)
    assert err is None
    res_after = sys_.timer_resolution()
    assert res_after is not None
    assert res_after <= 1.0

    # Releasing timer resolution succeeds
    err_cancel = sys_.cancel_timer_resolution(5000)
    assert err_cancel is None

    # Enumerating network interfaces returns a list without crashing
    adapters = sys_.active_network_interfaces()
    assert isinstance(adapters, list)

    # Trimming working set of own process succeeds
    assert sys_.trim_working_set(os.getpid()) is True
