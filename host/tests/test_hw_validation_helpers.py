import os, json, sys, tempfile, threading
import pytest
from unittest.mock import MagicMock, patch, mock_open

from app import hw_validation as hv


def _make_i2c_signal(data_bytes, spb=10):
    """Real I2C write traffic: START, 8 data bits, ACK clock, STOP."""
    scl, sda = [], []
    scl += [1] * spb
    sda += [1] * spb
    scl += [1, 1, 0, 1]
    sda += [1, 1, 1, 0]
    scl += [1] * (spb - 4)
    sda += [0] * (spb - 4)
    for byte in data_bytes:
        for b in range(8):
            bit = (byte >> (7 - b)) & 1
            scl += [0] * spb
            sda += [bit] * spb
            scl += [1] * spb
            sda += [bit] * spb
        scl += [0] * spb
        sda += [0] * spb
        scl += [1] * spb
        sda += [0] * spb
    scl += [0] * spb
    sda += [0] * spb
    scl += [1] * spb
    sda += [0] * (spb // 2) + [1] * (spb - spb // 2)
    return [scl, sda]

class TestLog:
    def test_log_prints_and_flushes(self, capsys):
        hv.log("hello world")
        captured = capsys.readouterr()
        assert "  hello world" in captured.out

class TestSaveResult:
    def test_save_result_writes_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            original_dir = hv.RESULTS_DIR
            hv.RESULTS_DIR = tmp
            try:
                hv.save_result("test_result", b'\x01\x02\x03', {"key": "val"})
                bin_path = os.path.join(tmp, "test_result.bin")
                json_path = os.path.join(tmp, "test_result.json")
                assert os.path.exists(bin_path)
                assert os.path.exists(json_path)
                with open(bin_path, "rb") as f:
                    assert f.read() == b'\x01\x02\x03'
                with open(json_path) as f:
                    assert json.load(f) == {"key": "val"}
            finally:
                hv.RESULTS_DIR = original_dir

    def test_save_result_none_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            original_dir = hv.RESULTS_DIR
            hv.RESULTS_DIR = tmp
            try:
                hv.save_result("empty", None, {})
                bin_path = os.path.join(tmp, "empty.bin")
                with open(bin_path, "rb") as f:
                    assert f.read() == b''
            finally:
                hv.RESULTS_DIR = original_dir

class TestCheck:
    def setup_method(self):
        hv.PASS = 0
        hv.FAIL = 0
        hv.TOTAL = 0

    def test_check_pass(self):
        hv.check(True, "it worked")
        assert hv.PASS == 1
        assert hv.FAIL == 0
        assert hv.TOTAL == 1

    def test_check_fail(self):
        hv.check(False, "it broke")
        assert hv.PASS == 0
        assert hv.FAIL == 1
        assert hv.TOTAL == 1

    def test_check_multiple(self):
        for i in range(3):
            hv.check(True, f"ok {i}")
        hv.check(False, "bad")
        assert hv.PASS == 3
        assert hv.FAIL == 1
        assert hv.TOTAL == 4

class TestFloatingChannelActivity:
    def setup_method(self):
        hv.PASS = 0
        hv.FAIL = 0
        hv.TOTAL = 0

    def test_logs_without_recording_failure(self, capsys):
        ch = [
            [0, 1, 0, 1],
            [1, 1, 1, 1],
        ]

        hv.log_floating_channel_activity(ch, ns=4, label="fast")

        captured = capsys.readouterr()
        assert "fast CH0: 3 transitions" in captured.out
        assert hv.PASS == 0
        assert hv.FAIL == 0
        assert hv.TOTAL == 0

class TestPrintHeader:
    def test_print_header_format(self, capsys):
        hv.print_header("My Test")
        captured = capsys.readouterr()
        assert "My Test" in captured.out
        assert "=" * 60 in captured.out

class TestPrintProgress:
    def test_print_progress_partial(self, capsys):
        hv.print_progress(5, 10, "working")
        captured = capsys.readouterr()
        assert "5/10" in captured.out
        assert "50%" in captured.out

    def test_print_progress_complete_newline(self, capsys):
        hv.print_progress(10, 10, "done")
        captured = capsys.readouterr()
        assert "10/10" in captured.out
        assert "100%" in captured.out

    def test_print_progress_zero_total(self, capsys):
        hv.print_progress(0, 0, "none")
        captured = capsys.readouterr()
        assert "0/0" in captured.out

class TestDecodeI2CBest:
    def test_decode_i2c_best_returns_best_offset(self):
        samples = [1] * 200
        ch = [samples[:], samples[:]]
        result, offset = hv.decode_i2c_best(
            ch, samplerate=100000,
            scl_idx=0, sda_idx=1,
            filter_threshold=0, offsets=[0]
        )
        assert isinstance(result, list)
        assert offset == 0

    def test_decode_i2c_best_chooses_highest_score(self):
        samples = [1 if i < 100 else 0 for i in range(200)]
        ch = [samples[:], samples[:]]
        result, offset = hv.decode_i2c_best(
            ch, samplerate=100000,
            scl_idx=0, sda_idx=1,
            filter_threshold=0, offsets=[-1, 0, 1]
        )
        assert isinstance(result, list)
        assert isinstance(offset, int)

    def test_decode_i2c_best_decodes_real_traffic(self):
        # Real I2C write: scoring actually runs (DATA bytes present) and a
        # byte that is neither 0x00 nor 0xFF scores 1.
        ch = _make_i2c_signal(b'\x30')
        result, offset = hv.decode_i2c_best(
            ch, samplerate=100000,
            scl_idx=0, sda_idx=1,
            filter_threshold=0, offsets=[-3, 0, 3]
        )
        # All three offsets decode the same byte -> score tie -> the FIRST
        # offset achieving the max score wins.
        assert offset == -3
        assert [v for t, v in result if t == "DATA"] == [0x30]
        assert ('START', None) in result
        assert ('STOP', None) in result

    def test_decode_i2c_best_prefers_high_score_offset(self):
        # Offsets >= ~175 clamp every sample point to the final sample
        # (SDA high on the STOP plateau) -> all-1 byte -> 0xFF -> score 0.
        # Offset 0 decodes 0x30 -> score 1, so best_offset must be 0.
        ch = _make_i2c_signal(b'\x30')
        result, offset = hv.decode_i2c_best(
            ch, samplerate=100000,
            scl_idx=0, sda_idx=1,
            filter_threshold=0, offsets=[0, 200]
        )
        assert offset == 0
        assert [v for t, v in result if t == "DATA"] == [0x30]


class TestDecodeUARTSafe:
    def setup_method(self):
        hv.PASS = 0
        hv.FAIL = 0
        hv.TOTAL = 0

    def test_rejects_low_sampling_margin(self):
        result = hv.decode_uart_safe([[1] * 100], samplerate=500000,
                                     ch_idx=0, baud=115200)

        assert result == []
        assert hv.FAIL == 1

    def test_decodes_when_sampling_margin_is_high_enough(self):
        ch = [[1] * 10]
        bit_samples = 20
        for byte in b"H":
            ch[0].extend([0] * bit_samples)
            for bit in range(8):
                ch[0].extend([byte >> bit & 1] * bit_samples)
            ch[0].extend([1] * bit_samples)
            ch[0].extend([1] * bit_samples)

        result = hv.decode_uart_safe(ch, samplerate=2_000_000,
                                     ch_idx=0, baud=100_000)

        assert [b.value for b in result] == [0x48]
        assert hv.FAIL == 0


class TestRunWithTimeout:
    def test_returns_result_on_completion(self):
        assert hv.run_with_timeout(1.0, lambda: 42) == 42

    def test_raises_timeout_error_on_deadline(self):
        release = threading.Event()
        def slow():
            release.wait(2.0)
        with pytest.raises(TimeoutError) as excinfo:
            hv.run_with_timeout(0.01, slow)
        assert "run_with_timeout" in str(excinfo.value)
        release.set()  # let the daemon worker finish

    def test_worker_exception_is_reraises(self):
        def boom():
            raise ValueError("boom")
        with pytest.raises(ValueError, match="boom"):
            hv.run_with_timeout(1.0, boom)


class TestCheckChannelsClean:
    def setup_method(self):
        hv.PASS = 0
        hv.FAIL = 0
        hv.TOTAL = 0

    def test_clean_channels_pass(self):
        ch = [[0, 0, 0, 1, 1], [1, 1, 1, 1, 1]]
        hv.check_channels_clean(ch, ns=5, max_trans=1)
        assert hv.PASS == 2
        assert hv.FAIL == 0

    def test_noisy_channel_fails(self):
        ch = [[0, 1, 0, 1, 0], [1, 1, 1, 1, 1]]
        hv.check_channels_clean(ch, ns=5, max_trans=1)
        assert hv.PASS == 1
        assert hv.FAIL == 1

    def test_max_trans_exact_boundary_passes(self):
        ch = [[0, 1, 0, 1, 0]]  # exactly 4 transitions
        hv.check_channels_clean(ch, ns=5, max_trans=4)
        assert hv.PASS == 1
        assert hv.FAIL == 0

    def test_except_ch_skips_noisy_channels(self):
        ch = [[0, 1, 0, 1, 0], [0, 1, 0, 1, 0], [0, 0, 0, 0, 0]]
        hv.check_channels_clean(ch, ns=5, max_trans=1, except_ch=[0])
        # CH0 skipped, CH1 noisy -> FAIL, CH2 clean -> PASS
        assert hv.PASS == 1
        assert hv.FAIL == 1

    def test_ns_limits_the_samples_counted(self):
        # 10 samples with 9 transitions, but only the first 2 are counted
        # (min(ns, len(sig))): 1 transition, within max_trans=1.
        ch = [[0, 1, 0, 1, 0, 1, 0, 1, 0, 1]]
        hv.check_channels_clean(ch, ns=2, max_trans=1)
        assert hv.PASS == 1
        assert hv.FAIL == 0
