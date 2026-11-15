# Grandstream GWN for Home Assistant

A custom integration for Grandstream GWN networking (access points, switches, routers). It connects through the official GWN Northbound API, using a **self-hosted GWN Manager** (local) or **GWN.Cloud**.

## What you get

| Platform | Entities |
|---|---|
| `device_tracker` | One per client (Wi-Fi or wired), for home/away presence. Attributes: SSID, AP, band, RSSI, switch port, IP, traffic |
| `binary_sensor` | Online status for each AP, switch or router. WAN status for routers |
| `sensor` | Per device: clients, upload, download, live download/upload rate (Mbit/s), CPU, memory, temperature, load, IP. Per router: WAN IP and WAN rates. Per network: routers/switches/APs online, connected, wireless and wired clients (with OS and manufacturer breakdowns), total rates. Per SSID: clients |
| `switch` | Turn each SSID on or off (for example, a guest network) |
| `button` | Reboot each AP, switch or router |
| services | `grandstream_gwn.block_client` / `grandstream_gwn.unblock_client` (by MAC address) |

Tested on GWN.Cloud with a GWN7062E router and a GWN7660E access point. Other GWN models and GWN Manager should work, since they use the same API, but are untested. Reports are welcome.

## Which controller do I have?

* **GWN Manager**: a VM or Docker container, usually at `https://<ip>:8443`. Recommended because traffic stays on your LAN.
* **GWN.Cloud**: you log in at `gwn.cloud`. Use your region URL, such as `https://www.gwn.cloud` or `https://eu.gwn.cloud`.
* **Embedded controller** (the controller built into a GWN AP or router): this has no Northbound API, so it is not supported. To use this integration, adopt the devices into a free GWN Manager.

## Setup

1. In GWN Manager or GWN.Cloud, open **System → API Developer Mode** (it may be under *Settings → API Developer*), turn it on, and copy the **App ID** and **Secret Key**.
2. Install the integration, then restart Home Assistant:
   * **HACS:** HACS → ⋮ → **Custom repositories** → add `https://github.com/FrostCreations/ha-grandstream-gwn` with type **Integration**, then download **Grandstream GWN**.
   * **Manually:** copy `custom_components/grandstream_gwn` into your Home Assistant `config/custom_components/` folder.
3. Go to **Settings → Devices & services → Add integration → Grandstream GWN**. Enter the controller URL, App ID and Secret Key. Leave *Verify SSL* off for a local GWN Manager with a self-signed certificate.
4. Optional: open **Configure** to tune the poll interval (default 30 s), the away delay (default 120 s), and the AP health poll interval (default 300 s).

## Dashboard

`dashboards/gwn_network.yaml` is a ready-made Network dashboard in the style of the GWN.Cloud overview: device and client counts, WAN status, bandwidth graphs, a client table, OS and manufacturer breakdowns, per-device health with restart buttons, and SSID toggles. It uses built-in cards only. The comment at the top explains which entity IDs to replace with your own.

## Known limits

* **PoE port control is not available.** The GWN API can read PoE status but cannot switch it. To power-cycle a PoE device, reboot the AP, or use a smart plug on the switch.
* **SSID toggle** reads the full SSID configuration and writes all of it back with only `ssidEnable` changed, as the GWN API requires. It also sends the devices that broadcast the SSID (`bindMacs`); without that list the cloud saves the change but never pushes it to them. The integration learns that list from each device's status and saves it, so a disabled SSID can still be turned back on after a restart. Changes take 1–2 minutes to reach the devices. Confirmed working on GWN.Cloud with a GWN7062E and a GWN7660E.
* **Client block**: the API's `block` value is inverted (0 = block), which the integration handles. Confirmed on GWN.Cloud. If blocking does the opposite on other firmware, set `BLOCK_VALUE = 1` in `api.py`. Like SSID changes, it takes 1–2 minutes to reach the device.
* The controller refreshes traffic and RSSI only about every 90 s, but it shows connects and disconnects right away. Polling faster than about 15 s gains nothing.
* Upload/download units are assumed to be bytes. If the numbers look wrong for your firmware, open an issue with a sample `ap/list` response.
* Wired clients appear only while they are connected.

## Development

```bash
pip install aiohttp pytest
pytest tests
```

The tests run the API client against a fake controller. They check request signing, token refresh, pagination, the full-payload SSID update, and the client-block payload.

`tools/probe.py` tests a controller from the command line without Home Assistant (see `TESTING.md`). `tools/deploy_to_ha.cmd` copies the integration into a Home Assistant Docker container over SSH.

API notes: see the docstring at the top of `custom_components/grandstream_gwn/api.py`.

## License

MIT. See `LICENSE`.

Not affiliated with or endorsed by Grandstream Networks.
