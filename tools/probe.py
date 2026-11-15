"""Talk to a real GWN controller with the integration's API client, outside Home Assistant.

Read-only by default. Write tests always restore the original state.

  pip install aiohttp
  python tools/probe.py --url https://192.168.1.10:8443 --app-id APPID --insecure
  python tools/probe.py ... --dump gwn_dump.json          # save raw responses
  python tools/probe.py ... --toggle-ssid 12               # SSID off, wait, back on
  python tools/probe.py ... --block-test AA:BB:CC:DD:EE:FF # block, wait, unblock
  python tools/probe.py ... --reboot AA:BB:CC:DD:EE:FF     # reboot one device

The Secret Key is read from the GWN_SECRET environment variable, or prompted for.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import importlib.util
import json
import os
import sys
import time
from pathlib import Path

import aiohttp

_spec = importlib.util.spec_from_file_location(
    "gwn_api", Path(__file__).resolve().parents[1] / "custom_components/grandstream_gwn/api.py"
)
api = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(api)


def ok(msg: str) -> None:
    print(f"  [OK]   {msg}")


def warn(msg: str) -> None:
    print(f"  [WARN] {msg}")


def fail(msg: str) -> None:
    print(f"  [FAIL] {msg}")


def confirm(prompt: str) -> bool:
    return input(f"\n{prompt} [y/N] ").strip().lower() == "y"


async def step(name: str, coro, dump: dict):
    """Run one API call, time it, store the raw result."""
    start = time.monotonic()
    try:
        result = await coro
    except api.GWNError as err:
        fail(f"{name}: {err}")
        dump[name] = {"error": str(err)}
        return None
    ok(f"{name} ({(time.monotonic() - start) * 1000:.0f} ms)")
    dump[name] = result
    return result


def show_keys(label: str, row: dict | None) -> None:
    if row:
        print(f"         {label} fields: {', '.join(sorted(row))}")


async def main(args: argparse.Namespace) -> int:
    secret = os.environ.get("GWN_SECRET") or getpass.getpass("Secret Key: ")
    dump: dict = {}
    async with aiohttp.ClientSession() as session:
        client = api.GWNClient(session, args.url, args.app_id, secret, verify_ssl=not args.insecure)

        print("\n1. Authentication")
        try:
            await client.authenticate()
            ok("token issued")
        except api.GWNAuthError as err:
            fail(f"credentials rejected: {err}")
            return 1
        except api.GWNConnectionError as err:
            fail(f"cannot reach controller: {err}")
            print("         Check the URL/port, and try --insecure for a self-signed certificate.")
            return 1

        print("\n2. Networks")
        networks = await step("network/list", client.get_networks(), dump) or []
        for n in networks:
            print(f"         id={n.get('id')}  name={n.get('networkName')}")
        if not networks:
            fail("no networks visible to this App ID. Check API permissions / 'restrict to networks'.")
            return 1

        for net in networks:
            nid = int(net["id"])
            print(f"\n3. Network {nid} ({net.get('networkName')})")

            devices = await step(f"ap/list[{nid}]", client.get_devices(nid), dump) or []
            for d in devices:
                status = "online" if str(d.get("status")).lower() in ("1", "online", "true") else f"status={d.get('status')}"
                print(
                    f"         {d.get('mac')}  {d.get('apType') or '?':12} {d.get('name') or '':20} "
                    f"{status}  clients={d.get('clients')}  up={d.get('upload')} down={d.get('download')}"
                )
            show_keys("device", devices[0] if devices else None)

            clients = await step(f"client/list[{nid}]", client.get_connected_clients(nid), dump) or []
            wired = sum(1 for c in clients if not c.get("ssid"))
            print(f"         {len(clients)} connected ({len(clients) - wired} wireless, {wired} wired)")
            for c in clients[: args.show]:
                name = c.get("name") or c.get("clientName") or c.get("dhcpHostname") or ""
                print(
                    f"         {c.get('clientId')}  {name[:24]:24} {c.get('ipv4') or '':15} "
                    f"ssid={c.get('ssid') or '-'}  ap={c.get('apName') or '-'}  rssi={c.get('rssi')}"
                )
            if len(clients) > args.show:
                print(f"         ... {len(clients) - args.show} more (use --show N)")
            show_keys("client", clients[0] if clients else None)

            ssids = await step(f"ssid/list[{nid}]", client.get_ssids(nid), dump) or []
            for s in ssids:
                print(f"         id={s.get('id')}  {s.get('ssidName')!r:24} wifiEnabled={s.get('wifiEnabled')}")
            if ssids and "wifiEnabled" not in ssids[0]:
                warn("ssid/list has no 'wifiEnabled' field; the SSID switch state will show unknown")

            print("\n4. Per-device health (ap/info)")
            for d in devices:
                info = await step(f"ap/info[{d.get('mac')}]", client.get_ap_details(d["mac"]), dump)
                if info:
                    print(
                        f"         cpu={info.get('cpuUsage')}  mem={info.get('usedMemory')}  "
                        f"temp={info.get('temperature')}"
                    )

        # ---------------------------------------------------------- writes
        if args.toggle_ssid is not None:
            await toggle_ssid(client, args.toggle_ssid)
        if args.block_test:
            await block_test(client, networks, args.block_test)
        if args.reboot:
            if confirm(f"Reboot {args.reboot}? Clients on it will drop for 1-3 minutes."):
                await step("ap/reboot", client.reboot_device(args.reboot), dump)
                print("         Watch the device's LED / GWN Manager; it should go offline then return.")

    if args.dump:
        Path(args.dump).write_text(json.dumps(dump, indent=2, default=str), encoding="utf-8")
        print(f"\nRaw responses saved to {args.dump}")
        print("It contains client MACs, hostnames and IPs. Review it before sharing it anywhere.")
    print()
    return 0


async def toggle_ssid(client, ssid_id: int) -> None:
    print(f"\nWrite test: SSID toggle (id={ssid_id})")
    before = await client.get_ssid_configuration(ssid_id)
    print(f"         {len(before)} config fields read; ssidSsid={before.get('ssidSsid')!r} ssidEnable={before.get('ssidEnable')}")
    nulls = sorted(k for k, v in before.items() if v is None)
    if nulls:
        print(f"         unset (null) fields, handled before sending: {', '.join(nulls)}")
    if not confirm(f"Turn SSID {before.get('ssidSsid')!r} OFF, then back ON when you press Enter?"):
        return
    # enable-like fields are flags, safe to print (unlike keys/passwords)
    flags = {k: v for k, v in before.items() if "enable" in k.lower() and not isinstance(v, (dict, list))}
    print(f"         enable-type fields: {flags}")
    print(f"         ssidEnable is {type(before.get('ssidEnable')).__name__} {before.get('ssidEnable')!r}")
    net_id, macs = await find_ssid_devices(client, ssid_id, before.get("ssidSsid", ""))
    print(f"         network={net_id}; devices broadcasting it (sent as bindMacs): {macs or 'NONE FOUND'}")
    if not macs:
        fail("can't tell which devices broadcast this SSID; is it currently enabled?")
        return
    reply = await client.set_ssid_enabled(ssid_id, False, bind_macs=macs, network_id=net_id)
    ok(f"disable sent; controller replied data={reply!r}")
    await report_state(client, ssid_id, "right after disable")
    print("         Waiting 20 s for the cloud to save/push the change...")
    await asyncio.sleep(20)
    await report_state(client, ssid_id, "20 s after disable")
    print("         Check now: is 'GWN' gone from the phone's Wi-Fi list? Does gwn.cloud show it disabled?")
    input("         Press Enter to turn it back on... ")
    await client.set_ssid_enabled(ssid_id, True, bind_macs=macs, network_id=net_id)
    ok("enable sent")
    after = await client.get_ssid_configuration(ssid_id)
    def norm(v):
        return "" if v is None else v

    changed = sorted(k for k in set(before) | set(after) if norm(before.get(k)) != norm(after.get(k)))
    if changed:
        warn(f"config differs from the original in: {', '.join(changed)}. Check these in GWN Manager!")
    else:
        ok("configuration identical to before the test")


async def find_ssid_devices(client, ssid_id: int, ssid_name: str):
    """Network id and the device MACs whose ap/info lists this SSID."""
    for net in await client.get_networks():
        nid = int(net["id"])
        if not any(int(s.get("id", -1)) == ssid_id for s in await client.get_ssids(nid)):
            continue
        details = {}
        for dev in await client.get_devices(nid):
            try:
                details[dev["mac"]] = await client.get_ap_details(dev["mac"])
            except api.GWNError:
                pass
        return nid, api.devices_broadcasting(ssid_name, ssid_id, details)
    return None, []


async def report_state(client, ssid_id: int, when: str) -> None:
    cfg = await client.get_ssid_configuration(ssid_id)
    listed = None
    for net in await client.get_networks():
        for s in await client.get_ssids(int(net["id"])):
            if int(s.get("id", -1)) == ssid_id:
                listed = s.get("wifiEnabled")
    print(f"         {when}: configuration ssidEnable={cfg.get('ssidEnable')!r}, ssid/list wifiEnabled={listed!r}")


async def report_block(client, net_id: int, mac: str, when: str) -> None:
    """Show the client's `block` field and whether it is still connected."""
    try:
        everyone = await client._post_paged("/v1.0.0/client/list", {"networkId": net_id, "untilNow": 1})
        live = await client.get_connected_clients(net_id)
    except api.GWNError as err:
        warn(f"{when}: could not read client list: {err}")
        return
    row = next((c for c in everyone if api.normalize_mac(c.get("clientId", "")) == mac), None)
    connected = any(api.normalize_mac(c.get("clientId", "")) == mac for c in live)
    block = row.get("block") if row else "not in list"
    print(f"         {when}: block field={block!r}, connected={connected}")


async def block_test(client, networks, mac: str) -> None:
    mac = api.normalize_mac(mac)
    print(f"\nWrite test: client block ({mac})")
    net_id = None
    for n in networks:
        if any(api.normalize_mac(c.get("clientId", "")) == mac for c in await client.get_connected_clients(int(n["id"]))):
            net_id = int(n["id"])
    if net_id is None:
        fail("client is not connected right now; connect it first")
        return
    if not confirm(f"Block {mac} (network {net_id}), then unblock when you press Enter?"):
        return
    await report_block(client, net_id, mac, "before")
    reply = await client.set_client_blocked(net_id, mac, True)
    ok(f"block sent (block={api.BLOCK_VALUE}); controller replied data={reply!r}")
    await report_block(client, net_id, mac, "right after block")
    print("         Waiting 20 s for the cloud to save/push the change...")
    await asyncio.sleep(20)
    await report_block(client, net_id, mac, "20 s after block")
    print(
        "         Give it 1-2 minutes. On the phone, turn off mobile data and try loading a page.\n"
        "         Also check gwn.cloud > Clients for a blocked status."
    )
    input("         Press Enter to unblock... ")
    reply = await client.set_client_blocked(net_id, mac, False)
    ok(f"unblock sent; controller replied data={reply!r}")
    await report_block(client, net_id, mac, "right after unblock")
    print("         The device should reconnect within 1-2 minutes.")
    print(
        "         If the device stayed online while 'blocked' and dropped after 'unblock',\n"
        "         the value is inverted on your firmware: set BLOCK_VALUE = 1 in api.py."
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", required=True, help="e.g. https://192.168.1.10:8443 or https://www.gwn.cloud")
    p.add_argument("--app-id", required=True)
    p.add_argument("--insecure", action="store_true", help="skip SSL verification (self-signed cert)")
    p.add_argument("--show", type=int, default=10, help="how many clients to list")
    p.add_argument("--dump", help="write raw API responses to this JSON file")
    p.add_argument("--toggle-ssid", type=int, metavar="SSID_ID")
    p.add_argument("--block-test", metavar="MAC")
    p.add_argument("--reboot", metavar="MAC")
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    sys.exit(asyncio.run(main(p.parse_args())))
