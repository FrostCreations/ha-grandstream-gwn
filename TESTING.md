# Testing on real hardware

This guide takes you from "never connected" to "every feature confirmed". It works in stages, so when something breaks you know which layer failed:

1. **Controller and API**: is the API on, and do your keys work?
2. **Probe script**: does the integration's API client read your network correctly? (No Home Assistant involved yet.)
3. **Write tests**: do SSID toggle, client block and reboot work on your firmware?
4. **Home Assistant**: do the entities show up and behave correctly?

Plan for about an hour. Keep a phone handy as a test client.

---

## Stage 0: What you need

- [ ] At least one Grandstream GWN access point (a switch or router is a bonus).
- [ ] A controller that has an API. Find out which one you have:
  - **GWN Manager** (self-hosted): you log in at `https://<some-ip>:8443`. ✅ Best choice.
  - **GWN.Cloud**: you log in at `gwn.cloud`. ✅ Works.
  - **Embedded controller**: you manage everything from one AP's own web page. ❌ It has no API. Install GWN Manager (free from Grandstream) and adopt your devices into it first.
- [ ] Python 3.11+ on your PC, with aiohttp installed:

```bash
pip install aiohttp pytest
```

- [ ] A test client you can safely disconnect, such as a spare phone. **Don't** use the device running Home Assistant or the PC you're testing from as the block target.

## Stage 1: Turn on the API

1. Log in to GWN Manager or GWN.Cloud as an administrator.
2. Find **API Developer Mode**. Depending on version, it's under *System → API Developer Mode* or *Settings → API Developer*. Turn it on.
3. Copy the **App ID** and **Secret Key**. Treat the secret like a password.
4. If there's an option like **"Restrict APIs to specific networks"**, leave it **off** for now. It needs a different login method that this integration doesn't support yet.

## Stage 2: Read-only probe

From the project folder, run the probe with the secret in an environment variable, so it doesn't end up in your shell history:

```powershell
$env:GWN_SECRET = Read-Host "Secret"
```

```bash
python tools/probe.py --url https://192.168.1.10:8443 --app-id YOUR_APP_ID --insecure --dump gwn_dump.json
```

- Replace the URL with yours. For GWN.Cloud, use your region (for example `https://www.gwn.cloud`) and drop `--insecure`.
- `--insecure` skips the certificate check. Local GWN Manager uses a self-signed certificate.

The probe changes nothing. It prints `[OK]`, `[WARN]` or `[FAIL]` per API call.

### Checklist: compare with the GWN web UI

| Check | Pass if |
|---|---|
| Authentication | `[OK] token issued` |
| Networks | Every network/site you have is listed |
| Devices | Every AP/switch/router appears with the right name, and online ones show `online` |
| Clients | The count is close to the GWN *Clients* page (within a few; the timing differs). Your phone appears with the right SSID and AP |
| Wired clients | Wired devices show `ssid=-` |
| SSIDs | Each SSID is listed with `wifiEnabled=1` (or `0` for disabled ones) |
| ap/info | CPU, memory and temperature look plausible for each AP. Switches/routers may `[FAIL]` here, which is fine |

**Upload/download units.** The integration assumes bytes. Pick an AP and compare its `up=`/`down=` numbers with the traffic GWN shows for it. If GWN says 3.2 GB and the probe says about 3,200,000,000, it's bytes ✅. If the probe says about 3,200 (MB) or about 3,200,000 (KB), note that and tell me. It's a one-line fix.

**Look at the field lists.** The lines like `device fields: ...` and `client fields: ...` show what your firmware actually returns. If a field the integration uses is missing (`clients`, `upload`, `download`, `status`, `clientId`, `ssid`, `apName`, `wifiEnabled`), that entity will be blank. Keep `gwn_dump.json` for fixing it.

> ⚠️ `gwn_dump.json` contains your clients' MACs, hostnames and IPs. Review it before sharing it anywhere.

## Stage 3: Write tests

Each test asks for confirmation and **puts things back the way they were**. Run them one at a time.

### 3a. SSID toggle

Pick an SSID you can live without for a minute (a guest or IoT network, **not** the one your PC is on). Get its `id` from the Stage 2 output, then run:

```bash
python tools/probe.py --url https://192.168.1.10:8443 --app-id YOUR_APP_ID --insecure --toggle-ssid 12
```

| Check | Pass if |
|---|---|
| Disable | The probe's read-back shows `ssidEnable=0` and gwn.cloud shows it disabled straight away. The phone loses that Wi-Fi within **1–2 minutes** (cloud changes take time to reach the device). Turn off mobile data and try loading a page to be sure; iOS keeps listing a network for a while after it stops broadcasting |
| Enable | The SSID comes back |
| Config intact | The probe prints `[OK] configuration identical to before the test` |

If it prints `[WARN] config differs ... in: <fields>`, open that SSID in GWN Manager and fix those settings by hand. Then tell me the field names. That would mean the API is resetting something on write, which the integration has to handle.

### 3b. Client block

Connect your test phone, get its MAC address from Stage 2, and run:

```bash
python tools/probe.py --url https://192.168.1.10:8443 --app-id YOUR_APP_ID --insecure --block-test AA:BB:CC:DD:EE:FF
```

| Result | Meaning |
|---|---|
| The phone drops after "block" and reconnects after "unblock" | ✅ Works as built |
| The phone stays online after "block" and drops after "unblock" | The value is inverted on your firmware. Set `BLOCK_VALUE = 1` near the top of `custom_components/grandstream_gwn/api.py` |
| Nothing happens either way | Note any `[FAIL]` message. The endpoint may differ on your firmware |

If you end up stuck blocked, unblock the phone in GWN Manager under *Clients → Blocked*.

### 3c. Reboot (optional)

Pick an AP whose clients can tolerate a 1–3 minute outage:

```bash
python tools/probe.py --url https://192.168.1.10:8443 --app-id YOUR_APP_ID --insecure --reboot AA:BB:CC:DD:EE:FF
```

It passes if the AP goes offline in GWN Manager and comes back by itself.

## Stage 4: Home Assistant

### 4a. Use a test instance (recommended)

A throwaway Home Assistant in Docker keeps your real setup safe while you test. From the project folder, run:

```powershell
mkdir ha-test-config\custom_components -Force
Copy-Item -Recurse custom_components\grandstream_gwn ha-test-config\custom_components\
```

```bash
docker run -d --name ha-test -p 8123:8123 -v "${PWD}/ha-test-config:/config" ghcr.io/home-assistant/home-assistant:stable
```

Open http://localhost:8123 and complete onboarding.

Docker on Windows can't see your network's broadcast traffic, but that doesn't matter here: the integration only makes outgoing HTTPS calls to the controller.

Prefer your real Home Assistant instead? Copy `custom_components/grandstream_gwn` into its `config/custom_components/` folder (via the Samba or File editor add-on), then restart.

### 4b. Turn on debug logging

Add this to `configuration.yaml`, then restart:

```yaml
logger:
  default: warning
  logs:
    custom_components.grandstream_gwn: debug
```

Logs are under **Settings → System → Logs** (click *Show full logs*), or run `docker logs -f ha-test`.

### 4c. Add the integration

Go to **Settings → Devices & services → Add integration → Grandstream GWN**. Enter the same URL, App ID and Secret Key you used for the probe. Leave *Verify SSL* off for a local GWN Manager.

### 4d. Test matrix

Work down the list and tick each item.

**Setup**
- [ ] A wrong Secret Key shows "App ID or Secret Key rejected"
- [ ] A wrong URL shows "Could not reach the controller"
- [ ] Correct details create the entry, and adding it a second time says "already configured"

**Devices and sensors**
- [ ] Each AP/switch/router appears as a device with its model and firmware
- [ ] *Online* is on for each live device
- [ ] *Clients* per AP matches GWN within a poll interval
- [ ] *CPU*, *Memory*, *Temperature* fill in within about 5 min (they're polled slowly on purpose)
- [ ] The network's *Connected clients* sensor is about the same as the GWN total, and its attributes split wired from wireless
- [ ] *Upload*/*Download* show sensible GB values (see the units note in Stage 2)

**Presence (the important one)**
- [ ] Your phone's tracker shows `home`
- [ ] Turn on airplane mode: it stays `home` for the away delay (default 2 min), then flips to `not_home`
- [ ] Turn off airplane mode: it goes back to `home` within one poll (default 30 s)
- [ ] Leave the house with the phone: it shows `not_home`, and `home` again when you return
- [ ] The tracker's attributes show the correct SSID, AP and band. Roam to another AP if you have one and check that `ap_name` changes
- [ ] Restart Home Assistant while the phone is away: the tracker comes back as `not_home`, not missing
- [ ] Watch an iPhone or Android phone that's been idle with the screen off for 30+ min. If it flaps to `not_home`, raise the away delay in **Configure** (5–10 min is common)

**SSID switch**
- [ ] Each SSID has a device "Wi-Fi <name>" with an *Enabled* switch and a *Clients* sensor
- [ ] Turning the test SSID off removes it from your phone's list; turning it on brings it back
- [ ] After the toggle, the SSID's settings in GWN Manager are unchanged

**Reboot button**
- [ ] Pressing *Restart* on a test AP reboots it, and *Online* goes off and then back on

**Block service**
- [ ] In **Developer tools → Actions**, run `grandstream_gwn.block_client` with your test phone's MAC address. The phone drops.
- [ ] `grandstream_gwn.unblock_client` lets it back on
- [ ] A made-up MAC address gives a clear "has not been seen" error

**Resilience**
- [ ] **Configure** → change the poll interval: the integration reloads and keeps working
- [ ] Stop GWN Manager (or unplug its network): entities go *unavailable*, and the logs show a connection error rather than a crash
- [ ] Start GWN Manager again: entities recover by themselves within a poll or two
- [ ] Regenerate the Secret Key in GWN: Home Assistant shows a re-authentication prompt, and entering the new key fixes it
- [ ] Leave it running for 24 h, then check the logs for repeated warnings or errors

### 4e. Automation smoke test

Create one real automation to prove it all works end to end. For example:

```yaml
alias: Test - phone left Wi-Fi
triggers:
  - trigger: state
    entity_id: device_tracker.YOUR_PHONE
    to: not_home
actions:
  - action: persistent_notification.create
    data:
      message: "Phone left Wi-Fi at {{ now().strftime('%H:%M') }}"
```

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `cannot reach controller` / SSL error | Wrong port (GWN Manager uses 8443), or a self-signed certificate. Add `--insecure`, or turn off *Verify SSL* |
| `credentials rejected` | API Developer Mode is off, or the wrong App ID/secret. Copy them again with no spaces |
| `retCode 50003 Authentication Failure` on every call | Signature mismatch. Note your GWN Manager version and send me the probe output |
| Auth works but no networks appear | The App ID lacks permissions, or "restrict to networks" is on |
| Clients missing that GWN shows | Some clients using private (randomized) MACs only appear after reconnecting. Note which ones |
| Occasional `trailing data in response` | A known GWN.Cloud hiccup under load. Harmless if it's occasional |
| A sensor stays *unknown* | Your firmware doesn't return that field. Check the field list in the probe dump |
| CAPTCHA demanded in the GWN web UI | Not caused by this integration (it never uses the web login). Too many failed UI logins trigger it |

## Reporting results

When something fails, collect these three things so it can be fixed quickly:

1. The probe output (copy it from the terminal).
2. `gwn_dump.json`, with MACs, names and IPs edited out if you're sharing it publicly.
3. The Home Assistant log lines containing `grandstream_gwn`, plus your GWN Manager or GWN.Cloud version.

Once everything is ticked off, the integration is ready to use day to day. Then it's worth putting it in a GitHub repository so you can install and update it through HACS.
