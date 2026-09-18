from modules.file_forensics.engine import device_paths


def test_translates_a_real_drive_letter_path(monkeypatch):
    monkeypatch.setattr(
        device_paths, "_query_dos_device",
        lambda drive: r"\Device\HarddiskVolume3" if drive == "C:" else None,
    )
    result = device_paths.to_device_path(r"C:\Users\me\file.txt")
    assert result == r"\Device\HarddiskVolume3\Users\me\file.txt"


def test_a_path_with_no_drive_letter_returns_none():
    assert device_paths.to_device_path(r"\\server\share\file.txt") is None


def test_a_drive_query_manager_cannot_answer_returns_none(monkeypatch):
    monkeypatch.setattr(device_paths, "_query_dos_device", lambda drive: None)
    assert device_paths.to_device_path(r"Z:\ghost\file.txt") is None
