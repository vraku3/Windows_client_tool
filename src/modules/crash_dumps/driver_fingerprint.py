"""Driver culprit fingerprinting for Windows crash dumps, live kernel reports, and bugchecks.

Identifies, classifies, and fingerprints third-party .sys drivers vs Microsoft inbox components,
extracting culprits from WER fault buckets, Event 1001 problem signatures, dump strings,
and crash parameters. Qt-free.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Dict, Optional, Set, Tuple

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DriverInfo:
    filename: str
    vendor: str
    description: str
    category: str  # "Graphics (GPU)", "Network / Wi-Fi", "Storage", "Audio", "Motherboard / RGB", "Security / Antivirus", "Virtualization", "System"
    remedy: str = ""


@dataclass
class CulpritInfo:
    filename: str
    is_third_party: bool
    vendor: str = ""
    description: str = ""
    category: str = "Unknown"
    remedy: str = ""

    @property
    def display_tag(self) -> str:
        """Short label for display in table rows and summary lines."""
        if self.is_third_party:
            vendor_str = f" ({self.vendor})" if self.vendor else ""
            return f"{self.filename}{vendor_str}"
        return self.filename


#: Microsoft Windows core kernel and inbox drivers that are part of the base OS.
#: When a crash names one of these, it is almost always the VICTIM of a third-party
#: driver or memory corruption, not an OS bug.
MICROSOFT_INBOX_DRIVERS: Set[str] = {
    # Core kernel / HAL / Debugger
    "ntoskrnl.exe", "ntkrnlmp.exe", "hal.dll", "kd.dll", "kdcom.dll", "mcupdate.dll",
    "bootvid.dll", "ci.dll", "pshed.dll", "werkernel.sys", "crashdmp.sys",
    
    # Core filesystem & volume management
    "ntfs.sys", "fltmgr.sys", "fastfat.sys", "refs.sys", "volmgr.sys", "volmgrx.sys",
    "volsnap.sys", "partmgr.sys", "disk.sys", "classpnp.sys", "fileinfo.sys",
    "luafv.sys", "wcifs.sys", "bindflt.sys", "cldflt.sys", "iorate.sys", "mountmgr.sys",
    
    # Core storage controllers (inbox)
    "storport.sys", "storahci.sys", "stornvme.sys", "spaceport.sys", "dump_diskdump.sys",
    "dump_storahci.sys", "dump_storport.sys", "dump_stornvme.sys", "atapi.sys",
    
    # Core buses & ACPI
    "acpi.sys", "pci.sys", "msisadrv.sys", "vmbus.sys", "isapnp.sys", "pciide.sys",
    
    # Core networking
    "tcpip.sys", "ndis.sys", "netio.sys", "fwpkclnt.sys", "wfplwfs.sys", "http.sys",
    "bowser.sys", "mrxsmb.sys", "mrxsmb10.sys", "mrxsmb20.sys", "wtd.sys", "vwififlt.sys",
    "nwifi.sys", "ndiswan.sys", "raspppoe.sys", "raspptp.sys", "rassstp.sys",
    
    # Core driver frameworks & cryptography
    "wdf01000.sys", "wdfldr.sys", "cng.sys", "ksecdd.sys", "ksecpkg.sys", "clfs.sys",
    "pdc.sys", "mssecflt.sys", "wdigest.sys",
    
    # Core graphics infrastructure (victim wrappers)
    "watchdog.sys", "dxgkrnl.sys", "dxgmms1.sys", "dxgmms2.sys", "basicdisplay.sys",
    "basicrender.sys", "cdd.dll", "win32k.sys", "win32kbase.sys", "win32kfull.sys",
    
    # Core USB & HID
    "usbport.sys", "usbstor.sys", "usbhub.sys", "usbhub3.sys", "usbxhci.sys", "usbccgp.sys",
    "hidusb.sys", "hidclass.sys", "hidparse.sys", "kbdclass.sys", "mouclass.sys",
    
    # Core Audio
    "ksthunk.sys", "portcls.sys", "drmk.sys", "hdaudbus.sys", "ks.sys",
}

#: Database of well-known third-party Windows drivers frequently implicated in BSODs.
KNOWN_DRIVERS: Dict[str, DriverInfo] = {
    # --- Graphics / GPU Drivers ---
    "nvlddmkm.sys": DriverInfo(
        "nvlddmkm.sys", "NVIDIA", "NVIDIA Windows Kernel Display Driver", "Graphics (GPU)",
        "Perform a clean reinstall of NVIDIA drivers using Display Driver Uninstaller (DDU) in Safe Mode, then install the latest Game Ready or Studio driver."
    ),
    "nvhda64v.sys": DriverInfo(
        "nvhda64v.sys", "NVIDIA", "NVIDIA High Definition Audio Driver", "Audio",
        "Update or reinstall NVIDIA HD Audio component via NVIDIA driver installer."
    ),
    "amdkmdag.sys": DriverInfo(
        "amdkmdag.sys", "AMD", "AMD Radeon Kernel Mode Graphics Driver", "Graphics (GPU)",
        "Use AMD Cleanup Utility or DDU in Safe Mode, then reinstall the latest WHQL AMD Adrenalin driver suite."
    ),
    "atikmdag.sys": DriverInfo(
        "atikmdag.sys", "AMD", "ATI / AMD Radeon Kernel Graphics Driver", "Graphics (GPU)",
        "Reinstall latest AMD Radeon display drivers; check GPU core/memory clock stability."
    ),
    "amdkmafd.sys": DriverInfo(
        "amdkmafd.sys", "AMD", "AMD Audio Co-Processor / Bus Driver", "Audio",
        "Update AMD chipset and graphics drivers."
    ),
    "igdkmd64.sys": DriverInfo(
        "igdkmd64.sys", "Intel", "Intel Graphics Kernel Mode Driver", "Graphics (GPU)",
        "Update Intel UHD/Iris Xe/Arc graphics drivers from Intel Driver & Support Assistant (DSA)."
    ),
    "igdgmr64.sys": DriverInfo(
        "igdgmr64.sys", "Intel", "Intel Arc Graphics Kernel Driver", "Graphics (GPU)",
        "Update Intel Arc graphics drivers from Intel DSA."
    ),

    # --- Network & Wi-Fi Drivers ---
    "rtwlane01.sys": DriverInfo(
        "rtwlane01.sys", "Realtek", "Realtek Wireless LAN Driver", "Network / Wi-Fi",
        "Update Realtek Wi-Fi adapter driver from motherboard or OEM manufacturer support page."
    ),
    "rtwlane601.sys": DriverInfo(
        "rtwlane601.sys", "Realtek", "Realtek 802.11ax Wi-Fi 6 Adapter Driver", "Network / Wi-Fi",
        "Update Realtek Wi-Fi 6 drivers; disable power-saving 'Allow the computer to turn off this device'."
    ),
    "rtwlanu.sys": DriverInfo(
        "rtwlanu.sys", "Realtek", "Realtek USB Wireless LAN Driver", "Network / Wi-Fi",
        "Update Realtek USB Wi-Fi adapter drivers; test another USB port or 5GHz network band."
    ),
    "rt640x64.sys": DriverInfo(
        "rt640x64.sys", "Realtek", "Realtek PCIe GbE Family Controller", "Network (Ethernet)",
        "Install latest Realtek Ethernet PCIe controller driver from Realtek support."
    ),
    "rtx64x64.sys": DriverInfo(
        "rtx64x64.sys", "Realtek", "Realtek 2.5GbE Gaming Ethernet Family Controller", "Network (Ethernet)",
        "Update Realtek 2.5G network driver; disable Energy Efficient Ethernet (EEE) in adapter properties."
    ),
    "netwtw08.sys": DriverInfo(
        "netwtw08.sys", "Intel", "Intel Wi-Fi Adapter Driver (Wi-Fi 6 AX200 series)", "Network / Wi-Fi",
        "Update Intel PROSet/Wireless Wi-Fi drivers via Intel Driver & Support Assistant."
    ),
    "netwtw10.sys": DriverInfo(
        "netwtw10.sys", "Intel", "Intel Wi-Fi Adapter Driver (Wi-Fi 6E AX210 / BE200 series)", "Network / Wi-Fi",
        "Update Intel PROSet/Wireless Wi-Fi drivers to latest release."
    ),
    "netwtw12.sys": DriverInfo(
        "netwtw12.sys", "Intel", "Intel Wi-Fi 7 Adapter Driver", "Network / Wi-Fi",
        "Update Intel Wi-Fi 7 drivers to the latest firmware release."
    ),
    "e1d68x64.sys": DriverInfo(
        "e1d68x64.sys", "Intel", "Intel Gigabit Network Connection Driver", "Network (Ethernet)",
        "Update Intel Ethernet controller drivers."
    ),
    "e2fnexpress.sys": DriverInfo(
        "e2fnexpress.sys", "Intel", "Intel Ethernet Controller I225-V / I226-V (2.5GbE)", "Network (Ethernet)",
        "Update Intel I225/I226-V firmware and drivers; disable Ultra Low Power mode if disconnects persist."
    ),
    "e2xw64.sys": DriverInfo(
        "e2xw64.sys", "Killer / Intel", "Killer Performance Suite Ethernet Driver", "Network (Ethernet)",
        "Uninstall Killer Control Center and install bare Intel/Killer INF drivers without software suite."
    ),
    "qca106x64.sys": DriverInfo(
        "qca106x64.sys", "Qualcomm", "Qualcomm Atheros Wireless Network Adapter", "Network / Wi-Fi",
        "Update Qualcomm Wi-Fi adapter driver."
    ),
    "mtkwl6ex.sys": DriverInfo(
        "mtkwl6ex.sys", "MediaTek", "MediaTek Wi-Fi 6/6E Wireless Adapter", "Network / Wi-Fi",
        "Update MediaTek Wi-Fi drivers from motherboard/laptop OEM."
    ),

    # --- Storage & Chipset Drivers ---
    "iastor.sys": DriverInfo(
        "iastor.sys", "Intel", "Intel Rapid Storage Technology (RST) Driver", "Storage",
        "Update Intel RST storage controller drivers or switch SATA/NVMe mode in BIOS."
    ),
    "iastora.sys": DriverInfo(
        "iastora.sys", "Intel", "Intel Rapid Storage Technology (RST) RAID Driver", "Storage",
        "Update Intel RST drivers or disable RST VMD in BIOS if operating single NVMe drives."
    ),
    "iastorvd.sys": DriverInfo(
        "iastorvd.sys", "Intel", "Intel Volume Management Device (VMD) Driver", "Storage",
        "Update Intel VMD driver or install chipset driver package."
    ),
    "amdsata.sys": DriverInfo(
        "amdsata.sys", "AMD", "AMD SATA Controller Driver", "Storage",
        "Revert to Microsoft standard SATA AHCI controller driver (storahci.sys) or update AMD Chipset."
    ),
    "amdxata.sys": DriverInfo(
        "amdxata.sys", "AMD", "AMD Storage Controller Driver", "Storage",
        "Update AMD Chipset driver suite."
    ),
    "secnvme.sys": DriverInfo(
        "secnvme.sys", "Samsung", "Samsung NVMe Controller Driver", "Storage",
        "Update Samsung NVMe driver via Samsung Magician or switch to Microsoft inbox stornvme.sys."
    ),

    # --- Motherboard, RGB & Hardware Monitoring Utilities ---
    "ene.sys": DriverInfo(
        "ene.sys", "ENE Technology", "ENE RGB LED / DRAM Controller Driver", "Motherboard / RGB",
        "Commonly installed by ASUS Armoury Crate, Corsair iCUE, or MSI Mystic Light. Update or uninstall RGB software."
    ),
    "eneio.sys": DriverInfo(
        "eneio.sys", "ENE Technology", "ENE IO Access Driver", "Motherboard / RGB",
        "Known crash culprit in third-party RGB synchronization software; uninstall RGB sync utilities."
    ),
    "asusgio3.sys": DriverInfo(
        "asusgio3.sys", "ASUS", "ASUS ASUSTeK Motherboard IO Control Driver", "Motherboard / RGB",
        "Part of ASUS Armoury Crate / AI Suite. Update or uninstall ASUS software."
    ),
    "asio3.sys": DriverInfo(
        "asio3.sys", "ASUS", "ASUS ASUSTeK Motherboard Low-Level Driver", "Motherboard / RGB",
        "Update ASUS Armoury Crate or remove ASUS AI Suite."
    ),
    "corsairllaservice.sys": DriverInfo(
        "corsairllaservice.sys", "Corsair", "Corsair LLAService / iCUE Kernel Driver", "Motherboard / RGB",
        "Update Corsair iCUE software to the latest release or reinstall iCUE."
    ),
    "corsairvhid.sys": DriverInfo(
        "corsairvhid.sys", "Corsair", "Corsair Virtual HID Driver", "Peripheral Utility",
        "Update Corsair iCUE device drivers."
    ),
    "steelseries.sys": DriverInfo(
        "steelseries.sys", "SteelSeries", "SteelSeries Engine Peripheral Driver", "Peripheral Utility",
        "Update SteelSeries GG / Engine software."
    ),
    "rzendpt.sys": DriverInfo(
        "rzendpt.sys", "Razer", "Razer Synapse Audio Endpoint Driver", "Peripheral Utility",
        "Update Razer Synapse software or uninstall Razer virtual audio drivers."
    ),
    "cpuz157_x64.sys": DriverInfo(
        "cpuz157_x64.sys", "CPUID", "CPU-Z Hardware Diagnostic Kernel Driver", "Hardware Diagnostic",
        "Update CPU-Z or any software packaging CPUID drivers (HWMonitor, NZXT CAM)."
    ),

    # --- Audio Drivers ---
    "rtkvhd64.sys": DriverInfo(
        "rtkvhd64.sys", "Realtek", "Realtek High Definition Audio Driver", "Audio",
        "Update Realtek High Definition Audio drivers from motherboard OEM."
    ),

    # --- Antivirus & Security Filters ---
    "bdss.sys": DriverInfo(
        "bdss.sys", "Bitdefender", "Bitdefender Storage Filter Driver", "Security / Antivirus",
        "Update Bitdefender security suite or run the Bitdefender uninstall tool."
    ),
    "epfw.sys": DriverInfo(
        "epfw.sys", "ESET", "ESET Personal Firewall Kernel Driver", "Security / Antivirus",
        "Update ESET security products to the latest version."
    ),
    "edpa.sys": DriverInfo(
        "edpa.sys", "CrowdStrike", "CrowdStrike Falcon Sensor Driver", "Security / Antivirus",
        "Inspect CrowdStrike Falcon Sensor agent release notes and kernel update channels."
    ),
    "mbam.sys": DriverInfo(
        "mbam.sys", "Malwarebytes", "Malwarebytes Real-Time Protection Driver", "Security / Antivirus",
        "Update Malwarebytes or temporarily disable web/exploit protection to test stability."
    ),
    "farflt.sys": DriverInfo(
        "farflt.sys", "Malwarebytes", "Malwarebytes Ransomware Protection Filter", "Security / Antivirus",
        "Update Malwarebytes application suite."
    ),

    # --- Virtualization & Emulation ---
    "vboxdrv.sys": DriverInfo(
        "vboxdrv.sys", "Oracle", "Oracle VirtualBox Host Interface Driver", "Virtualization",
        "Update Oracle VirtualBox to the latest release or disable Hyper-V coexistence."
    ),
    "vboxnetflt.sys": DriverInfo(
        "vboxnetflt.sys", "Oracle", "Oracle VirtualBox Bridged Networking Driver", "Virtualization",
        "Update Oracle VirtualBox."
    ),
    "vmnet.sys": DriverInfo(
        "vmnet.sys", "VMware", "VMware Virtual Ethernet Adapter Driver", "Virtualization",
        "Update VMware Workstation / Player."
    ),

    # --- Anti-Cheat Drivers ---
    "easyanticheat.sys": DriverInfo(
        "easyanticheat.sys", "Epic Games", "Easy Anti-Cheat Kernel Driver", "Gaming Anti-Cheat",
        "Verify game file integrity via Steam/Epic Games launcher; repair Easy Anti-Cheat service."
    ),
    "easyanticheat_eos.sys": DriverInfo(
        "easyanticheat_eos.sys", "Epic Games", "Easy Anti-Cheat EOS Kernel Driver", "Gaming Anti-Cheat",
        "Repair Easy Anti-Cheat from the game installation directory."
    ),
    "vgk.sys": DriverInfo(
        "vgk.sys", "Riot Games", "Riot Vanguard Kernel Anti-Cheat Driver", "Gaming Anti-Cheat",
        "Reinstall Riot Vanguard; ensure TPM 2.0 and Secure Boot are enabled in BIOS."
    ),
}


def is_third_party_driver(filename: str) -> bool:
    """Return True if the driver filename represents a third-party non-Microsoft inbox driver."""
    clean = (os.path.basename(filename) if filename else "").strip().lower()
    if not clean:
        return False
    if clean.endswith(".sys") and clean not in MICROSOFT_INBOX_DRIVERS:
        return True
    return False


def get_driver_info(filename: str) -> Optional[DriverInfo]:
    """Lookup known vendor and descriptive info for a driver filename."""
    clean = (os.path.basename(filename) if filename else "").strip().lower()
    return KNOWN_DRIVERS.get(clean)


def extract_driver_culprit(text_or_data: str | dict) -> Optional[CulpritInfo]:
    """Fingerprint suspect driver culprit from WER bucket text, crash message, or structured data.
    
    Extracts third-party .sys drivers cleanly from:
    1. WER Bucket patterns (e.g. LKD_0x117_Tdr:3_TdrVTR:2_IMAGE_amdkmdag.sys, AV_nvlddmkm.sys)
    2. Problem signature P9 fields
    3. Error text / stack unwind references
    4. Structured raw dicts containing faulting_module or bucket names
    """
    raw_text = ""
    if isinstance(text_or_data, dict):
        # Check direct fields first
        for key in ("faulting_module", "culprit_driver", "driver", "bucket", "Bucket"):
            val = text_or_data.get(key)
            if val and isinstance(val, str):
                c = extract_driver_culprit(val)
                if c:
                    return c
        raw_text = " ".join(str(v) for v in text_or_data.values() if v)
    else:
        raw_text = str(text_or_data or "")

    if not raw_text:
        return None

    # Priority 1: IMAGE_<driver>.sys or AV_<driver>.sys in WER Bucket signatures
    m = re.search(r"\b(?:IMAGE_|AV_|0x[0-9a-fA-F]+_[A-Za-z0-9]+_)([a-zA-Z0-9_\-]+\.sys)\b", raw_text, re.IGNORECASE)
    if m:
        drv = m.group(1).lower()
        info = get_driver_info(drv)
        return CulpritInfo(
            filename=drv,
            is_third_party=is_third_party_driver(drv),
            vendor=info.vendor if info else "",
            description=info.description if info else ("Third-party kernel driver" if is_third_party_driver(drv) else "Windows kernel component"),
            category=info.category if info else ("Third-Party Driver" if is_third_party_driver(drv) else "System Component"),
            remedy=info.remedy if info else ("Update or reinstall the associated driver software." if is_third_party_driver(drv) else ""),
        )

    # Priority 2: Problem signature P9: <driver>.sys or Faulting driver: <driver>.sys
    m = re.search(r"(?:P9|faulting\s*(?:module|driver)|driver)[:\s=]+([a-zA-Z0-9_\-]+\.sys)\b", raw_text, re.IGNORECASE)
    if m:
        drv = m.group(1).lower()
        info = get_driver_info(drv)
        return CulpritInfo(
            filename=drv,
            is_third_party=is_third_party_driver(drv),
            vendor=info.vendor if info else "",
            description=info.description if info else ("Third-party kernel driver" if is_third_party_driver(drv) else "Windows kernel component"),
            category=info.category if info else ("Third-Party Driver" if is_third_party_driver(drv) else "System Component"),
            remedy=info.remedy if info else ("Update or reinstall the associated driver software." if is_third_party_driver(drv) else ""),
        )

    # Priority 3: Check for any known third-party driver name in the text
    lower = raw_text.lower()
    for drv_name, info in KNOWN_DRIVERS.items():
        if drv_name in lower:
            return CulpritInfo(
                filename=drv_name,
                is_third_party=True,
                vendor=info.vendor,
                description=info.description,
                category=info.category,
                remedy=info.remedy,
            )

    # Priority 4: Any standalone third-party .sys pattern
    matches = re.findall(r"\b([a-zA-Z0-9_\-]+\.sys)\b", raw_text, re.IGNORECASE)
    for m_drv in matches:
        drv = m_drv.lower()
        if is_third_party_driver(drv):
            info = get_driver_info(drv)
            return CulpritInfo(
                filename=drv,
                is_third_party=True,
                vendor=info.vendor if info else "",
                description=info.description if info else "Third-party kernel driver",
                category=info.category if info else "Third-Party Driver",
                remedy=info.remedy if info else "Update or reinstall the associated driver software.",
            )

    # Priority 5: Fallback to Microsoft inbox driver if explicitly named as faulting module
    if matches:
        drv = matches[0].lower()
        return CulpritInfo(
            filename=drv,
            is_third_party=False,
            vendor="Microsoft",
            description="Windows OS core component (usually a victim rather than root cause)",
            category="System Component",
            remedy="Check for hardware faults (RAM/Disk) or third-party drivers corrupting kernel memory.",
        )

    return None
