from modules.cleanup.components.quick_cleanup_tab import largest_slice_text


def _fmt(n):
    return f"{n / 1024 ** 3:.1f} GB"


def test_the_biggest_slice_is_named_even_if_it_is_an_advanced_one():
    slices = [("Temp Files", 1_600_000_000, "#1"),
              ("Package Cache", 34_000_000_000, "#2"),
              ("Windows Logs", 322_000_000, "#3")]
    text = largest_slice_text(slices, _fmt)
    assert text.startswith("Largest: Package Cache")
    assert "31.7 GB" in text


def test_nothing_found_says_nothing():
    assert largest_slice_text([], _fmt) == ""
    assert largest_slice_text([("Empty", 0, "#1")], _fmt) == ""
