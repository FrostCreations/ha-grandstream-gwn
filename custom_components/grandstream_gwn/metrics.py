"""Derived values for dashboards: device kind, live rates and WAN status.

Kept free of Home Assistant imports so it can be unit tested directly.
"""

from __future__ import annotations

import re
from typing import Any

# A counter that has not moved for this long means the link is idle. The
# controller refreshes byte counters only every ~30-90 s, so a single
# unchanged poll is normal and must not read as "0 Mbps".
IDLE_AFTER = 180.0

ROUTER, SWITCH, AP = "router", "switch", "ap"


def num(value: Any) -> float | None:
    """Parse '42%', '20 MB', '51℃', 3, '3' into a float."""
    if value is None or value == "" or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"-?\d+(\.\d+)?", str(value))
    return float(match.group()) if match else None


def device_kind(device: dict[str, Any]) -> str:
    """router / switch / ap, from deviceType if present, else the model.

    Grandstream models: GWN70xx are routers, GWN77xx/78xx switches, the rest
    (GWN76xx etc.) access points.
    """
    explicit = str(device.get("deviceType") or "").lower()
    if explicit in (ROUTER, SWITCH, AP):
        return explicit
    model = str(device.get("apType") or device.get("model") or "").upper()
    if model.startswith("GWN70"):
        return ROUTER
    if model.startswith(("GWN77", "GWN78")):
        return SWITCH
    return AP


class RateTracker:
    """Turns ever-growing byte counters into Mbps."""

    def __init__(self) -> None:
        # key -> (last counter value, time it last changed, last rate)
        self._state: dict[Any, tuple[float, float, float | None]] = {}

    def update(self, key: Any, value: Any, now: float) -> float | None:
        counter = num(value)
        if counter is None:
            return None
        prev = self._state.get(key)
        if prev is None:
            self._state[key] = (counter, now, None)
            return None
        last, last_t, last_rate = prev
        if counter < last:  # device rebooted, counter reset
            self._state[key] = (counter, now, last_rate)
            return last_rate
        if counter == last:
            if now - last_t >= IDLE_AFTER:
                self._state[key] = (last, last_t, 0.0)
                return 0.0
            return last_rate
        elapsed = max(now - last_t, 1.0)
        rate = round((counter - last) * 8 / elapsed / 1_000_000, 3)
        self._state[key] = (counter, now, rate)
        return rate


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "up", "connected", "online", "yes")
    return bool(value)


def parse_wan(info: dict[str, Any]) -> dict[str, Any] | None:
    """Pick the active WAN port out of a device/info response.

    device/info for a router has a `portInfo` list; WAN ports carry
    role "WAN"/"wan" or a `wanName`, with `ipv4Info.connectStatus`,
    `ipv4Info.ip4Address`, `connectDuration` and `aggregate.rxBytes/txBytes`.
    """
    ports = info.get("portInfo") or info.get("ports") or []
    wans = [
        p for p in ports
        if isinstance(p, dict)
        and ("wan" in str(p.get("role", "")).lower() or p.get("wanName"))
    ]
    if not wans:
        return None

    def connected(p: dict[str, Any]) -> bool:
        ipv4 = p.get("ipv4Info") or {}
        status = ipv4.get("connectStatus", p.get("linkStatus"))
        return _truthy(status)

    port = next((p for p in wans if connected(p)), wans[0])
    ipv4 = port.get("ipv4Info") or {}
    agg = port.get("aggregate") or {}
    return {
        "name": port.get("wanName") or port.get("silkScreenPort") or "WAN",
        "connected": connected(port),
        "ip": ipv4.get("ip4Address") or ipv4.get("ipAddress") or None,
        "duration": port.get("connectDuration"),
        "rx_bytes": agg.get("rxBytes"),
        "tx_bytes": agg.get("txBytes"),
        "wan_count": len(wans),
    }
