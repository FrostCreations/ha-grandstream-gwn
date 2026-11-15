"""Tests for dashboard metrics (device kind, rates, WAN parsing)."""

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "gwn_metrics", Path(__file__).parents[1] / "custom_components/grandstream_gwn/metrics.py"
)
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)


def test_device_kind_from_model_and_type():
    assert m.device_kind({"apType": "GWN7062E"}) == m.ROUTER
    assert m.device_kind({"apType": "GWN7660E"}) == m.AP
    assert m.device_kind({"apType": "GWN7811P"}) == m.SWITCH
    assert m.device_kind({"apType": "GWN7703"}) == m.SWITCH
    assert m.device_kind({"apType": "GWN7660E", "deviceType": "ROUTER"}) == m.ROUTER


def test_rate_from_counters():
    r = m.RateTracker()
    assert r.update("k", 1_000_000, 0) is None  # first sample: no rate yet
    # 7.5 MB in 60 s = 1 Mbps
    assert r.update("k", 1_000_000 + 7_500_000, 60) == 1.0


def test_rate_holds_between_controller_refreshes_then_goes_idle():
    r = m.RateTracker()
    r.update("k", 0, 0)
    assert r.update("k", 7_500_000, 60) == 1.0
    assert r.update("k", 7_500_000, 90) == 1.0      # unchanged once: keep last rate
    assert r.update("k", 7_500_000, 60 + m.IDLE_AFTER) == 0.0  # long idle: 0


def test_rate_survives_counter_reset():
    r = m.RateTracker()
    r.update("k", 0, 0)
    r.update("k", 7_500_000, 60)
    assert r.update("k", 100, 120) == 1.0  # reboot reset: keep last rate, no negative
    assert r.update("k", 100 + 3_750_000, 180) == 0.5


def test_rate_ignores_missing_values():
    r = m.RateTracker()
    assert r.update("k", None, 0) is None
    assert r.update("k", "", 0) is None


def test_parse_wan_picks_connected_port():
    info = {
        "deviceType": "ROUTER",
        "portInfo": [
            {"role": "LAN", "silkScreenPort": "LAN1"},
            {"role": "WAN", "wanName": "WAN2", "ipv4Info": {"connectStatus": 0}},
            {
                "role": "WAN",
                "wanName": "WAN1",
                "connectDuration": 4140,
                "ipv4Info": {"connectStatus": 1, "ip4Address": "203.0.113.10"},
                "aggregate": {"rxBytes": 1000, "txBytes": 500},
            },
        ],
    }
    wan = m.parse_wan(info)
    assert wan["name"] == "WAN1"
    assert wan["connected"] is True
    assert wan["ip"] == "203.0.113.10"
    assert wan["rx_bytes"] == 1000
    assert wan["wan_count"] == 2


def test_parse_wan_handles_missing_ports():
    assert m.parse_wan({}) is None
    assert m.parse_wan({"portInfo": [{"role": "LAN"}]}) is None
