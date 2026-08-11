"""Tests for per-firmware miner credentials (config, scheduler, discovery)."""

from tests.conftest import FIRMWARE_PASSWORDS, FIRMWARE_USERNAMES
from wright_telemetry.config import (
    _KNOWN_FIRMWARE_TYPES as KNOWN_FIRMWARE_TYPES,
)
from wright_telemetry.config import (
    SENSITIVE_MASK,
    encode_password,
    firmware_credentials_map,
    mask_config,
    resolve_firmware_credentials,
    save_firmware_credentials,
)
from wright_telemetry.discovery import DiscoveredMiner, discovered_to_miner_cfgs
from wright_telemetry.scheduler import _build_collectors, _resolve_miners

GLOBAL_PW = encode_password("globalpw")
VNISH_PW = encode_password("vnishpw")


def _legacy_cfg() -> dict:
    """A config written before per-firmware credentials existed."""
    return {
        "discovery": {
            "default_username": "root",
            "default_password_b64": GLOBAL_PW,
        }
    }


def _mixed_cfg() -> dict:
    """Legacy globals plus a vnish-specific override."""
    cfg = _legacy_cfg()
    save_firmware_credentials(
        cfg, {"vnish": {"username": "admin", "password_b64": VNISH_PW}}
    )
    return cfg


# ── resolve_firmware_credentials ──────────────────────────────────────────────

def test_legacy_config_falls_back_to_globals():
    cfg = _legacy_cfg()
    for firmware in KNOWN_FIRMWARE_TYPES:
        assert resolve_firmware_credentials(cfg, firmware) == ("root", GLOBAL_PW)


def test_per_firmware_override_wins():
    assert resolve_firmware_credentials(_mixed_cfg(), "vnish") == ("admin", VNISH_PW)


def test_unconfigured_firmware_still_falls_back():
    assert resolve_firmware_credentials(_mixed_cfg(), "braiins") == ("root", GLOBAL_PW)


def test_missing_firmware_falls_back():
    """A miner with no firmware recorded gets the global defaults."""
    assert resolve_firmware_credentials(_mixed_cfg(), None) == ("root", GLOBAL_PW)


def test_empty_config_yields_safe_defaults():
    assert resolve_firmware_credentials({}, "braiins") == ("root", "")


def test_explicitly_cleared_password_is_not_overridden_by_global():
    """An empty stored password means 'no password', not 'use the global'."""
    cfg = _legacy_cfg()
    save_firmware_credentials(cfg, {"luxos": {"username": "root", "password_b64": ""}})
    assert resolve_firmware_credentials(cfg, "luxos") == ("root", "")


def test_blank_username_falls_back_to_global_username():
    cfg = _legacy_cfg()
    cfg["discovery"]["default_username"] = "operator"
    save_firmware_credentials(cfg, {"vnish": {"username": "", "password_b64": VNISH_PW}})
    # save_firmware_credentials normalises a blank username to "root"
    assert resolve_firmware_credentials(cfg, "vnish") == ("root", VNISH_PW)


# ── save_firmware_credentials ─────────────────────────────────────────────────

def test_fully_blank_entry_drops_the_override():
    cfg = _mixed_cfg()
    save_firmware_credentials(cfg, {"vnish": {"username": "", "password_b64": ""}})
    assert "firmware_credentials" not in cfg["discovery"]
    assert resolve_firmware_credentials(cfg, "vnish") == ("root", GLOBAL_PW)


def test_save_merges_without_clobbering_other_firmware():
    cfg = _mixed_cfg()
    save_firmware_credentials(
        cfg, {"bitmain": {"username": "root", "password_b64": encode_password("bmpw")}}
    )
    stored = cfg["discovery"]["firmware_credentials"]
    assert set(stored) == {"vnish", "bitmain"}
    assert resolve_firmware_credentials(cfg, "vnish") == ("admin", VNISH_PW)


def test_globals_are_preserved_for_backwards_compatibility():
    cfg = _mixed_cfg()
    assert cfg["discovery"]["default_username"] == "root"
    assert cfg["discovery"]["default_password_b64"] == GLOBAL_PW


# ── firmware_credentials_map ──────────────────────────────────────────────────

def test_map_seeds_every_firmware_from_globals():
    result = firmware_credentials_map(_mixed_cfg())
    assert result["vnish"] == {"username": "admin", "password_b64": VNISH_PW}
    assert result["braiins"] == {"username": "root", "password_b64": GLOBAL_PW}
    assert set(result) == set(KNOWN_FIRMWARE_TYPES)


def test_map_honours_explicit_firmware_list():
    assert set(firmware_credentials_map(_mixed_cfg(), ["vnish", "luxos"])) == {
        "vnish", "luxos",
    }


# ── mask_config ───────────────────────────────────────────────────────────────

def test_mask_covers_per_firmware_passwords():
    masked = mask_config(_mixed_cfg())["discovery"]
    assert masked["default_password_b64"] == SENSITIVE_MASK
    assert masked["firmware_credentials"]["vnish"]["password_b64"] == SENSITIVE_MASK
    # usernames are not secrets and stay readable
    assert masked["firmware_credentials"]["vnish"]["username"] == "admin"


def test_mask_does_not_mutate_the_original():
    cfg = _mixed_cfg()
    mask_config(cfg)
    assert cfg["discovery"]["firmware_credentials"]["vnish"]["password_b64"] == VNISH_PW


# ── discovered_to_miner_cfgs ──────────────────────────────────────────────────

def _found() -> list[DiscoveredMiner]:
    return [
        DiscoveredMiner(ip="10.0.0.1", firmware="vnish", hostname="v1", mac_address=None),
        DiscoveredMiner(ip="10.0.0.2", firmware="luxos", hostname="l1", mac_address=None),
    ]


def test_discovered_cfgs_apply_per_firmware_credentials():
    cfg = _mixed_cfg()
    result = discovered_to_miner_cfgs(
        _found(), "root", GLOBAL_PW,
        credentials_for=lambda fw: resolve_firmware_credentials(cfg, fw),
    )
    assert (result[0]["username"], result[0]["password_b64"]) == ("admin", VNISH_PW)
    assert (result[1]["username"], result[1]["password_b64"]) == ("root", GLOBAL_PW)


def test_discovered_cfgs_without_resolver_keep_legacy_behaviour():
    result = discovered_to_miner_cfgs(_found(), "legacyuser", GLOBAL_PW)
    for entry in result:
        assert entry["username"] == "legacyuser"
        assert entry["password_b64"] == GLOBAL_PW


# ── scheduler wiring ──────────────────────────────────────────────────────────

class _FakeController:
    has_scan_manager = True

    def __init__(self, miners):
        self._miners = miners

    def get_discovered_miners(self):
        return self._miners


def test_scheduler_applies_per_firmware_credentials():
    miners = [
        {"name": "a", "url": "http://10.0.0.1", "firmware": "vnish"},
        {"name": "b", "url": "http://10.0.0.2", "firmware": "braiins"},
        {"name": "c", "url": "http://10.0.0.3"},
    ]
    resolved = _resolve_miners(_mixed_cfg(), controller=_FakeController(miners))
    by_name = {m["name"]: m for m in resolved}
    assert (by_name["a"]["username"], by_name["a"]["password_b64"]) == ("admin", VNISH_PW)
    assert (by_name["b"]["username"], by_name["b"]["password_b64"]) == ("root", GLOBAL_PW)
    assert (by_name["c"]["username"], by_name["c"]["password_b64"]) == ("root", GLOBAL_PW)


def test_scheduler_respects_explicit_per_miner_credentials():
    """A credential stored on the miner itself still beats the firmware default."""
    miners = [{
        "name": "a", "url": "http://10.0.0.1", "firmware": "vnish",
        "username": "custom", "password_b64": encode_password("custompw"),
    }]
    resolved = _resolve_miners(_mixed_cfg(), controller=_FakeController(miners))
    assert resolved[0]["username"] == "custom"
    assert resolved[0]["password_b64"] == encode_password("custompw")


# ── End-to-end: config → scheduler → collector ────────────────────────────────

def _all_firmware_cfg() -> dict:
    """A config giving every firmware its own distinct username and password."""
    cfg = {"discovery": {"default_username": "root", "default_password_b64": GLOBAL_PW}}
    save_firmware_credentials(cfg, {
        firmware: {
            "username": FIRMWARE_USERNAMES[firmware],
            "password_b64": encode_password(pw),
        }
        for firmware, pw in FIRMWARE_PASSWORDS.items()
    })
    return cfg


def test_each_firmware_collector_gets_its_own_password():
    """The full chain must hand every collector the password for *its* firmware."""
    cfg = _all_firmware_cfg()
    miners = [
        {"name": firmware, "url": f"http://10.0.0.{i}", "firmware": firmware}
        for i, firmware in enumerate(FIRMWARE_PASSWORDS, start=1)
    ]
    resolved = _resolve_miners(cfg, controller=_FakeController(miners))

    collectors = _build_collectors(resolved)
    try:
        for miner_cfg, collector in collectors:
            firmware = miner_cfg["firmware"]
            assert collector.password == FIRMWARE_PASSWORDS[firmware], (
                f"{firmware} collector got the wrong password"
            )
            assert collector.username == FIRMWARE_USERNAMES[firmware], (
                f"{firmware} collector got the wrong username"
            )
    finally:
        for _, collector in collectors:
            collector.close()


def test_no_credential_is_shared_between_firmwares():
    """Guards the fixtures themselves: usernames and passwords stay distinct.

    If these collapse to a shared value (e.g. everything back to "root"), the
    cross-firmware leak tests above silently stop proving anything.
    """
    assert len(set(FIRMWARE_PASSWORDS.values())) == len(FIRMWARE_PASSWORDS)
    assert len(set(FIRMWARE_USERNAMES.values())) == len(FIRMWARE_USERNAMES)
    assert set(FIRMWARE_USERNAMES) == set(FIRMWARE_PASSWORDS)


def test_one_firmware_password_change_does_not_affect_others():
    cfg = _all_firmware_cfg()
    save_firmware_credentials(
        cfg, {"vnish": {"username": "vnish-user", "password_b64": encode_password("rotated")}}
    )
    _, vnish_pw = resolve_firmware_credentials(cfg, "vnish")
    assert vnish_pw == encode_password("rotated")
    for firmware, pw in FIRMWARE_PASSWORDS.items():
        if firmware == "vnish":
            continue
        assert resolve_firmware_credentials(cfg, firmware)[1] == encode_password(pw)


# ── GUI persistence: untouched rows must stay on the global fallback ──────────

def _engine_stub():
    """A ScanningEngine with only what update_firmware_credentials touches."""
    from wright_telemetry.gui.engine import ScanningEngine

    engine = object.__new__(ScanningEngine)

    class _Controller:
        def request_config_reload(self) -> None:
            pass

    engine.controller = _Controller()
    return engine


def _run_gui_update(monkeypatch, cfg: dict, creds: dict) -> dict:
    import wright_telemetry.config as config_mod

    monkeypatch.setattr(config_mod, "load_config", lambda: cfg)
    monkeypatch.setattr(config_mod, "save_config", lambda c: None)
    _engine_stub().update_firmware_credentials(creds)
    return cfg


def test_gui_untouched_rows_do_not_freeze_the_global_password(monkeypatch):
    """Editing one firmware must not pin the others to today's global password.

    Regression: every row was persisted with its *resolved* password, so a
    legacy global-password config gained explicit entries for all firmwares
    and later rotating the global no longer reached them.
    """
    cfg = _legacy_cfg()
    _run_gui_update(monkeypatch, cfg, {
        "braiins": {"username": "edited", "password": "newpw"},
        "vnish":   {"username": "root", "password": None},
        "luxos":   {"username": "root", "password": None},
    })

    stored = cfg["discovery"]["firmware_credentials"]
    assert set(stored) == {"braiins"}
    assert stored["braiins"]["password_b64"] == encode_password("newpw")

    # The untouched firmwares still track the global, so rotating it reaches them.
    cfg["discovery"]["default_password_b64"] = encode_password("rotated")
    assert resolve_firmware_credentials(cfg, "vnish")[1] == encode_password("rotated")
    assert resolve_firmware_credentials(cfg, "braiins")[1] == encode_password("newpw")


def test_gui_username_only_edit_is_persisted(monkeypatch):
    cfg = _legacy_cfg()
    _run_gui_update(monkeypatch, cfg, {
        "vnish": {"username": "admin", "password": None},
    })
    user, pw = resolve_firmware_credentials(cfg, "vnish")
    assert user == "admin"
    assert pw == GLOBAL_PW  # password still falls through to the global


def test_gui_edit_keeps_existing_entry_password(monkeypatch):
    """An untouched password field on a firmware that already has an entry."""
    cfg = _mixed_cfg()
    _run_gui_update(monkeypatch, cfg, {
        "vnish": {"username": "renamed", "password": None},
    })
    user, pw = resolve_firmware_credentials(cfg, "vnish")
    assert user == "renamed"
    assert pw == VNISH_PW
