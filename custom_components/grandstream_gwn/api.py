"""Async client for the Grandstream GWN Manager / GWN.Cloud Northbound API.

Works against a self-hosted GWN Manager (e.g. https://192.168.1.10:8443) and
GWN.Cloud (e.g. https://www.gwn.cloud, https://eu.gwn.cloud). Both expose the
same API; only the base URL differs.

Enable it in GWN Manager under System > API Developer Mode to get an App ID
and Secret Key.

Notes learned from the official docs and community testing:
* Token: GET/POST {base}/oauth/token, grant_type=client_credentials. No /oapi.
* Every other call: POST {base}/oapi/<endpoint> with query params
  access_token, appID, timestamp (epoch ms) and signature, where
  signature = sha256("&access_token=..&appID=..&secretKey=..&timestamp=..&" +
                     sha256(body) + "&")
  The secret key is part of the signed string but never sent.
* The signed body bytes must equal the sent bytes, so JSON is compact.
* Endpoints without parameters reject a signed "{}" (retCode 50003); send
  no body at all for those.
* Responses: {"retCode": 0, "msg": "", "data": {...}}.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

TOKEN_MARGIN = 60  # refresh this many seconds before expiry
PAGE_SIZE = 100
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=20)

# Value of the `block` field that means "block". Community testing reports
# the controller uses 0 = block, 1 = unblock. Flip if your firmware differs.
BLOCK_VALUE = 0

# retCodes that mean "token/signature problem, refresh and retry once"
TRANSIENT_RETCODES = {50004}  # "Service error" under cloud load
AUTH_RETCODES = {50001, 50002, 50003, 40001, 40002}


class GWNError(Exception):
    """Base error."""


class GWNConnectionError(GWNError):
    """Controller unreachable or returned garbage."""


class GWNTransientError(GWNConnectionError):
    """One-off cloud glitch (garbled or "Service error" answer); worth one retry."""


class GWNAuthError(GWNError):
    """Credentials rejected."""


class GWNApiError(GWNError):
    """API returned a non-zero retCode."""

    def __init__(self, ret_code: int, msg: str, endpoint: str) -> None:
        super().__init__(f"{endpoint}: retCode {ret_code} {msg}")
        self.ret_code = ret_code


def normalize_mac(mac: str) -> str:
    """Return AA:BB:CC:DD:EE:FF for any common MAC notation."""
    raw = re.sub(r"[^0-9A-Fa-f]", "", mac or "").upper()
    if len(raw) != 12:
        return (mac or "").upper()
    return ":".join(raw[i : i + 2] for i in range(0, 12, 2))


def build_signature(
    access_token: str, app_id: str, secret_key: str, timestamp_ms: int, body: str | None
) -> str:
    """Compute the GWN Northbound API request signature."""
    sign = (
        f"&access_token={access_token}&appID={app_id}"
        f"&secretKey={secret_key}&timestamp={timestamp_ms}&"
    )
    if body:
        sign += hashlib.sha256(body.encode("utf-8")).hexdigest() + "&"
    return hashlib.sha256(sign.encode("utf-8")).hexdigest()


# SSID text fields that must be "" rather than null when unset
SSID_TEXT_FIELDS = ("ssidRemark",)


def clean_ssid_payload(config: dict[str, Any]) -> dict[str, Any]:
    """Make an ssid/configuration result acceptable to ssid/update."""
    payload = {k: v for k, v in config.items() if v is not None}
    for key in SSID_TEXT_FIELDS:
        if key in config and config[key] is None:
            payload[key] = ""
    return payload


def devices_broadcasting(
    ssid_name: str, ssid_id: int, details_by_mac: dict[str, dict[str, Any]]
) -> list[str]:
    """MACs whose ap/info `ssid` list includes this SSID (by name or id).

    The API has no "which devices is this SSID assigned to" call, but each
    device's ap/info lists the SSIDs it is broadcasting, e.g.
    "ssid": [{"GWN": {"5g": [...]}}]. A disabled SSID drops out of that list,
    so callers should remember the last non-empty answer.
    """
    wanted = {str(ssid_name), str(ssid_id)}
    found = []
    for mac, info in details_by_mac.items():
        for entry in (info or {}).get("ssid") or []:
            if isinstance(entry, dict) and wanted & {str(k) for k in entry}:
                found.append(normalize_mac(mac))
                break
    return sorted(set(found))


class GWNClient:
    """Minimal async GWN API client."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        base_url: str,
        app_id: str,
        secret_key: str,
        verify_ssl: bool = True,
    ) -> None:
        self._session = session
        self._base = base_url.strip().rstrip("/")
        self._app_id = app_id.strip()
        self._secret = secret_key.strip()
        self._ssl: bool | None = None if verify_ssl else False
        self._token: str | None = None
        self._token_expiry = 0.0
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ auth

    async def authenticate(self, force: bool = False) -> None:
        """Fetch an access token if missing/expired."""
        async with self._lock:
            if not force and self._token and time.time() < self._token_expiry - TOKEN_MARGIN:
                return
            url = f"{self._base}/oauth/token"
            params = {
                "client_id": self._app_id,
                "client_secret": self._secret,
                "grant_type": "client_credentials",
            }
            try:
                async with self._session.get(
                    url, params=params, ssl=self._ssl, timeout=REQUEST_TIMEOUT
                ) as resp:
                    if resp.status in (400, 401, 403):
                        raise GWNAuthError(f"Token request rejected (HTTP {resp.status})")
                    if resp.status != 200:
                        raise GWNConnectionError(f"Token request failed (HTTP {resp.status})")
                    data = await resp.json(content_type=None)
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
                raise GWNConnectionError(
                    f"Cannot reach {self._base}: {type(err).__name__} {err}".rstrip()
                ) from err

            token = data.get("access_token") if isinstance(data, dict) else None
            if not token:
                raise GWNAuthError(f"No access_token in response: {data}")
            self._token = token
            self._token_expiry = time.time() + int(data.get("expires_in", 3600))

    # --------------------------------------------------------------- request

    async def _post(self, endpoint: str, body: dict[str, Any] | None = None) -> Any:
        """Signed POST; refreshes the token and retries once on auth errors."""
        refresh = False
        for attempt in (1, 2):
            await self.authenticate(force=refresh)
            try:
                return await self._post_once(endpoint, body)
            except GWNTransientError as err:
                if attempt == 1:
                    _LOGGER.debug("%s, retrying", err)
                    await asyncio.sleep(1)
                    continue
                raise
            except GWNApiError as err:
                if attempt == 1 and err.ret_code in AUTH_RETCODES:
                    _LOGGER.debug("Auth retCode %s on %s, refreshing token", err.ret_code, endpoint)
                    refresh = True
                    continue
                raise
            except GWNAuthError:
                if attempt == 1:
                    refresh = True
                    continue
                raise
        raise GWNError("unreachable")

    async def _post_once(self, endpoint: str, body: dict[str, Any] | None) -> Any:
        assert self._token
        payload = json.dumps(body, separators=(",", ":")) if body else None
        ts = int(time.time() * 1000)
        params = {
            "access_token": self._token,
            "appID": self._app_id,
            "timestamp": str(ts),
            "signature": build_signature(self._token, self._app_id, self._secret, ts, payload),
        }
        url = f"{self._base}/oapi{endpoint}"
        try:
            async with self._session.post(
                url,
                params=params,
                data=payload,
                headers={"Content-Type": "application/json"},
                ssl=self._ssl,
                timeout=REQUEST_TIMEOUT,
            ) as resp:
                if resp.status == 401:
                    raise GWNAuthError(f"{endpoint}: HTTP 401")
                if resp.status != 200:
                    raise GWNConnectionError(f"{endpoint}: HTTP {resp.status}")
                text = await resp.text()
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise GWNConnectionError(f"{endpoint}: {type(err).__name__} {err}".rstrip()) from err

        try:
            # GWN.Cloud occasionally glues a second JSON object after a valid one
            # under load; raw_decode reads just the first and we treat any
            # trailing error object as a transient failure.
            data, end = json.JSONDecoder().raw_decode(text.strip())
        except ValueError as err:
            raise GWNConnectionError(f"{endpoint}: invalid JSON") from err
        if text.strip()[end:].strip():
            raise GWNTransientError(f"{endpoint}: trailing data in response")

        ret = int(data.get("retCode", 0))
        if ret in TRANSIENT_RETCODES:
            raise GWNTransientError(f"{endpoint}: retCode {ret} {data.get('msg', '')}")
        if ret != 0:
            raise GWNApiError(ret, str(data.get("msg", "")), endpoint)
        return data.get("data")

    async def _post_paged(self, endpoint: str, body: dict[str, Any]) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        page = 1
        while True:
            data = await self._post(endpoint, {**body, "pageNum": page, "pageSize": PAGE_SIZE})
            if not isinstance(data, dict):
                break
            chunk = data.get("result") or []
            results.extend(chunk)
            total_pages = int(data.get("totalPage") or data.get("pages") or 0)
            if total_pages:
                if page >= total_pages:
                    break
            elif len(chunk) < PAGE_SIZE:
                break
            page += 1
            if page > 100:  # safety net
                break
        return results

    # -------------------------------------------------------------- read API

    async def get_networks(self) -> list[dict[str, Any]]:
        return await self._post_paged(
            "/v1.0.0/network/list", {"type": "asc", "order": "id", "search": ""}
        )

    async def get_devices(self, network_id: int) -> list[dict[str, Any]]:
        """APs, switches and routers in a network."""
        return await self._post_paged(
            "/v1.0.0/ap/list", {"networkId": network_id, "filter": {"showType": "all"}}
        )

    async def get_device_info(self, network_id: int, mac: str) -> dict[str, Any]:
        return await self._post("/v1.0.0/device/info", {"networkId": network_id, "mac": mac}) or {}

    async def get_ap_details(self, mac: str) -> dict[str, Any]:
        """CPU/memory/temperature etc. Only answers for a single MAC per call."""
        return await self._post("/v2.0.0/ap/info", {"mac": [mac]}) or {}

    async def get_switch_ports(self, network_id: int, mac: str) -> Any:
        return await self._post("/v1.0.0/switch/port/info", {"networkId": network_id, "mac": mac})

    async def get_connected_clients(self, network_id: int) -> list[dict[str, Any]]:
        """Currently connected clients (wired + wireless) with live counters."""
        return await self._post_paged(
            "/v1.0.0/client/list",
            {"networkId": network_id, "untilNow": 0, "filter": {"ssidId": "all_client"}},
        )

    async def get_ssids(self, network_id: int) -> list[dict[str, Any]]:
        return await self._post_paged("/v1.0.0/ssid/list", {"networkId": network_id})

    async def get_ssid_configuration(self, ssid_id: int) -> dict[str, Any]:
        data = await self._post("/v1.0.0/ssid/configuration", {"id": ssid_id}) or {}
        return data.get("configuration", data)

    async def get_firmware_versions(self, network_id: int) -> list[dict[str, Any]]:
        data = await self._post("/v1.0.0/upgrade/version", {"networkId": network_id}) or {}
        return data.get("result", []) if isinstance(data, dict) else []

    # ------------------------------------------------------------- write API

    async def reboot_device(self, mac: str) -> None:
        await self._post("/v1.0.0/ap/reboot", {"mac": [normalize_mac(mac)]})

    async def set_ssid_enabled(
        self,
        ssid_id: int,
        enabled: bool,
        bind_macs: list[str],
        network_id: int | None = None,
    ) -> Any:
        """Toggle an SSID.

        bind_macs must list the devices that broadcast this SSID. The API
        docs mark `bindMacs` as required: without it the cloud saves the new
        setting but never pushes it to any device (seen on GWN.Cloud: the UI
        showed "disabled" while the router kept broadcasting). bindMacs only
        adds devices; removing needs `removeMacs`, which we never send, so
        passing the current devices cannot unassign anything.

        ssid/update replaces the whole SSID config, so we read the current
        configuration first and send it back with only ssidEnable changed.
        Never send a partial payload: missing keys get reset on the controller.

        ssid/configuration returns unset fields as null, but ssid/update
        rejects null for some of them (e.g. retCode 70034 "ssid remark format
        error" for ssidRemark). Unset text fields are sent as "", and other
        null fields are left out, since null already meant "not set".
        """
        macs = sorted({normalize_mac(m) for m in bind_macs if m})
        if not macs:
            raise GWNError(
                f"Don't know which devices broadcast SSID {ssid_id} yet, so the change "
                "would not reach them. Wait for the next device poll, or toggle it once in GWN."
            )
        current = await self.get_ssid_configuration(ssid_id)
        if not current or "ssidSsid" not in current:
            raise GWNError(f"Refusing to update SSID {ssid_id}: could not read its configuration")
        payload = clean_ssid_payload(current)
        payload.update({"id": ssid_id, "ssidEnable": 1 if enabled else 0, "bindMacs": macs})
        if network_id is not None:
            payload["networkId"] = network_id
        return await self._post("/v1.0.0/ssid/update", payload)

    async def set_client_blocked(self, network_id: int, mac: str, blocked: bool) -> Any:
        """Block or unblock a client.

        Community testing reports the controller's `block` field is inverted
        (0 = block, 1 = unblock). If your firmware behaves the other way,
        flip BLOCK_VALUE at the top of this file.
        """
        return await self._post(
            "/v1.0.0/client/block",
            {
                "networkId": network_id,
                "clientId": normalize_mac(mac),
                "block": BLOCK_VALUE if blocked else 1 - BLOCK_VALUE,
            },
        )
