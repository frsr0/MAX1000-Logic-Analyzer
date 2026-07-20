import numpy as np

from app.capture.sample_format import WaveformData
from app.capture.session import TriggerConfig
from app.decoders.base import DecodeContext
from app.decoders.manchester import ManchesterDecoder
from app.decoders.nrz import NrzDecoder
from app.decoders.i2s import I2sDecoder
from app.decoders.can import CanDecoder, can_crc15
from app.decoders.lin import LinDecoder, lin_checksum, lin_pid
from app.decoders.midi import MidiDecoder
from app.decoders.ps2 import Ps2Decoder
from app.decoders.quadrature import QuadratureDecoder
from app.measurements import digital
from app.measurements.base import MeasurementContext, run_measurement
from app.triggers.software_trigger import find_software_trigger


def test_manchester_decoder_decodes_msb_word():
    bits = [int(x) for x in f"{0xA5:08b}"]
    encoded = []
    for b in bits:
        encoded.extend([1, 1, 0, 0] if b == 0 else [0, 0, 1, 1])
    signal = np.array(encoded, dtype=np.uint8)
    wf = WaveformData(sample_rate=4_000_000, digital=np.zeros(len(signal), dtype=np.uint16))
    wf.digital = signal.astype(np.uint16)
    ctx = DecodeContext(wf, {"data": "d0"})
    result = ManchesterDecoder().decode(ctx, {"bit_rate": 1_000_000,
                                               "word_bits": 8,
                                               "bit_order": "msb",
                                               "zero_pair": "10"})
    assert result.events[0]["fields"]["word"] == 0xA5
    assert result.events[0]["fields"]["valid"] is True


def test_nrz_decoder_decodes_clocked_lsb_word():
    values = [1, 0, 1, 0, 0, 1, 0, 1]  # LSB-first representation of 0xA5
    data = np.zeros(32, dtype=np.uint16)
    clock = np.zeros(32, dtype=np.uint16)
    for i, value in enumerate(values):
        p = i * 4
        data[p:p + 4] = value
        clock[p + 2:p + 4] = 1
    wf = WaveformData(sample_rate=1_000_000, digital=np.zeros(32, dtype=np.uint16))
    wf.digital = data | (clock << 1)
    ctx = DecodeContext(wf, {"data": "d0", "clock": "d1"})
    result = NrzDecoder().decode(ctx, {"word_bits": 8, "bit_order": "lsb",
                                       "edge": "rising"})
    assert result.events[0]["fields"]["word"] == 0xA5


def test_protocol_and_sequence_trigger_search_on_decoder_events():
    wf = WaveformData(sample_rate=1_000_000,
                      digital=np.zeros(32, dtype=np.uint16))
    events = [
        {"type": "uart_byte", "start_sample": 4, "start_time": 4e-6,
         "fields": {"byte": 0x55}},
        {"type": "i2c_address", "start_sample": 12, "start_time": 12e-6,
         "fields": {"address": 0x3C, "ack": True}},
    ]
    assert find_software_trigger(
        wf, TriggerConfig(type="uart_byte", value=0x55), events) == 4
    assert find_software_trigger(
        wf, TriggerConfig(type="i2c_address", value=0x3C), events) == 12
    assert find_software_trigger(
        wf, TriggerConfig(type="sequence", window_s=20e-6,
                          sequence_steps=[{"type": "uart_byte", "value": 0x55},
                                          {"type": "i2c_address", "value": 0x3C}]),
        events) == 4
    assert find_software_trigger(
        wf, TriggerConfig(type="sequence", window_s=20e-6,
                          sequence_steps=[{"type": "i2c_byte", "value": 0x55},
                                          {"type": "i2c_address", "value": 0x3C}]),
        events) is None


def test_jitter_measurement_reports_rms_and_peak_to_peak():
    signal = np.zeros(30, dtype=np.uint16)
    signal[[1, 5, 10, 14, 20, 25]] = 1
    wf = WaveformData(sample_rate=1_000_000, digital=signal)
    ctx = MeasurementContext(wf, 0, len(signal), settings={})
    result = run_measurement("dig_jitter", ctx, ["d0"])
    assert result["value"] is not None
    assert result["peak_to_peak"] > 0


def _uart_events(values, baud=19200):
    out = []
    for i, value in enumerate(values):
        out.append({"type": "uart_byte", "start_sample": i * 100,
                    "end_sample": i * 100 + 80,
                    "start_time": i * 100 / 1_000_000,
                    "end_time": (i * 100 + 80) / 1_000_000,
                    "fields": {"byte": value, "baud": baud}})
    return out


def test_lin_and_midi_stacked_decoders():
    identifier = 0x12
    pid = lin_pid(identifier)
    data = bytes([0x10, 0x20])
    checksum = lin_checksum(data, pid, enhanced=True)
    wf = WaveformData(sample_rate=1_000_000,
                      digital=np.zeros(600, dtype=np.uint16))
    ctx = DecodeContext(wf, {}, upstream_events=_uart_events([0x55, pid, *data, checksum]))
    lin = LinDecoder().decode(ctx, {"data_length": 2, "checksum": "enhanced"})
    assert lin.events[0]["fields"]["checksum_ok"] is True

    midi_ctx = DecodeContext(wf, {}, upstream_events=_uart_events([0x90, 60, 100, 0x80, 60, 0]))
    midi = MidiDecoder().decode(midi_ctx, {})
    assert [e["fields"]["data_hex"] for e in midi.events] == ["3c64", "3c00"]


def test_ps2_quadrature_and_i2s_decoders():
    # PS/2 frame: start, 0x1c LSB-first, odd parity, stop.
    byte = 0x1C
    data_bits = [(byte >> i) & 1 for i in range(8)]
    parity = 1 if (sum(data_bits) % 2) == 0 else 0
    bits = [0, *data_bits, parity, 1]
    clock = np.zeros(11 * 4, dtype=np.uint16)
    data = np.ones(11 * 4, dtype=np.uint16)
    for i, bit in enumerate(bits):
        data[i * 4:(i + 1) * 4] = bit
        clock[i * 4 + 2:i * 4 + 4] = 1
    wf = WaveformData(sample_rate=1_000_000, digital=data | (clock << 1))
    ps2 = Ps2Decoder().decode(DecodeContext(wf, {"clock": "d1", "data": "d0"}),
                              {"edge": "rising"})
    assert ps2.events[0]["fields"]["byte"] == byte
    assert ps2.events[0]["fields"]["valid"] is True

    states = [(0, 0), (0, 1), (1, 1), (1, 0), (0, 0)]
    a = np.repeat([x for x, _ in states], 3).astype(np.uint16)
    b = np.repeat([y for _, y in states], 3).astype(np.uint16)
    quad_wf = WaveformData(sample_rate=1_000_000, digital=a | (b << 1))
    quad = QuadratureDecoder().decode(DecodeContext(quad_wf, {"a": "d0", "b": "d1"}), {})
    assert quad.events[-1]["fields"]["position"] == 4

    sck = np.tile([0, 1], 16).astype(np.uint16)
    ws = np.zeros(32, dtype=np.uint16)
    sd = np.array([0, 1] * 16, dtype=np.uint16)
    i2s_wf = WaveformData(sample_rate=1_000_000, digital=sck | (ws << 1) | (sd << 2))
    i2s = I2sDecoder().decode(DecodeContext(i2s_wf, {"sck": "d0", "ws": "d1", "sd": "d2"}),
                               {"word_bits": 8, "sample_bits": 8})
    assert i2s.events


def test_can_decoder_standard_data_frame():
    identifier = 0x123
    data_byte = 0xA5
    logical = [0]
    logical.extend((identifier >> i) & 1 for i in range(10, -1, -1))
    logical.extend([0, 0, 0])  # RTR, IDE, reserved
    logical.extend([0, 0, 0, 1])  # DLC=1
    logical.extend((data_byte >> i) & 1 for i in range(7, -1, -1))
    crc = can_crc15(logical)
    logical.extend((crc >> i) & 1 for i in range(14, -1, -1))
    raw = []
    previous = None
    run = 0
    for bit in logical:
        raw.append(bit)
        if bit == previous:
            run += 1
        else:
            previous, run = bit, 1
        if run == 5:
            stuffed = 1 - bit
            raw.append(stuffed)
            previous, run = stuffed, 1
    raw.extend([1, 0, 1, 1, 1, 1, 1, 1, 1])  # delimiters, ACK, EOF
    signal = np.ones(4 + len(raw) * 4, dtype=np.uint16)
    for i, bit in enumerate(raw):
        signal[4 + i * 4:4 + (i + 1) * 4] = bit
    wf = WaveformData(sample_rate=2_000_000, digital=signal)
    result = CanDecoder().decode(DecodeContext(wf, {"rx": "d0"}), {"bit_rate": 500_000})
    assert result.events
    assert result.events[0]["fields"]["identifier"] == identifier
    assert result.events[0]["fields"]["data_hex"] == "a5"
