import numpy as np

from app.capture.sample_format import WaveformData
from app.capture.session import TriggerConfig
from app.decoders.base import DecodeContext
from app.decoders.manchester import ManchesterDecoder
from app.decoders.nrz import NrzDecoder
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
