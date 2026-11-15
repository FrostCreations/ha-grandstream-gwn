"""Reboot buttons for APs, switches and routers."""

from __future__ import annotations

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import GWNConfigEntry
from .api import GWNError
from .coordinator import GWNCoordinator
from .entity import GWNDeviceEntity, add_when_seen


async def async_setup_entry(
    hass: HomeAssistant, entry: GWNConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coord = entry.runtime_data
    entry.async_on_unload(
        add_when_seen(
            coord, async_add_entities, lambda: coord.data.devices, lambda m: [GWNRebootButton(coord, m)]
        )
    )


class GWNRebootButton(GWNDeviceEntity, ButtonEntity):
    """Reboot an AP, router or switch (ap/reboot works for every device type).

    No entity category, so it shows on dashboards rather than only on the
    device page.
    """

    _attr_device_class = ButtonDeviceClass.RESTART

    def __init__(self, coord: GWNCoordinator, mac: str) -> None:
        super().__init__(coord, mac, "reboot")

    async def async_press(self) -> None:
        try:
            await self.coordinator.client.reboot_device(self.mac)
        except GWNError as err:
            raise HomeAssistantError(f"Reboot failed: {err}") from err
