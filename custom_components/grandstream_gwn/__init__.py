"""Grandstream GWN (GWN Manager / GWN.Cloud) integration."""

from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_URL, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import GWNClient, GWNError, normalize_mac
from .const import (
    ATTR_MAC,
    CONF_APP_ID,
    CONF_APP_SECRET,
    CONF_VERIFY_SSL,
    DOMAIN,
    SERVICE_BLOCK_CLIENT,
    SERVICE_UNBLOCK_CLIENT,
)
from .coordinator import GWNCoordinator

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.DEVICE_TRACKER,
    Platform.SENSOR,
    Platform.SWITCH,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
SERVICE_SCHEMA = vol.Schema({vol.Required(ATTR_MAC): cv.string})

type GWNConfigEntry = ConfigEntry[GWNCoordinator]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Register services once."""

    async def handle_block(call: ServiceCall) -> None:
        mac = normalize_mac(call.data[ATTR_MAC])
        blocked = call.service == SERVICE_BLOCK_CLIENT
        for entry in hass.config_entries.async_loaded_entries(DOMAIN):
            coord: GWNCoordinator = entry.runtime_data
            client = coord.known_clients.get(mac)
            if client is None:
                continue
            try:
                await coord.client.set_client_blocked(client["networkId"], mac, blocked)
            except GWNError as err:
                raise HomeAssistantError(f"Could not update {mac}: {err}") from err
            await coord.async_request_refresh()
            return
        raise HomeAssistantError(f"Client {mac} has not been seen by any GWN controller")

    for service in (SERVICE_BLOCK_CLIENT, SERVICE_UNBLOCK_CLIENT):
        hass.services.async_register(DOMAIN, service, handle_block, schema=SERVICE_SCHEMA)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: GWNConfigEntry) -> bool:
    verify = entry.data.get(CONF_VERIFY_SSL, True)
    client = GWNClient(
        async_get_clientsession(hass, verify_ssl=verify),
        entry.data[CONF_URL],
        entry.data[CONF_APP_ID],
        entry.data[CONF_APP_SECRET],
        verify,
    )
    coordinator = GWNCoordinator(hass, entry, client)
    await coordinator.async_load_ssid_devices()
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    entry.async_on_unload(entry.add_update_listener(_reload_on_options))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _reload_on_options(hass: HomeAssistant, entry: GWNConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: GWNConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
