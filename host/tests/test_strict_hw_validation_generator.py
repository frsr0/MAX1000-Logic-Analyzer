import queue
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app import hw_validation as hv


class Clock:
    def __init__(self, step=0.1):
        self.value = -step
        self.step = step

    def __call__(self):
        self.value += self.step
        return self.value


def sampled(ns=512, active=(0, 3, 7)):
    return [([i & 1 for i in range(ns)] if c in active else [0] * ns)
            for c in range(23)], ns


def uart_bytes(payload):
    return [SimpleNamespace(value=value) for value in payload]


def device():
    value = MagicMock()
    value.sys_clk = 100_000_000
    value.sample_clk = 200_000_000
    value._raw_flags = 0
    value.spi = MagicMock()
    value.pkt = MagicMock()
    value.pkt.get_status.return_value = {"capture_status": hv.ST_CAPTURE_DONE}
    return value


@pytest.fixture(autouse=True)
def quiet_generator():
    old = (hv.PASS, hv.FAIL, hv.TOTAL, hv.SKIPPED,
           hv._JUMPER_PAIR_CACHE, hv._JUMPER_PAIR_SEARCHED)
    hv.PASS = hv.FAIL = hv.TOTAL = hv.SKIPPED = 0
    hv._JUMPER_PAIR_CACHE = None
    hv._JUMPER_PAIR_SEARCHED = False
    with patch.object(hv, "save_result"), patch.object(hv, "print_header"), \
         patch.object(hv, "log"), patch.object(hv.time, "sleep"):
        yield
    (hv.PASS, hv.FAIL, hv.TOTAL, hv.SKIPPED,
     hv._JUMPER_PAIR_CACHE, hv._JUMPER_PAIR_SEARCHED) = old


def pack_lsb(bits):
    packed = bytearray()
    for start in range(0, len(bits), 8):
        value = 0
        for bit_index, bit in enumerate(bits[start:start + 8]):
            value |= (bit & 1) << bit_index
        packed.append(value)
    return bytes(packed)


@pytest.mark.parametrize("debug", [False, True])
def test_uart_generator_completes_fsm_decodes_payload_and_sweeps_outputs(debug):
    value = device()
    status_calls = 0

    def transaction(command, **_kwargs):
        nonlocal status_calls
        if command == hv.CMD_GEN_STATUS:
            status_calls += 1
            return (hv.ST_OK, 0, bytes([0x40 if status_calls == 1 else 0x01]))
        return (hv.ST_OK, 0, b"")

    value.pkt.transaction.side_effect = transaction
    value.capture_with_gen.return_value = b"capture"
    value._wait_gen_idle.return_value = True
    expected_bits = [symbol & 1 for symbol in hv.bit_bang.uart_symbols(b"Hello" * 20)]
    value.gen_rx_read.return_value = pack_lsb(expected_bits)
    with patch.object(hv, "samples_to_channels", return_value=sampled(active=(0, 1, 3, 7))), \
         patch.object(hv, "decode_uart", return_value=uart_bytes(b"Hello")), \
         patch.object(hv, "log_floating_channel_activity"):
        hv.test_gen_uart(value, debug_on=debug)
    assert hv.FAIL == 0
    assert hv.PASS >= 5
    assert value.capture_with_gen.call_count == (2 if debug else 17)


def test_uart_generator_reports_rejected_capture_timeout_empty_and_sweep_failures():
    value = device()
    value.capture_with_gen.return_value = b""
    value.pkt.transaction.return_value = None
    value._wait_gen_idle.return_value = False
    hv.test_gen_uart(value, debug_on=False)
    assert hv.FAIL == 17

    value = device()
    calls = 0

    def status_without_busy(command, **_kwargs):
        nonlocal calls
        if command == hv.CMD_GEN_CAPTURE:
            return (hv.ST_OK, 0, b"")
        if command == hv.CMD_GEN_START:
            return (hv.ST_OK, 0, b"")
        calls += 1
        return (hv.ST_OK, 0, b"\x00")

    hv.PASS = hv.FAIL = hv.TOTAL = 0
    value.pkt.transaction.side_effect = status_without_busy
    value.capture_with_gen.return_value = b"capture"
    value._wait_gen_idle.return_value = False
    value.gen_rx_read.return_value = b"\x00"
    with patch.object(hv, "samples_to_channels", return_value=sampled(active=())), \
         patch.object(hv, "decode_uart", return_value=[]), \
         patch.object(hv, "log_floating_channel_activity"):
        hv.test_gen_uart(value, debug_on=True)
    assert hv.FAIL == 5


def test_decode_i2c_best_selects_offset_with_most_non_idle_bytes():
    decoded = {
        -1: [("DATA", 0x00)],
        0: [("DATA", 0x22), ("DATA", 0xFF)],
        1: [("DATA", 0x22), ("DATA", 0x33)],
    }
    with patch.object(hv, "decode_i2c", side_effect=lambda *_a, **kw: decoded[kw["sda_offset"]]):
        result, offset = hv.decode_i2c_best([], 1_000_000, offsets=(-1, 0, 1))
    assert offset == 1
    assert result == decoded[1]


def test_run_with_timeout_returns_value_propagates_error_and_enforces_deadline():
    assert hv.run_with_timeout(1, lambda left, right: left + right, 2, 3) == 5
    with pytest.raises(ValueError, match="bad"):
        hv.run_with_timeout(1, lambda: (_ for _ in ()).throw(ValueError("bad")))
    event = MagicMock()
    event.wait.return_value = False
    with patch.object(hv.threading, "Event", return_value=event), \
         patch.object(hv.threading, "Thread"):
        with pytest.raises(TimeoutError, match="did not complete"):
            hv.run_with_timeout(0.01, lambda: None)


def test_long_stress_accepts_stable_stream_and_records_debug_activity():
    value = device()

    def rolling(**kwargs):
        kwargs["full_out"].extend(b"x" * 5000)
        return iter((b"chunk", 1024, n * 1024) for n in range(1, 1000))

    value.rolling_capture.side_effect = rolling
    with patch.object(hv.time, "time", Clock(0.1)), \
         patch.object(hv, "samples_to_channels", return_value=sampled(2501, (0,))), \
         patch.object(hv, "log_floating_channel_activity"), \
         patch.object(hv, "check_channels_clean"):
        hv.test_long_stress(value, debug_on=True)
    assert hv.FAIL == 0
    assert hv.PASS == 5


def test_long_stress_handles_stop_worker_error_timeout_and_outer_error():
    value = device()
    value.rolling_capture.return_value = iter([])
    with patch.object(hv.time, "time", Clock(1.0)):
        hv.test_long_stress(value)
    assert hv.FAIL == 2

    def raising_stream():
        yield (b"ok", 1, 1)
        raise OSError("stream")

    value.rolling_capture.return_value = raising_stream()
    with patch.object(hv.time, "time", Clock(1.0)):
        hv.test_long_stress(value)
    assert hv.FAIL == 5

    value.rolling_capture.return_value = iter([(b"unused", 1, 1)])
    with patch.object(queue.Queue, "get", side_effect=queue.Empty), \
         patch.object(hv.time, "time", Clock(1.0)):
        hv.test_long_stress(value)
    assert hv.FAIL == 8

    value.rolling_capture.side_effect = RuntimeError("open")
    with patch.object(hv.time, "time", Clock(1.0)):
        hv.test_long_stress(value)
    assert hv.FAIL == 9


def test_jumper_helpers_score_waveform_restore_mapping_and_cache_discovery():
    value = device()
    bits = [symbol & 1 for symbol in hv.bit_bang.uart_symbols(b"A")]
    signal = [bit for bit in bits for _ in range(10)]
    assert hv._channel_transitions([signal], len(signal))[0] > 0
    fraction, offset = hv._uart_waveform_match_fraction(signal, b"A", 10_000, 1_000)
    assert fraction == 1.0
    assert offset == 0
    assert hv._uart_waveform_match_fraction([], b"A", 1, 1) == (0.0, None)
    assert hv._uart_waveform_match_fraction(signal, b"", 1, 1) == (0.0, None)
    assert hv._uart_waveform_match_fraction(signal, b"A", 0, 1) == (0.0, None)
    inverted, _ = hv._uart_waveform_match_fraction(signal, b"A", 10_000, 1_000, invert=True)
    assert inverted < fraction

    hv._restore_pin_map(value)
    assert value.set_pin_map.call_count == 16

    with patch.object(hv, "_discover_jumper_pair", return_value=(20, 7)) as discover:
        assert hv._get_jumper_pair(value) == (20, 7)
        assert hv._get_jumper_pair(value) == (20, 7)
    discover.assert_called_once_with(value)
    hv._JUMPER_PAIR_CACHE = None
    assert hv._get_jumper_pair(value) is None


def test_jumper_discovery_ignores_mirror_noise_and_returns_decoded_partner():
    value = device()
    value.capture_with_gen.return_value = b"probe"
    rows, ns = sampled(128, (5, 7))
    transitions = [0] * 16
    transitions[5] = transitions[7] = 50
    with patch.object(hv.time, "time", Clock(0.01)), \
         patch.object(hv, "samples_to_channels", return_value=(rows, ns)), \
         patch.object(hv, "_channel_transitions", return_value=transitions), \
         patch.object(hv, "decode_uart_safe", return_value=uart_bytes(b"PinProbe!")):
        assert hv._discover_jumper_pair(value, deadline_s=1) == (21, 7)


def test_jumper_discovery_handles_abort_errors_empty_captures_and_deadline():
    value = device()
    value.pkt.transaction.side_effect = OSError("abort")
    value.capture_with_gen.return_value = b""
    with patch.object(hv.time, "time", Clock(0.01)):
        assert hv._discover_jumper_pair(value, deadline_s=0.2) is None
    with patch.object(hv.time, "time", Clock(1.0)):
        assert hv._discover_jumper_pair(value, deadline_s=0.5) is None


def test_jumper_loopback_skips_without_fixture_and_validates_direct_pair():
    value = device()
    with patch.object(hv, "_get_jumper_pair", return_value=None), \
         patch.object(hv, "_restore_pin_map"):
        hv.test_jumper_loopback(value)
    assert hv.SKIPPED == 1

    value.capture_with_gen.return_value = b"wire"
    rows, ns = sampled(512, (3, 7))
    with patch.object(hv, "_get_jumper_pair", return_value=(3, 7)), \
         patch.object(hv, "_restore_pin_map"), \
         patch.object(hv, "samples_to_channels", return_value=(rows, ns)), \
         patch.object(hv, "decode_uart_safe", return_value=uart_bytes(b"MAX1000 jumper")), \
         patch.object(hv, "_uart_waveform_match_fraction", return_value=(0.99, 0)):
        hv.test_jumper_loopback(value)
    assert hv.FAIL == 0
    assert hv.PASS == 5


def test_jumper_loopback_retries_payload_and_reports_empty_trigger_capture():
    value = device()
    value.capture_with_gen.side_effect = [b"identity", b"", b"payload", b""]
    rows, ns = sampled(128, (7,))
    with patch.object(hv, "_get_jumper_pair", return_value=(20, 7)), \
         patch.object(hv, "_restore_pin_map"), \
         patch.object(hv, "samples_to_channels", return_value=(rows, ns)), \
         patch.object(hv, "decode_uart_safe", return_value=[]), \
         patch.object(hv, "_uart_waveform_match_fraction", return_value=(0.2, None)):
        hv.test_jumper_loopback(value)
    assert hv.FAIL == 2


def test_generator_matrix_decodes_uart_spi_and_i2c_on_both_capture_paths():
    value = device()
    value.capture_with_gen.return_value = b"matrix"
    rows, ns = sampled(128, tuple(range(16)))
    with patch.object(hv, "_get_jumper_pair", return_value=(20, 7)), \
         patch.object(hv, "samples_to_channels", return_value=(rows, ns)), \
         patch.object(hv, "decode_uart_safe", return_value=uart_bytes(b"JMP-UART\x00\xffU\xaa")), \
         patch.object(hv, "decode_spi", return_value=list(b"\xa5\x3c\xde\xad\x00\xff")), \
         patch.object(hv, "decode_i2c", return_value=[("DATA", value) for value in b"\xa6\x2d\x08"]), \
         patch.object(hv, "_restore_pin_map"):
        hv.test_jumper_generator_matrix(value)
    assert hv.FAIL == 0
    assert hv.PASS == 6


def test_generator_matrix_skips_or_raises_on_missing_capture():
    value = device()
    with patch.object(hv, "_get_jumper_pair", return_value=None):
        hv.test_jumper_generator_matrix(value)
    assert hv.SKIPPED == 1
    with patch.object(hv, "_get_jumper_pair", return_value=(20, 7)):
        value.capture_with_gen.return_value = b""
        with pytest.raises(RuntimeError, match="UART capture returned no data"):
            hv.test_jumper_generator_matrix(value)


def test_live_generator_decodes_each_frame_and_handles_missing_frames_or_fixture():
    value = device()
    with patch.object(hv, "_get_jumper_pair", return_value=None):
        hv.test_live_generator_decode(value)
    assert hv.SKIPPED == 1

    value.capture_with_gen.return_value = b"frame"
    rows, ns = sampled(64, (7,))
    with patch.object(hv, "_get_jumper_pair", return_value=(20, 7)), \
         patch.object(hv, "samples_to_channels", return_value=(rows, ns)), \
         patch.object(hv, "decode_uart_safe", side_effect=lambda *_a, **_k: uart_bytes(value._gen_data)), \
         patch.object(hv, "_uart_waveform_match_fraction", return_value=(1.0, 0)), \
         patch.object(hv, "_restore_pin_map"):
        hv.test_live_generator_decode(value)

    value.capture_with_gen.return_value = b""
    with patch.object(hv, "_get_jumper_pair", return_value=(20, 7)), \
         patch.object(hv, "_restore_pin_map"):
        hv.test_live_generator_decode(value)
    assert hv.FAIL == 0


def test_repeating_uart_ring_accepts_sustained_decode_and_handles_no_fixture():
    value = device()
    with patch.object(hv, "_get_jumper_pair", return_value=None):
        hv.test_repeating_uart_continuous_ring(value)
    assert hv.SKIPPED == 1

    value.continuous_ring_capture_with_repeating_uart.return_value = iter(
        (b"chunk", n, n) for n in range(14))
    rows, ns = sampled(64, (7,))
    with patch.object(hv, "_get_jumper_pair", return_value=(20, 7)), \
         patch.object(hv, "samples_to_channels", return_value=(rows, ns)), \
         patch.object(hv, "decode_uart_safe", return_value=uart_bytes(b"R33!")), \
         patch.object(hv, "_uart_waveform_match_fraction", return_value=(1.0, 0)), \
         patch.object(hv, "_restore_pin_map"), patch.object(hv.threading, "Timer"):
        hv.test_repeating_uart_continuous_ring(value)
    assert hv.FAIL == 0
    assert hv.PASS == 1


def test_repeating_uart_ring_counts_misses_and_tolerates_stream_without_close():
    value = device()
    value.continuous_ring_capture_with_repeating_uart.return_value = iter(
        (b"chunk", n, n) for n in range(14))
    rows, ns = sampled(64, (7,))
    decodes = [uart_bytes(b"R33!") if n in (2, 4, 6) else [] for n in range(14)]
    with patch.object(hv, "_get_jumper_pair", return_value=(20, 7)), \
         patch.object(hv, "samples_to_channels", return_value=(rows, ns)), \
         patch.object(hv, "decode_uart_safe", side_effect=decodes), \
         patch.object(hv, "_uart_waveform_match_fraction", return_value=(0.2, None)), \
         patch.object(hv, "_restore_pin_map"), patch.object(hv.threading, "Timer"):
        hv.test_repeating_uart_continuous_ring(value)
    assert hv.FAIL == 1


def test_accelerometer_dialogue_validates_i2c_spi_and_capture_decoders():
    value = device()
    value.accel_read_i2c.side_effect = [None, 0x33, 0x33, 0x07]
    value.accel_whoami_spi.side_effect = [{2: 0x33}, {2: 0x33}]
    value.accel_capture_dialogue.return_value = b"dialogue"
    rows, ns = sampled(64, (13, 14, 15))
    i2c = [("START", 0), ("DATA", 0x32), ("DATA", 0x0F),
           ("DATA", 0x33), ("DATA", 0x33)]
    with patch.object(hv, "samples_to_channels", return_value=(rows, ns)), \
         patch.object(hv, "decode_i2c", return_value=i2c), \
         patch.object(hv, "decode_spi", return_value=[0x33]):
        hv.test_accelerometer_whoami(value)
    assert hv.FAIL == 0
    assert value.accel_capture_dialogue.call_count == 2


def test_accelerometer_dialogue_reports_missing_slave_and_empty_mirrors():
    value = device()
    value.accel_read_i2c.return_value = None
    value.accel_whoami_spi.return_value = None
    value.accel_capture_dialogue.return_value = b""
    hv.test_accelerometer_whoami(value)
    assert hv.FAIL == 4


def test_codec_matrix_covers_completed_exact_mismatch_and_incomplete_rates():
    value = device()
    raw = bytes((index // 2) & 0xFF for index in range(262_144 * 2))
    mismatch = bytearray(raw)
    mismatch[10] ^= 1
    value.read_capture_range.side_effect = [raw, raw, bytes(mismatch)]
    with patch.object(hv, "_wait_capture_done", side_effect=[True, False, False, False, False]), \
         patch.object(hv.time, "time", Clock(0.1)):
        hv.test_codec_readback_matrix(value)
    assert hv.FAIL == 1
    assert hv.PASS == 3


class RateDevice:
    def __init__(self, delta_lossless=True, fail_one=False):
        self.pkt = MagicMock()
        self.spi = MagicMock()
        self.codec = "raw"
        self.delta_lossless = delta_lossless
        self.fail_one = fail_one

    def reset(self):
        pass

    def set_analog_config(self, _mode):
        pass

    def set_schmitt(self, _enabled):
        pass

    def set_debug_ch0(self, _enabled, **_kwargs):
        pass

    def set_readback_compression(self, codec):
        self.codec = codec

    def stream_ring_capture(self, rate, _chunk, _stop):
        if self.fail_one and self.codec == "rle" and rate == 30_000_000:
            raise OSError("stream")
        if self.codec == "raw":
            total = rate if rate <= 500_000 else 1000
        elif self.codec == "delta_rle":
            ceiling = 500_000 if self.delta_lossless else 250_000
            total = rate if rate <= ceiling else 500
        else:
            total = 0
        yield b"", 0, 0, 0
        yield b"", total, 0, (1 if rate == 24_000_000 else 0)


@pytest.mark.parametrize("delta_lossless", [True, False])
def test_live_rate_ceiling_characterises_lossless_lossy_overrun_and_exception(delta_lossless):
    value = RateDevice(delta_lossless=delta_lossless, fail_one=True)
    with patch.object(hv.time, "time", Clock(0.1)), \
         patch.object(hv.threading, "Timer"):
        hv.test_live_rate_ceiling(value)
    # The injected RLE transport fault occurs once for each source-frequency
    # sweep; a degraded delta ceiling is an additional strict failure.
    assert hv.FAIL == (3 if delta_lossless else 4)
    assert value.codec == "raw"
