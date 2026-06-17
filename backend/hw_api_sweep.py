"""Feature sweep of the web app API against real hardware."""
import json
import time
import urllib.request

BASE = "http://localhost:8000"
CLIENT_ID = "hw-api-sweep"
results = []


def req(method, path, body=None, raw=False):
    r = urllib.request.Request(BASE + path, method=method)
    r.add_header("X-Client-ID", CLIENT_ID)
    data = None
    if body is not None:
        r.add_header("Content-Type", "application/json")
        data = json.dumps(body).encode()
    with urllib.request.urlopen(r, data=data, timeout=120) as resp:
        payload = resp.read()
        return payload if raw else json.loads(payload or b"null")


def check(name, fn):
    try:
        detail = fn() or ""
        results.append((name, True, detail))
        print(f"  PASS  {name}  {detail}")
    except Exception as e:
        results.append((name, False, str(e)))
        print(f"  FAIL  {name}  {e}")


def wait_capture(prev_sid, timeout=90):
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = req("GET", "/api/status")
        if (st["capture_state"] in ("done", "idle", "error")
                and st["last_session_id"] and st["last_session_id"] != prev_sid):
            if st["capture_state"] == "error" or st["last_error"]:
                raise RuntimeError(st["last_error"] or "capture error")
            return st["last_session_id"]
        time.sleep(0.25)
    raise RuntimeError(f"capture timeout (state={st['capture_state']})")


def channel_counts(meta):
    channels = meta.get("session", {}).get("channels", [])
    digital = sum(1 for ch in channels if ch.get("type") == "digital")
    analog = len(meta.get("analog_channels", []))
    return digital, analog


# ── connection ──
check("POST /api/control/acquire", lambda: req(
    "POST", "/api/control/acquire",
    {"name": "hardware API sweep", "force": True})["holder_name"])
check("GET /api/devices", lambda: ", ".join(
    f"{d['id']}:{d['available']}" for d in req("GET", "/api/devices")["devices"]))
check("POST /api/connect hardware", lambda: req(
    "POST", "/api/connect", {"device_id": "hardware"})["metadata"]["device_name"])
check("GET /api/device/metadata", lambda: "sys_clk=%.0fM" % (
    req("GET", "/api/device/metadata")["sys_clk_hz"] / 1e6))


def self_test():
    r = req("POST", "/api/device/self-test")
    if not r["passed"]:
        raise RuntimeError("; ".join(
            f"{c['name']}: {c['detail']}" for c in r["checks"] if not c["passed"]))
    return "; ".join(c["name"] + " ok" for c in r["checks"])


check("POST /api/device/self-test", self_test)

# ── digital capture with PWM signal ──
sid = {}


def digital_capture():
    prev = req("GET", "/api/status")["last_session_id"]
    req("POST", "/api/generator/configure",
        {"protocol": "pwm", "freq_hz": 50000, "duty_pct": 30})
    req("POST", "/api/generator/start")
    req("POST", "/api/capture/start",
        {"settings": {"sample_rate": 2_000_000, "num_samples": 40000}})
    sid["d"] = wait_capture(prev)
    req("POST", "/api/generator/stop")
    meta = req("GET", f"/api/sessions/{sid['d']}/metadata")
    digital_ch, _ = channel_counts(meta)
    if digital_ch != 16:
        raise RuntimeError(f"expected 16 digital channels, got {digital_ch}")
    return f"session {sid['d']} n={meta['num_samples']}"


check("digital capture 40k@2MHz with PWM", digital_capture)


def analog_capture():
    prev = req("GET", "/api/status")["last_session_id"]
    req("POST", "/api/capture/start",
        {"settings": {"mode": "analog", "sample_rate": 100_000,
                      "num_samples": 5000, "analog_enabled": True,
                      "enabled_digital": []}})
    sid["a"] = wait_capture(prev, timeout=180)
    meta = req("GET", f"/api/sessions/{sid['a']}/metadata")
    digital_ch, analog_ch = channel_counts(meta)
    if digital_ch:
        raise RuntimeError("analog-only capture unexpectedly has digital channels")
    if analog_ch != 8:
        raise RuntimeError(f"expected 8 analog channels, got {analog_ch}")
    return (f"session {sid['a']} n={meta['num_samples']} "
            f"analog_ch={len(meta['analog_channels'])}")


check("analog-only capture 5k@100kHz", analog_capture)


def mixed_capture():
    prev = req("GET", "/api/status")["last_session_id"]
    req("POST", "/api/capture/start",
        {"settings": {"mode": "mixed", "sample_rate": 100_000,
                      "num_samples": 5000, "analog_enabled": True,
                      "enabled_digital": list(range(16))}})
    sid["m"] = wait_capture(prev, timeout=180)
    meta = req("GET", f"/api/sessions/{sid['m']}/metadata")
    digital_ch, analog_ch = channel_counts(meta)
    if digital_ch != 16:
        raise RuntimeError(f"expected 16 digital channels, got {digital_ch}")
    if analog_ch != 8:
        raise RuntimeError(f"expected 8 analog channels, got {analog_ch}")
    return (f"session {sid['m']} n={meta['num_samples']} "
            f"digital_ch={digital_ch} analog_ch={analog_ch}")


check("mixed capture 5k@100kHz", mixed_capture)


def digital_after_mixed_capture():
    prev = req("GET", "/api/status")["last_session_id"]
    req("POST", "/api/capture/start",
        {"settings": {"mode": "single", "sample_rate": 1_000_000,
                      "num_samples": 5000, "analog_enabled": False,
                      "enabled_digital": list(range(16))}})
    sid["dm"] = wait_capture(prev)
    meta = req("GET", f"/api/sessions/{sid['dm']}/metadata")
    digital_ch, analog_ch = channel_counts(meta)
    if digital_ch != 16:
        raise RuntimeError("digital capture after mixed lost digital channels")
    if analog_ch:
        raise RuntimeError("digital capture after mixed still has analog channels")
    return f"session {sid['dm']} n={meta['num_samples']}"


check("digital capture after mixed recovery", digital_after_mixed_capture)

# ── waveform endpoints ──
check("GET waveform binary", lambda: f"{len(req('GET', f'/api/sessions/{sid['d']}/waveform?start=0&end=20000&max_points=2000', raw=True))} bytes")
check("GET overview", lambda: f"{len(req('GET', f'/api/sessions/{sid['d']}/overview', raw=True))} bytes binary")
check("GET edges d0", lambda: f"{len(req('GET', f'/api/sessions/{sid['d']}/edges?channel=d0&start=0&limit=100')['edges'])} edges")
check("GET value-at", lambda: str(req("GET", f"/api/sessions/{sid['d']}/value-at?sample=1000&channels=d0,d3")["values"]))
check("GET sanity", lambda: f"{len(req('GET', f'/api/sessions/{sid['d']}/sanity'))} findings")
check("GET spectrum (analog)", lambda: f"{len(req('GET', f'/api/sessions/{sid['a']}/spectrum?channel=a0')['freqs'])} bins")

# ── generator loopbacks via API ──


def uart_builtin_selftest():
    r = req("POST", "/api/generator/self-test")
    if not r["passed"]:
        raise RuntimeError(r["detail"])
    sid["u"] = r["session_id"]
    return r["detail"][:60]


check("POST /api/generator/self-test (UART loopback)", uart_builtin_selftest)


def uart_send_capture():
    r = req("POST", "/api/generator/send",
            {"config": {"protocol": "uart", "data_hex": "4d41583130303021",
                        "baud": 57600, "tx_pin": 3},
             "capture": True, "capture_rate": 1_000_000,
             "capture_samples": 30000})
    if not r["passed"]:
        raise RuntimeError(r["detail"])
    return f"57600Bd 'MAX1000!': {r['detail'][:50]}"


check("POST /api/generator/send UART 57600 loopback", uart_send_capture)


def i2c_send_capture():
    r = req("POST", "/api/generator/send",
            {"config": {"protocol": "i2c", "baud": 400000, "tx_pin": 2,
                        "scl_pin": 1, "i2c_address": 0x19,
                        "i2c_register": 0x0F, "i2c_read_len": 1},
             "capture": True, "capture_rate": 8_000_000,
             "capture_samples": 8000, "expected_hex": "33"})
    if not r.get("passed"):
        raise RuntimeError(r.get("detail", "I2C generator compare failed"))
    sid["i"] = r.get("session_id")
    meta = req("GET", f"/api/sessions/{sid['i']}/metadata")
    dec = meta["session"]["decoders"][0]
    if dec["event_count"] < 3:
        raise RuntimeError(f"only {dec['event_count']} i2c events")
    return f"{dec['event_count']} I2C events decoded; WHO_AM_I=0x33"


check("POST /api/generator/send I2C loopback", i2c_send_capture)

# ── decoders on the hardware UART session ──


def run_decoder():
    decs = req("GET", "/api/decoders")
    n_dec = len(decs["decoders"] if isinstance(decs, dict) else decs)
    # PWM decoder on the debug-PWM capture (independent of generator health)
    r = req("POST", f"/api/sessions/{sid['d']}/decoders",
            {"decoder_id": "pwm", "name": "pwm-hw",
             "channels": {"signal": "d0"}, "settings": {}})
    dec_id = r["id"]
    req("POST", f"/api/sessions/{sid['d']}/decoders/{dec_id}/run")
    for _ in range(40):
        insts = req("GET", f"/api/sessions/{sid['d']}/metadata")["session"]["decoders"]
        inst = next(i for i in insts if i["id"] == dec_id)
        if inst["status"] in ("done", "error"):
            break
        time.sleep(0.25)
    ann = req("GET", f"/api/sessions/{sid['d']}/decoders/{dec_id}/annotations")
    events = ann["events"] if isinstance(ann, dict) else ann
    req("GET", f"/api/sessions/{sid['d']}/decoders/{dec_id}/table")
    if len(events) < 10:
        raise RuntimeError(f"only {len(events)} pwm events")
    sid["dec"] = dec_id
    return f"{n_dec} decoders avail; {len(events)} PWM events on d0"


check("decoder add+run+annotations+table", run_decoder)

# ── measurements, markers ──


def measurements():
    types = req("GET", "/api/measurements/types")
    n_types = len(types["types"] if isinstance(types, dict) else types)
    req("POST", f"/api/sessions/{sid['d']}/measurements",
        {"type": "dig_frequency", "channels": ["d0"]})
    res = req("GET", f"/api/sessions/{sid['d']}/measurements/results")
    rl = res.get("measurements", res) if isinstance(res, dict) else res
    return f"{n_types} types; first result: {rl[0].get('display', rl[0])}"


check("measurements", measurements)


def markers():
    req("POST", f"/api/sessions/{sid['d']}/markers",
        {"sample": 100, "label": "A"})
    m = req("GET", f"/api/sessions/{sid['d']}/markers")
    ml = m["markers"] if isinstance(m, dict) else m
    return f"{len(ml)} markers"


check("markers CRUD", markers)

# ── exports ──
for kind in ("csv", "json", "vcd", "npz", "report"):
    check(f"export {kind}", lambda k=kind: f"{len(req('POST', f'/api/sessions/{sid['d']}/export/{k}', {}, raw=True))} bytes")

# ── sessions CRUD ──


def sessions_crud():
    lst = req("GET", "/api/sessions")
    sl = lst["sessions"] if isinstance(lst, dict) else lst
    dup = req("POST", f"/api/sessions/{sid['d']}/duplicate")
    req("PATCH", f"/api/sessions/{dup['id']}", {"name": "renamed-by-sweep"})
    req("POST", f"/api/sessions/{sid['d']}/compare/{dup['id']}")
    req("DELETE", f"/api/sessions/{dup['id']}")
    return f"{len(sl)} sessions; dup/rename/compare/delete ok"


check("sessions list/duplicate/patch/compare/delete", sessions_crud)

# ── diagnostics / misc ──
check("GET /api/logs", lambda: f"{len(req('GET', '/api/logs'))} entries")
check("GET /api/diagnostics", lambda: "ok" if req("GET", "/api/diagnostics") else "empty")
check("POST debug-bundle", lambda: f"{len(req('POST', '/api/diagnostics/debug-bundle', {}, raw=True))} bytes zip")
check("GET /api/qr", lambda: f"{len(req('GET', '/api/qr', raw=True))} bytes png")
check("GET / (frontend)", lambda: "serves html" if b"<" in req("GET", "/", raw=True)[:100] else "no html")

req("POST", "/api/disconnect")
print()
npass = sum(1 for _, ok, _ in results if ok)
print(f"{'ALL PASS' if npass == len(results) else 'FAILURES'} ({npass}/{len(results)})")
