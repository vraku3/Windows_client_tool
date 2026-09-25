import datetime
import logging
import platform
import socket

import psutil

logger = logging.getLogger(__name__)


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
        rows.append(("Architecture", str(cpu.Architecture) if cpu.Architecture is not None else ""))
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


def get_gpu_info(worker=None):
    gpus = []
    try:
        c = _wmi()
        for gpu in c.Win32_VideoController():
            gpus.append({
                "Name": gpu.Name or "",
                "RAM": _fmt_bytes(int(gpu.AdapterRAM)) if gpu.AdapterRAM else "N/A",
                "Driver Version": gpu.DriverVersion or "",
                "Driver Date": str(gpu.DriverDate or "")[:8],
                "Resolution": gpu.VideoModeDescription or "",
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
        rows.append(("Release Date", str(bios.ReleaseDate or "")[:8]))
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
