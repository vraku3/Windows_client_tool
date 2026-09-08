from modules.driver_manager import driver_reader as dr


def test_every_error_code_meaning_is_a_real_sentence():
    for code, meaning in dr._ERROR_CODE_MEANINGS.items():
        assert isinstance(code, int) and code > 0
        assert len(meaning) > 10


def test_decode_error_code_never_returns_none():
    for code in list(dr._ERROR_CODE_MEANINGS) + [0, 99999]:
        assert dr.decode_error_code(code) is not None
