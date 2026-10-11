import subprocess
import sys
import time

from modules.cleanup.cleanup_scanner import lock_owners


def test_a_file_held_by_another_process_is_attributed_to_it(tmp_path):
    held = tmp_path / "held.log"
    held.write_text("x")
    code = f"import time; f = open(r'{held}', 'rb'); time.sleep(30)"
    proc = subprocess.Popen([sys.executable, "-c", code])
    try:
        owners = {}
        for _ in range(50):
            owners = lock_owners.owners_of([str(held)])
            if owners:
                break
            time.sleep(0.1)
        assert any(name.lower().startswith("python") for name in owners), owners
    finally:
        proc.kill()


def test_nothing_held_is_an_empty_answer(tmp_path):
    free = tmp_path / "free.log"
    free.write_text("x")
    assert lock_owners.owners_of([str(free)]) == {}


def test_no_paths_costs_nothing():
    assert lock_owners.owners_of([]) == {}
