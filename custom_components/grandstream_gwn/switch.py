"""SSID on/off switches."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import GWNConfigEntry
from .api import GWNError
from .const import DOMAIN
from .coordinator import GWNCoordinator
from .entity import add_when_seen


def ssid_device_info(coord: GWNCoordinator, ssid_id: int, ssid: dict[str, Any]) -> DeviceInfo:
    entry_id = coord.config_entry.entry_id
    return DeviceInfo(
        identifiers={(DOMAIN, f"{entry_id}_ssid{ssid_id}")},
        manufacturer="Grandstream",
        model="SSID",
        name=f"Wi-Fi {ssid.get('ssidName') or ssid_id}",
    )


async def async_setup_entry(
    hass: HomeAssistant, entry: GWNConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coord = entry.runtime_data
    entry.async_on_unload(
        add_when_seen(
            coord, async_add_entities, lambda: coord.data.ssids, lambda sid: [GWNSsidSwitch(coord, sid)]
        )
    )


class GWNSsidSwitch(CoordinatorEntity[GWNCoordinator], SwitchEntity):
    """Enable/disable an SSID across the network."""

    _attr_has_entity_name = True
    _attr_translation_key = "ssid_enabled"

    def __init__(self, coord: GWNCoordinator, ssid_id: int) -> None:
        super().__init__(coord)
        self.ssid_id = ssid_id
        self._attr_unique_id = f"{coord.config_entry.entry_id}_ssid{ssid_id}_enabled"
        self._attr_device_info = ssid_device_info(coord, ssid_id, coord.data.ssids[ssid_id])
        self._pending: bool | None = None

    @property
    def available(self) -> bool:
        return super().available and self.ssid_id in self.coordinator.data.ssids

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"devices": self.coordinator.bind_macs_for(self.ssid_id)}

    @property
    def is_on(self) -> bool | None:
        if self._pending is not None:
            return self._pending
        value = self.coordinator.data.ssids.get(self.ssid_id, {}).get("wifiEnabled")
        if value is None:
            return None
        return str(value).lower() in ("1", "true")

    async def _set(self, enabled: bool) -> None:
        try:
            await self.coordinator.client.set_ssid_enabled(
                self.ssid_id,
                enabled,
                bind_macs=self.coordinator.bind_macs_for(self.ssid_id),
                network_id=self.coordinator.data.ssids[self.ssid_id]["networkId"],
            )
        except GWNError as err:
            raise HomeAssistantError(f"Could not change SSID: {err}") from err
        # optimistic until the next poll confirms it
        self._pending = enabled
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()

    def _handle_coordinator_update(self) -> None:
        self._pending = None
        super()._handle_coordinator_update()

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)
