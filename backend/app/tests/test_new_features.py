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
from app.decoders.hdlc import HdlcDecoder, hdlc_crc16
from app.decoders.jtag import JtagDecoder
from app.decoders.infrared import InfraredDecoder
from app.decoders.smbus import SmbusDecoder, smbus_pec
from app.generator.bitbang import expand_symbols, preview, preset_symbols
from app.generator.protocols import encode, uart_symbols
from app.exports.importers import csv_session, vcd_session
from app.measurements import digital
from app.measurements.base import MeasurementContext, run_measurement
from app.triggers.software_trigger import find_software_trigger
from app.waveform.analogue import cross_correlation_delay, spectrogram, spectrum_peaks
from app.validation import junit_xml, validate_events


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


def test_raw_trigger_occurrence_selects_nth_match():
    bits = np.zeros(24, dtype=np.uint16)
    bits[[2, 7, 14, 21]] = 1
    wf = WaveformData(sample_rate=1_000_000, digital=bits)
    assert find_software_trigger(wf, TriggerConfig(type="rising", occurrence=2)) == 7
    assert find_software_trigger(wf, TriggerConfig(type="pattern", pattern="1",
                                                   occurrence=3)) == 14
    assert find_software_trigger(wf, TriggerConfig(type="rising", occurrence=2,
                                                   holdoff_s=10e-6)) == 14
    wide = np.zeros(24, dtype=np.uint16); wide[2:10] = 1; wide[14:18] = 1
    wide_wf = WaveformData(sample_rate=1_000_000, digital=wide)
    assert find_software_trigger(wide_wf, TriggerConfig(type="rising", min_duration_s=6e-6)) == 2
    derived = WaveformData(sample_rate=1_000_000,
                           digital=np.zeros(24, dtype=np.uint16),
                           derived_digital={"x0": wide})
    assert find_software_trigger(derived, TriggerConfig(type="any_edge",
                                                        channel_refs=["x0"])) == 2


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


def test_bitbang_script_expansion_and_bounds():
    extra = {"script": [{"symbols": [0, 1], "gap_symbols": 2, "repeat": 2}],
             "repeat": 2}
    symbols = expand_symbols(extra, 1_000_000)
    assert symbols == [3, 3, 0, 1, 3, 3, 0, 1] * 2
    p = preview({"symbols": [0, 1, 2, 3]}, 1_000_000)
    assert p["count"] == 4 and p["duration_s"] == 4e-6
    assert len(preset_symbols("walking", 12)) == 12
    assert preset_symbols("counter", 4) == [0, 1, 2, 3]
    framed = uart_symbols(b"A", 115200, parity="even", stop_bits=2,
                          fault="wrong_parity")
    assert framed[0] == 3 and 2 in framed
    assert encode("manchester", b"\xA5", 1_000_000)
    assert encode("spi", b"\xA5", 1_000_000, {"cpol": 1, "cpha": 1,
                                                "word_size": 8})
    assert len(encode("lin", b"\x01\x02", 19_200, {"identifier": 0x12})) > 20
    assert expand_symbols({"encoding": "nrz", "data_hex": "A5"}, 1_000_000)


def test_csv_and_vcd_importers_preserve_signal_names():
    session, wf = csv_session("sample,time_s,CLK,AIN (V)\n0,0,0,1.0\n1,1e-6,1,2.0\n",
                             1_000_000)
    assert session.channels[0].name == "CLK"
    assert wf.analog["AIN"].tolist() == [1.0, 2.0]
    vcd = """$timescale 1 us $end
$var wire 1 ! CLK $end
$enddefinitions $end
#0
0!
#2
1!
#4
0!
"""
    imported, vwf = vcd_session(vcd)
    assert imported.channels[0].name == "CLK"
    assert vwf.digital_channel(0).tolist()[:5] == [0, 0, 1, 1, 0]


def test_derived_waveform_analysis_finds_peaks_and_delay():
    sample_rate = 1_000.0
    t = np.arange(1024) / sample_rate
    signal = np.sin(2 * np.pi * 125 * t) + 0.2 * np.sin(2 * np.pi * 250 * t)
    freqs, magnitude = np.fft.rfftfreq(signal.size, 1 / sample_rate), np.abs(np.fft.rfft(signal))
    peaks = spectrum_peaks(freqs, magnitude, count=3)
    assert abs(peaks[0]["frequency_hz"] - 125) < 2
    assert peaks[0]["magnitude"] > peaks[1]["magnitude"]

    bins, frames, values = spectrogram(signal, sample_rate, window=128, hop=64)
    assert len(frames) > 0 and len(bins) == 65 and len(values) == len(frames)
    assert all(len(row) == len(bins) for row in values)

    delayed = np.concatenate([np.zeros(7), signal[:-7]])
    result = cross_correlation_delay(signal, delayed, sample_rate)
    assert abs(abs(result["delay_s"]) - 7 / sample_rate) < 1e-9


def test_setup_hold_and_channel_skew_measurements_are_registered():
    data = np.zeros(32, dtype=np.uint16)
    clock = np.zeros(32, dtype=np.uint16)
    data[[3, 11, 19, 27]] = 1
    clock[[5, 13, 21, 29]] = 1
    wf = WaveformData(sample_rate=1_000_000, digital=data | (clock << 1))
    ctx = MeasurementContext(wf, 0, wf.num_samples)
    setup = run_measurement("dig_setup_hold", ctx, ["d0", "d1"])
    skew = run_measurement("dig_channel_skew", ctx, ["d0", "d1"])
    assert setup["clock_edges"] == 4 and setup["min_setup"] == 1e-6
    assert skew["pairs"] == 4 and skew["min"] == 2e-6


def test_extended_timing_and_analog_statistics_are_registered():
    bits = np.tile([0, 0, 1, 1], 8).astype(np.uint16)
    analog = np.sin(np.linspace(0, 4 * np.pi, len(bits))).astype(np.float32)
    wf = WaveformData(sample_rate=1_000_000, digital=bits,
                      analog={"a0": analog})
    ctx = MeasurementContext(wf, 0, wf.num_samples)
    periods = run_measurement("dig_period_stats", ctx, ["d0"])
    pulses = run_measurement("dig_pulse_histogram", ctx, ["d0"])
    crest = run_measurement("ana_crest", ctx, ["a0"])
    assert periods["count"] > 0 and periods["median"] is not None
    assert pulses["counts"] and crest["value"] > 1


def test_hdlc_decoder_unstuffs_and_checks_crc():
    body = bytes([0xC0, 0x21, 0x7E])
    payload = body + hdlc_crc16(body).to_bytes(2, "little")
    raw = []
    ones = 0
    for byte in payload:
        for bit_index in range(8):
            bit = (byte >> bit_index) & 1
            raw.append(bit)
            ones = ones + 1 if bit else 0
            if ones == 5:
                raw.append(0)
                ones = 0
    bits = [0, 1, 1, 1, 1, 1, 1, 0] + raw + [0, 1, 1, 1, 1, 1, 1, 0]
    signal = np.repeat(bits, 4).astype(np.uint16)
    wf = WaveformData(sample_rate=4_000_000, digital=signal)
    result = HdlcDecoder().decode(DecodeContext(wf, {"data": "d0"}), {"bit_rate": 1_000_000})
    assert result.events[0]["fields"]["payload_hex"] == payload.hex()
    assert result.events[0]["fields"]["crc_ok"] is True


def test_jtag_decoder_groups_shift_bits():
    tck = np.tile([0, 1], 8).astype(np.uint16)
    tms = np.zeros(16, dtype=np.uint16); tms[-1] = 1
    tdi = np.array([0, 1] * 8, dtype=np.uint16)
    tdo = np.array([1, 0] * 8, dtype=np.uint16)
    digital = tck | (tms << 1) | (tdi << 2) | (tdo << 3)
    wf = WaveformData(sample_rate=1_000_000, digital=digital)
    result = JtagDecoder().decode(DecodeContext(wf, {"tck": "d0", "tms": "d1",
                                                       "tdi": "d2", "tdo": "d3"}), {})
    assert result.events and result.events[0]["fields"]["bits"] == 8


def test_nec_infrared_decoder_validates_complement_bytes():
    values = [0x12, 0xED, 0x34, 0xCB]
    signal = [np.ones(100, dtype=np.uint16)]
    signal += [np.zeros(9000, dtype=np.uint16), np.ones(4500, dtype=np.uint16)]
    for value in values:
        for bit in range(8):
            signal += [np.zeros(560, dtype=np.uint16),
                       np.ones(1690 if (value >> bit) & 1 else 560, dtype=np.uint16)]
    signal.append(np.ones(560, dtype=np.uint16))
    wf = WaveformData(sample_rate=1_000_000, digital=np.concatenate(signal))
    result = InfraredDecoder().decode(DecodeContext(wf, {"data": "d0"}), {"protocol": "nec"})
    assert result.events[0]["fields"]["address"] == 0x12
    assert result.events[0]["fields"]["command"] == 0x34
    assert result.events[0]["fields"]["valid"] is True


def test_smbus_stacked_decoder_validates_pec():
    address, command = 0x2A, 0x09
    data = [0x34, 0x12]
    read = 0
    pec = smbus_pec([(address << 1) | read, command, *data])
    events = [{"type": "i2c_address", "start_sample": 10,
               "fields": {"address": address, "rw": read}},
              *({"type": "i2c_byte", "start_sample": 20 + i,
                 "end_sample": 21 + i, "fields": {"byte": value}}
                for i, value in enumerate([command, *data, pec]))]
    wf = WaveformData(sample_rate=1_000_000, digital=np.zeros(64, dtype=np.uint16))
    result = SmbusDecoder().decode(DecodeContext(wf, {}, upstream_events=events), {})
    assert result.events[0]["fields"]["command"] == command
    assert result.events[0]["fields"]["pec_ok"] is True


def test_session_assertions_support_expected_events_and_junit():
    events = [{"type": "uart_byte", "severity": "normal",
               "start_time": 0.0, "end_time": 1e-6,
               "fields": {"byte": 0x55}}]
    result = validate_events(events, {"expected_events": [{"type": "uart_byte",
        "fields": {"byte": 0x55}}], "min_events": 1, "max_errors": 0})
    assert result["passed"] is True
    assert "testsuite" in junit_xml(result)
