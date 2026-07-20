"""API smoke + end-to-end flow tests against the mock device."""
import json
import struct
import time

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.state import capture_manager

HDR = {"X-Client-Id": "test-client"}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c
    capture_manager.disconnect()


def wait_capture_done(client, timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = client.get("/api/capture/state").json()
        if st["state"] in ("done", "error", "cancelled"):
            return st
        time.sleep(0.05)
    raise TimeoutError("capture did not finish")


def wait_decoder_done(client, session_id, decoder_id, timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = client.get(f"/api/sessions/{session_id}").json()
        inst = next(d for d in s["decoders"] if d["id"] == decoder_id)
        if inst["status"] in ("done", "error", "cancelled"):
            return inst
        time.sleep(0.05)
    raise TimeoutError("decoder did not finish")


def parse_binary(data: bytes):
    assert data[:4] == b"MSAW"
    hlen = struct.unpack("<I", data[4:8])[0]
    header = json.loads(data[8:8 + hlen])
    return header


def test_status_and_devices(client):
    st = client.get("/api/status").json()
    assert "app_version" in st
    devs = client.get("/api/devices").json()["devices"]
    assert any(d["id"] == "mock" for d in devs)


def test_frontend_spa_fallback_routes_when_built(client):
    assert client.get("/").status_code == 200
    assert client.get("/definitely-not-a-file").status_code == 200


def test_connect_mock(client):
    r = client.post("/api/connect", json={"device_id": "mock"}, headers=HDR)
    assert r.status_code == 200
    meta = client.get("/api/device/metadata").json()
    assert meta["mock"] is True
    caps = client.get("/api/device/capabilities").json()
    assert caps["digital_channels"] == 16
    assert any(t["execution"] == "post_capture" for t in caps["trigger_matrix"])


def test_validate_settings(client):
    r = client.post("/api/capture/settings/validate", json={
        "sample_rate": 999e9, "num_samples": 100})
    findings = r.json()["findings"]
    assert any(f["level"] == "error" for f in findings)


def test_decoder_channel_roles_include_analog(client):
    decoders = client.get("/api/decoders").json()["decoders"]
    uart = next(d for d in decoders if d["id"] == "uart")
    rx = next(c for c in uart["channels"] if c["role"] == "rx")
    assert "analog" in rx["types"]
    i2c = next(d for d in decoders if d["id"] == "i2c")
    for role in ("scl", "sda"):
        ch = next(c for c in i2c["channels"] if c["role"] == role)
        assert "analog" in ch["types"]
    spi = next(d for d in decoders if d["id"] == "spi")
    for role in ("sclk", "mosi", "miso", "cs"):
        ch = next(c for c in spi["channels"] if c["role"] == role)
        assert "analog" in ch["types"]
    rs485 = next(d for d in decoders if d["id"] == "rs485")
    roles = {c["role"]: c for c in rs485["channels"]}
    assert roles["a"]["types"] == ["analog"]
    assert roles["b"]["types"] == ["analog"]


def test_capture_flow_uart(client):
    r = client.post("/api/capture/start", json={
        "settings": {"sample_rate": 1_000_000, "num_samples": 60_000,
                     "mock_scenario": "uart"},
        "name": "uart demo"}, headers=HDR)
    assert r.status_code == 200, r.text
    st = wait_capture_done(client)
    assert st["state"] == "done"
    sid = st["last_session_id"]
    assert sid

    # waveform metadata + binary window + overview
    meta = client.get(f"/api/sessions/{sid}/metadata").json()
    assert meta["num_samples"] == 60_000
    w = client.get(f"/api/sessions/{sid}/waveform?start=0&end=1000")
    h = parse_binary(w.content)
    assert h["mode"] == "raw"
    w2 = client.get(f"/api/sessions/{sid}/waveform?start=0&end=60000&resolution=1000")
    h2 = parse_binary(w2.content)
    assert h2["mode"] == "lod"
    ov = client.get(f"/api/sessions/{sid}/overview")
    assert parse_binary(ov.content)["mode"] == "overview"

    # edges + value-at
    e = client.get(f"/api/sessions/{sid}/edges?channel=d0&kind=any").json()
    assert e["count"] > 10
    v = client.get(f"/api/sessions/{sid}/value-at?sample=0&channels=d0,d1").json()
    assert v["values"]["d0"] in (0, 1)

    # UART decoder end-to-end
    r = client.post(f"/api/sessions/{sid}/decoders", json={
        "decoder_id": "uart", "channels": {"rx": "d0"},
        "settings": {"baud": 10_000}})
    dec = r.json()
    inst = wait_decoder_done(client, sid, dec["id"])
    assert inst["status"] == "done", inst
    assert inst["event_count"] == len(b"Hello MAX1000!")
    table = client.get(
        f"/api/sessions/{sid}/decoders/{dec['id']}/table").json()
    got = bytes(e["fields"]["byte"] for e in table["events"])
    assert got == b"Hello MAX1000!"
    ann = client.get(
        f"/api/sessions/{sid}/decoders/{dec['id']}/annotations").json()
    assert ann["count"] == inst["event_count"]

    # search filter
    t2 = client.get(f"/api/sessions/{sid}/decoders/{dec['id']}/table"
                    f"?search=0x48").json()
    assert t2["total"] >= 1

    # measurements
    m = client.post(f"/api/sessions/{sid}/measurements", json={
        "type": "dig_edge_count", "channels": ["d1"]}).json()
    assert m["result"]["value"] > 0
    res = client.get(f"/api/sessions/{sid}/measurements/results").json()
    assert len(res["measurements"]) == 1

    # markers
    mk = client.post(f"/api/sessions/{sid}/markers", json={
        "sample": 1234, "label": "M1", "note": "test"}).json()
    assert mk["sample"] == 1234
    client.patch(f"/api/sessions/{sid}/markers/{mk['id']}",
                 json={"label": "M1b"})
    assert client.get(f"/api/sessions/{sid}/markers").json()[
        "markers"][0]["label"] == "M1b"

    # exports
    for fmt, body in [("csv", {"start": 0, "end": 500}), ("json", {}),
                      ("vcd", {}), ("pulseview", {}), ("npz", None),
                      ("report", None), ("pdf", None)]:
        url = f"/api/sessions/{sid}/export/{fmt}"
        r = client.post(url, json=body) if body is not None else client.post(url)
        assert r.status_code == 200, f"{fmt}: {r.text}"

    # sanity checks
    sc = client.get(f"/api/sessions/{sid}/sanity").json()
    assert any(f["check"] == "samples" for f in sc["findings"])

    # session ops
    r = client.patch(f"/api/sessions/{sid}",
                     json={"name": "renamed", "tags": ["uart"]})
    assert r.json()["name"] == "renamed"
    dup = client.post(f"/api/sessions/{sid}/duplicate").json()
    cmp_r = client.post(f"/api/sessions/{sid}/compare/{dup['id']}").json()
    assert cmp_r["identical_digital"] is True
    assert client.delete(f"/api/sessions/{dup['id']}").status_code == 200


def test_derived_channel_and_region_decode(client):
    client.post("/api/capture/start", json={
        "settings": {"sample_rate": 1_000_000, "num_samples": 20_000,
                     "mock_scenario": "glitchy"}}, headers=HDR)
    st = wait_capture_done(client)
    sid = st["last_session_id"]
    r = client.post(f"/api/sessions/{sid}/derived-channels", json={
        "source": "d0", "derive": {"kind": "min_pulse", "min_width": 3},
        "name": "CH0 filtered"})
    assert r.status_code == 200, r.text
    ch = r.json()
    assert ch["type"] == "derived"
    # derived channel usable in waveform query
    w = client.get(f"/api/sessions/{sid}/waveform?start=0&end=500"
                   f"&channels={ch['id']}")
    h = parse_binary(w.content)
    assert any(a["name"].startswith("derived") for a in h["arrays"])


def test_analog_capture_and_measurements(client):
    client.post("/api/capture/start", json={
        "settings": {"sample_rate": 500_000, "num_samples": 25_000,
                     "analog_enabled": True,
                     "mock_scenario": "analog_demo"}}, headers=HDR)
    st = wait_capture_done(client)
    sid = st["last_session_id"]
    meta = client.get(f"/api/sessions/{sid}/metadata").json()
    assert "a0" in meta["analog_channels"]
    m = client.post(f"/api/sessions/{sid}/measurements", json={
        "type": "ana_p2p", "channels": ["a0"]}).json()
    assert m["result"]["value"] > 1.0
    sp = client.get(f"/api/sessions/{sid}/spectrum?channel=a0").json()
    assert len(sp["freqs"]) > 10
    # threshold-derived digital from analog
    r = client.post(f"/api/sessions/{sid}/derived-channels", json={
        "source": "a0", "derive": {"kind": "threshold", "level": 1.65}})
    assert r.status_code == 200


def test_analog_only_capture_has_no_digital_channels(client):
    client.post("/api/capture/start", json={
        "settings": {"sample_rate": 500_000, "num_samples": 10_000,
                     "mode": "analog", "analog_enabled": True,
                     "enabled_digital": [],
                     "mock_scenario": "analog_demo"}}, headers=HDR)
    st = wait_capture_done(client)
    sid = st["last_session_id"]
    meta = client.get(f"/api/sessions/{sid}/metadata").json()
    channel_types = {ch["type"] for ch in meta["session"]["channels"]}
    assert channel_types == {"analog"}
    assert len(meta["analog_channels"]) == 4


def test_mixed_capture_reenables_digital_channels(client):
    client.post("/api/capture/start", json={
        "settings": {"sample_rate": 100_000, "num_samples": 1024,
                     "mode": "mixed", "analog_enabled": True,
                     "enabled_digital": [],
                     "mock_scenario": "analog_demo"}}, headers=HDR)
    st = wait_capture_done(client)
    sid = st["last_session_id"]
    meta = client.get(f"/api/sessions/{sid}/metadata").json()
    channels = meta["session"]["channels"]
    digital = [ch for ch in channels if ch["type"] == "digital"]
    assert len(digital) == 16
    assert all(ch["enabled"] for ch in digital)
    assert len(meta["analog_channels"]) == 4


def test_generator_loopback_self_test(client):
    r = client.post("/api/generator/send", json={
        "config": {"protocol": "uart", "data_hex": "414243",
                   "baud": 115200, "tx_pin": 0},
        "capture": True, "capture_rate": 2_000_000,
        "capture_samples": 30_000}, headers=HDR)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["passed"] is True, body
    assert body["decoded_hex"] == "414243"
    meta = client.get(f"/api/sessions/{body['session_id']}/metadata").json()
    assert meta["session"]["generator"]["config"]["protocol"] == "uart"


def test_generator_builtin_self_test_uses_strict_decode_on_mock(client):
    r = client.post("/api/generator/self-test", headers=HDR)

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["passed"] is True, body
    assert body["sent_hex"] == "48656c6c6f21"
    assert body["decoded_hex"] == "48656c6c6f21"


def test_generator_rs485_loopback(client):
    caps = client.get("/api/generator/capabilities").json()
    assert "rs485" in caps["protocols"]
    rs485 = next(route for route in caps["routes"] if route["protocol"] == "rs485")
    assert "de_timing" in rs485["features"]
    spi = next(route for route in caps["routes"] if route["protocol"] == "spi")
    assert "cs" in spi["features"]
    assert "miso" in spi["features"]

    r = client.post("/api/generator/send", json={
        "config": {"protocol": "rs485", "data_hex": "343835",
                   "baud": 115200, "tx_pin": 0},
        "capture": True, "capture_rate": 2_000_000,
        "capture_samples": 30_000}, headers=HDR)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["passed"] is True, body


def test_generator_swd_loopback_logs_decoded_transactions(client):
    client.post("/api/connect", json={"device_id": "mock"}, headers=HDR)
    r = client.post("/api/generator/send", json={
        "config": {
            "protocol": "swd", "baud": 1_000_000, "tx_pin": 1, "scl_pin": 0,
            "extra": {"requests": [{"ap": False, "read": True, "addr": 0, "data": 0}]},
        },
        "capture": True, "capture_rate": 2_000_000,
        "capture_samples": 20_000,
    }, headers=HDR)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["passed"] is True, body
    assert "SWD transaction" in body["detail"]


def test_generator_rejects_payload_larger_than_fpga_fifo(client):
    r = client.post("/api/generator/send", json={
        "config": {"protocol": "uart", "data_hex": "55" * 257,
                   "baud": 115200, "tx_pin": 0},
        "capture": True, "capture_rate": 2_000_000,
        "capture_samples": 30_000}, headers=HDR)

    assert r.status_code == 400
    assert "FIFO holds 256 bytes" in r.json()["detail"]


def test_generator_i2c_loopback(client):
    r = client.post("/api/generator/send", json={
        "config": {"protocol": "i2c", "data_hex": "a55a", "baud": 50_000,
                   "i2c_address": 0x3C, "i2c_register": 0x10,
                   "tx_pin": 2, "scl_pin": 1},
        "capture": True, "capture_rate": 2_000_000,
        "capture_samples": 60_000}, headers=HDR)
    body = r.json()
    assert r.status_code == 200, body
    assert body["passed"] is True, body


def test_machine_in_loop_emulator(client):
    presets = client.get("/api/mil/presets").json()["presets"]
    by_id = {p["id"]: p for p in presets}
    assert by_id["modbus-rtu-demo"]["source"] == "builtin"
    assert by_id["uart-register-demo"]["source"] == "builtin"
    assert by_id["rs485-modbus-demo"]["source"] == "builtin"

    r = client.post("/api/mil/load", json={"preset_id": "modbus-rtu-demo"},
                    headers=HDR)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["loaded"] is True
    assert body["config"]["protocol"] == "modbus_uart"

    assert client.post("/api/mil/start", headers=HDR).json()["running"] is True
    # Unit 1, function 3, start 0, count 2, CRC c40b.
    r = client.post("/api/mil/transaction",
                    json={"request_hex": "010300000002c40b"},
                    headers=HDR)
    assert r.status_code == 200, r.text
    response = r.json()
    assert response["action"] == "read"
    assert response["response_hex"].startswith("01030400eb0000")
    events = client.get("/api/mil/status").json()["events"]
    txrx = events[-1]
    assert txrx["kind"] == "transaction"
    assert txrx["request_hex"] == "010300000002c40b"
    assert txrx["response_hex"].startswith("01030400eb0000")
    assert txrx["protocol"] == "modbus_uart"
    assert txrx["rx_pin"] == 0
    assert txrx["tx_pin"] == 1
    assert txrx["response_delay_us"] == 1000.0
    assert txrx["inter_byte_gap_us"] == 0.0
    assert response["session_id"]
    meta = client.get(f"/api/sessions/{response['session_id']}/metadata").json()
    assert meta["has_waveform"] is True
    assert meta["session"]["tags"][:2] == ["mil", "modbus_uart"]
    w = client.get(
        f"/api/sessions/{response['session_id']}/waveform?start=0&end=512")
    h = parse_binary(w.content)
    assert h["mode"] == "raw"

    r = client.post("/api/mil/load", json={"preset_id": "uart-register-demo"},
                    headers=HDR)
    assert r.status_code == 200, r.text
    client.post("/api/mil/start", headers=HDR)
    r = client.post("/api/mil/transaction", json={"request_hex": "030001"},
                    headers=HDR)
    assert r.status_code == 200, r.text
    assert r.json()["response_hex"] == "030142"

    r = client.post("/api/mil/load", json={"preset_id": "rs485-modbus-demo"},
                    headers=HDR)
    assert r.status_code == 200, r.text
    client.post("/api/mil/start", headers=HDR)
    r = client.post("/api/mil/transaction",
                    json={"request_hex": "110301000002c767"},
                    headers=HDR)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["action"] == "read"
    assert body["response_hex"].startswith("11030404000000")

    inline = r.json()
    cfg = client.get("/api/mil/status").json()["config"]
    cfg["timing"]["response_delay_us"] = 2500
    cfg["timing"]["inter_byte_gap_us"] = 100
    r = client.post("/api/mil/load", json={"config": cfg}, headers=HDR)
    assert r.status_code == 200, r.text
    client.post("/api/mil/start", headers=HDR)
    r = client.post("/api/mil/transaction",
                    json={"request_hex": "110301000002c767"},
                    headers=HDR)
    assert r.status_code == 200, r.text
    event = client.get("/api/mil/status").json()["events"][-1]
    assert event["response_delay_us"] == 2500
    assert event["inter_byte_gap_us"] == 100

    assert client.post("/api/mil/stop", headers=HDR).json()["running"] is False
    assert client.post("/api/mil/load", json={}, headers=HDR).status_code == 400
    assert client.post("/api/mil/transaction", json={"request_hex": "01"},
                       headers=HDR).status_code == 400


def test_control_lock(client):
    other = {"X-Client-Id": "intruder"}
    r = client.post("/api/capture/stop", headers=other)
    assert r.status_code == 409
    r = client.post("/api/control/acquire",
                    json={"name": "intruder", "force": True}, headers=other)
    assert r.json()["acquired"] is True
    # original client now locked out
    r = client.post("/api/capture/stop", headers=HDR)
    assert r.status_code == 409
    client.post("/api/control/release", headers=other)
    r = client.post("/api/capture/stop", headers=HDR)
    assert r.status_code == 200


def test_session_import_export_roundtrip(client):
    sid = client.get("/api/sessions").json()["sessions"][0]["id"]
    exported = client.post(f"/api/sessions/{sid}/export/json", json={}).text
    r = client.post("/api/sessions", json={"json_text": exported})
    assert r.status_code == 200
    imported = r.json()
    assert imported["id"] != sid
    meta = client.get(f"/api/sessions/{imported['id']}/metadata").json()
    assert meta["has_waveform"] is True


def test_channel_height_scale_persists(client):
    sid = client.get("/api/sessions").json()["sessions"][0]["id"]
    chans = client.get(f"/api/sessions/{sid}").json()["channels"]
    cid = chans[0]["id"]
    # default is 1.0 for an untouched channel
    assert chans[0].get("display_height_scale", 1.0) == 1.0

    r = client.patch(f"/api/sessions/{sid}",
                     json={"channels": [{"id": cid, "display_height_scale": 2.5}]},
                     headers=HDR)
    assert r.status_code == 200, r.text
    patched = next(c for c in r.json()["channels"] if c["id"] == cid)
    assert patched["display_height_scale"] == 2.5

    # survives a fresh GET (i.e. round-trips through the store)
    reread = client.get(f"/api/sessions/{sid}").json()["channels"]
    assert next(c for c in reread if c["id"] == cid)["display_height_scale"] == 2.5


def test_diagnostics_endpoints(client):
    assert client.get("/api/logs").status_code == 200
    d = client.get("/api/diagnostics").json()
    assert d["version"]
    r = client.post("/api/diagnostics/debug-bundle")
    assert r.status_code == 200 and r.content[:2] == b"PK"
    st = client.post("/api/device/self-test", headers=HDR).json()
    assert st["passed"] is True
    r = client.get("/api/qr")
    assert r.status_code in (200, 501)
    assert client.get("/connect").status_code == 200


def test_websocket_status(client):
    with client.websocket_connect("/ws/status") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "status_snapshot"
        ws.send_text(json.dumps({"type": "ping"}))
        assert ws.receive_json()["type"] == "pong"


def test_error_handling(client):
    assert client.get("/api/sessions/nope").status_code == 404
    assert client.get("/api/sessions/nope/waveform").status_code == 404
    r = client.post("/api/sessions", json={"json_text": "not json"})
    assert r.status_code == 400


def test_generator_sweep_preview_endpoint(client):
    r = client.post("/api/generator/sweep-preview", json={
        "base": {"protocol": "bitbang", "baud": 100_000,
                 "data_hex": "55", "extra": {"preset": "pulse", "count": 8}},
        "axes": {"extra.repeat": [1, 2]}, "limit": 8})
    assert r.status_code == 200, r.text
    assert r.json()["count"] == 2
    assert r.json()["failed"] == 0


def test_generator_capture_sweep_endpoint_uses_mock_loopback(client):
    client.post("/api/connect", json={"device_id": "mock"}, headers=HDR)
    r = client.post("/api/generator/sweep-capture", json={
        "base": {"protocol": "uart", "baud": 100_000,
                 "data_hex": "55", "tx_pin": 0},
        "axes": {"baud": [100_000, 200_000]}, "limit": 8,
        "capture_rate": 2_000_000, "capture_samples": 4_000,
        "expected_hex": "55"}, headers=HDR)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == body["requested_count"] == 2
    assert body["passed"] == 2
    assert all(row["session_id"] for row in body["rows"])
    too_large = client.post("/api/generator/sweep-capture", json={
        "base": {"protocol": "uart", "data_hex": "55"},
        "capture_rate": 200_000_001}, headers=HDR)
    assert too_large.status_code == 422


def test_api_management_error_and_filter_paths(client):
    sessions = client.get("/api/sessions").json()["sessions"]
    sid = None
    for candidate in sessions:
        detail = client.get(f"/api/sessions/{candidate['id']}").json()
        if any(c.get("id") == "d0" for c in detail.get("channels", [])):
            meta = client.get(f"/api/sessions/{candidate['id']}/metadata").json()
            if meta.get("has_waveform"):
                sid = candidate["id"]
                break
    if sid is None:
        client.post("/api/connect", json={"device_id": "mock"}, headers=HDR)
        client.post("/api/capture/start", json={
            "settings": {"sample_rate": 100_000, "num_samples": 2_000,
                         "mock_scenario": "uart"}}, headers=HDR)
        sid = wait_capture_done(client)["last_session_id"]

    assert client.get("/api/measurements/types").json()["types"]
    assert client.post(f"/api/sessions/{sid}/measurements",
                       json={"type": "missing"}).status_code == 400
    missing = client.patch(f"/api/sessions/{sid}/measurements/missing",
                           json={"scope": "capture"})
    assert missing.status_code == 404
    assert client.delete(f"/api/sessions/{sid}/measurements/missing").status_code == 404

    bad_bus = client.post(f"/api/sessions/{sid}/buses",
                          json={"name": "bad", "members": ["d999"]})
    assert bad_bus.status_code == 400
    good_bus = client.post(f"/api/sessions/{sid}/buses",
                           json={"name": "bus", "members": ["d0", "d1"],
                                 "display_base": "bin"})
    assert good_bus.status_code == 200

    dec = client.post(f"/api/sessions/{sid}/decoders", json={
        "decoder_id": "uart", "channels": {"rx": "d0"}, "run": False}).json()
    patched = client.patch(f"/api/sessions/{sid}/decoders/{dec['id']}", json={
        "name": "UART test", "enabled": False, "settings": {"baud": 9600},
        "region": [1, 2], "channels": {"rx": "d0"}}).json()
    assert patched["name"] == "UART test" and patched["enabled"] is False
    cleared = client.patch(f"/api/sessions/{sid}/decoders/{dec['id']}",
                           json={"clear_region": True}).json()
    assert cleared["region"] is None
    assert client.post(f"/api/sessions/{sid}/decoders/{dec['id']}/cancel").status_code == 200
    assert client.delete(f"/api/sessions/{sid}/decoders/{dec['id']}").status_code == 200
    assert client.delete(f"/api/sessions/{sid}/decoders/missing").status_code == 404
    assert client.post(f"/api/sessions/{sid}/decoders",
                       json={"decoder_id": "missing"}).status_code == 400

    runnable = client.post(f"/api/sessions/{sid}/decoders", json={
        "decoder_id": "uart", "channels": {"rx": "d0"},
        "settings": {"baud": 9_600}}).json()
    assert client.post(f"/api/sessions/{sid}/decoders/{runnable['id']}/run",
                       json={"region": [0, 100]}).status_code == 200
    waited = wait_decoder_done(client, sid, runnable["id"])
    assert waited["status"] in ("done", "error")
    assert client.get(f"/api/sessions/{sid}/decoders/{runnable['id']}/annotations"
                      "?start=0&end=100&limit=1").status_code == 200
    assert client.get(f"/api/sessions/{sid}/decoders/{runnable['id']}/table"
                      "?severity=normal&field=byte&value=0x00").status_code == 200
    assert client.get(f"/api/sessions/{sid}/decoder-events?start=0&end=100&limit=1").status_code == 200
    assert client.post(f"/api/sessions/{sid}/export/csv", json={
        "decoder_instance": runnable["id"]}).status_code == 200
    assert client.post(f"/api/sessions/{sid}/export/json", json={"include_raw": False}).status_code == 200
    assert client.post(f"/api/sessions/{sid}/export/vcd", json={}).status_code == 200
    assert client.post(f"/api/sessions/{sid}/export/npz").status_code == 200
    assert client.post(f"/api/sessions/{sid}/export/report").status_code == 200
    assert client.delete(f"/api/sessions/{sid}/decoders/{runnable['id']}").status_code == 200

    marker_a = client.post(f"/api/sessions/{sid}/markers", json={
        "sample": 10, "kind": "cursor_a"}).json()
    marker_b = client.post(f"/api/sessions/{sid}/markers", json={
        "sample": 100, "kind": "cursor_b"}).json()
    measurement = client.post(f"/api/sessions/{sid}/measurements", json={
        "type": "dig_edge_count", "channels": ["d0"], "scope": "cursors"}).json()
    assert measurement["result"]["region"] == [10, 100]
    results = client.get(f"/api/sessions/{sid}/measurements/results?cursor_a=10&cursor_b=100")
    assert results.status_code == 200
    assert client.patch(f"/api/sessions/{sid}/measurements/{measurement['id']}",
                        json={"settings": {"threshold": 1}}).status_code == 200
    assert client.delete(f"/api/sessions/{sid}/measurements/{measurement['id']}").status_code == 200
    assert client.delete(f"/api/sessions/{sid}/markers/{marker_a['id']}").status_code == 200
    assert client.delete(f"/api/sessions/{sid}/markers/{marker_b['id']}").status_code == 200

    assert client.get(f"/api/sessions/{sid}/waveform?start=0&end=10&channels=a999").status_code == 200
    assert client.get(f"/api/sessions/{sid}/metadata").status_code == 200
    assert client.get(f"/api/sessions/{sid}/raw?start=0&end=4&channels=d0,d1").status_code == 200
    assert client.get(f"/api/sessions/{sid}/waveform?start=0&channels=d0,d1").status_code == 200
    assert client.get(f"/api/sessions/{sid}/overview?bins=4").status_code == 200
    assert client.get(f"/api/sessions/{sid}/edges?channel=d999").status_code == 200
    assert client.get(f"/api/sessions/{sid}/value-at?sample=0&channels=d999").status_code == 200
    assert client.get(f"/api/sessions/{sid}/value-at?sample=0&channels={good_bus.json()['id']}").status_code == 200
    assert client.get(f"/api/sessions/{sid}/spectrum?channel=a999").status_code == 404
    assert client.post(f"/api/sessions/{sid}/derived-channels", json={
        "source": "d0", "derive": {"kind": "majority3"}}).status_code == 200
    assert client.post(f"/api/sessions/{sid}/derived-channels", json={
        "source": "d999", "derive": {"op": "invert"}}).status_code == 400
    assert client.get(f"/api/sessions/{sid}/sanity").status_code == 200
    patched_session = client.patch(f"/api/sessions/{sid}", json={
        "name": "renamed", "notes": "updated", "tags": ["tested"],
        "channels": [{"id": "missing", "name": "ignored"},
                     {"id": "d0", "name": "DATA", "enabled": True}]} )
    assert patched_session.status_code == 200
    current_channels = client.get(f"/api/sessions/{sid}").json()["channels"]
    all_channels = [{"id": c["id"], "enabled": c["id"] == "d0"}
                    for c in reversed(current_channels)]
    assert client.patch(f"/api/sessions/{sid}", json={"channels": all_channels}).status_code == 200
    assert client.post(f"/api/sessions/{sid}/duplicate").status_code == 200
    assert client.post(f"/api/sessions/{sid}/compare/{sid}").status_code == 200
    extra_marker = client.post(f"/api/sessions/{sid}/markers", json={"sample": 2}).json()
    assert client.patch(f"/api/sessions/{sid}/markers/{extra_marker['id']}",
                        json={"label": "patched"}).status_code == 200
    assert client.delete(f"/api/sessions/{sid}/markers/{extra_marker['id']}").status_code == 200
    assert client.delete(f"/api/sessions/{sid}/markers/missing").status_code == 404
    assert client.patch(f"/api/sessions/{sid}/markers/missing", json={"label": "x"}).status_code == 404
    assert client.post("/api/sessions/missing/duplicate").status_code == 404
    assert client.delete("/api/sessions/nope").status_code == 404

    assert client.get(f"/api/sessions/{sid}/raw?start=0&end=4&channels=d0").status_code == 200
    assert client.get(f"/api/sessions/{sid}/edges?channel=d0").status_code == 200
    assert client.get(f"/api/sessions/{sid}/decoder-events").status_code == 200


def test_session_websocket_topics(client):
    for path in ("/ws/session/missing", "/ws/decoder/missing"):
        with client.websocket_connect(path) as ws:
            ws.send_text(json.dumps({"type": "ping"}))
            assert ws.receive_json()["type"] == "pong"


def test_generator_control_plane_and_mock_diagnostics(client):
    client.post("/api/connect", json={"device_id": "mock"}, headers=HDR)
    assert client.get("/api/generator/capabilities").status_code == 200
    cfg = {"protocol": "uart", "data_hex": "41", "baud": 9600, "tx_pin": 0}
    configured = client.post("/api/generator/configure", json=cfg, headers=HDR)
    assert configured.status_code == 200
    assert client.get("/api/generator/status").status_code == 200
    assert client.post("/api/generator/start", headers=HDR).status_code == 200
    assert client.post("/api/generator/stop", headers=HDR).status_code == 200
    sent = client.post("/api/generator/send", json={"capture": False}, headers=HDR)
    assert sent.status_code == 200 and sent.json()["captured"] is False
    self_test = client.post("/api/generator/self-test", headers=HDR)
    assert self_test.status_code == 200, self_test.text

    mock = client.post("/api/diagnostics/mock-capture", json={
        "scenario": "demo_mixed", "sample_rate": 100_000,
        "num_samples": 1_000, "analog": True}, headers=HDR)
    assert mock.status_code == 200, mock.text
    assert wait_capture_done(client)["last_session_id"]


def test_diagnostics_qr_landing_and_live_accel_guards(client, monkeypatch):
    import app.api.diagnostics as diagnostics_api

    monkeypatch.setattr(diagnostics_api, "_lan_urls",
                        lambda: ["http://localhost:8000", "http://192.0.2.1:8000"])
    assert client.get("/api/connect").status_code == 200
    qr = client.get("/api/qr")
    assert qr.status_code in (200, 501)
    client.post("/api/connect", json={"device_id": "mock"}, headers=HDR)
    live = client.post("/api/diagnostics/live-accel-session", headers=HDR)
    assert live.status_code == 409
    # Exercise the real-hardware guard in mock-capture without touching a device.
    original_kind = capture_manager.device_kind
    capture_manager.device_kind = "hardware"
    try:
        blocked = client.post("/api/diagnostics/mock-capture", json={}, headers=HDR)
        assert blocked.status_code == 409
    finally:
        capture_manager.device_kind = original_kind


def test_api_disconnected_guards_and_hardware_error_mapping(client):
    import app.api.generator as generator_api
    generator_api._last_config.clear()
    client.post("/api/disconnect", headers=HDR)
    assert client.get("/api/device/metadata").status_code == 409
    assert client.get("/api/device/capabilities").status_code == 409
    assert client.get("/api/device/debug").status_code == 409
    assert client.get("/api/generator/capabilities").status_code == 409
    assert client.get("/api/generator/status").status_code == 409
    assert client.post("/api/generator/configure", json={"protocol": "uart"},
                       headers=HDR).status_code == 502
    assert client.post("/api/generator/start", headers=HDR).status_code == 502
    assert client.post("/api/generator/stop", headers=HDR).status_code == 502
    assert client.post("/api/capture/settings/validate", json={}).status_code == 409
    assert client.post("/api/capture/start", json={"settings": {}}, headers=HDR).status_code == 409
    assert client.post("/api/capture/arm", json={"settings": {}}, headers=HDR).status_code == 409
    assert client.get("/api/capture/scenarios").json()["scenarios"] == []
    assert client.post("/api/connect", json={"device_id": "missing"},
                       headers=HDR).status_code == 502
    assert client.post("/api/generator/send", json={}, headers=HDR).status_code == 400
    client.post("/api/connect", json={"device_id": "mock"}, headers=HDR)
    assert client.get("/api/capture/scenarios").json()["scenarios"]
    armed = client.post("/api/capture/arm", json={"settings": {
        "sample_rate": 100_000, "num_samples": 2_000}}, headers=HDR)
    assert armed.status_code == 200
    assert client.post("/api/capture/disarm", headers=HDR).status_code == 200
    assert client.post("/api/diagnostics/run-self-test", headers=HDR).status_code == 200


def test_websocket_topics_and_ping(client):
    for path in ("/ws/status", "/ws/capture"):
        with client.websocket_connect(path) as ws:
            hello = ws.receive_json()
            assert hello["type"] in ("status_snapshot", "capture_state")
            ws.send_json({"type": "ping"})
            assert ws.receive_json()["type"] == "pong"
    with client.websocket_connect("/ws/logs") as ws:
        ws.send_text("not json")
        ws.send_json({"type": "ignored"})
