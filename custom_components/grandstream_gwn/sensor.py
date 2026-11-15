"""Device, network and SSID sensors."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfDataRate,
    UnitOfInformation,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import GWNConfigEntry
from .coordinator import GWNCoordinator, _num
from .entity import GWNDeviceEntity, GWNNetworkEntity, add_when_seen
from .metrics import AP, ROUTER, SWITCH


def _int(value: Any) -> int | None:
    """Counts are whole numbers; _num returns floats ("1.0")."""
    number = _num(value)
    return int(number) if number is not None else None


def load_average(device: dict[str, Any]) -> float | None:
    """1-minute load average. GWN reports it as Linux fixed point (x 65536)."""
    loads = device.get("details", {}).get("loadAverage")
    if not loads:
        return None
    value = _num(loads[0])
    return round(value / 65536, 2) if value is not None else None


@dataclass(frozen=True, kw_only=True)
class GWNSensorDescription(SensorEntityDescription):
    value_fn: Callable[[dict[str, Any]], Any]


DEVICE_SENSORS: tuple[GWNSensorDescription, ...] = (
    GWNSensorDescription(
        key="clients",
        translation_key="clients",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: _int(d.get("clients")),
    ),
    GWNSensorDescription(
        key="upload",
        translation_key="upload",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        suggested_unit_of_measurement=UnitOfInformation.GIGABYTES,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda d: _num(d.get("upload")),
    ),
    GWNSensorDescription(
        key="download",
        translation_key="download",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        suggested_unit_of_measurement=UnitOfInformation.GIGABYTES,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda d: _num(d.get("download")),
    ),
    GWNSensorDescription(
        key="cpu",
        translation_key="cpu",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: GWNCoordinator.detail_value(d, "cpuUsage"),
    ),
    GWNSensorDescription(
        key="memory",
        translation_key="memory",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.MEGABYTES,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: GWNCoordinator.detail_value(d, "usedMemory") or None,
    ),
    GWNSensorDescription(
        key="temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: GWNCoordinator.detail_value(d, "temperature"),
    ),
    GWNSensorDescription(
        key="load",
        translation_key="load",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=2,
        value_fn=lambda d: load_average(d),
    ),
    GWNSensorDescription(
        key="ip",
        translation_key="ip",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.get("ipv4") or d.get("ip"),
    ),
    GWNSensorDescription(
        key="download_rate",
        translation_key="download_rate",
        device_class=SensorDeviceClass.DATA_RATE,
        native_unit_of_measurement=UnitOfDataRate.MEGABITS_PER_SECOND,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda d: d.get("download_rate"),
    ),
    GWNSensorDescription(
        key="upload_rate",
        translation_key="upload_rate",
        device_class=SensorDeviceClass.DATA_RATE,
        native_unit_of_measurement=UnitOfDataRate.MEGABITS_PER_SECOND,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda d: d.get("upload_rate"),
    ),
)


def _wan(d: dict[str, Any]) -> dict[str, Any]:
    return d.get("wan") or {}


ROUTER_SENSORS: tuple[GWNSensorDescription, ...] = (
    GWNSensorDescription(
        key="wan_ip",
        translation_key="wan_ip",
        value_fn=lambda d: _wan(d).get("ip"),
    ),
    GWNSensorDescription(
        key="wan_download_rate",
        translation_key="wan_download_rate",
        device_class=SensorDeviceClass.DATA_RATE,
        native_unit_of_measurement=UnitOfDataRate.MEGABITS_PER_SECOND,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda d: _wan(d).get("download_rate"),
    ),
    GWNSensorDescription(
        key="wan_upload_rate",
        translation_key="wan_upload_rate",
        device_class=SensorDeviceClass.DATA_RATE,
        native_unit_of_measurement=UnitOfDataRate.MEGABITS_PER_SECOND,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda d: _wan(d).get("upload_rate"),
    ),
)


@dataclass(frozen=True, kw_only=True)
class GWNNetworkSensorDescription(SensorEntityDescription):
    value_fn: Callable[[GWNCoordinator, int], Any]
    attrs_fn: Callable[[GWNCoordinator, int], dict[str, Any]] | None = None


def _clients(coord: GWNCoordinator, nid: int) -> list[dict[str, Any]]:
    return [c for c in coord.data.clients.values() if c["networkId"] == nid]


def _kind_sensor(key: str, kind: str) -> GWNNetworkSensorDescription:
    def attrs(coord: GWNCoordinator, nid: int) -> dict[str, Any]:
        online, total = coord.kind_counts(nid, kind)
        return {"online": online, "offline": total - online, "total": total}

    return GWNNetworkSensorDescription(
        key=key,
        translation_key=key,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda coord, nid: coord.kind_counts(nid, kind)[0],
        attrs_fn=attrs,
    )


NETWORK_SENSORS: tuple[GWNNetworkSensorDescription, ...] = (
    _kind_sensor("routers_online", ROUTER),
    _kind_sensor("switches_online", SWITCH),
    _kind_sensor("aps_online", AP),
    GWNNetworkSensorDescription(
        key="wireless_clients",
        translation_key="wireless_clients",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda coord, nid: sum(1 for c in _clients(coord, nid) if c.get("ssid")),
    ),
    GWNNetworkSensorDescription(
        key="wired_clients",
        translation_key="wired_clients",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda coord, nid: sum(1 for c in _clients(coord, nid) if not c.get("ssid")),
    ),
    GWNNetworkSensorDescription(
        key="download_rate",
        translation_key="download_rate",
        device_class=SensorDeviceClass.DATA_RATE,
        native_unit_of_measurement=UnitOfDataRate.MEGABITS_PER_SECOND,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda coord, nid: coord.network_rate(nid, "download"),
    ),
    GWNNetworkSensorDescription(
        key="upload_rate",
        translation_key="upload_rate",
        device_class=SensorDeviceClass.DATA_RATE,
        native_unit_of_measurement=UnitOfDataRate.MEGABITS_PER_SECOND,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda coord, nid: coord.network_rate(nid, "upload"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: GWNConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coord = entry.runtime_data
    entry.async_on_unload(
        add_when_seen(
            coord,
            async_add_entities,
            lambda: coord.data.devices,
            lambda mac: [GWNDeviceSensor(coord, mac, d) for d in DEVICE_SENSORS]
            + (
                [GWNDeviceSensor(coord, mac, d) for d in ROUTER_SENSORS]
                if coord.data.devices[mac].get("kind") == ROUTER
                else []
            ),
        )
    )
    entry.async_on_unload(
        add_when_seen(
            coord,
            async_add_entities,
            lambda: coord.data.networks,
            lambda nid: [GWNNetworkClientsSensor(coord, nid)]
            + [GWNNetworkSensor(coord, nid, d) for d in NETWORK_SENSORS],
        )
    )
    entry.async_on_unload(
        add_when_seen(
            coord,
            async_add_entities,
            lambda: coord.data.ssids,
            lambda sid: [GWNSsidClientsSensor(coord, sid)],
        )
    )


class GWNDeviceSensor(GWNDeviceEntity, SensorEntity):
    entity_description: GWNSensorDescription

    def __init__(self, coord: GWNCoordinator, mac: str, desc: GWNSensorDescription) -> None:
        super().__init__(coord, mac, desc.key)
        self.entity_description = desc

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.device)


class GWNNetworkSensor(GWNNetworkEntity, SensorEntity):
    entity_description: GWNNetworkSensorDescription

    def __init__(self, coord: GWNCoordinator, network_id: int, desc: GWNNetworkSensorDescription) -> None:
        super().__init__(coord, network_id, desc.key)
        self.entity_description = desc

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator, self.network_id)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        fn = self.entity_description.attrs_fn
        return fn(self.coordinator, self.network_id) if fn else None


class GWNNetworkClientsSensor(GWNNetworkEntity, SensorEntity):
    _attr_translation_key = "connected_clients"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coord: GWNCoordinator, network_id: int) -> None:
        super().__init__(coord, network_id, "connected_clients")

    @property
    def native_value(self) -> int:
        return sum(1 for c in self.coordinator.data.clients.values() if c["networkId"] == self.network_id)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        clients = [c for c in self.coordinator.data.clients.values() if c["networkId"] == self.network_id]
        wireless = sum(1 for c in clients if c.get("ssid"))

        def tally(field: str) -> dict[str, int]:
            counts: dict[str, int] = {}
            for c in clients:
                label = c.get(field) or "Unknown"
                counts[label] = counts.get(label, 0) + 1
            return dict(sorted(counts.items(), key=lambda kv: -kv[1]))

        return {
            "wireless": wireless,
            "wired": len(clients) - wireless,
            "by_os": tally("dhcpOs"),
            "by_manufacturer": tally("dhcpManufacture"),
            "by_ssid": tally("ssid"),
        }


class GWNSsidClientsSensor(CoordinatorEntity[GWNCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "ssid_clients"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coord: GWNCoordinator, ssid_id: int) -> None:
        super().__init__(coord)
        self.ssid_id = ssid_id
        ssid = coord.data.ssids[ssid_id]
        from .switch import ssid_device_info  # shared device for SSID entities

        self._attr_unique_id = f"{coord.config_entry.entry_id}_ssid{ssid_id}_clients"
        self._attr_device_info = ssid_device_info(coord, ssid_id, ssid)

    @property
    def available(self) -> bool:
        return super().available and self.ssid_id in self.coordinator.data.ssids

    @property
    def native_value(self) -> int | None:
        ssid = self.coordinator.data.ssids.get(self.ssid_id, {})
        name = ssid.get("ssidName")
        live = sum(1 for c in self.coordinator.data.clients.values() if name and c.get("ssid") == name)
        return live if live else _int(ssid.get("onlineDevices"))
