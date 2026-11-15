"""Online status for APs, switches and routers."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import GWNConfigEntry
from .coordinator import GWNCoordinator
from .entity import GWNDeviceEntity, add_when_seen, device_online
from .metrics import ROUTER


async def async_setup_entry(
    hass: HomeAssistant, entry: GWNConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coord = entry.runtime_data
    entry.async_on_unload(
        add_when_seen(
            coord,
            async_add_entities,
            lambda: coord.data.devices,
            lambda m: [GWNOnlineSensor(coord, m)]
            + ([GWNWanSensor(coord, m)] if coord.data.devices[m].get("kind") == ROUTER else []),
        )
    )


class GWNOnlineSensor(GWNDeviceEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, coord: GWNCoordinator, mac: str) -> None:
        super().__init__(coord, mac, "online")

    @property
    def is_on(self) -> bool:
        return device_online(self.coordinator, self.mac)

    @property
    def extra_state_attributes(self) -> dict:
        d = self.device
        return {k: d[v] for k, v in (("uptime", "upTime"), ("firmware", "versionFirmware")) if d.get(v)}


class GWNWanSensor(GWNDeviceEntity, BinarySensorEntity):
    """Router WAN link (from device/info)."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_translation_key = "wan"

    def __init__(self, coord: GWNCoordinator, mac: str) -> None:
        super().__init__(coord, mac, "wan")

    @property
    def is_on(self) -> bool | None:
        wan = self.device.get("wan")
        return wan.get("connected") if wan else None

    @property
    def extra_state_attributes(self) -> dict:
        wan = self.device.get("wan") or {}
        attrs = {"port": wan.get("name"), "ip": wan.get("ip"), "connected_for": wan.get("duration")}
        return {k: v for k, v in attrs.items() if v not in (None, "")}
