import datetime
import logging
import platform
import socket

import psutil

logger = logging.getLogger(__name__)



# Win32_Processor.Architecture is a number; showing "9" told nobody anything.
_CPU_ARCHITECTURES = {0: "x86", 1: "MIPS", 2: "Alpha", 3: "PowerPC", 5: "ARM",
                      6: "Itanium", 9: "x64 (AMD64)", 12: "ARM64"}


def cpu_architecture_name(code) -> str:
    if code is None:
        return ""
    try:
        return _CPU_ARCHITECTURES.get(int(code), f"Unknown ({code})")
    except (TypeError, ValueError):
        return str(code)


def _wmi():
    import wmi
    return wmi.WMI()


def _fmt_bytes(n):
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "N/A"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def get_overview(worker=None):
    """Returns list of (label, value) tuples."""
    rows = []
    rows.append(("Hostname", socket.gethostname()))
    from core.windows_utils import windows_display_name
    rows.append(("OS", windows_display_name()))
    uptime_secs = datetime.datetime.now().timestamp() - psutil.boot_time()
    days, rem = divmod(int(uptime_secs), 86400)
    hours, rem = divmod(rem, 3600)
    mins = rem // 60
    rows.append(("Uptime", f"{days}d {hours}h {mins}m"))
    try:
        c = _wmi()
        sys_info = c.Win32_ComputerSystem()[0]
        rows.append(("Manufacturer", sys_info.Manufacturer or ""))
        rows.append(("Model", sys_info.Model or ""))
        rows.append(("Domain", sys_info.Domain or ""))
    except Exception as e:
        logger.debug("WMI Win32_ComputerSystem unavailable: %s", e)
    try:
        cpu = _wmi().Win32_Processor()[0]
        rows.append(("CPU", cpu.Name.strip()))
    except Exception:
        logger.warning("Ignored Exception", exc_info=True)
        rows.append(("CPU", platform.processor()))
    vm = psutil.virtual_memory()
    rows.append(("RAM Total", _fmt_bytes(vm.total)))
    rows.append(("RAM Available", _fmt_bytes(vm.available)))
    total_d, free_d = 0, 0
    for part in psutil.disk_partitions(all=False):
        try:
            usage = psutil.disk_usage(part.mountpoint)
            total_d += usage.total
            free_d += usage.free
        except Exception as e:
            logger.debug("disk_usage failed for %s: %s", part.mountpoint, e)
    rows.append(("Disk Total", _fmt_bytes(total_d)))
    rows.append(("Disk Free", _fmt_bytes(free_d)))
    return rows


def get_cpu_info(worker=None):
    rows = []
    rows.append(("Physical Cores", str(psutil.cpu_count(logical=False))))
    rows.append(("Logical Cores", str(psutil.cpu_count(logical=True))))
    freq = psutil.cpu_freq()
    rows.append(("Current Freq", f"{freq.current:.0f} MHz" if freq else "N/A"))
    rows.append(("Max Freq", f"{freq.max:.0f} MHz" if freq else "N/A"))
    try:
        c = _wmi()
        cpu = c.Win32_Processor()[0]
        rows.append(("Model", cpu.Name.strip()))
        rows.append(("Socket", cpu.SocketDesignation or ""))
        rows.append(("Architecture", cpu_architecture_name(cpu.Architecture)))
        rows.append(("L2 Cache", f"{cpu.L2CacheSize} KB" if cpu.L2CacheSize else "N/A"))
        rows.append(("L3 Cache", f"{cpu.L3CacheSize} KB" if cpu.L3CacheSize else "N/A"))
    except Exception as e:
        logger.debug("WMI CPU info not available: %s", e)
    return rows


def get_memory_info(worker=None):
    vm = psutil.virtual_memory()
    summary = [
        ("Total", _fmt_bytes(vm.total)),
        ("Available", _fmt_bytes(vm.available)),
        ("Used", f"{vm.percent}%"),
    ]
    sticks = []
    try:
        c = _wmi()
        for stick in c.Win32_PhysicalMemory():
            sticks.append({
                "Bank": stick.BankLabel or "",
                "Capacity": _fmt_bytes(int(stick.Capacity)) if stick.Capacity else "N/A",
                "Speed": f"{stick.Speed} MHz" if stick.Speed else "N/A",
                "Manufacturer": stick.Manufacturer or "",
                "PartNumber": (stick.PartNumber or "").strip(),
            })
    except Exception as e:
        logger.debug("get_wmi_summary failed: %s", e)
    return summary, sticks


def get_storage_info(worker=None):
    drives = []
    try:
        c = _wmi()
        for disk in c.Win32_DiskDrive():
            drives.append({
                "Model": (disk.Model or "").strip(),
                "Size": _fmt_bytes(int(disk.Size)) if disk.Size else "N/A",
                "Interface": disk.InterfaceType or "",
                "Serial": (disk.SerialNumber or "").strip(),
                "Partitions": str(disk.Partitions),
            })
    except Exception as e:
        logger.debug("get_storage_info WMI failed: %s", e)
    partitions = []
    for part in psutil.disk_partitions(all=False):
        try:
            usage = psutil.disk_usage(part.mountpoint)
            partitions.append({
                "Mount": part.mountpoint,
                "FS": part.fstype,
                "Total": _fmt_bytes(usage.total),
                "Used": _fmt_bytes(usage.used),
                "Free": _fmt_bytes(usage.free),
                "Use%": f"{usage.percent}%",
            })
        except Exception as e:
            logger.debug("disk_usage failed for %s: %s", part.mountpoint, e)
    return drives, partitions


def wmi_date_text(value) -> str:
    """'20221202000000.000000+000' (or a datetime) as 2022-12-02."""
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d")
    text = str(value or "")[:8]
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return text


def video_mode_text(desc) -> str:
    """'2560 x 1440 x 4294967296 colors' -> '2560 x 1440'; the colour count
    is a raw enum-ish number nobody can read."""
    text = str(desc or "")
    parts = text.split(" x ")
    if len(parts) >= 3:
        return " x ".join(parts[:2])
    return text


def _registry_vram(name):
    """Dedicated video memory from the display class key (64-bit, so it is
    right above 4 GB, where Win32_VideoController.AdapterRAM is not)."""
    import winreg
    base = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as root:
            for i in range(64):
                try:
                    sub = winreg.EnumKey(root, i)
                except OSError:
                    break
                try:
                    with winreg.OpenKey(root, sub) as key:
                        desc = winreg.QueryValueEx(key, "DriverDesc")[0]
                        if desc != name:
                            continue
                        size = winreg.QueryValueEx(
                            key, "HardwareInformation.qwMemorySize")[0]
                        return int.from_bytes(size, "little") if isinstance(
                            size, (bytes, bytearray)) else int(size)
                except OSError:
                    continue
    except OSError:
        pass
    return None


def gpu_memory_text(name, adapter_ram) -> str:
    """AdapterRAM is a uint32 that COM hands back signed, so 24 GB read as
    -1 MB. Prefer the registry's 64-bit size; else mask and say it is a floor."""
    real = _registry_vram(name)
    if real:
        return _fmt_bytes(real)
    if not adapter_ram:
        return "N/A"
    masked = int(adapter_ram) & 0xFFFFFFFF
    return _fmt_bytes(masked) + (" (at least)" if masked >= 0xFFFFF000 else "")


def get_gpu_info(worker=None):
    gpus = []
    try:
        c = _wmi()
        for gpu in c.Win32_VideoController():
            gpus.append({
                "Name": gpu.Name or "",
                "RAM": gpu_memory_text(gpu.Name, gpu.AdapterRAM),
                "Driver Version": gpu.DriverVersion or "",
                "Driver Date": wmi_date_text(gpu.DriverDate),
                "Resolution": video_mode_text(gpu.VideoModeDescription),
            })
    except Exception as e:
        logger.debug("get_gpu_info WMI failed: %s", e)
    return gpus


def get_network_info(worker=None):
    adapters = []
    stats = psutil.net_if_stats()
    addrs = psutil.net_if_addrs()
    for name, addr_list in addrs.items():
        ip = mac = ""
        for a in addr_list:
            family_name = a.family.name if hasattr(a.family, "name") else str(a.family)
            if family_name == "AF_INET":
                ip = a.address
            if family_name in ("AF_PACKET", "AF_LINK") or "AF_LINK" in str(a.family):
                mac = a.address
        stat = stats.get(name)
        adapters.append({
            "Name": name,
            "IP": ip,
            "MAC": mac,
            "Speed": f"{stat.speed} Mbps" if stat else "N/A",
            "Up": "Yes" if (stat and stat.isup) else "No",
        })
    return adapters


def get_bios_info(worker=None):
    rows = []
    try:
        c = _wmi()
        bios = c.Win32_BIOS()[0]
        rows.append(("Manufacturer", bios.Manufacturer or ""))
        rows.append(("Version", bios.SMBIOSBIOSVersion or ""))
        rows.append(("Release Date", wmi_date_text(bios.ReleaseDate)))
        rows.append(("Serial Number", bios.SerialNumber or ""))
    except Exception as e:
        logger.debug("WMI BIOS info not available: %s", e)
    try:
        c = _wmi()
        sys_info = c.Win32_ComputerSystem()[0]
        rows.append(("System Manufacturer", sys_info.Manufacturer or ""))
        rows.append(("System Model", sys_info.Model or ""))
    except Exception as e:
        logger.debug("WMI ComputerSystem info not available: %s", e)
    return rows


def generate_html_report():
    """Generate full HTML report string."""
    sections = {
        "Overview": get_overview(),
        "CPU": get_cpu_info(),
        "BIOS": get_bios_info(),
    }
    html = [
        "<html><head><title>Hardware Report</title>",
        "<style>body{font-family:sans-serif} table{border-collapse:collapse;width:100%}",
        "td,th{border:1px solid #ccc;padding:4px 8px} th{background:#eee}</style></head><body>",
        "<h1>Hardware Report</h1>",
        f"<p>Generated: {datetime.datetime.now()}</p>",
    ]
    for title, rows in sections.items():
        html.append(f"<h2>{title}</h2><table><tr><th>Property</th><th>Value</th></tr>")
        for k, v in rows:
            html.append(f"<tr><td>{k}</td><td>{v}</td></tr>")
        html.append("</table>")
    html.append("</body></html>")
    return "\n".join(html)
