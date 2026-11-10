import os
import shutil
import glob

app_dir = r"C:\Users\iorda\OneDrive\1 Personal\Aplicații"
dest = os.path.join(app_dir, "WinClientTool-Portable.exe")
share = os.path.join(app_dir, "Windows Client Tool Share")
share_old = os.path.join(share, "old")
dist_exe = r"dist\WinClientTool-Portable.exe"

try:
    os.makedirs(share_old, exist_ok=True)

    current_share = os.path.join(share, "WinClientTool-Portable.exe")
    if os.path.exists(current_share):
        old_target = os.path.join(share_old, "WinClientTool-Portable.exe")
        shutil.copy2(current_share, old_target)

    shutil.copy2(dist_exe, dest)
    shutil.copy2(dist_exe, current_share)

    for f in glob.glob(os.path.join(share_old, "*.exe_old")) + glob.glob(os.path.join(share_old, "*-new.exe")):
        try:
            os.remove(f)
        except OSError:
            pass

    print(f"Deployed to {dest}")
    print(f"Rotated share: {current_share} and old copy in {share_old}")
except PermissionError:
    print(f"Build succeeded at {dist_exe} (outside sandbox path {app_dir} is restricted).")

