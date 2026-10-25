"""Trusted Windows executable paths, independent of Qt and the environment."""
import ctypes
import os


def cmd_exe() -> str:
    """Command Prompt by trusted absolute path, without PATH or ComSpec lookup."""
    buf = ctypes.create_unicode_buffer(260)
    length = ctypes.windll.kernel32.GetSystemDirectoryW(buf, len(buf))
    if not 0 < length < len(buf):
        raise OSError("the System32 folder could not be read")
    path = os.path.join(buf.value, "cmd.exe")
    if not os.path.isfile(path):
        raise OSError(f"Command Prompt not found at {path}")
    return path


def powershell_exe() -> str:
    """Windows PowerShell by ABSOLUTE path, from GetSystemDirectoryW.

    A bare "powershell.exe" is searched for in the launching program's own folder and the
    current directory before System32 (CreateProcessW's documented order). The portable
    exe runs elevated from a user-writable folder, so a planted powershell.exe beside it
    would run as administrator. %SystemRoot% is not trusted either: a process can inherit
    an altered environment."""
    buf = ctypes.create_unicode_buffer(260)
    length = ctypes.windll.kernel32.GetSystemDirectoryW(buf, len(buf))
    if not 0 < length < len(buf):
        raise OSError("the System32 folder could not be read")
    path = os.path.join(buf.value, "WindowsPowerShell", "v1.0", "powershell.exe")
    if not os.path.isfile(path):
        raise OSError(f"Windows PowerShell not found at {path}")
    return path
