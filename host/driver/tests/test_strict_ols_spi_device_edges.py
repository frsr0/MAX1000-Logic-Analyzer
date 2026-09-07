"""Strict edge tests for the high-level SPI device façade."""

import struct
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock, call

import pytest

from driver import bit_bang
from driver import ols_spi_device as device_module
from driver.ols_spi_device import (
    MIXED_COMPRESSED_GROUP_FRAMES,
    MODE_ANALOG_ONLY,
    MODE_DIGITAL,
    MODE_MIXED,
    OLSDeviceSPI,
    compress_mixed_group,
    decompress_mixed_group,
    decompress_mixed_stream,
)
from driver.spi_protocol import (
    CMD_GEN_START,
    REG_FLAGS,
    REG_FLAGS_COMPRESS_MASK,
    REG_GEN_BAUD,
    REG_GEN_RX_DATA,
    REG_PATTERN_CTRL,
    ST_CAPTURE_ARMED,
    ST_CAPTURE_DONE,
    ST_OK,
)
from driver.wire_format import analog_frame_stride


def _device():
    dev = OLSDeviceSPI()
    dev.spi = MagicMock()
    dev.pkt = MagicMock()
    return dev


def _mixed_prefix(mode):
    stride = analog_frame_stride(MODE_MIXED)
    lanes = max(0, (stride - 2) // 3 * 2)
    header_bytes = max(1, (lanes * 2 + 7) // 8)
    return int(mode).to_bytes(header_bytes, "little") + b"\x00" * (
        MIXED_COMPRESSED_GROUP_FRAMES * 2
    )


def test_mixed_codec_rejects_all_malformed_group_shapes():
    with pytest.raises(ValueError, match="expected"):
        compress_mixed_group(b"")
    with pytest.raises(ValueError, match="header"):
        decompress_mixed_group(b"")
    with pytest.raises(ValueError, match="delta lane"):
        decompress_mixed_group(_mixed_prefix(0))
    escaped = _mixed_prefix(0) + struct.pack("<H15b", 4095, 1, *([0] * 14))
    with pytest.raises(ValueError, match="escaped"):
        decompress_mixed_group(escaped)
    with pytest.raises(ValueError, match="raw lane"):
        decompress_mixed_group(_mixed_prefix(1))
    with pytest.raises(ValueError, match="unknown mixed lane mode 2"):
        decompress_mixed_group(_mixed_prefix(2))
    assert decompress_mixed_stream(b"") == b""


def test_compression_configuration_rejects_invalid_mode_and_disables_failed_codec():
    dev = _device()
    with pytest.raises(ValueError, match="unsupported"):
        dev.set_readback_compression("brotli")

    dev._compressed_block_reads_supported["delta_rle"] = False
    dev.pkt.read_register.return_value = REG_FLAGS_COMPRESS_MASK | 7
    dev.pkt.write_register.side_effect = OSError("device vanished")
    assert dev.set_readback_compression("delta") is True
    assert dev._readback_hardware_is_raw is True


def test_compression_mode_flushes_safely_and_reports_register_failures():
    dev = _device()
    dev.spi.flush.side_effect = OSError("stale device")
    dev.pkt.read_register.return_value = -1
    assert dev.set_readback_compression("rle") is False

    dev.pkt.read_register.return_value = 0
    dev.pkt.write_register.return_value = True
    assert dev.set_readback_compression("rle", force_hardware=True) is True
    assert dev._readback_hardware_is_raw is False


def test_packed_mode_handles_read_failure_enable_and_disable():
    dev = _device()
    dev.pkt.read_register.return_value = -1
    assert dev.set_packed_mode(True) is False

    dev.pkt.read_register.return_value = 3
    dev.pkt.write_register.return_value = True
    assert dev.set_packed_mode(True) is True
    assert dev.raw_flags & device_module.MODE_PACKED_MSO
    assert dev.set_packed_mode(False) is True
    assert not (dev.raw_flags & device_module.MODE_PACKED_MSO)


def test_open_retries_then_configures_latency_and_detection(monkeypatch):
    attempts = []

    class SPI:
        def __init__(self, speed_hz):
            self.speed_hz = speed_hz
            self.dev = MagicMock()
            self.dev.getLatencyTimer.return_value = 16

        def open(self):
            attempts.append(self)
            if len(attempts) < 3:
                raise OSError("busy")

    monkeypatch.setattr(device_module, "OLS_SPI", SPI)
    monkeypatch.setattr(device_module.time, "sleep", lambda _: None)
    monkeypatch.setenv("OLS_SPEED_HZ", "12345678")
    dev = OLSDeviceSPI()
    dev._detect_sample_clk = MagicMock()
    dev._detect_gen_div_width = MagicMock()
    dev.open()

    assert len(attempts) == 3
    assert dev.spi.speed_hz == 12_345_678
    dev.spi.dev.setLatencyTimer.assert_called_once_with(1)
    dev._detect_sample_clk.assert_called_once()
    dev._detect_gen_div_width.assert_called_once()


def test_open_reraises_third_failure_and_latency_query_is_optional(monkeypatch):
    class BrokenSPI:
        def __init__(self, speed_hz):
            self.dev = MagicMock()

        def open(self):
            raise OSError("still busy")

    monkeypatch.setattr(device_module, "OLS_SPI", BrokenSPI)
    monkeypatch.setattr(device_module.time, "sleep", lambda _: None)
    with pytest.raises(OSError, match="still busy"):
        OLSDeviceSPI().open()

    class GoodSPI(BrokenSPI):
        def open(self):
            self.dev.getLatencyTimer.side_effect = OSError("unsupported")

        def close(self):
            pass

    monkeypatch.setattr(device_module, "OLS_SPI", GoodSPI)
    dev = OLSDeviceSPI()
    dev._detect_sample_clk = MagicMock()
    dev._detect_gen_div_width = MagicMock()
    dev.open()
    dev.close()
    assert dev.spi is None and dev.pkt is None


def test_metadata_clock_detection_and_digital_analog_modes(monkeypatch):
    dev = _device()
    dev.pkt.transaction.return_value = None
    assert dev.get_metadata() == b""

    dev._set_clocks(200_000_000)
    assert (dev.sample_clk, dev.sys_clk) == (200_000_000, 100_000_000)
    dev._set_clocks(80_000_000)
    assert (dev.sample_clk, dev.sys_clk) == (80_000_000, 80_000_000)

    dev.get_metadata = MagicMock(
        side_effect=[b"short", b"\x00" * 5 + (125_000).to_bytes(4, "little")]
    )
    monkeypatch.setattr(device_module.time, "sleep", lambda _: None)
    dev._detect_sample_clk()
    assert dev.sample_clk == 125_000_000

    dev.set_analog_config(MODE_DIGITAL, adc_channel=99)
    assert dev.analog_mode == MODE_DIGITAL and dev.analog_channel == 31
    dev.set_analog_config(MODE_MIXED)
    assert dev.analog_mode == MODE_MIXED
    dev.set_analog_config(MODE_ANALOG_ONLY, adc_channel=-2)
    assert dev.analog_mode & MODE_ANALOG_ONLY and dev.analog_channel == 0


def test_generator_loaders_clamp_payloads_and_skip_empty_fifo_writes(monkeypatch):
    dev = _device()
    monkeypatch.setattr(bit_bang, "max_uart_bytes", lambda: 2)
    monkeypatch.setattr(bit_bang, "max_spi_bytes", lambda: 2)
    monkeypatch.setattr(bit_bang, "max_i2c_bytes", lambda: 2)

    assert dev._gen_load_uart(b"abc", 9600) == b"ab"
    assert dev._gen_load_spi(b"abc", 4) == b"ab"
    assert dev._gen_load_i2c(b"abc", 100_000) == b"ab"
    load_count = dev.pkt.load_gen_data.call_count
    assert dev._gen_load_uart(b"", 9600) == b""
    assert dev._gen_load_spi(b"", 4) == b""
    assert dev._gen_load_i2c(b"", 100_000) == b""
    assert dev.pkt.load_gen_data.call_count == load_count


def test_i2c_read_and_swd_loaders_clamp_and_report_empty_sequences(monkeypatch):
    dev = _device()
    monkeypatch.setattr(bit_bang, "max_i2c_read_bytes", lambda frame_len: 1)
    monkeypatch.setattr(bit_bang, "i2c_read_symbols", lambda *args: [1, 2])
    assert dev._gen_load_i2c_read(b"write", 100_000, 9, 0x33) == b"write"

    monkeypatch.setattr(bit_bang, "max_swd_ops", lambda **kwargs: 1)
    monkeypatch.setattr(bit_bang, "swd_sequence_symbols", lambda *args, **kwargs: [])
    assert dev._gen_load_swd([("r", 0, 0), ("r", 0, 4)], 1_000_000) is False
    monkeypatch.setattr(bit_bang, "swd_sequence_symbols", lambda *args, **kwargs: [1])
    assert dev._gen_load_swd([("r", 0, 0)], 1_000_000) is True


@pytest.mark.parametrize(
    "values, expected",
    [([-1], b""), ([0], b""), ([(2 << 8) | 0xAA, (1 << 8) | 0xBB], b"\xaa\xbb")],
)
def test_generator_rx_fifo_stops_on_error_empty_or_last_byte(values, expected):
    dev = _device()
    dev.pkt.read_register.side_effect = values
    assert dev.gen_rx_read() == expected
    assert OLSDeviceSPI._rx_bits(b"\x05") == [1, 0, 1, 0, 0, 0, 0, 0]


def test_accelerometer_capture_rejects_arm_and_completes_success(monkeypatch):
    dev = _device()
    dev._ensure_open = MagicMock()
    dev._pins = MagicMock()
    dev._write_capture_config = MagicMock()
    dev._stream_readback = MagicMock(return_value=b"abcd")
    monkeypatch.setattr(device_module.time, "sleep", lambda _: None)

    with pytest.raises(ValueError, match="positive"):
        dev.accel_capture_dialogue([1], 2, rate_hz=0)

    dev.pkt.transaction.side_effect = [(ST_OK, 0, b""), None]
    assert dev.accel_capture_dialogue([1], 2) == b""

    dev.pkt.transaction.side_effect = [(ST_OK, 0, b""), (ST_CAPTURE_ARMED, 0, b"")]
    dev.pkt.get_status.return_value = {"capture_status": ST_CAPTURE_DONE}
    assert dev.accel_capture_dialogue([1], 2, rate_hz=1, nsamples=2) == b"abcd"


def test_generator_run_accepts_fire_and_hold_status_or_rejects_timeout(monkeypatch):
    dev = _device()
    dev._pins = MagicMock()
    dev.gen_rx_read = MagicMock(return_value=b"\x01")
    dev._wait_gen_idle = MagicMock(return_value=True)
    monkeypatch.setattr(device_module.time, "sleep", lambda _: None)

    dev.pkt.transaction.side_effect = [(ST_OK, 0, b""), None]
    dev.pkt.get_status.return_value = {"gen_busy": True}
    assert dev._gen_run_and_rx([1], 2)[:1] == [1]

    dev.pkt.transaction.side_effect = [(ST_OK, 0, b""), None]
    dev.pkt.get_status.return_value = {}
    times = iter((1.0, 1.1, 2.0))
    monkeypatch.setattr(device_module.time, "time", lambda: next(times))
    assert dev._gen_run_and_rx([1], 2) == []


def test_accelerometer_reads_cover_empty_invalid_and_candidate_paths(monkeypatch):
    dev = _device()
    dev._gen_run_and_rx = MagicMock(return_value=[])
    assert dev.accel_read_i2c(0x0F) is None
    assert dev.accel_read_spi(0x0F) is None

    dev._gen_run_and_rx.return_value = [1] * 4096
    dev._i2c_rx_decode = MagicMock(return_value=([1, 2, 3], [0, 0, 0]))
    assert dev.accel_read_i2c(0x0F) is None

    candidates = dev.accel_read_spi(0x0F)
    assert set(candidates) == {-2, -1, 0, 1, 2}
    assert dev.accel_whoami_spi() == candidates


def test_live_generator_kick_clear_and_idle_timeout(monkeypatch):
    dev = _device()
    dev._wait_gen_idle = MagicMock(return_value=False)
    assert dev._gen_kick(b"x") is False
    dev._wait_gen_idle.return_value = True
    dev.pkt.transaction.return_value = None
    assert dev._gen_kick(b"x") is False

    with pytest.raises(ValueError, match="empty"):
        dev.set_live_gen(b"", 10_000)
    with pytest.raises(ValueError, match="below"):
        dev.set_live_gen(b"x", 1)

    dev.start_gen = MagicMock()
    dev.set_live_gen(b"x", 100_000)
    assert dev.live_gen_active is True
    dev.pkt.transaction.side_effect = OSError("gone")
    dev.clear_live_gen()
    assert dev.live_gen_active is False

    dev.pkt.get_status.return_value = {"gen_busy": True}
    times = iter((1.0, 1.1, 2.0))
    monkeypatch.setattr(device_module.time, "time", lambda: next(times))
    monkeypatch.setattr(device_module.time, "sleep", lambda _: None)
    assert OLSDeviceSPI._wait_gen_idle(dev, timeout=0.5) is False


def test_i2c_rx_decoder_aligns_echo_and_reports_no_match():
    syms = bit_bang.i2c_symbols(bytes([0x32, 0x0F]))
    rx = [symbol & 1 for symbol in syms]
    assert OLSDeviceSPI._i2c_rx_decode(syms, rx, [0x32, 0x0F]) == (
        [0x32, 0x0F],
        [1, 1],
    )
    assert OLSDeviceSPI._i2c_rx_decode(syms, rx, [0x99]) == (None, None)


def test_accelerometer_i2c_success_and_whoami_wrapper():
    dev = _device()
    dev._gen_run_and_rx = MagicMock(return_value=[1])
    dev._i2c_rx_decode = MagicMock(return_value=([0x32, 0x0F, 0x33, 0xAB], [0, 0, 0, 1]))
    assert dev.accel_read_i2c(0x0F) == 0xAB
    assert dev.accel_whoami_i2c() == 0xAB


def test_trigger_configuration_validation_encoding_and_disable():
    dev = _device()
    dev.trigger_decode(match_byte=0x157, channel=99, baud=0)
    assert dev.protocol_trigger() == {"match_byte": 0x57, "channel": 15, "baud": 1}
    dev.trigger_decode(enable=False)
    assert dev.protocol_trigger() is None

    dev.configure_pattern_trigger(None)
    dev.pkt.write_register.assert_called_with(REG_PATTERN_CTRL, 0)
    for config, message in [
        ({"channels": []}, "exactly 1"),
        ({"channels": [16]}, "range 0..15"),
        ({"channels": [1], "clock_channel": 16}, "clock/start"),
    ]:
        with pytest.raises(ValueError, match=message):
            dev.configure_pattern_trigger(config)

    dev.pkt.reset_mock()
    dev.configure_pattern_trigger(
        {
            "channels": [2],
            "clock_source": "internal",
            "clock_edge": "falling",
            "start_mode": "none",
            "start_polarity": 1,
            "bit_order": "lsb_first",
            "frame_width": 4,
            "start_channel": 3,
            "clock_channel": 4,
            "value": 0b1101,
            "match_mask": 0b1111,
            "baud_div": 100_000,
        }
    )
    assert dev._reverse_pattern_bits(0b1101, 4) == 0b1011
    assert dev.pkt.write_register.call_count == 5


def test_frontend_protocol_trigger_returns_match_no_match_and_trim(monkeypatch):
    dev = _device()
    assert dev.protocol_trigger_match_pos(b"data", 1_000_000) is None
    dev.trigger_decode(match_byte=0x57, channel=0, baud=115200)

    fake = types.ModuleType("app.gui_decoders")
    fake.samples_to_channels = lambda data, stride: ([[0, 1, 0]], 3)
    fake.decode_uart = lambda *args, **kwargs: [
        SimpleNamespace(value=0x11, pos=2),
        SimpleNamespace(value=0x57, pos=4),
    ]
    monkeypatch.setitem(sys.modules, "app.gui_decoders", fake)
    assert dev.protocol_trigger_match_pos(b"0123456789", 1_000_000) == 4
    assert dev.apply_protocol_trigger(b"0123456789", 1_000_000) == (b"89", 4)

    fake.decode_uart = lambda *args, **kwargs: []
    assert dev.protocol_trigger_match_pos(b"data", 1_000_000) is None
    assert dev.apply_protocol_trigger(b"data", 1_000_000) == (b"data", None)
    fake.samples_to_channels = lambda data, stride: ([], 0)
    assert dev.protocol_trigger_match_pos(b"data", 1_000_000) is None


def test_ring_status_retry_trace_wait_stop_and_unknown_trigger(monkeypatch, capsys):
    dev = _device()
    dev.pkt.get_status.side_effect = [{}, {"producer_index": 4, "oldest_index": 2}]
    monkeypatch.setattr(device_module.time, "sleep", lambda _: None)
    assert dev._get_ring_status(retries=2) == {"producer_index": 4, "oldest_index": 2}
    assert dev._ring_seeded is True

    dev.pkt.get_status.side_effect = [{}, {}, {}, {}, {}]
    assert dev._get_ring_status() == {}
    assert dev._trigger_register_values("unknown") == (0, 0)

    monkeypatch.setenv("OLS_RING_TRACE", "1")
    dev._ring_trace("hello")
    assert "[RINGTRACE] hello" in capsys.readouterr().out

    dev.pkt.get_status.side_effect = [{"capture_status": 0}]
    stop = SimpleNamespace(is_set=lambda: True)
    assert dev._wait_capture_done(1, stop_evt=stop) == {"capture_status": 0}


def test_uncompressed_block_fallback_empty_restore_and_scalar_batch():
    dev = _device()
    assert dev._read_blocks_uncompressed([]) == []

    dev.pkt.read_register.return_value = REG_FLAGS_COMPRESS_MASK | 3
    dev.pkt.read_capture_blocks.return_value = (b"not-a-list",)
    dev.pkt.read_capture_block.side_effect = [b"a", b"b"]
    assert dev._read_blocks_uncompressed([0, 1024]) == [b"a", b"b"]
    assert dev.pkt.write_register.call_args_list[0] == call(REG_FLAGS, 3)
    assert dev.pkt.write_register.call_args_list[-1] == call(
        REG_FLAGS, REG_FLAGS_COMPRESS_MASK | 3
    )


def test_mixed_compressed_range_handles_alignment_missing_and_short_blocks(monkeypatch):
    dev = _device()
    frame_words = device_module.analog_wire_stride(MODE_MIXED) // 2
    assert dev._read_capture_range_mixed_compressed(1, 1) == b""

    dev.pkt.read_capture_blocks.return_value = [b""]
    assert dev._read_capture_range_mixed_compressed(0, frame_words) == b""

    dev.pkt.read_capture_blocks.return_value = [b"encoded"]
    monkeypatch.setattr(device_module, "decompress_mixed_stream", lambda block: b"x")
    assert dev._read_capture_range_mixed_compressed(0, frame_words) == b""

    del dev.pkt.read_capture_blocks
    dev.pkt.read_capture_block.return_value = b""
    assert dev._read_capture_range_mixed_compressed(0, frame_words) == b""


def test_capture_helpers_cover_packed_metadata_fast_clock_and_ack_validation():
    dev = _device()
    samples = b"\x01\x00\x02\x00"
    dev.raw_flags = device_module.MODE_PACKED_MSO
    assert dev._trim_packed_capture(samples, None) == samples
    assert dev._trim_packed_capture(samples, {"producer_index": 0}) == samples
    assert dev._capture_read_words(5, None) == 5

    dev.sample_clk = 200_000_000
    assert dev._repair_boundary_glitches(samples) == samples
    dev.sample_clk = 100_000_000
    assert dev._repair_boundary_glitches(b"xx") == b"xx"
    with pytest.raises(ValueError, match="capture_seq"):
        dev.ack_capture_done(None)
    assert dev._stream_readback(0, 0) == b""


def test_optional_generator_routes_clear_and_partially_update_state():
    dev = _device()
    dev._pins()
    dev._aux_pins()
    dev._set_gen_capture_channels()
    dev._set_gen_capture_aux()
    dev._pins(tx_pin=9)
    dev._pins(scl_pin=8)
    dev._set_gen_capture_channels(tx_channel=17)
    dev._set_gen_capture_channels(scl_channel=18)
    assert dev.gen_pins == {"tx": 9, "scl": 8}


def test_rs485_generator_programs_differential_pair(monkeypatch):
    dev = _device()
    dev.start_gen = MagicMock()
    dev._gen_load_uart = MagicMock(return_value=b"abc")
    monkeypatch.setattr(device_module.time, "sleep", lambda _: None)
    dev.send_rs485(b"abc", baud=19_200, b_pin=5, a_pin=6, repeat=True)
    assert dev._gen_data == b"abc"
    dev._gen_load_uart.assert_called_once_with(b"abc", 19_200)
    dev.start_gen.assert_called_once()


def _prepare_generated_capture(dev):
    dev._ensure_open = MagicMock()
    dev.reset = MagicMock()
    dev._wait_gen_idle = MagicMock(return_value=True)
    dev.set_debug_ch0 = MagicMock()
    dev._write_capture_config = MagicMock()
    dev._repair_boundary_glitches = MagicMock(side_effect=lambda data, start: data)
    dev._stream_readback = MagicMock(return_value=b"\x01\x00" * 4)
    dev.ack_capture_done = MagicMock()
    dev.pkt.transaction.side_effect = None
    dev.pkt.transaction.return_value = (ST_OK, 0, b"")
    dev.pkt.get_status.side_effect = [
        {"capture_seq": 4},
        {"capture_status": ST_CAPTURE_DONE, "capture_seq": 5},
    ]


@pytest.mark.parametrize("proto", ["RS485", "SPI", "SWD"])
def test_generated_capture_protocol_routes_complete(proto, monkeypatch):
    dev = _device()
    _prepare_generated_capture(dev)
    monkeypatch.setattr(device_module.time, "sleep", lambda _: None)
    kwargs = {}
    if proto in {"RS485", "SPI"}:
        dev._gen_data = b"payload"
    if proto == "SWD":
        dev._gen_load_swd = MagicMock(return_value=True)
        kwargs["swd_ops"] = [("r", 0, 0)]
    result = dev.capture_with_gen(
        rate_hz=1_000_000,
        nsamples=4,
        timeout=0.01,
        proto=proto,
        reset_board=False,
        **kwargs,
    )
    assert result == b"\x01\x00" * 4


def test_generated_capture_gen_first_rejects_start_arm_and_stop(monkeypatch):
    dev = _device()
    _prepare_generated_capture(dev)
    dev._gen_data = b"payload"
    monkeypatch.setattr(device_module.time, "sleep", lambda _: None)

    dev.pkt.transaction.side_effect = [(ST_OK, 0, b""), None]
    assert dev.capture_with_gen(
        nsamples=2, timeout=0.01, gen_first=True, reset_board=False
    ) == b""

    _prepare_generated_capture(dev)
    dev.pkt.get_status.side_effect = [{"capture_seq": 1}]
    dev.pkt.arm_capture.return_value = -1
    assert dev.capture_with_gen(
        nsamples=2, timeout=0.01, gen_first=True, reset_board=False
    ) == b""

    _prepare_generated_capture(dev)
    dev.pkt.get_status.side_effect = [{"capture_seq": 1}]
    dev.pkt.arm_capture.return_value = ST_OK
    stop = SimpleNamespace(is_set=lambda: True)
    assert dev.capture_with_gen(
        nsamples=2,
        timeout=0.01,
        gen_first=True,
        reset_board=False,
        stop_evt=stop,
    ) == b""


def test_repeating_uart_capture_validates_retries_kicks_and_aborts(monkeypatch):
    dev = _device()
    dev._ensure_open = MagicMock()
    dev._wait_gen_idle = MagicMock(return_value=True)
    dev.continuous_ring_capture = MagicMock(return_value=iter([(b"data", 2, 4)]))
    dev._gen_kick = MagicMock(side_effect=OSError("missed kick"))
    monkeypatch.setattr(device_module.time, "sleep", lambda _: None)

    with pytest.raises(ValueError, match="must not be empty"):
        list(dev.continuous_ring_capture_with_repeating_uart(1, 1, 1, object(), b""))

    dev.pkt.transaction.side_effect = [
        (ST_OK, 0, b""),
        None,
        (ST_OK, 0, b""),
        (ST_OK, 0, b""),
        (ST_OK, 0, b""),
    ]
    stop = SimpleNamespace(is_set=lambda: False)
    assert list(
        dev.continuous_ring_capture_with_repeating_uart(
            1_000_000, 2, 4, stop, b"A"
        )
    ) == [(b"data", 2, 4)]
    dev._gen_kick.assert_called_once()

    dev.pkt.transaction.side_effect = [(ST_OK, 0, b""), None, (ST_OK, 0, b""), None]
    with pytest.raises(RuntimeError, match="could not start"):
        list(
            dev.continuous_ring_capture_with_repeating_uart(
                1_000_000, 2, 4, stop, b"A"
            )
        )


def test_i2c_capture_sets_auto_increment_only_for_multi_byte_read():
    dev = _device()
    dev.capture_with_gen = MagicMock(return_value=b"capture")
    assert dev.i2c_capture_with_gen(read_len=2, reg_addr=0x0F) == b"capture"
    assert dev.capture_with_gen.call_args.kwargs["i2c_frame"][1] == 0x8F
    dev.i2c_capture_with_gen(read_len=1, reg_addr=0x0F)
    assert dev.capture_with_gen.call_args.kwargs["i2c_frame"][1] == 0x0F


def test_find_spi_device_covers_empty_metadata_duplicate_index_and_import_failure(monkeypatch):
    fake = types.ModuleType("ftd2xx")
    fake.createDeviceInfoList = lambda: 0
    monkeypatch.setitem(sys.modules, "ftd2xx", fake)
    assert device_module.find_spi_device() is False

    fake.createDeviceInfoList = lambda: 2
    fake.listDevices = MagicMock(side_effect=[[b"S", b"plain"], [b"S", b"plain"]])
    assert device_module.find_spi_device() is True

    fake.listDevices = MagicMock(side_effect=OSError("listing failed"))
    handle = MagicMock()
    handle.getDeviceInfo.return_value = {"description": b"plain"}
    fake.open = MagicMock(return_value=handle)
    assert device_module.find_spi_device() is True

    fake.createDeviceInfoList = MagicMock(side_effect=OSError("driver missing"))
    assert device_module.find_spi_device() is False


def test_remaining_configuration_branches_are_observable(monkeypatch):
    dev = _device()
    dev.readback_compression_mode = "raw"
    dev.pkt.read_register.return_value = 0
    dev.pkt.write_register.return_value = False
    assert dev.set_readback_compression("raw") is False
    assert dev._readback_hardware_is_raw is True

    dev.spi.flush.reset_mock()
    dev.pkt.write_register.return_value = True
    assert dev.set_readback_compression("raw") is True
    dev.spi.flush.assert_not_called()

    dev._compressed_block_reads_supported["delta_rle"] = False
    dev.pkt.read_register.return_value = 7
    dev.pkt.write_register.reset_mock()
    assert dev.set_readback_compression("delta") is True
    dev.pkt.write_register.assert_not_called()

    dev.get_metadata = MagicMock(
        return_value=b"\x00" * 5 + (200_000).to_bytes(4, "little")
    )
    dev._detect_sample_clk()
    assert dev.sample_clk == 200_000_000

    dev.get_metadata.return_value = b"\x00" * 9
    monkeypatch.setattr(device_module.time, "sleep", lambda _: None)
    dev._detect_sample_clk()

    dev.pkt.reset_mock()
    dev._write_capture_config(div=1, samples=2, delay_count=2, fast_mode=None)
    assert not any(c.args[0] == device_module.REG_FAST_MODE for c in dev.pkt.write_register.call_args_list)


def test_generator_fifo_exhaustion_empty_i2c_symbols_and_direct_start(monkeypatch):
    dev = _device()
    dev.pkt.read_register.return_value = (2 << 8) | 0xAA
    assert dev.gen_rx_read(max_bytes=1) == b"\xaa"

    monkeypatch.setattr(bit_bang, "i2c_read_symbols", lambda *args: [])
    assert dev._gen_load_i2c_read(b"x", 100_000, 1, 3) == b"x"

    dev._pins = MagicMock()
    dev.gen_rx_read = MagicMock(return_value=b"\x01")
    dev._wait_gen_idle = MagicMock(return_value=True)
    dev.pkt.transaction.return_value = (ST_OK, 0, b"")
    assert dev._gen_run_and_rx([1], 2)[:1] == [1]


def test_accelerometer_capture_timeout_still_reads_buffer(monkeypatch):
    dev = _device()
    dev._ensure_open = MagicMock()
    dev._pins = MagicMock()
    dev._write_capture_config = MagicMock()
    dev._stream_readback = MagicMock(return_value=b"ok")
    dev.pkt.transaction.side_effect = [(ST_OK, 0, b""), (ST_CAPTURE_ARMED, 0, b"")]
    dev.pkt.get_status.return_value = {"capture_status": 0}
    times = iter((1.0, 1.1, 2.0))
    monkeypatch.setattr(device_module.time, "time", lambda: next(times))
    monkeypatch.setattr(device_module.time, "sleep", lambda _: None)
    assert dev.accel_capture_dialogue([1], 2, rate_hz=1, nsamples=1, timeout=0.1) == b"ok"


def test_protocol_decoder_import_failure_returns_none(monkeypatch):
    dev = _device()
    dev.trigger_decode()
    monkeypatch.setitem(sys.modules, "app.gui_decoders", None)
    assert dev.protocol_trigger_match_pos(b"data", 1_000_000) is None


def test_capture_range_forces_raw_when_hardware_flags_remain_compressed():
    dev = _device()
    dev.readback_compression_mode = "delta_rle"
    dev.compress_readback_enabled = True
    dev._compressed_block_reads_supported["delta_rle"] = False
    dev._readback_hardware_is_raw = False
    dev._read_blocks_uncompressed = MagicMock(return_value=[b"\x01\x00" * 512])
    assert dev.read_capture_range(0, 2) == b"\x01\x00" * 2
    dev._read_blocks_uncompressed.assert_called_once_with([0])


@pytest.mark.parametrize("retry_mode", ["raises", "decode_raises", "short"])
def test_capture_range_compressed_retry_failures_fall_back_raw(retry_mode, monkeypatch):
    dev = _device()
    dev.readback_compression_mode = "delta_rle"
    dev.compress_readback_enabled = True
    dev.pkt.read_capture_blocks.return_value = [b"bad"]
    if retry_mode == "raises":
        dev.pkt.read_capture_block.side_effect = OSError("retry failed")
    else:
        dev.pkt.read_capture_block.return_value = b"retry"

    def decode(block, codec):
        if block == b"bad":
            return b""
        if retry_mode == "decode_raises":
            raise ValueError("invalid compressed block")
        return b"short"

    monkeypatch.setattr(device_module, "decompress_block_readback_stream", decode)
    dev._read_blocks_uncompressed = MagicMock(return_value=[b"\x02\x00" * 512])
    assert dev.read_capture_range(0, 2) == b"\x02\x00" * 2


def test_capture_range_handles_missing_retry_api_partial_probe_and_empty_blocks(monkeypatch):
    class Packet:
        def read_capture_blocks(self, addrs, compressed=False):
            return [b"bad", b"good"]

    dev = OLSDeviceSPI()
    dev.pkt = Packet()
    dev.readback_compression_mode = "delta_rle"
    dev.compress_readback_enabled = True
    dev._read_blocks_uncompressed = MagicMock(
        return_value=[b"", b"\x03\x00" * 512]
    )
    monkeypatch.setattr(
        device_module,
        "decompress_block_readback_stream",
        lambda block, codec: b"\x04\x00" * 512 if block == b"good" else b"",
    )
    data = dev.read_capture_range(0, 520)
    assert data == b""
    assert dev._compressed_block_reads_supported["delta_rle"] is True

    dev = _device()
    dev.pkt.read_capture_blocks.return_value = [b""]
    assert dev.read_capture_range(0, 2) == b""
    dev.pkt.read_capture_blocks.return_value = [b"x"]
    assert dev.read_capture_range(0, 2) == b""


def test_uncompressed_blocks_without_batch_api_use_individual_reads():
    class Packet:
        def read_register(self, addr):
            return 0

        def read_capture_block(self, addr, compressed=False):
            return bytes((addr // 1024,))

    dev = OLSDeviceSPI()
    dev.pkt = Packet()
    assert dev._read_blocks_uncompressed([0, 1024]) == [b"\x00", b"\x01"]


def test_boundary_repair_byteswaps_on_big_endian_array(monkeypatch):
    real_array = device_module.array

    class BigEndianArray:
        def __init__(self, typecode, values=None):
            self.inner = real_array(typecode, values or [])

        def frombytes(self, data):
            self.inner.frombytes(data)

        def tobytes(self):
            if len(self.inner) == 1 and self.inner[0] == 1:
                return b"\x00\x01"
            return self.inner.tobytes()

        def byteswap(self):
            self.inner.byteswap()

        def __len__(self):
            return len(self.inner)

        def __getitem__(self, item):
            return self.inner[item]

        def __setitem__(self, item, value):
            self.inner[item] = value

    monkeypatch.setattr(device_module, "array", BigEndianArray)
    dev = _device()
    dev.sample_clk = 100_000_000
    assert isinstance(dev._repair_boundary_glitches(b"\x00\x01" * 3), bytes)


class _SequenceStop:
    def __init__(self, values):
        self.values = iter(values)

    def is_set(self):
        return next(self.values, True)

    def wait(self, timeout):
        return self.is_set()


def _prepare_ring(dev):
    dev._ensure_open = MagicMock()
    dev._write_capture_config = MagicMock()
    dev.set_debug_ch0 = MagicMock()
    dev.pkt.arm_capture.return_value = ST_OK


def test_continuous_ring_arm_retry_failure_and_recovery(monkeypatch):
    monkeypatch.setattr(device_module.time, "sleep", lambda _: None)
    dev = _device()
    _prepare_ring(dev)
    dev.pkt.arm_capture.side_effect = [-1, -1]
    dev.pkt.transaction.side_effect = OSError("abort failed")
    assert list(dev.continuous_ring_capture(1_000_000, 1, 2, _SequenceStop([False]))) == []

    dev = _device()
    _prepare_ring(dev)
    dev.pkt.arm_capture.side_effect = [-1, ST_OK]
    assert list(dev.continuous_ring_capture(1_000_000, 1, 2, _SequenceStop([True]))) == []


def test_continuous_ring_callbacks_buffer_trim_pending_and_metadata_errors(monkeypatch):
    monkeypatch.setattr(device_module, "analog_wire_stride", lambda mode: 2)
    monkeypatch.setattr(device_module, "analog_frame_stride", lambda mode: 2)
    monkeypatch.setattr(device_module, "wire_to_payload", lambda data, mode: data)
    dev = _device()
    _prepare_ring(dev)
    dev.analog_mode = MODE_MIXED
    dev._get_ring_status = MagicMock(return_value={"producer_index": 20, "oldest_index": 0})
    dev.read_capture_range = MagicMock(return_value=b"\x01\x00" * 8)
    progress = MagicMock()
    full = bytearray()
    gen = dev.continuous_ring_capture(
        1_000_000,
        1,
        2,
        _SequenceStop([False] * 5),
        progress_cb=progress,
        full_out=full,
    )
    first = next(gen)
    second = next(gen)
    third = next(gen)
    gen.close()
    assert first[0] == b"\x01\x00"
    assert len(third[0]) == 4
    assert len(full) == 6
    assert progress.call_count == 3

    dev = _device()
    _prepare_ring(dev)
    dev._get_ring_status = MagicMock(return_value={})
    with pytest.raises(RuntimeError, match="metadata"):
        next(dev.continuous_ring_capture(1_000_000, 1, 2, _SequenceStop([False])))


def test_continuous_ring_resyncs_oldest_and_retries_empty_read(monkeypatch):
    monkeypatch.setattr(device_module.time, "sleep", lambda _: None)
    dev = _device()
    _prepare_ring(dev)
    dev._get_ring_status = MagicMock(side_effect=[
        {"producer_index": 2, "oldest_index": 0},
        {"producer_index": 12, "oldest_index": 10},
    ])
    dev.read_capture_range = MagicMock(side_effect=[b"\x01\x00", b"", b"\x02\x00"])
    gen = dev.continuous_ring_capture(
        1_000_000, 1, 2, _SequenceStop([False, False, True])
    )
    assert next(gen)[0] == b"\x01\x00"
    assert list(gen) == []


def test_stream_ring_retry_metadata_wait_rle_and_cleanup(monkeypatch):
    monkeypatch.setattr(device_module.time, "sleep", lambda _: None)
    dev = _device()
    _prepare_ring(dev)
    dev.pkt.arm_capture.side_effect = [-1, -1]
    dev.pkt.transaction.side_effect = OSError("abort failed")
    assert list(dev.stream_ring_capture(1_000_000, 1, _SequenceStop([False]))) == []

    dev = _device()
    _prepare_ring(dev)
    dev.readback_compression_mode = "rle"
    dev.compress_readback_enabled = True
    dev._get_ring_status = MagicMock(return_value={})
    with pytest.raises(RuntimeError, match="metadata"):
        next(dev.stream_ring_capture(1_000_000, 1, _SequenceStop([False])))

    dev = _device()
    _prepare_ring(dev)
    dev.readback_compression_mode = "rle"
    dev.compress_readback_enabled = True
    dev._get_ring_status = MagicMock(return_value={
        "producer_index": 10,
        "oldest_index": 2,
        "overrun_count": 1,
    })
    dev.pkt.start_rle_stream_read.side_effect = [(10, 3, b"x"), (10, 3, b"")]
    assert list(dev.stream_ring_capture(1_000_000, 1, _SequenceStop([False, False]))) == []

    dev = _device()
    _prepare_ring(dev)
    dev.readback_compression_mode = "rle"
    dev.compress_readback_enabled = True
    dev._get_ring_status = MagicMock(return_value={"producer_index": 5, "oldest_index": 0})
    dev.pkt.start_rle_stream_read.return_value = (5, 0, b"\x34\x12")
    progress = MagicMock()
    full = bytearray()
    results = list(
        dev.stream_ring_capture(
            1_000_000,
            1,
            _SequenceStop([False, True]),
            progress_cb=progress,
            full_out=full,
        )
    )
    assert results == [(b"\x34\x12", 1, 1, 0)]
    assert full == b"\x34\x12" and progress.called


def test_stream_ring_wait_and_empty_block_read_paths(monkeypatch):
    monkeypatch.setattr(device_module.time, "sleep", lambda _: None)
    dev = _device()
    _prepare_ring(dev)
    dev._get_ring_status = MagicMock(return_value={"producer_index": 0, "oldest_index": 0})
    assert list(dev.stream_ring_capture(1_000_000, 1, _SequenceStop([False, True]))) == []

    dev = _device()
    _prepare_ring(dev)
    dev._get_ring_status = MagicMock(return_value={"producer_index": 2, "oldest_index": 0})
    dev.read_capture_range = MagicMock(return_value=b"")
    dev.pkt.transaction.side_effect = OSError("cleanup failed")
    assert list(dev.stream_ring_capture(1_000_000, 1, _SequenceStop([False]))) == []


def test_last_small_metadata_open_and_trace_branches(monkeypatch):
    dev = OLSDeviceSPI()
    dev.pkt = MagicMock()
    dev.pkt.read_register.return_value = 0
    dev.pkt.write_register.return_value = True
    assert dev.set_readback_compression("delta") is True

    class SPI:
        def __init__(self, speed_hz):
            self.dev = MagicMock()
            self.dev.getLatencyTimer.return_value = 1

        def open(self):
            pass

    monkeypatch.setattr(device_module, "OLS_SPI", SPI)
    dev = OLSDeviceSPI()
    dev._detect_sample_clk = MagicMock()
    dev._detect_gen_div_width = MagicMock()
    dev.open()
    dev.spi.dev.setLatencyTimer.assert_not_called()

    dev = _device()
    dev.pkt.transaction.side_effect = [(ST_OK, 0, b""), (ST_CAPTURE_ARMED, 0, b"")]
    dev.pkt.get_status.return_value = {"capture_status": 0}
    dev._ensure_open = MagicMock()
    dev._pins = MagicMock()
    dev._write_capture_config = MagicMock()
    dev._stream_readback = MagicMock(return_value=b"x")
    times = iter((1.0, 1.05, 1.2))
    monkeypatch.setattr(device_module.time, "time", lambda: next(times))
    monkeypatch.setattr(device_module.time, "sleep", lambda _: None)
    assert dev.accel_capture_dialogue([1], 1, rate_hz=1, nsamples=1, timeout=0.1) == b"x"

    dev = _device()
    dev.configure_pattern_trigger({"channels": [0], "bit_order": "msb_first"})


def test_stream_ring_successful_retry_trace_and_no_callback(monkeypatch):
    monkeypatch.setattr(device_module.time, "sleep", lambda _: None)
    monkeypatch.setenv("OLS_RING_TRACE", "1")
    dev = _device()
    _prepare_ring(dev)
    dev.pkt.arm_capture.side_effect = [-1, ST_OK]
    dev._get_ring_status = MagicMock(return_value={"producer_index": 4, "oldest_index": 0})
    dev.readback_compression_mode = "rle"
    dev.compress_readback_enabled = True
    dev.pkt.start_rle_stream_read.return_value = (4, 0, b"\x01\x00")
    assert list(dev.stream_ring_capture(1_000_000, 1, _SequenceStop([False, True]))) == [
        (b"\x01\x00", 1, 1, 0)
    ]


def test_generated_capture_abort_pending_no_generator_invalid_command_and_trace(monkeypatch, tmp_path):
    dev = _device()
    _prepare_generated_capture(dev)
    dev.pkt.transaction.side_effect = OSError("abort failed")
    dev._pending_debug_enable = True
    monkeypatch.setattr(device_module.time, "sleep", lambda _: None)
    assert dev.capture_with_gen(nsamples=2, timeout=0.01, reset_board=False) == b""
    assert dev.debug_ch0_enabled is True and dev._pending_debug_enable is None

    dev = _device()
    _prepare_generated_capture(dev)
    dev._gen_data = b"x"
    dev.pkt.transaction.side_effect = [(ST_OK, 0, b""), None]
    assert dev.capture_with_gen(nsamples=2, timeout=0.01, reset_board=False) == b""

    trace = tmp_path / "gen.trace"
    monkeypatch.setenv("OLS_GEN_TRACE", str(trace))
    dev = _device()
    _prepare_generated_capture(dev)
    dev._gen_data = b"x"
    dev.pkt.get_status.side_effect = [
        {"capture_seq": 1},
        {"capture_status": 0, "capture_seq": 2},
        {"capture_status": 0, "capture_seq": 2},
        {"capture_status": ST_CAPTURE_DONE, "capture_seq": 2},
    ]
    assert dev.capture_with_gen(nsamples=2, timeout=0.1, reset_board=False)
    assert "cmd resp" in trace.read_text() and "status transitions" in trace.read_text()


def test_capture_pending_arm_stop_and_ack_paths(monkeypatch):
    monkeypatch.setattr(device_module.time, "sleep", lambda _: None)
    dev = _device()
    dev._ensure_open = MagicMock()
    dev.reset = MagicMock()
    dev.set_debug_ch0 = MagicMock()
    dev._kick_live_gen = MagicMock()
    dev._write_capture_config = MagicMock()
    dev._pending_debug_enable = True
    dev.pkt.get_status.return_value = {"capture_seq": 1}
    dev.pkt.arm_capture.return_value = -1
    assert dev.capture(nsamples=2, timeout=0.01) == b""
    assert dev.debug_ch0_enabled is True

    dev.pkt.arm_capture.return_value = ST_OK
    stop = SimpleNamespace(is_set=lambda: True)
    assert dev.capture(nsamples=2, timeout=0.01, stop_evt=stop) == b""

    stop_values = iter((False, True))
    stop = SimpleNamespace(is_set=lambda: next(stop_values, True))
    dev._wait_capture_done = MagicMock(return_value={"capture_status": ST_CAPTURE_DONE})
    assert dev.capture(nsamples=2, timeout=0.01, trigger="rising", stop_evt=stop) == b""

    dev._wait_capture_done = MagicMock(
        return_value={"capture_status": ST_CAPTURE_DONE, "capture_seq": 2}
    )
    dev._stream_readback = MagicMock(return_value=b"\x00\x00\x01\x00")
    dev._repair_boundary_glitches = MagicMock(side_effect=lambda data, start: data)
    dev.ack_capture_done = MagicMock()
    dev.pkt.get_status.return_value = {"capture_seq": 1}
    assert dev.capture(nsamples=2, timeout=0.01, trigger="rising") == b"\x01\x00"
    dev.ack_capture_done.assert_called_once_with(2)


def test_i2c_rolling_stop_empty_and_callbacks(monkeypatch):
    monkeypatch.setattr(device_module.time, "sleep", lambda _: None)
    dev = _device()
    dev._ensure_open = MagicMock()
    dev._write_capture_config = MagicMock()
    dev.pkt.get_status.return_value = {"capture_status": 0}
    assert list(
        dev.i2c_rolling_capture(
            1_000_000, 1, 2, _SequenceStop([False, True])
        )
    ) == []

    dev = _device()
    dev._ensure_open = MagicMock()
    dev._write_capture_config = MagicMock()
    dev.pkt.get_status.return_value = {"capture_status": ST_CAPTURE_DONE}
    dev.read_capture_range = MagicMock(side_effect=[b"", b"\x01\x00" * 3])
    progress = MagicMock()
    full = bytearray()
    results = list(
        dev.i2c_rolling_capture(
            1_000_000,
            1,
            2,
            _SequenceStop([False, False, True]),
            progress_cb=progress,
            full_out=full,
        )
    )
    assert len(results) == 1 and len(results[0][0]) == 2
    assert progress.called and full


def test_rolling_continuous_callbacks_narrow_restore_and_analog_trim():
    dev = _device()
    dev._ensure_open = MagicMock()
    dev.continuous_ring_capture = MagicMock(
        return_value=iter([(b"\x01\x00" * 3, 3, 1)])
    )
    progress = MagicMock()
    result = list(dev.rolling_capture(1, 1, 2, _SequenceStop([False]), progress_cb=progress))
    assert len(result[0][0]) == 4 and progress.called

    dev.raw_flags = device_module.MODE_NARROW_DIGITAL
    dev.readback_compression_mode = "delta_rle"
    dev.compress_readback_enabled = True
    dev.set_readback_compression = MagicMock(return_value=True)
    dev.continuous_ring_capture.return_value = iter([])
    assert list(dev.rolling_capture(1, 1, 2, _SequenceStop([False]))) == []
    assert dev.set_readback_compression.call_args_list[-1] == call(
        "delta_rle", force_hardware=True
    )

    dev = _device()
    dev._ensure_open = MagicMock()
    dev.analog_mode = MODE_MIXED
    dev.continuous_ring_capture = MagicMock(
        return_value=iter([(b"abcdef", 1, 1)])
    )
    progress = MagicMock()
    result = list(
        dev.rolling_capture(
            1, 1, 1, _SequenceStop([False]), payload_stride=2, progress_cb=progress
        )
    )
    assert result[0][0] == b"ef" and progress.called


def test_find_spi_device_accepts_descriptive_channel_b(monkeypatch):
    fake = types.ModuleType("ftd2xx")
    fake.createDeviceInfoList = lambda: 1
    fake.listDevices = lambda index: [b"SERIAL", b"USB SPI B"]
    monkeypatch.setitem(sys.modules, "ftd2xx", fake)
    assert device_module.find_spi_device() is True


def test_sample_clock_short_retry_and_continuous_short_pending(monkeypatch):
    dev = _device()
    dev.get_metadata = MagicMock(return_value=b"short")
    monkeypatch.setattr(device_module.time, "sleep", lambda _: None)
    dev._detect_sample_clk()

    dev = _device()
    _prepare_ring(dev)
    dev._get_ring_status = MagicMock(return_value={"producer_index": 4, "oldest_index": 0})
    dev.read_capture_range = MagicMock(return_value=b"\x01\x00")
    assert list(
        dev.continuous_ring_capture(
            1_000_000, 2, 4, _SequenceStop([False, True])
        )
    ) == []


def test_repeating_uart_direct_start_skips_retry(monkeypatch):
    dev = _device()
    dev._ensure_open = MagicMock()
    dev._wait_gen_idle = MagicMock()
    dev.continuous_ring_capture = MagicMock(return_value=iter([]))
    dev.pkt.transaction.return_value = (ST_OK, 0, b"")
    monkeypatch.setattr(device_module.time, "sleep", lambda _: None)
    assert list(
        dev.continuous_ring_capture_with_repeating_uart(
            1, 1, 1, _SequenceStop([True]), b"A"
        )
    ) == []
    assert dev.pkt.load_gen_data.call_count == 1


def test_generated_capture_gen_first_success_and_finish_stop(monkeypatch):
    dev = _device()
    _prepare_generated_capture(dev)
    dev._gen_data = b"x"
    dev.pkt.arm_capture.return_value = ST_OK
    dev.pkt.get_status.side_effect = [
        {"capture_seq": 1},
        {"capture_status": ST_CAPTURE_DONE, "capture_seq": 2},
    ]
    monkeypatch.setattr(device_module.time, "sleep", lambda _: None)
    assert dev.capture_with_gen(
        nsamples=2, timeout=0.01, gen_first=True, reset_board=False
    )

    dev = _device()
    _prepare_generated_capture(dev)
    dev._gen_data = b"x"
    dev.pkt.arm_capture.return_value = ST_OK
    dev.pkt.get_status.side_effect = [
        {"capture_seq": 1},
        {"capture_status": 0, "capture_seq": 2},
    ]
    times = iter((0.0, 0.0, 0.1, 1.0, 2.0, 2.0, 2.1, 2.2))
    monkeypatch.setattr(device_module.time, "time", lambda: next(times))
    stop = _SequenceStop([False, True])
    assert dev.capture_with_gen(
        nsamples=2,
        timeout=1,
        gen_first=True,
        reset_board=False,
        stop_evt=stop,
    ) == b""


def test_generated_atomic_stop_during_quiet_window(monkeypatch):
    dev = _device()
    _prepare_generated_capture(dev)
    dev._gen_data = b"x"
    stop = SimpleNamespace(is_set=lambda: True)
    assert dev.capture_with_gen(
        nsamples=2, timeout=0.01, reset_board=False, stop_evt=stop
    ) == b""


def test_capture_post_wait_stop_is_immediate(monkeypatch):
    dev = _device()
    dev._ensure_open = MagicMock()
    dev.reset = MagicMock()
    dev.set_debug_ch0 = MagicMock()
    dev._kick_live_gen = MagicMock()
    dev._write_capture_config = MagicMock()
    dev.pkt.get_status.return_value = {"capture_seq": 1}
    dev.pkt.arm_capture.return_value = ST_OK
    dev._wait_capture_done = MagicMock(return_value={"capture_status": ST_CAPTURE_DONE})
    stop = SimpleNamespace(is_set=lambda: True)
    monkeypatch.setattr(device_module.time, "sleep", lambda _: None)
    assert dev.capture(
        nsamples=2, timeout=0.01, trigger="rising", stop_evt=stop
    ) == b""


def test_i2c_rolling_busy_then_done_trims_and_calls_outputs(monkeypatch):
    dev = _device()
    dev._ensure_open = MagicMock()
    dev._write_capture_config = MagicMock()
    dev.pkt.get_status.side_effect = [
        {"capture_status": 0},
        {"capture_status": ST_CAPTURE_DONE},
        {"capture_status": ST_CAPTURE_DONE},
        {"capture_status": ST_CAPTURE_DONE},
    ]
    dev.read_capture_range = MagicMock(return_value=b"\x01\x00")
    progress = MagicMock()
    full = bytearray()
    monkeypatch.setattr(device_module.time, "sleep", lambda _: None)
    results = list(
        dev.i2c_rolling_capture(
            1_000_000,
            1,
            2,
            _SequenceStop([False, False, False, False, True]),
            progress_cb=progress,
            full_out=full,
        )
    )
    assert len(results) == 3
    assert len(results[-1][0]) == 4
    assert progress.call_count == 3 and len(full) == 6


def _compressed_rolling_device():
    dev = _device()
    dev._ensure_open = MagicMock()
    dev.readback_compression_mode = "delta_rle"
    dev.compress_readback_enabled = True
    dev.set_readback_compression = MagicMock(return_value=True)
    dev.continuous_ring_capture = MagicMock(return_value=iter([(b"raw", 1, 1)]))
    return dev


@pytest.mark.parametrize("failure", ["false", "raises", "known"])
def test_compressed_rolling_configuration_failures_use_raw_fallback(failure):
    dev = _compressed_rolling_device()
    if failure == "false":
        dev.set_readback_compression.side_effect = [False, True, True]
    elif failure == "raises":
        dev.set_readback_compression.side_effect = [OSError("unsupported"), True, True]
    else:
        dev._rle_stream_supported = False
    result = list(dev.rolling_capture(1, 1, 2, _SequenceStop([False])))
    assert result and result[0][0] == b"raw"
    assert dev._rle_stream_supported is False


def test_compressed_rolling_stream_iteration_trim_callback_and_failure_fallback():
    dev = _compressed_rolling_device()
    dev.stream_ring_capture = MagicMock(
        return_value=iter([(b"\x01\x00" * 3, 3, 1, 0)])
    )
    progress = MagicMock()
    result = list(
        dev.rolling_capture(1, 1, 2, _SequenceStop([False]), progress_cb=progress)
    )
    assert result[0][0] == b"\x01\x00" * 2
    assert progress.called and dev._rle_stream_supported is True

    class FailingIterator:
        def __iter__(self):
            return self

        def __next__(self):
            raise OSError("stream failed")

    dev = _compressed_rolling_device()
    dev.stream_ring_capture = MagicMock(return_value=FailingIterator())
    assert list(dev.rolling_capture(1, 1, 2, _SequenceStop([False])))[0][0] == b"raw"


def test_compressed_rolling_stop_and_failed_raw_fallback():
    dev = _compressed_rolling_device()
    dev.stream_ring_capture = MagicMock(side_effect=OSError("cannot start"))
    assert list(dev.rolling_capture(1, 1, 2, _SequenceStop([True]))) == []

    dev = _compressed_rolling_device()
    dev.stream_ring_capture = MagicMock(side_effect=OSError("cannot start"))
    dev.set_readback_compression.side_effect = [True, False, True]
    with pytest.raises(RuntimeError, match="raw live fallback"):
        list(dev.rolling_capture(1, 1, 2, _SequenceStop([False])))


def test_noncontinuous_rolling_pending_status_empty_and_output_paths(monkeypatch):
    dev = _device()
    dev._ensure_open = MagicMock()
    dev._write_capture_config = MagicMock()
    dev.set_debug_ch0 = MagicMock()
    dev.set_bitbang_pwm = MagicMock()
    dev._pending_debug_enable = True
    dev._pending_debug_freq = 10
    dev._pending_debug_duty = 25
    dev.pkt.get_status.side_effect = [
        {"capture_status": 0},
        {"capture_status": ST_CAPTURE_DONE},
        {"capture_status": ST_CAPTURE_DONE},
        {"capture_status": ST_CAPTURE_DONE},
        {"capture_status": ST_CAPTURE_DONE},
    ]
    dev.read_capture_range = MagicMock(side_effect=[b"", b"\x01\x00", b"\x02\x00", b"\x03\x00"])
    progress = MagicMock()
    full = bytearray()
    monkeypatch.setattr(device_module.time, "sleep", lambda _: None)
    results = list(
        dev.rolling_capture(
            1_000_000,
            1,
            2,
            _SequenceStop([False, False, False, False, False, True]),
            progress_cb=progress,
            full_out=full,
            use_continuous=False,
        )
    )
    assert dev.set_bitbang_pwm.called
    assert len(results[-1][0]) == 4
    assert progress.called and len(full) == 6


def test_partial_compression_failure_with_known_support_keeps_codec(monkeypatch):
    dev = _device()
    dev.readback_compression_mode = "delta_rle"
    dev.compress_readback_enabled = True
    dev._compressed_block_reads_supported["delta_rle"] = True
    dev.pkt.read_capture_blocks.return_value = [b"bad", b"good"]
    dev.pkt.read_capture_block.return_value = b""
    monkeypatch.setattr(
        device_module,
        "decompress_block_readback_stream",
        lambda block, codec: b"\x01\x00" * 512 if block == b"good" else b"",
    )
    dev._read_blocks_uncompressed = MagicMock(return_value=[b"\x02\x00" * 512])
    assert len(dev.read_capture_range(0, 520)) == 1040
    assert dev._compressed_block_reads_supported["delta_rle"] is True


def test_generated_capture_timeout_and_finish_stop(monkeypatch):
    dev = _device()
    _prepare_generated_capture(dev)
    dev._gen_data = b"x"
    dev.pkt.get_status.side_effect = [{"capture_seq": 1}]
    assert dev.capture_with_gen(
        nsamples=2, timeout=0, reset_board=False
    )

    dev = _device()
    _prepare_generated_capture(dev)
    dev._gen_data = b"x"
    dev.pkt.get_status.side_effect = [
        {"capture_seq": 1},
        {"capture_status": 0, "capture_seq": 2},
    ]
    times = iter((0.0, 1.0, 2.0, 2.0, 2.1, 2.2))
    monkeypatch.setattr(device_module.time, "time", lambda: next(times))
    stop = SimpleNamespace(is_set=lambda: True)
    assert dev.capture_with_gen(
        nsamples=2, timeout=1, reset_board=False, stop_evt=stop
    ) == b""

    with pytest.raises(ValueError, match="positive"):
        dev.capture_with_gen(rate_hz=0)


def test_i2c_rolling_poll_timeout_proceeds_to_read(monkeypatch):
    dev = _device()
    dev._ensure_open = MagicMock()
    dev._write_capture_config = MagicMock()
    dev.pkt.get_status.return_value = {"capture_status": 0}
    dev.read_capture_range = MagicMock(return_value=b"\x01\x00")
    times = iter((0.0, 0.1, 1.0))
    monkeypatch.setattr(device_module.time, "time", lambda: next(times))
    monkeypatch.setattr(device_module.time, "sleep", lambda _: None)
    results = list(
        dev.i2c_rolling_capture(
            1_000_000, 1, 2, _SequenceStop([False, False, True])
        )
    )
    assert results == [(b"\x01\x00", 1, 2)]


def test_rle_mode_stream_failure_restores_forced_raw_fallback():
    dev = _compressed_rolling_device()
    dev.readback_compression_mode = "rle"
    dev.stream_ring_capture = MagicMock(side_effect=OSError("cannot start"))
    result = list(dev.rolling_capture(1, 1, 2, _SequenceStop([False])))
    assert result[0][0] == b"raw"
    assert dev.set_readback_compression.call_args_list[-1] == call(
        "rle", force_hardware=True
    )


def test_rle_mode_success_needs_no_mode_restoration():
    dev = _compressed_rolling_device()
    dev.readback_compression_mode = "rle"
    dev.stream_ring_capture = MagicMock(
        return_value=iter([(b"\x01\x00", 1, 1, 0)])
    )
    assert list(dev.rolling_capture(1, 1, 2, _SequenceStop([False]))) == [
        (b"\x01\x00", 1, 2)
    ]
    dev.set_readback_compression.assert_not_called()


def test_noncontinuous_rolling_inner_stop_and_poll_timeout(monkeypatch):
    dev = _device()
    dev._ensure_open = MagicMock()
    dev._write_capture_config = MagicMock()
    dev.set_debug_ch0 = MagicMock()
    dev.pkt.get_status.return_value = {"capture_status": 0}
    assert list(
        dev.rolling_capture(
            1_000_000, 1, 2, _SequenceStop([False, True]), use_continuous=False
        )
    ) == []

    dev = _device()
    dev._ensure_open = MagicMock()
    dev._write_capture_config = MagicMock()
    dev.set_debug_ch0 = MagicMock()
    dev.pkt.get_status.return_value = {"capture_status": 0}
    dev.read_capture_range = MagicMock(return_value=b"\x01\x00")
    times = iter((0.0, 1.0))
    monkeypatch.setattr(device_module.time, "time", lambda: next(times))
    monkeypatch.setattr(device_module.time, "sleep", lambda _: None)
    assert list(
        dev.rolling_capture(
            1_000_000, 1, 2, _SequenceStop([False, True]), use_continuous=False
        )
    ) == [(b"\x01\x00", 1, 2)]
