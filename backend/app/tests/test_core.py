"""Core unit tests: sample format, LOD, decoders, measurements, exports."""
import numpy as np
import pytest

from app.capture.lod import LodPyramid
from app.capture.sample_format import (WaveformData, find_edges,
                                       payload_to_digital,
                                       wire_words_to_digital)
from app.capture.session import (CaptureSettings, Session, TriggerConfig,
                                 default_digital_channels)
from app.capture.session_store import SessionStore
from app.capture.waveform_store import MAGIC, overview_payload, window_payload
from app.decoders import registry
from app.decoders.base import DecodeContext
from app.exports.csv_export import decoder_csv, samples_csv
from app.exports.json_export import session_from_json, session_to_json
from app.exports.vcd_export import vcd_export
from app.hardware import mock_signals as ms
from app.hardware.mock_device import MockDevice
from app.measurements import digital  # noqa: F401  (registers types)
from app.measurements.base import MeasurementContext, run_measurement
from app.triggers.software_trigger import find_software_trigger
from app.waveform.digital import debounce, majority3, min_pulse_filter

RATE = 1_000_000.0


def make_wf(digital=None, analog=None, rate=RATE) -> WaveformData:
    return WaveformData(sample_rate=rate, digital=digital, analog=analog or {})


# ── sample format ────────────────────────────────────────────────────

def test_wire_words_to_digital():
    # two 32-bit words: payload 0x1234 and 0xBEEF in low halves
    raw = bytes([0x34, 0x12, 0, 0, 0xEF, 0xBE, 0, 0])
    arr = wire_words_to_digital(raw)
    assert arr.tolist() == [0x1234, 0xBEEF]


def test_payload_to_digital():
    raw = bytes([0x01, 0x00, 0xFF, 0xFF])
    assert payload_to_digital(raw).tolist() == [1, 0xFFFF]


def test_find_edges():
    bits = np.array([0, 0, 1, 1, 0, 1], dtype=np.uint8)
    assert find_edges(bits, "rising").tolist() == [2, 5]
    assert find_edges(bits, "falling").tolist() == [4]
    assert find_edges(bits, "any").tolist() == [2, 4, 5]


# ── LOD / chunking ───────────────────────────────────────────────────

def test_lod_pyramid_digital():
    n = 100_000
    dig = (ms.square(n, RATE, 1000).astype(np.uint16)
           | (np.ones(n, dtype=np.uint16) << 15))
    wf = make_wf(digital=dig)
    lod = LodPyramid(wf)
    assert len(lod.digital_levels) >= 3
    lvl = lod.digital_levels[0]
    # CH15 constant high: and & or bits set everywhere
    assert np.all((lvl.and_mask >> 15) & 1 == 1)
    # CH0 toggling at 1 kHz: edge counts sum to total edges
    total_edges = int(lvl.edges[0].sum())
    assert total_edges == len(find_edges(wf.digital_channel(0), "any"))
    # coarser levels preserve edge totals
    for deeper in lod.digital_levels[1:]:
        assert int(deeper.edges[0].sum()) == total_edges


def test_window_payload_raw_and_lod():
    n = 200_000
    dig = ms.square(n, RATE, 5000).astype(np.uint16)
    wf = make_wf(digital=dig)
    lod = LodPyramid(wf)
    raw = window_payload("s1", wf, lod, 0, 1000)
    assert raw[:4] == MAGIC
    big = window_payload("s1", wf, lod, 0, n, max_points=2000)
    assert big[:4] == MAGIC
    import json
    import struct
    hlen = struct.unpack("<I", big[4:8])[0]
    header = json.loads(big[8:8 + hlen])
    assert header["mode"] == "lod"
    assert any(a["name"] == "digital_and" for a in header["arrays"])


def test_overview_payload():
    n = 50_000
    wf = make_wf(digital=ms.square(n, RATE, 100).astype(np.uint16),
                 analog={"a0": ms.sine_wave(n, RATE, 500)})
    payload = overview_payload("s1", wf, bins=256)
    assert payload[:4] == MAGIC


# ── session store ────────────────────────────────────────────────────

def test_session_save_load_roundtrip(tmp_path):
    store = SessionStore(root=tmp_path)
    s = Session(name="test", sample_rate=RATE, num_samples=1000)
    s.channels = default_digital_channels()
    store.save(s)
    wf = make_wf(digital=ms.square(1000, RATE, 1000).astype(np.uint16))
    store.save_waveform(s.id, wf)

    store2 = SessionStore(root=tmp_path)
    loaded = store2.get(s.id)
    assert loaded is not None and loaded.name == "test"
    wf2 = store2.load_waveform(s.id)
    assert np.array_equal(wf2.digital, wf.digital)
    dup = store2.duplicate(s.id)
    assert dup.id != s.id
    assert np.array_equal(store2.load_waveform(dup.id).digital, wf.digital)
    assert store2.delete(dup.id)


# ── mock device ──────────────────────────────────────────────────────

def test_mock_device_capture():
    dev = MockDevice()
    dev.connect()
    progress_calls = []
    res = dev.capture(
        CaptureSettings(sample_rate=RATE, num_samples=20_000,
                        mock_scenario="uart"),
        progress=lambda r, t, p: progress_calls.append((r, t, p)))
    assert res.digital is not None and len(res.digital) == 20_000
    assert progress_calls and progress_calls[-1][0] == 20_000
    # UART line idles high on CH0
    assert int(res.digital[0] & 1) == 1


def test_mock_device_trigger_and_analog():
    dev = MockDevice()
    dev.connect()
    res = dev.capture(CaptureSettings(
        sample_rate=RATE, num_samples=10_000, analog_enabled=True,
        mock_scenario="analog_demo",
        trigger=TriggerConfig(type="rising", channels=[0])))
    assert res.analog and "a0" in res.analog
    assert res.trigger_sample is not None


# ── decoders ─────────────────────────────────────────────────────────

def test_uart_decoder():
    n = 80_000
    msg = b"Hello!"
    sig = ms.uart_signal(n, RATE, 9600, msg, start_sample=500)
    wf = make_wf(digital=sig.astype(np.uint16))
    dec = registry.get("uart")
    ctx = DecodeContext(wf, {"rx": "d0"})
    result = dec.decode(ctx, {**dec.defaults(), "baud": 9600})
    got = bytes(e["fields"]["byte"] for e in result.events
                if e["type"] == "uart_byte")
    assert got == msg
    assert all(not e["fields"]["framing_error"] for e in result.events)


def test_uart_decoder_autobaud():
    n = 80_000
    sig = ms.uart_signal(n, RATE, 19200, b"AB", start_sample=500)
    wf = make_wf(digital=sig.astype(np.uint16))
    dec = registry.get("uart")
    result = dec.decode(DecodeContext(wf, {"rx": "d0"}),
                        {**dec.defaults(), "auto_baud": True, "baud": 300})
    got = bytes(e["fields"]["byte"] for e in result.events)
    assert got == b"AB"


def test_i2c_decoder():
    n = 200_000
    scl, sda = ms.i2c_signal(n, RATE, 5000, 0x3C, True, b"\x10\xA5",
                             start_sample=100,
                             ack_per_byte=[True, True, False])
    dig = scl.astype(np.uint16) << 1 | sda.astype(np.uint16) << 2
    wf = make_wf(digital=dig)
    dec = registry.get("i2c")
    result = dec.decode(DecodeContext(wf, {"scl": "d1", "sda": "d2"}),
                        dec.defaults())
    types = [e["type"] for e in result.events]
    assert "i2c_start" in types and "i2c_stop" in types
    addr_ev = next(e for e in result.events if e["type"] == "i2c_address")
    assert addr_ev["fields"]["address"] == 0x3C
    assert addr_ev["fields"]["rw"] == "write"
    data = [e["fields"]["byte"] for e in result.events
            if e["type"] == "i2c_byte"]
    assert data == [0x10, 0xA5]
    # last byte NACKed
    last = [e for e in result.events if e["type"] == "i2c_byte"][-1]
    assert last["fields"]["ack"] is False


def test_spi_decoder():
    n = 120_000
    sclk, mosi, miso, cs = ms.spi_signal(n, RATE, 10_000, b"\xDE\xAD",
                                         b"\x12\x34", start_sample=200)
    dig = (sclk.astype(np.uint16) << 4 | mosi.astype(np.uint16) << 5
           | miso.astype(np.uint16) << 6 | cs.astype(np.uint16) << 7)
    wf = make_wf(digital=dig)
    dec = registry.get("spi")
    result = dec.decode(DecodeContext(
        wf, {"sclk": "d4", "mosi": "d5", "miso": "d6", "cs": "d7"}),
        dec.defaults())
    words = [e["fields"] for e in result.events if e["type"] == "spi_word"]
    assert [w["mosi"] for w in words] == [0xDE, 0xAD]
    assert [w["miso"] for w in words] == [0x12, 0x34]


def test_pwm_decoder():
    n = 50_000
    sig = ms.square(n, RATE, 2000, duty=0.25)
    wf = make_wf(digital=sig.astype(np.uint16))
    dec = registry.get("pwm")
    result = dec.decode(DecodeContext(wf, {"signal": "d0"}), dec.defaults())
    assert len(result.events) > 50
    f = result.events[10]["fields"]
    assert f["frequency_hz"] == pytest.approx(2000, rel=0.05)
    assert f["duty_pct"] == pytest.approx(25, abs=2)


def test_parallel_decoder():
    n = 1000
    counter = (np.arange(n) // 10).astype(np.uint16) & 0xF
    wf = make_wf(digital=counter)
    dec = registry.get("parallel")
    result = dec.decode(
        DecodeContext(wf, {f"bit{i}": f"d{i}" for i in range(4)}),
        dec.defaults())
    vals = [e["fields"]["value"] for e in result.events]
    assert vals[:5] == [0, 1, 2, 3, 4]


def test_modbus_stacked_decoder():
    from app.decoders.modbus import modbus_crc16
    payload = bytes([0x11, 0x03, 0x00, 0x6B, 0x00, 0x03])
    crc = modbus_crc16(payload)
    frame = payload + bytes([crc & 0xFF, crc >> 8])
    n = 200_000
    sig = ms.uart_signal(n, RATE, 9600, frame, start_sample=500, gap_bits=0)
    wf = make_wf(digital=sig.astype(np.uint16))
    uart = registry.get("uart")
    up = uart.decode(DecodeContext(wf, {"rx": "d0"}),
                     {**uart.defaults(), "baud": 9600})
    mb = registry.get("modbus_rtu")
    result = mb.decode(DecodeContext(wf, {}, upstream_events=up.events),
                       mb.defaults())
    frames = [e for e in result.events if e["type"] == "modbus_frame"]
    assert len(frames) == 1
    assert frames[0]["fields"]["address"] == 0x11
    assert frames[0]["fields"]["crc_ok"] is True


def test_decoder_region():
    n = 100_000
    sig = ms.uart_signal(n, RATE, 9600, b"XY", start_sample=50_000)
    wf = make_wf(digital=sig.astype(np.uint16))
    dec = registry.get("uart")
    # region before the data: no events
    r1 = dec.decode(DecodeContext(wf, {"rx": "d0"}, region=[0, 40_000]),
                    {**dec.defaults(), "baud": 9600})
    assert len(r1.events) == 0
    # region covering data: events with absolute sample positions
    r2 = dec.decode(DecodeContext(wf, {"rx": "d0"}, region=[45_000, n]),
                    {**dec.defaults(), "baud": 9600})
    assert bytes(e["fields"]["byte"] for e in r2.events) == b"XY"
    assert r2.events[0]["start_sample"] >= 50_000


# ── measurements ─────────────────────────────────────────────────────

def test_digital_measurements():
    n = 100_000
    wf = make_wf(digital=ms.square(n, RATE, 1000, duty=0.3).astype(np.uint16))
    ctx = MeasurementContext(wf, 0, n)
    f = run_measurement("dig_frequency", ctx, ["d0"])
    assert f["value"] == pytest.approx(1000, rel=0.02)
    d = run_measurement("dig_duty", ctx, ["d0"])
    assert d["value"] == pytest.approx(30, abs=1.5)
    e = run_measurement("dig_rising_edges", ctx, ["d0"])
    assert e["value"] == pytest.approx(100, abs=1)


def test_analog_measurements():
    from app.measurements import analogue  # noqa: F401
    n = 100_000
    wf = make_wf(analog={"a0": ms.sine_wave(n, RATE, 500, amplitude=1.0,
                                            offset=1.5, noise=0.0)})
    ctx = MeasurementContext(wf, 0, n)
    assert run_measurement("ana_mean", ctx, ["a0"])["value"] == pytest.approx(1.5, abs=0.01)
    assert run_measurement("ana_p2p", ctx, ["a0"])["value"] == pytest.approx(2.0, abs=0.05)
    assert run_measurement("ana_frequency", ctx, ["a0"])["value"] == pytest.approx(500, rel=0.02)
    rms = run_measurement("ana_rms", ctx, ["a0"])["value"]
    assert rms == pytest.approx(np.sqrt(1.5**2 + 0.5), rel=0.02)


def test_glitch_measurement_and_filters():
    n = 50_000
    clean = ms.square(n, RATE, 1000)
    glitchy = ms.glitchy_signal(n, RATE, 1000, glitch_every=5000)
    wf = make_wf(digital=glitchy.astype(np.uint16))
    ctx = MeasurementContext(wf, 0, n)
    g = run_measurement("dig_glitch_count", ctx, ["d0"])
    assert g["value"] > 0
    filtered = min_pulse_filter(glitchy, 3)
    # filter removes 1-sample glitches; raw input unchanged
    assert np.count_nonzero(filtered != clean) <= np.count_nonzero(glitchy != clean)
    assert majority3(glitchy).shape == glitchy.shape
    assert debounce(glitchy, 3).shape == glitchy.shape


# ── software triggers ────────────────────────────────────────────────

def test_software_trigger_search():
    n = 10_000
    bits = np.zeros(n, dtype=np.uint16)
    bits[6000:] = 1
    wf = make_wf(digital=bits)
    t = find_software_trigger(wf, TriggerConfig(type="rising", channels=[0]))
    assert t == 6000
    t2 = find_software_trigger(wf, TriggerConfig(type="high", channels=[0]))
    assert t2 == 6000
    t3 = find_software_trigger(
        wf, TriggerConfig(type="pulse_wider", channels=[0], width_s=0.001))
    assert t3 is not None


# ── exports ──────────────────────────────────────────────────────────

def _session_with_wf():
    n = 2000
    s = Session(name="exp test", sample_rate=RATE, num_samples=n)
    s.channels = default_digital_channels()
    wf = make_wf(digital=ms.square(n, RATE, 10_000).astype(np.uint16),
                 analog={"a0": ms.sine_wave(n, RATE, 5000)})
    return s, wf


def test_csv_export():
    s, wf = _session_with_wf()
    text = samples_csv(s, wf, 0, 100)
    lines = text.strip().splitlines()
    assert len(lines) == 101
    assert lines[0].startswith("sample,time_s,CH0")


def test_decoder_csv_export():
    events = [{"start_sample": 1, "end_sample": 5, "start_time": 1e-6,
               "end_time": 5e-6, "type": "uart_byte", "label": "0x41",
               "severity": "normal", "fields": {"byte": 65}}]
    text = decoder_csv(events)
    assert "uart_byte" in text and "65" in text


def test_vcd_export():
    s, wf = _session_with_wf()
    text = vcd_export(s, wf)
    assert "$timescale" in text and "$dumpvars" in text
    assert "#0" in text


def test_json_export_roundtrip():
    s, wf = _session_with_wf()
    text = session_to_json(s, wf, {"dec_1": []})
    s2, wf2, events = session_from_json(text)
    assert s2.name == s.name
    assert np.array_equal(wf2.digital, wf.digital)
    assert np.allclose(wf2.analog["a0"], wf.analog["a0"])
    assert "dec_1" in events
