"""Base entities and helpers for adding entities as they appear."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from homeassistant.core import callback
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import GWNCoordinator, device_label, is_online


class GWNDeviceEntity(CoordinatorEntity[GWNCoordinator]):
    """An entity that belongs to a GWN AP, switch or router."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: GWNCoordinator, mac: str, key: str) -> None:
        super().__init__(coordinator)
        self.mac = mac
        self._attr_unique_id = f"{mac}_{key}"
        dev = self.device
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, mac)},
            connections={(CONNECTION_NETWORK_MAC, mac.lower())},
            manufacturer="Grandstream",
            model=dev.get("apType") or dev.get("model"),
            name=device_label(dev, mac),
            sw_version=dev.get("versionFirmware"),
        )

    @property
    def device(self) -> dict[str, Any]:
        return self.coordinator.data.devices.get(self.mac, {})

    @property
    def available(self) -> bool:
        return super().available and self.mac in self.coordinator.data.devices


class GWNNetworkEntity(CoordinatorEntity[GWNCoordinator]):
    """An entity that belongs to a GWN network (site)."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: GWNCoordinator, network_id: int, key: str) -> None:
        super().__init__(coordinator)
        self.network_id = network_id
        entry_id = coordinator.config_entry.entry_id
        self._attr_unique_id = f"{entry_id}_net{network_id}_{key}"
        net = coordinator.data.networks.get(network_id, {})
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry_id}_net{network_id}")},
            manufacturer="Grandstream",
            model="GWN network",
            name=f"GWN {net.get('networkName') or network_id}",
        )

    @property
    def available(self) -> bool:
        return super().available and self.network_id in self.coordinator.data.networks


def add_when_seen(
    coordinator: GWNCoordinator,
    async_add_entities: Callable[[Iterable[Entity]], None],
    keys: Callable[[], Iterable[Any]],
    factory: Callable[[Any], Iterable[Entity]],
) -> Callable[[], None]:
    """Create entities for each key now, and for new keys on later polls."""
    added: set[Any] = set()

    @callback
    def _check() -> None:
        new = [k for k in keys() if k not in added]
        if not new:
            return
        added.update(new)
        async_add_entities([e for k in new for e in factory(k)])

    _check()
    return coordinator.async_add_listener(_check)


def device_online(coordinator: GWNCoordinator, mac: str) -> bool:
    return is_online(coordinator.data.devices.get(mac, {}))
