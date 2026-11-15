"""Tests for the GWN API client against a fake controller (no Home Assistant needed).

Run:  pip install aiohttp pytest && pytest tests
"""

import asyncio
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest
from aiohttp import ClientSession, web

_spec = importlib.util.spec_from_file_location(
    "gwn_api", Path(__file__).parents[1] / "custom_components/grandstream_gwn/api.py"
)
api = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(api)

APP_ID, SECRET = "app123", "s3cret"


def expected_sig(token, ts, body):
    s = f"&access_token={token}&appID={APP_ID}&secretKey={SECRET}&timestamp={ts}&"
    if body:
        s += hashlib.sha256(body.encode()).hexdigest() + "&"
    return hashlib.sha256(s.encode()).hexdigest()


class FakeController:
    def __init__(self):
        self.tokens_issued = 0
        self.fail_next_with = None
        self.glitch_next = 0  # answers to garble like GWN.Cloud does under load
        self.ssid_updates = []
        self.calls = []

    def app(self):
        app = web.Application()
        app.router.add_get("/oauth/token", self.token)
        app.router.add_post("/oapi/{path:.*}", self.oapi)
        return app

    async def token(self, req):
        if req.query.get("client_secret") != SECRET:
            return web.Response(status=401)
        self.tokens_issued += 1
        return web.json_response({"access_token": f"tok{self.tokens_issued}", "expires_in": 3600})

    async def oapi(self, req):
        body = await req.text()
        q = req.query
        assert "secretKey" not in q, "secret must never be sent"
        if q["signature"] != expected_sig(q["access_token"], q["timestamp"], body):
            return web.json_response({"retCode": 50003, "msg": "Authentication Failure"})
        if self.fail_next_with:
            code, self.fail_next_with = self.fail_next_with, None
            return web.json_response({"retCode": code, "msg": "expired"})
        if self.glitch_next:
            self.glitch_next -= 1
            return web.Response(text='{"retCode":0,"data":{}}{"retCode":50004,"msg":"Service error"}')
        path = "/" + req.match_info["path"]
        data = json.loads(body) if body else {}
        self.calls.append((path, data))
        if path == "/v1.0.0/network/list":
            return web.json_response({"retCode": 0, "data": {"result": [{"id": 7}], "totalPage": 1}})
        if path == "/v1.0.0/client/list":
            page = data["pageNum"]
            rows = [{"clientId": f"aa-bb-cc-00-00-{page:02x}"}]
            return web.json_response({"retCode": 0, "data": {"result": rows, "totalPage": 3}})
        if path == "/v1.0.0/ap/list":
            dev = {"mac": "00:0B:82:00:00:02", "status": 1, "apType": "GWN7062E"}
            return web.json_response({"retCode": 0, "data": {"result": [dev], "totalPage": 1}})
        if path == "/v2.0.0/ap/info":
            return web.json_response({"retCode": 0, "data": {"ssid": [{"Guest": {}}]}})
        if path == "/v1.0.0/ssid/list":
            return web.json_response({"retCode": 0, "data": {"result": [{"id": 5, "ssidName": "Guest", "wifiEnabled": 1}], "totalPage": 1}})
        if path == "/v1.0.0/ssid/configuration":
            cfg = {"ssidSsid": "Guest", "ssidEnable": 1, "ssidWpaKey": "x", "ssidVlanid": 20,
                   "ssidRemark": None, "ssidSchedule": None}
            return web.json_response({"retCode": 0, "data": {"configuration": cfg}})
        if path == "/v1.0.0/ssid/update":
            if not data.get("bindMacs"):
                return web.json_response({"retCode": 0, "data": {}})  # saved, but pushed nowhere
            if data.get("ssidRemark") is None or None in data.values():
                return web.json_response({"retCode": 70034, "msg": "ssid remark format error"})
            self.ssid_updates.append(data)
            return web.json_response({"retCode": 0, "data": {}})
        if path == "/v1.0.0/glued":
            return web.Response(text='{"retCode":0,"data":{}}{"retCode":50004,"msg":"Service error"}')
        return web.json_response({"retCode": 0, "data": {}})


@pytest.fixture
def run():
    loop = asyncio.new_event_loop()
    yield loop.run_until_complete
    loop.close()


async def _with_client(fake, fn, secret=SECRET):
    runner = web.AppRunner(fake.app())
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        async with ClientSession() as session:
            client = api.GWNClient(session, f"http://127.0.0.1:{port}/", APP_ID, secret)
            return await fn(client)
    finally:
        await runner.cleanup()


def test_signature_matches_reference():
    assert api.build_signature("t", APP_ID, SECRET, 1, None) == expected_sig("t", 1, None)
    assert api.build_signature("t", APP_ID, SECRET, 1, '{"a":1}') == expected_sig("t", 1, '{"a":1}')


def test_normalize_mac():
    assert api.normalize_mac("aa-bb-cc-dd-ee-ff") == "AA:BB:CC:DD:EE:FF"
    assert api.normalize_mac("aabbccddeeff") == "AA:BB:CC:DD:EE:FF"


def test_signed_request_and_pagination(run):
    fake = FakeController()

    async def go(c):
        nets = await c.get_networks()
        clients = await c.get_connected_clients(7)
        return nets, clients

    nets, clients = run(_with_client(fake, go))
    assert nets == [{"id": 7}]
    assert [c["clientId"] for c in clients] == [f"aa-bb-cc-00-00-{i:02x}" for i in (1, 2, 3)]
    assert fake.tokens_issued == 1  # token reused


def test_token_refresh_on_auth_retcode(run):
    fake = FakeController()

    async def go(c):
        await c.get_networks()
        fake.fail_next_with = 50002
        return await c.get_networks()

    assert run(_with_client(fake, go)) == [{"id": 7}]
    assert fake.tokens_issued == 2


def test_bad_secret_raises_auth_error(run):
    with pytest.raises(api.GWNAuthError):
        run(_with_client(FakeController(), lambda c: c.authenticate(), secret="wrong"))


def test_glued_response_is_connection_error(run):
    with pytest.raises(api.GWNConnectionError):
        run(_with_client(FakeController(), lambda c: c._post("/v1.0.0/glued", {"x": 1})))


def test_one_cloud_glitch_is_retried(run):
    fake = FakeController()

    async def go(c):
        fake.glitch_next = 1
        return await c.get_networks()

    assert run(_with_client(fake, go)) == [{"id": 7}]
    assert fake.tokens_issued == 1  # glitch is not an auth problem


def test_repeated_glitch_still_fails(run):
    fake = FakeController()
    fake.glitch_next = 2
    with pytest.raises(api.GWNConnectionError):
        run(_with_client(fake, lambda c: c.get_networks()))


def test_ssid_toggle_sends_full_config(run):
    fake = FakeController()
    run(_with_client(fake, lambda c: c.set_ssid_enabled(
        5, False, bind_macs=["000b82000002", "00:0B:82:00:00:01"], network_id=7)))
    assert fake.ssid_updates == [
        {"ssidSsid": "Guest", "ssidEnable": 0, "ssidWpaKey": "x", "ssidVlanid": 20,
         "ssidRemark": "", "id": 5, "networkId": 7,
         "bindMacs": ["00:0B:82:00:00:01", "00:0B:82:00:00:02"]}
    ]


def test_ssid_toggle_refuses_without_devices(run):
    fake = FakeController()
    with pytest.raises(api.GWNError, match="Don't know which devices"):
        run(_with_client(fake, lambda c: c.set_ssid_enabled(5, False, bind_macs=[])))
    assert fake.ssid_updates == []


def test_devices_broadcasting_matches_real_ap_info_shape():
    # shapes taken from a real GWN.Cloud ap/info response
    details = {
        "00:0B:82:00:00:02": {"ssid": [{"GWN": {"5g": [{"rssi": -56, "clientMac": "020000000001"}]}}]},
        "00:0B:82:00:00:01": {"ssid": [{"GWN": {}}]},
        "AA:AA:AA:AA:AA:AA": {"ssid": [{"Other": {}}]},
        "BB:BB:BB:BB:BB:BB": {},
    }
    assert api.devices_broadcasting("GWN", 1001, details) == ["00:0B:82:00:00:01", "00:0B:82:00:00:02"]
    assert api.devices_broadcasting("Nope", 1, details) == []


def test_block_client_payload(run):
    fake = FakeController()
    run(_with_client(fake, lambda c: c.set_client_blocked(7, "aabbccddeeff", True)))
    assert fake.calls[-1] == (
        "/v1.0.0/client/block",
        {"networkId": 7, "clientId": "AA:BB:CC:DD:EE:FF", "block": api.BLOCK_VALUE},
    )
