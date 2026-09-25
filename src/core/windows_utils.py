# src/core/windows_utils.py
import logging
import os
import winreg

logger = logging.getLogger(__name__)


def ps_quote(value: str) -> str:
    """Escape a value for a PowerShell single-quoted string literal.

    PowerShell treats two adjacent quotes inside a single-quoted string as a
    literal quote ('it''s'), so doubling the quote is the whole escape. Every
    package/service/identifier that ends up interpolated into a
    ``powershell -Command "... '{value}' ..."`` line should go through this.
    """
    return str(value).replace("'", "''")


def is_reboot_pending() -> bool:
    """Check all three Windows reboot-pending indicators."""
    keys = [
        (winreg.HKEY_LOCAL_MACHINE,
         r"SYSTEM\CurrentControlSet\Control\Session Manager",
         "PendingFileRenameOperations"),
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing",
         "RebootPending"),
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update",
         "RebootRequired"),
    ]
    for hive, path, value in keys:
        try:
            with winreg.OpenKey(hive, path) as k:
                winreg.QueryValueEx(k, value)
                return True
        except OSError:
            logger.debug("is_reboot_pending: skipping an item that could not be read", exc_info=True)
            continue
    return False


# ── Well-known directories ─────────────────────────────────────────────
#
# Windows does not have to be on C:. It usually is, which is exactly why
# hardcoding it survives: the scanner that assumes C:\Windows finds nothing
# on a machine where Windows is on D:, reports "0 B to clean", and never
# says it looked in the wrong place. There were 136 such literals when this
# was written.
#
# The fallbacks are the conventional locations, used only when the variable
# is missing entirely — which on a healthy Windows install it is not.

def system_root() -> str:
    r"""The Windows directory, e.g. C:\Windows."""
    return os.environ.get("SystemRoot") or os.environ.get("windir") or r"C:\Windows"


def system_drive() -> str:
    r"""The drive Windows is installed on, e.g. C:."""
    return os.environ.get("SystemDrive") or os.path.splitdrive(system_root())[0] or "C:"


def program_files(x86: bool = False) -> str:
    r"""Program Files, or Program Files (x86) when `x86` is set."""
    if x86:
        return (os.environ.get("ProgramFiles(x86)")
                or os.path.join(system_drive() + os.sep, "Program Files (x86)"))
    return (os.environ.get("ProgramFiles")
            or os.path.join(system_drive() + os.sep, "Program Files"))


def program_data() -> str:
    r"""The all-users application data directory, e.g. C:\ProgramData."""
    return (os.environ.get("ProgramData")
            or os.environ.get("ALLUSERSPROFILE")
            or os.path.join(system_drive() + os.sep, "ProgramData"))


def system32() -> str:
    r"""The 64-bit system directory, e.g. C:\Windows\System32."""
    return os.path.join(system_root(), "System32")


def format_windows_name(product_name: str, display_version: str,
                        build: str, ubr: str) -> str:
    """"Windows 11 Pro 25H2 (build 26200.9550)" from the raw registry values.

    `ProductName` still says "Windows 10 ..." on Windows 11 (it never changed),
    so the major version comes from the build number: 22000 and above is 11.
    Whatever the registry gave is used as-is when it cannot be interpreted."""
    product_name = (product_name or "").strip()
    try:
        build_number = int(build)
    except (TypeError, ValueError):
        return product_name or "Windows"
    major = "11" if build_number >= 22000 else "10"
    edition = product_name
    for prefix in ("Windows 10 ", "Windows 11 "):
        if edition.startswith(prefix):
            edition = edition[len(prefix):]
    name = f"Windows {major} {edition}".strip()
    if display_version:
        name += f" {display_version}"
    build_text = f"{build_number}.{ubr}" if str(ubr or "").strip() else str(build_number)
    return f"{name} (build {build_text})"


def windows_display_name() -> str:
    """This machine's Windows name, e.g. "Windows 11 Pro 25H2 (build 26200.9550)"."""
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SOFTWARE\Microsoft\Windows NT\CurrentVersion") as key:
            def value(name):
                try:
                    return str(winreg.QueryValueEx(key, name)[0])
                except OSError:
                    return ""
            return format_windows_name(value("ProductName"), value("DisplayVersion"),
                                       value("CurrentBuild"), value("UBR"))
    except OSError:
        logger.warning("Could not read the Windows version from the registry",
                       exc_info=True)
        import platform
        return f"{platform.system()} {platform.release()}"


def cpu_brand_name() -> str:
    """"AMD Ryzen 9 9950X3D 16-Core Processor", not "AMD64 Family 26 Model 68
    Stepping 0, AuthenticAMD" (which is what `platform.processor()` returns)."""
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
            name = str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
            if name:
                return " ".join(name.split())
    except OSError:
        logger.warning("Could not read the CPU name from the registry", exc_info=True)
    import platform
    return platform.processor()
