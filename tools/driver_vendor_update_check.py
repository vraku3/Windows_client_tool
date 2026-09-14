"""Real-machine harness for the vendor-update pipeline (Task 11 of Phase 1).

Default mode is READ-ONLY: checks every currently-installed device against
its vendor provider (if one exists) and prints what it finds, downloading
nothing. Pass --download to also download and signature-verify (never
install) the first available update found, as a real end-to-end proof the
pipeline's download+verify half works against a live NVIDIA response.

Never installs anything -- that's the point of it being separate from the
app's own confirm-gated UI action.
"""
import argparse
import sys

sys.path.insert(0, "src")

from modules.driver_manager.driver_reader import fetch_drivers
from modules.driver_manager.vendor_updates.provider import provider_for, no_provider_reason
from modules.driver_manager.vendor_updates import nvidia_provider  # noqa: F401 -- registers NVIDIA
from modules.driver_manager.vendor_updates import amd_provider  # noqa: F401 -- registers AMD
from modules.driver_manager.vendor_updates import realtek_provider  # noqa: F401 -- registers Realtek
from modules.driver_manager.vendor_updates.pipeline import download_and_verify


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--download", action="store_true",
                        help="also download+verify (never install) the "
                             "first available update found")
    args = parser.parse_args()

    drivers = fetch_drivers()
    print(f"{len(drivers)} drivers found.\n")

    any_update_found = False
    for driver in drivers:
        provider = provider_for(driver)
        if provider is None:
            reason = no_provider_reason(driver)
            print(f"[{driver.device_name}] no check possible: {reason.value if reason else 'unknown'}")
            continue
        try:
            update = provider.check_for_update(driver)
        except Exception as exc:
            print(f"[{driver.device_name}] ({provider.vendor_name}): "
                 f"check failed unexpectedly: {exc}")
            continue
        if update is None:
            print(f"[{driver.device_name}] ({provider.vendor_name}): "
                 f"no update available (current: {driver.version})")
            continue
        print(f"[{driver.device_name}] ({provider.vendor_name}): "
             f"UPDATE AVAILABLE {driver.version} -> {update.latest_version} "
             f"({update.download_url})")
        if args.download and not any_update_found:
            any_update_found = True
            print("  Downloading and verifying (will NOT install)...")
            result = download_and_verify(
                update, allowed_domains=provider.allowed_download_domains,
                extra_headers=getattr(provider, "download_headers", None))
            if result.path:
                print(f"  Downloaded and signature-verified OK: {result.path}")
            else:
                print(f"  FAILED: {result.reason}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
