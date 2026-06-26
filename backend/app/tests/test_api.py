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
                      ("vcd", {}), ("npz", None), ("report", None)]:
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
    assert len(meta["analog_channels"]) == 8


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
    assert len(meta["analog_channels"]) == 8


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


def test_generator_rs485_loopback(client):
    caps = client.get("/api/generator/capabilities").json()
    assert "rs485" in caps["protocols"]

    r = client.post("/api/generator/send", json={
        "config": {"protocol": "rs485", "data_hex": "343835",
                   "baud": 115200, "tx_pin": 0},
        "capture": True, "capture_rate": 2_000_000,
        "capture_samples": 30_000}, headers=HDR)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["passed"] is True, body
    assert body["decoded_hex"] == "343835"


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
