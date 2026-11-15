"""Data coordinator: one poll fetches networks, devices, clients and SSIDs."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import GWNAuthError, GWNClient, GWNError, devices_broadcasting, normalize_mac
from .metrics import ROUTER, RateTracker, device_kind, parse_wan
from .const import (
    CONF_AWAY_DELAY,
    CONF_DETAIL_INTERVAL,
    CONF_SCAN_INTERVAL,
    DEFAULT_AWAY_DELAY,
    DEFAULT_DETAIL_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class GWNData:
    """Snapshot of the controller, keyed for fast entity lookup."""

    networks: dict[int, dict[str, Any]] = field(default_factory=dict)
    # device MAC -> ap/list row (+ "networkId", "details" from ap/info)
    devices: dict[str, dict[str, Any]] = field(default_factory=dict)
    # client MAC -> client/list row (+ "networkId"); only currently connected
    clients: dict[str, dict[str, Any]] = field(default_factory=dict)
    # ssid id -> ssid/list row (+ "networkId")
    ssids: dict[int, dict[str, Any]] = field(default_factory=dict)


def _num(value: Any) -> float | None:
    """Parse '42%', '20 MB', '51℃', 3, '3' into a float."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"-?\d+(\.\d+)?", str(value))
    return float(match.group()) if match else None


def device_label(device: dict[str, Any], mac: str) -> str:
    """Device name, or e.g. 'GWN7062E 1A2B' when no name is set in GWN."""
    if device.get("name"):
        return device["name"]
    model = device.get("apType") or device.get("model") or "GWN"
    return f"{model} {mac.replace(':', '')[-4:]}"


def is_online(device: dict[str, Any]) -> bool:
    status = device.get("status")
    if isinstance(status, str):
        return status.lower() in ("1", "online", "connected", "true")
    return bool(status)


class GWNCoordinator(DataUpdateCoordinator[GWNData]):
    """Polls the GWN controller."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: GWNClient) -> None:
        interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=interval),
        )
        self.client = client
        self.away_delay = entry.options.get(CONF_AWAY_DELAY, DEFAULT_AWAY_DELAY)
        self._detail_interval = entry.options.get(CONF_DETAIL_INTERVAL, DEFAULT_DETAIL_INTERVAL)
        self._details: dict[str, dict[str, Any]] = {}
        self._details_at = 0.0
        # client MAC -> monotonic time last seen connected (for away delay)
        self.last_seen: dict[str, float] = {}
        # every client MAC ever seen this run, with the last row we had
        self.known_clients: dict[str, dict[str, Any]] = {}
        # SSID id -> device MACs that broadcast it. Saved to disk because a
        # disabled SSID vanishes from ap/info, and we still need the list to
        # turn it back on (also after a restart).
        self.ssid_devices: dict[int, list[str]] = {}
        self._rates = RateTracker()
        self._wan_failed: set[str] = set()
        self._store: Store[dict[str, list[str]]] = Store(
            hass, 1, f"{DOMAIN}.{entry.entry_id}.ssid_devices"
        )

    async def async_load_ssid_devices(self) -> None:
        stored = await self._store.async_load() or {}
        self.ssid_devices = {int(k): list(v) for k, v in stored.items()}

    def bind_macs_for(self, ssid_id: int) -> list[str]:
        return self.ssid_devices.get(ssid_id, [])

    def _learn_ssid_devices(self, data: GWNData) -> None:
        details = {mac: dev.get("details", {}) for mac, dev in data.devices.items()}
        changed = False
        for ssid_id, ssid in data.ssids.items():
            macs = devices_broadcasting(ssid.get("ssidName", ""), ssid_id, details)
            if macs and macs != self.ssid_devices.get(ssid_id):
                self.ssid_devices[ssid_id] = macs
                changed = True
        if changed:
            self._store.async_delay_save(
                lambda: {str(k): v for k, v in self.ssid_devices.items()}, 5
            )

    async def _async_update_data(self) -> GWNData:
        try:
            return await self._fetch()
        except GWNAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except GWNError as err:
            raise UpdateFailed(str(err)) from err

    async def _fetch(self) -> GWNData:
        data = GWNData()
        for net in await self.client.get_networks():
            data.networks[int(net["id"])] = net

        async def per_network(net_id: int) -> None:
            devices, clients, ssids = await asyncio.gather(
                self.client.get_devices(net_id),
                self.client.get_connected_clients(net_id),
                self.client.get_ssids(net_id),
            )
            for dev in devices:
                mac = normalize_mac(dev.get("mac", ""))
                data.devices[mac] = {**dev, "mac": mac, "networkId": net_id}
            for cli in clients:
                mac = normalize_mac(cli.get("clientId") or cli.get("mac") or "")
                if mac:
                    data.clients[mac] = {**cli, "mac": mac, "networkId": net_id}
            for ssid in ssids:
                data.ssids[int(ssid["id"])] = {**ssid, "networkId": net_id}

        await asyncio.gather(*(per_network(n) for n in data.networks))

        await self._refresh_details(data)
        for mac, dev in data.devices.items():
            dev["details"] = self._details.get(mac, {})
            dev["kind"] = device_kind(dev)
        self._learn_ssid_devices(data)
        await self._refresh_wan(data)

        now = time.monotonic()
        for mac, dev in data.devices.items():
            dev["download_rate"] = self._rates.update((mac, "down"), dev.get("download"), now)
            dev["upload_rate"] = self._rates.update((mac, "up"), dev.get("upload"), now)
            wan = dev.get("wan")
            if wan:
                wan["download_rate"] = self._rates.update((mac, "wan_rx"), wan.get("rx_bytes"), now)
                wan["upload_rate"] = self._rates.update((mac, "wan_tx"), wan.get("tx_bytes"), now)

        for mac, cli in data.clients.items():
            self.last_seen[mac] = now
            self.known_clients[mac] = cli
        return data

    async def _refresh_details(self, data: GWNData) -> None:
        """ap/info is one call per device, so poll it on a slower cadence."""
        if time.monotonic() - self._details_at < self._detail_interval and self._details:
            return
        self._details_at = time.monotonic()
        for mac, dev in data.devices.items():
            if not is_online(dev):
                continue
            try:
                self._details[mac] = await self.client.get_ap_details(mac)
            except GWNError as err:
                # switches/routers may not answer ap/info; that's fine
                _LOGGER.debug("ap/info failed for %s: %s", mac, err)

    async def _refresh_wan(self, data: GWNData) -> None:
        """WAN status for each online router (one device/info call each)."""
        for mac, dev in data.devices.items():
            if dev["kind"] != ROUTER or not is_online(dev):
                continue
            try:
                info = await self.client.get_device_info(dev["networkId"], mac)
            except GWNError as err:
                if mac not in self._wan_failed:
                    _LOGGER.debug("device/info failed for %s: %s", mac, err)
                    self._wan_failed.add(mac)
                continue
            dev["wan"] = parse_wan(info) if isinstance(info, dict) else None
            if dev["wan"] is None and mac not in self._wan_failed:
                _LOGGER.debug("No WAN port found in device/info for %s: keys %s", mac, list(info)[:30])
                self._wan_failed.add(mac)

    # ------------------------------------------------------------ helpers

    def network_rate(self, network_id: int, direction: str) -> float | None:
        """Network throughput: routers' traffic if there are any, else the APs'.

        Summing every device would double count (AP traffic also passes the
        router).
        """
        devices = [d for d in self.data.devices.values() if d["networkId"] == network_id]
        routers = [d for d in devices if d.get("kind") == ROUTER]
        rates = [d.get(f"{direction}_rate") for d in (routers or devices)]
        known = [r for r in rates if r is not None]
        return round(sum(known), 3) if known else None

    def kind_counts(self, network_id: int, kind: str) -> tuple[int, int]:
        """(online, total) devices of a kind in a network."""
        devices = [
            d for d in self.data.devices.values()
            if d["networkId"] == network_id and d.get("kind") == kind
        ]
        return sum(1 for d in devices if is_online(d)), len(devices)

    def client_is_home(self, mac: str) -> bool:
        if self.data and mac in self.data.clients:
            return True
        seen = self.last_seen.get(mac)
        return seen is not None and time.monotonic() - seen < self.away_delay

    @staticmethod
    def detail_value(device: dict[str, Any], key: str) -> float | None:
        value = _num(device.get("details", {}).get(key))
        # some models report nonsense (e.g. CPU 192224%); drop it
        if value is not None and key == "cpuUsage" and not 0 <= value <= 100:
            return None
        return value
