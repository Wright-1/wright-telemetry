"""MAC normalization for portal lookups."""

from wright_telemetry.mac_util import normalize_mac_address


def test_empty() -> None:
    assert normalize_mac_address("") == ""
    assert normalize_mac_address("unknown") == ""
    assert normalize_mac_address(None) == ""


def test_uppercase_colons() -> None:
    assert normalize_mac_address("aa:bb:cc:dd:ee:ff") == "AA:BB:CC:DD:EE:FF"


def test_hyphen_to_colon() -> None:
    assert normalize_mac_address("AA-BB-CC-DD-EE-FF") == "AA:BB:CC:DD:EE:FF"


def test_compact_twelve_hex() -> None:
    assert normalize_mac_address("aabbccddeeff") == "AA:BB:CC:DD:EE:FF"
