import os, json, sys, tempfile
from unittest.mock import MagicMock, patch, mock_open

import hw_validation as hv

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
