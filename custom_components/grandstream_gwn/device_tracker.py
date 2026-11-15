"""Client presence via device_tracker."""

from __future__ import annotations

from typing import Any

from homeassistant.components.device_tracker import ScannerEntity, SourceType
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import GWNConfigEntry
from .api import normalize_mac
from .coordinator import GWNCoordinator, device_label
from .entity import add_when_seen


async def async_setup_entry(
    hass: HomeAssistant, entry: GWNConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data

    # Restore trackers for clients seen in earlier runs, so offline phones
    # show "not_home" after a restart instead of disappearing. ScannerEntity
    # uses the MAC address as its unique_id.
    registry = er.async_get(hass)
    restored: dict[str, str | None] = {
        normalize_mac(e.unique_id.removeprefix("client_")): e.original_name
        for e in er.async_entries_for_config_entry(registry, entry.entry_id)
        if e.domain == "device_tracker"
    }

    def keys():
        return set(coordinator.data.clients) | set(restored)

    entry.async_on_unload(
        add_when_seen(
            coordinator,
            async_add_entities,
            keys,
            lambda m: [GWNClientTracker(coordinator, m, restored.get(m))],
        )
    )


class GWNClientTracker(CoordinatorEntity[GWNCoordinator], ScannerEntity):
    """A Wi-Fi or wired client seen by the GWN controller."""

    def __init__(self, coordinator: GWNCoordinator, mac: str, saved_name: str | None = None) -> None:
        super().__init__(coordinator)
        self._mac = mac
        self._saved_name = saved_name

    @property
    def entity_registry_enabled_default(self) -> bool:
        # ScannerEntity disables trackers by default unless another
        # integration already knows the MAC. Presence is the point of this
        # integration, so always enable them.
        return True

    @property
    def _row(self) -> dict[str, Any]:
        return self.coordinator.known_clients.get(self._mac, {})

    @property
    def name(self) -> str:
        row = self._row
        return (
            row.get("name") or row.get("clientName") or row.get("dhcpHostname")
            or self._saved_name or self._mac
        )

    @property
    def source_type(self) -> SourceType:
        return SourceType.ROUTER

    @property
    def is_connected(self) -> bool:
        return self.coordinator.client_is_home(self._mac)

    @property
    def mac_address(self) -> str:
        return self._mac

    @property
    def ip_address(self) -> str | None:
        return self._row.get("ipv4") or None

    @property
    def hostname(self) -> str | None:
        return self._row.get("dhcpHostname") or None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        row = self._row
        ap_mac = row.get("apId") or ""
        ap = self.coordinator.data.devices.get(ap_mac) if ap_mac else None
        attrs = {
            "ssid": row.get("ssid"),
            "ap_name": row.get("apName") or (device_label(ap, ap_mac) if ap else None),
            "ap_mac": row.get("apId"),
            "band": row.get("channelClassStr"),
            "rssi": row.get("rssi"),
            "port": row.get("port"),
            "tx_bytes": row.get("txBytes"),
            "rx_bytes": row.get("rxBytes"),
            "tx_rate_mbps": row.get("txRate"),
            "rx_rate_mbps": row.get("rxRate"),
            "os": row.get("dhcpOs"),
            "manufacturer": row.get("dhcpManufacture"),
            "first_seen": row.get("firstSeen"),
        }
        return {k: v for k, v in attrs.items() if v not in (None, "")}
