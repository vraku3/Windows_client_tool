import os
import time

from modules.file_forensics.engine.file_metadata import read_metadata


def test_reads_real_metadata_for_a_real_file(tmp_path):
    f = tmp_path / "probe.txt"
    f.write_text("hello")

    meta = read_metadata(str(f))

    assert meta.path == str(f)
    assert meta.size == 5
    assert meta.read_only is False
    assert meta.created is not None
    assert meta.modified is not None


def test_a_missing_file_raises_file_not_found(tmp_path):
    import pytest
    with pytest.raises(FileNotFoundError):
        read_metadata(str(tmp_path / "does_not_exist.txt"))


def test_owner_lookup_failure_is_an_empty_string_not_an_exception(tmp_path, monkeypatch):
    f = tmp_path / "probe2.txt"
    f.write_text("x")
    import modules.file_forensics.engine.file_metadata as fm

    class _BoomResolver:
        def for_path(self, path):
            raise OSError("no ACL")

    monkeypatch.setattr(fm, "_owner_resolver", _BoomResolver())
    meta = fm.read_metadata(str(f))
    assert meta.owner == ""