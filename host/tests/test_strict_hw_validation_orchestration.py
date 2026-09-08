import os
import runpy
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest

from app import hw_validation as hv


@pytest.fixture(autouse=True)
def reset_counts():
    old = hv.PASS, hv.FAIL, hv.TOTAL, hv.SKIPPED, hv._JUMPER_PAIR_CACHE, hv._JUMPER_PAIR_SEARCHED
    hv.PASS = hv.FAIL = hv.TOTAL = hv.SKIPPED = 0
    hv._JUMPER_PAIR_CACHE = None
    hv._JUMPER_PAIR_SEARCHED = False
    yield
    hv.PASS, hv.FAIL, hv.TOTAL, hv.SKIPPED, hv._JUMPER_PAIR_CACHE, hv._JUMPER_PAIR_SEARCHED = old


def test_floating_exclusions_include_new_cached_rx_once():
    assert hv._floating_except() == [0, 7, 10, 11, 14, 15]
    hv._JUMPER_PAIR_CACHE = (3, 6)
    assert hv._floating_except()[-1] == 6
    hv._JUMPER_PAIR_CACHE = (3, 15)
    assert hv._floating_except().count(15) == 1


@pytest.mark.parametrize('argv,mode', [(['p'], 'full'), (['p', 'new'], 'new'), (['p', 'codec'], 'codec'), (['p', 'unknown'], 'full')])
def test_suite_mode(argv, mode):
    assert hv._suite_mode(argv) == mode


def test_watchdog_timeout_defaults_override_and_invalid(capsys):
    with patch.dict(os.environ, {}, clear=True):
        assert hv._watchdog_timeout('new') == hv.WATCHDOG_DEFAULTS['new']
    with patch.dict(os.environ, {hv.WATCHDOG_TIMEOUT_ENV: '-2.5'}, clear=True):
        assert hv._watchdog_timeout('full') == 0
    with patch.dict(os.environ, {hv.WATCHDOG_TIMEOUT_ENV: '12.9'}, clear=True):
        assert hv._watchdog_timeout('full') == 12
    with patch.dict(os.environ, {hv.WATCHDOG_TIMEOUT_ENV: 'bad'}, clear=True):
        assert hv._watchdog_timeout('codec') == hv.WATCHDOG_DEFAULTS['codec']
    assert 'must be seconds' in capsys.readouterr().out


def test_watchdog_child_disabled_success_and_timeout(capsys):
    with patch.dict(os.environ, {hv.WATCHDOG_CHILD_ENV: '1'}, clear=True):
        assert hv._run_under_watchdog() is None
    with patch.dict(os.environ, {hv.WATCHDOG_TIMEOUT_ENV: '0'}, clear=True):
        assert hv._run_under_watchdog() is None

    result = SimpleNamespace(returncode=7)
    with patch.dict(os.environ, {hv.WATCHDOG_TIMEOUT_ENV: '1'}, clear=True), \
         patch.object(hv.subprocess, 'run', return_value=result) as run:
        assert hv._run_under_watchdog() == 7
    assert run.call_args.kwargs['env'][hv.WATCHDOG_CHILD_ENV] == '1'

    with patch.dict(os.environ, {hv.WATCHDOG_TIMEOUT_ENV: '1'}, clear=True), \
         patch.object(hv.subprocess, 'run', side_effect=hv.subprocess.TimeoutExpired('x', 1)):
        assert hv._run_under_watchdog() == 124
    assert 'timed out' in capsys.readouterr().out


def test_skip_check_headers_progress_and_channel_reporting(capsys):
    with patch.object(hv, 'log', wraps=hv.log):
        hv.skip('fixture')
    assert hv.SKIPPED == 1
    hv.check(True, 'yes')
    hv.check(False, 'no')
    assert (hv.PASS, hv.FAIL, hv.TOTAL) == (1, 1, 2)
    hv.print_header('Header')
    hv.print_progress(0, 0, 'idle')
    hv.print_progress(2, 2, 'done')

    data = [[0, 1, 0], [0, 0, 0], [1, 0, 1]]
    hv.check_channels_clean(data, 3, except_ch=[1], max_trans=1, label='L')
    hv.log_floating_channel_activity(data, 3, except_ch=[1], label='F')
    assert hv.TOTAL == 4
    assert 'floating channels with activity' in capsys.readouterr().out


def test_save_result_writes_binary_json_and_empty_payload(tmp_path):
    with patch.object(hv, 'RESULTS_DIR', str(tmp_path)):
        hv.save_result('one', b'abc', {'x': 1})
        hv.save_result('empty', None, {})
    assert (tmp_path / 'one.bin').read_bytes() == b'abc'
    assert (tmp_path / 'empty.bin').read_bytes() == b''
    assert '"x": 1' in (tmp_path / 'one.json').read_text()


def test_decode_uart_safe_rejects_low_margin_and_delegates_valid_margin():
    with patch.object(hv, 'check') as check, patch.object(hv, 'decode_uart', return_value=['ok']) as decode:
        assert hv.decode_uart_safe([], 1000, baud=1000, min_spb=2) == []
        check.assert_called_once()
        assert hv.decode_uart_safe([], 10_000, baud=1000, min_spb=2) == ['ok']
    decode.assert_called_once()


def test_run_with_debug_toggles_both_states_with_and_without_timeout():
    dev = MagicMock(); dev.sys_clk = 100_000_000
    test_fn = MagicMock()
    with patch.object(hv.time, 'sleep'), patch.object(hv, 'run_with_timeout', wraps=hv.run_with_timeout) as timed:
        hv.run_with_debug(test_fn, dev, 'x', 1, flag=True)
        hv.run_with_debug(test_fn, dev, 'x', 2, timeout_s=1, flag=False)
    assert dev.set_debug_ch0.call_count == 4
    assert test_fn.call_count == 4
    assert timed.call_count == 2


def test_uart_cmd_id_import_ports_success_nonmatch_and_error():
    serial = SimpleNamespace(Serial=MagicMock())
    good = MagicMock(); good.read.return_value = b'1ALS'
    empty = MagicMock(); empty.read.return_value = b''
    for platform, glob_results in (
        ('win32', [['COM3', 'COM1', 'COM2']]),
        ('linux', [['COM3', 'COM1', 'COM2'], []]),
    ):
        serial.Serial.reset_mock()
        serial.Serial.side_effect = [OSError('busy'), empty, good]
        with patch.object(hv.sys, 'platform', platform), \
             patch.dict('sys.modules', {'serial': serial}), \
             patch('glob.glob', side_effect=glob_results) as glob, \
             patch.object(hv.time, 'sleep'), \
             patch.object(hv, 'check') as check:
            hv.test_uart_cmd_id()
        expected_globs = ([call('COM*')] if platform == 'win32' else
                          [call('/dev/ttyUSB*'), call('/dev/ttyACM*')])
        assert glob.call_args_list == expected_globs
        assert serial.Serial.call_args_list == [
            call('COM1', 115200, timeout=1),
            call('COM2', 115200, timeout=1),
            call('COM3', 115200, timeout=1),
        ]
        check.assert_called_once_with(True, 'UART ID match on COM3')

    serial.Serial.side_effect = OSError('none')
    with patch.dict('sys.modules', {'serial': serial}), patch('glob.glob', return_value=['COM1']), \
         patch.object(hv.time, 'sleep'), patch.object(hv, 'check') as check:
        hv.test_uart_cmd_id()
    check.assert_called_once_with(False, 'No UART device found with OLS ID')


def _patched_hardware_tests(stack):
    patched = {}
    for name, value in vars(hv).items():
        if name.startswith('test_') and callable(value):
            mock = MagicMock(name=name)
            stack.enter_context(patch.object(hv, name, mock))
            patched[name] = mock
    stack.enter_context(patch.object(hv, 'run_with_debug', MagicMock()))
    stack.enter_context(patch.object(hv.time, 'sleep'))
    stack.enter_context(patch.object(hv, 'log'))
    return patched


def _device():
    dev = MagicMock()
    dev.sys_clk = 100_000_000
    dev.spi = MagicMock()
    return dev


@pytest.mark.parametrize('pair,skipped,failed,expected', [((3, 4), 0, 0, 0), (None, 1, 0, 0), (None, 0, 1, 1)])
def test_main_orchestrates_full_suite_and_summary_branches(pair, skipped, failed, expected, capsys):
    dev = _device()
    hv.SKIPPED = skipped
    hv.FAIL = failed
    with ExitStack() as stack:
        tests = _patched_hardware_tests(stack)
        stack.enter_context(patch.object(hv, 'OLSDeviceSPI', return_value=dev))
        stack.enter_context(patch.object(hv, '_get_jumper_pair', return_value=pair))
        assert hv.main() == expected
    tests['test_spi_handoff'].assert_called_once_with(dev)
    tests['test_capture_during_readout'].assert_called_once_with(dev)
    assert dev.close.call_count >= 3
    output = capsys.readouterr().out
    if failed:
        assert 'TEST(S) FAILED' in output
    elif skipped:
        assert 'skipped' in output
    else:
        assert 'ALL TESTS PASSED' in output


def test_main_reports_abort_and_tolerates_reset_close_cleanup_errors():
    dev = _device()
    dev.open.side_effect = RuntimeError('open')
    dev.reset.side_effect = RuntimeError('reset')
    dev.close.side_effect = RuntimeError('close')
    with patch.object(hv, 'OLSDeviceSPI', return_value=dev), patch.object(hv, 'check', wraps=hv.check), \
         patch.object(hv, 'log'), patch.object(hv.time, 'sleep'):
        assert hv.main() == 1
    assert hv.FAIL == 1


@pytest.mark.parametrize('entry', ['main_new_only', 'main_codec_only', 'main_jumper_only', 'main_analog_only'])
def test_specialized_mains_success_and_failure_cleanup(entry):
    fn = getattr(hv, entry)
    dev = _device()
    with ExitStack() as stack:
        _patched_hardware_tests(stack)
        stack.enter_context(patch.object(hv, 'OLSDeviceSPI', return_value=dev))
        assert fn() == 0

    dev = _device(); dev.open.side_effect = RuntimeError('bad')
    dev.close.side_effect = RuntimeError('close')
    hv.FAIL = 0
    with ExitStack() as stack:
        _patched_hardware_tests(stack)
        stack.enter_context(patch.object(hv, 'OLSDeviceSPI', return_value=dev))
        assert fn() == 1
    assert hv.FAIL == 1


def test_module_uart_entrypoint_is_safe_without_ports(capsys):
    with patch.dict(os.environ, {hv.WATCHDOG_CHILD_ENV: '1'}), \
         patch.object(hv.sys, 'argv', [hv.__file__, 'uart']), patch.object(hv.time, 'sleep'), \
         patch('glob.glob', return_value=[]):
        with pytest.raises(SystemExit) as exc:
            runpy.run_path(hv.__file__, run_name='__main__')
    assert exc.value.code == 1
