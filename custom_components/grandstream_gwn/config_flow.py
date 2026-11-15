"""Config flow for Grandstream GWN."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_URL
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import GWNAuthError, GWNClient, GWNError
from .const import (
    CONF_APP_ID,
    CONF_APP_SECRET,
    CONF_AWAY_DELAY,
    CONF_DETAIL_INTERVAL,
    CONF_SCAN_INTERVAL,
    CONF_VERIFY_SSL,
    DEFAULT_AWAY_DELAY,
    DEFAULT_DETAIL_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_URL,
    DOMAIN,
)


async def _validate(hass, data: dict[str, Any]) -> None:
    client = GWNClient(
        async_get_clientsession(hass, verify_ssl=data[CONF_VERIFY_SSL]),
        data[CONF_URL],
        data[CONF_APP_ID],
        data[CONF_APP_SECRET],
        data[CONF_VERIFY_SSL],
    )
    await client.authenticate()
    await client.get_networks()


class GWNConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up a GWN Manager or GWN.Cloud connection."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input = {
                **user_input,
                CONF_URL: user_input[CONF_URL].strip().rstrip("/"),
                CONF_APP_ID: user_input[CONF_APP_ID].strip(),
                CONF_APP_SECRET: user_input[CONF_APP_SECRET].strip(),
            }
            host = urlparse(user_input[CONF_URL]).netloc or user_input[CONF_URL]
            await self.async_set_unique_id(f"{host}_{user_input[CONF_APP_ID]}")
            self._abort_if_unique_id_configured()
            try:
                await _validate(self.hass, user_input)
            except GWNAuthError:
                errors["base"] = "invalid_auth"
            except GWNError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(title=f"GWN ({host})", data=user_input)

        schema = vol.Schema(
            {
                vol.Required(CONF_URL, default=DEFAULT_URL): str,
                vol.Required(CONF_APP_ID): str,
                vol.Required(CONF_APP_SECRET): str,
                vol.Required(CONF_VERIFY_SSL, default=False): bool,
            }
        )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(schema, user_input or {}),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            data = {**entry.data, CONF_APP_SECRET: user_input[CONF_APP_SECRET].strip()}
            try:
                await _validate(self.hass, data)
            except GWNAuthError:
                errors["base"] = "invalid_auth"
            except GWNError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(entry, data=data)
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_APP_SECRET): str}),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return GWNOptionsFlow()


class GWNOptionsFlow(OptionsFlow):
    """Polling and presence tuning."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        opts = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SCAN_INTERVAL,
                        default=opts.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                    ): vol.All(int, vol.Range(min=10, max=3600)),
                    vol.Required(
                        CONF_AWAY_DELAY, default=opts.get(CONF_AWAY_DELAY, DEFAULT_AWAY_DELAY)
                    ): vol.All(int, vol.Range(min=0, max=3600)),
                    vol.Required(
                        CONF_DETAIL_INTERVAL,
                        default=opts.get(CONF_DETAIL_INTERVAL, DEFAULT_DETAIL_INTERVAL),
                    ): vol.All(int, vol.Range(min=60, max=3600)),
                }
            ),
        )
