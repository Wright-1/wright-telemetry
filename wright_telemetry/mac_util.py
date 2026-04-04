"""Normalize MAC addresses so agent and API use the same string form."""

from __future__ import annotations


def normalize_mac_address(mac: str | None) -> str:
    """Return uppercase colon-separated MAC, or empty string if missing / invalid."""
    if not mac:
        return ""
    s = mac.strip()
    if not s or s.lower() == "unknown":
        return ""
    s = s.upper().replace("-", ":").replace(" ", "")
    hex_only = s.replace(":", "")
    if ":" not in s and len(hex_only) == 12 and all(c in "0123456789ABCDEF" for c in hex_only):
        s = ":".join(hex_only[i : i + 2] for i in range(0, 12, 2))
    return s
