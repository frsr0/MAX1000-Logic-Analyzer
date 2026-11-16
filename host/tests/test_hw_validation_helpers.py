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

def test_group_jumper_wires_merges_both_directions_of_one_wire():
    routes = [(2, 1), (1, 2), (16, 15)]
    assert hv._group_jumper_wires(routes) == [[(2, 1), (1, 2)], [(16, 15)]]


def test_additional_jumper_routes_excludes_the_primary_wire():
    routes = [(2, 1), (1, 2), (16, 15)]
    assert hv._additional_jumper_routes(routes, (2, 1)) == [(16, 15)]
    assert hv._additional_jumper_routes([(2, 1), (1, 2)], (2, 1)) == []
    assert hv._additional_jumper_routes([], None) == []


def test_discovery_records_every_decoding_route_and_logs_wire_count():
    value = MagicMock()
    value.capture_with_gen.return_value = b"probe"
    rows = [[0, 1] * 64 for _ in range(16)]
    transitions = [50] * 16
    wired = {(2, 1), (1, 2), (16, 15)}

    def decoded(_channels, _rate, ch_idx, **_kwargs):
        if (value._gen_tx_pin, ch_idx) in wired:
            return [MagicMock(value=b) for b in b"PinProbe!"]
        return []

    with patch.object(hv, "samples_to_channels", return_value=(rows, 128)),          patch.object(hv, "_channel_transitions", return_value=transitions),          patch.object(hv, "decode_uart_safe", side_effect=decoded),          patch.object(hv, "log") as log:
        assert hv._discover_jumper_pair(value, deadline_s=60) == (2, 1)
    assert hv._JUMPER_ROUTES == [(2, 1), (1, 2), (16, 15)]
    lines = [call.args[0] for call in log.call_args_list]
    assert any("detected jumper routes: 2 -> CH1, 1 -> CH2, 16 -> CH15 "
               "(2 distinct wires)" in line for line in lines)
    hv._JUMPER_ROUTES.clear()


def test_analog_discovery_tries_every_pin_on_streamed_channels_before_deadline():
    value = MagicMock()
    tried = []

    def probe(_dev, tx, adc):
        tried.append((tx, adc))
        if (tx, adc) == (0, 4):
            return {"samples": 200, "amplitude": 4095, "edges": 20}
        return {"samples": 200, "amplitude": 100, "edges": 0}

    # Deadline already expired: only the unbounded streamed-channel pass runs.
    with patch.object(hv.time, "time", return_value=0),          patch.object(hv, "_capture_physical_analog_activity", side_effect=probe):
        pairs = hv._discover_analog_jumper_pairs(value, deadline_s=-1)
    assert pairs == [(0, 4, "pin0 -> AIN6/ADC4")]
    assert {tx for tx, _ in tried} == set(range(23))
    assert all(adc in (1, 2, 3, 4) for _, adc in tried)

def test_prefix_run_lengths_accepts_exact_square_wave_and_flags_a_dropped_sample():
    wave = [1] * 7 + ([0] * 104 + [1] * 104) * 4
    mode, bad, inner = hv._prefix_run_lengths(wave[:-30])
    assert (mode, bad) == (104, [])
    assert inner >= 6
    broken = wave[:300] + wave[301:]          # one dropped sample inside a run
    mode, bad, _ = hv._prefix_run_lengths(broken)
    assert mode == 104 and len(bad) == 1 and bad[0][1] == 103
    dup = wave[:300] + [wave[300]] + wave[300:]   # one duplicated sample
    mode, bad, _ = hv._prefix_run_lengths(dup)
    assert mode == 104 and bad[0][1] == 105


def test_prefix_run_lengths_needs_interior_runs():
    assert hv._prefix_run_lengths([1, 1, 0, 0]) == (None, [], 0)

def test_expected_jumper_wire_count_parses_environment(monkeypatch):
    monkeypatch.delenv(hv.JUMPER_EXPECT_WIRES_ENV, raising=False)
    assert hv._expected_jumper_wire_count() is None
    monkeypatch.setenv(hv.JUMPER_EXPECT_WIRES_ENV, " 2 ")
    assert hv._expected_jumper_wire_count() == 2
    monkeypatch.setenv(hv.JUMPER_EXPECT_WIRES_ENV, "two")
    assert hv._expected_jumper_wire_count() is None


def test_expected_jumper_wires_fails_when_a_wire_is_missing(monkeypatch):
    routes = [(2, 1), (1, 2)]                   # one distinct wire
    monkeypatch.setenv(hv.JUMPER_EXPECT_WIRES_ENV, "2")
    with patch.object(hv, "check") as check, patch.object(hv, "log"):
        hv._check_expected_jumper_wires(routes)
    assert check.call_args.args[0] is False
    assert "found 1 distinct" in check.call_args.args[1]
    with patch.object(hv, "check") as check:
        hv._check_expected_jumper_wires(routes + [(16, 15)])
    assert check.call_args.args[0] is True
    monkeypatch.delenv(hv.JUMPER_EXPECT_WIRES_ENV)
    with patch.object(hv, "check") as check:
        hv._check_expected_jumper_wires(routes)
    check.assert_not_called()

def test_changed_word_indices_reports_differing_16bit_words():
    before = bytes([1, 0, 2, 0, 3, 0, 4, 0])
    after = bytes([1, 0, 9, 0, 3, 0, 8, 1])
    assert hv._changed_word_indices(before, after) == [1, 3]
    assert hv._changed_word_indices(before, before) == []


def test_oracle_constants_cover_three_dividers():
    assert hv.ORACLE_FIRST_LOW_INDEX == {9: 1105, 99: 110, 195: 56}


def test_window_equal_fraction_scores_a_lagged_window():
    flat = [0] * 100
    assert hv._window_equal_fraction(flat, 0) == 1.0

    split = [0] * 50 + [1] * 50
    assert hv._window_equal_fraction(split, 50) == 0.0

    # A lag past the end compares nothing, which must score 0, not divide by 0.
    assert hv._window_equal_fraction(flat, 500) == 0.0
