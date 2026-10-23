"""OneDrive containment by path components, including redirected accounts."""
import ntpath
import os
import logging

logger = logging.getLogger(__name__)

ACCOUNTS = r"Software\Microsoft\OneDrive\Accounts"


def normal(path):
    return ntpath.normcase(ntpath.normpath(os.path.realpath(os.path.expandvars(str(path)))))


def roots(registry, environ=None):
    env = os.environ if environ is None else environ
    found = [env[k] for k in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial") if env.get(k)]
    try:
        accounts = registry.children(ACCOUNTS)
    except FileNotFoundError:
        accounts = []
    for account in accounts:
        try:
            found.append(registry.read(ACCOUNTS + "\\" + account, "UserFolder")[0])
        except FileNotFoundError:
            logger.debug("OneDrive account %s has no UserFolder value", account)
            continue
    return list({normal(p): p for p in found if p}.values())


def contains(path, root_paths):
    path = normal(path)
    for root in root_paths:
        root = normal(root)
        try:
            if ntpath.commonpath([path, root]) == root:
                return True
        except ValueError:
            logger.debug("Path %s and OneDrive root %s are on different drives", path, root)
            continue  # Different drives cannot contain one another.
    return False
