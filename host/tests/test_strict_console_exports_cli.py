import struct
import builtins
import runpy
import zipfile
from collections import namedtuple
from types import SimpleNamespace
from unittest.mock import MagicMock, mock_open, patch

import pytest

import app.OLS_Console as console
from app.OLS_Console import MODE_DIGITAL, MODE_MIXED, NUM_CHANNELS, OLScope


class Var:
    def __init__(self, value=None): self.value = value
    def get(self): return self.value
    def set(self, value): self.value = value


class Stop:
    def __init__(self, values=None):
        self.values = iter(values or [])
        self.flag = False
    def clear(self): self.flag = False
    def set(self): self.flag = True
    def is_set(self):
        try: return next(self.values)
        except StopIteration: return self.flag or True


class ImmediateThread:
    def __init__(self, target, daemon): self.target = target
    def start(self): self.target()


def export_scope():
    scope = OLScope.__new__(OLScope)
    scope.captured_bytes = b'\x00\x00\x01\x00'
    scope.samplerate = 1_000_000
    scope.capture_mode = MODE_DIGITAL
    scope.capture_stride = 2
    scope.status = {}
    scope.win = MagicMock()
    scope.wave = MagicMock()
    scope.decoded_uart = []
    return scope


def test_export_ols_cancel_and_analog_frames(tmp_path):
    scope = export_scope()
    with patch.object(console.filedialog, 'asksaveasfilename', return_value=''):
        scope._export_ols()
    assert 'text' not in scope.status

    target = tmp_path / 'analog.ols'
    scope.capture_mode = MODE_MIXED
    frames = [{'digital': 3, 'adc': [10, 20]}, {'digital': 1, 'adc': []}]
    with patch.object(console.filedialog, 'asksaveasfilename', return_value=str(target)), \
         patch.object(console, 'decode_analog_frames', return_value=frames):
        scope._export_ols()
    text = target.read_text()
    assert ';Analog: 8' in text
    assert '0003@0' in text
    assert ';A1: 20@0' in text


def test_export_ols_digital_handles_short_channel_matrix(tmp_path):
    scope = export_scope()
    target = tmp_path / 'digital.ols'
    channels = [[1, 0], [1]]
    with patch.object(console.filedialog, 'asksaveasfilename', return_value=str(target)), \
         patch.object(console, 'samples_to_channels', return_value=(channels, 2)):
        scope._export_ols()
    rows = [line for line in target.read_text().splitlines() if '@' in line]
    assert rows == ['0003@0', '0000@1']


def test_export_sr_cancel_and_analog_archive_with_missing_values(tmp_path):
    scope = export_scope()
    with patch.object(console.filedialog, 'asksaveasfilename', return_value=''):
        scope._export_sr()

    target = tmp_path / 'analog.sr'
    scope.capture_mode = MODE_MIXED
    frames = [{'digital': 0x1234, 'adc': [4095, 2048]}, {'digital': 1, 'adc': [0]}]
    with patch.object(console.filedialog, 'asksaveasfilename', return_value=str(target)), \
         patch.object(console, 'decode_analog_frames', return_value=frames):
        scope._export_sr()
    with zipfile.ZipFile(target) as archive:
        assert {'metadata', 'logic-1', 'analog-1', 'analog-2'} <= set(archive.namelist())
        assert archive.read('logic-1') == b'\x34\x12\x01\x00'
        assert len(archive.read('analog-2')) == 8

    empty_target = tmp_path / 'empty-analog.sr'
    with patch.object(console.filedialog, 'asksaveasfilename', return_value=str(empty_target)), \
         patch.object(console, 'decode_analog_frames', return_value=[]):
        scope._export_sr()
    with zipfile.ZipFile(empty_target) as archive:
        assert 'total analog=0' in archive.read('metadata').decode()


def test_export_sr_digital_archive_uses_capture_stride(tmp_path):
    scope = export_scope()
    target = tmp_path / 'digital.sr'
    scope.capture_mode = MODE_DIGITAL
    scope.capture_stride = 2
    scope.captured_bytes = b'\x01\xaa\x02\xbb'
    with patch.object(console.filedialog, 'asksaveasfilename', return_value=str(target)):
        scope._export_sr()
    with zipfile.ZipFile(target) as archive:
        assert archive.read('logic-1') == b'\x01\x02'
        assert 'total analog=0' in archive.read('metadata').decode()


def test_export_clip_analog_empty_and_ragged_frames():
    scope = export_scope()
    scope.capture_mode = MODE_MIXED
    with patch.object(console, 'decode_analog_frames', return_value=[]):
        scope._export_clip()
    assert 'Frames: 0' in scope.win.clipboard_append.call_args.args[0]

    scope.win.reset_mock()
    frames = [{'digital': 2, 'adc': [100, 200]}, {'digital': 3, 'adc': [300]}]
    with patch.object(console, 'decode_analog_frames', return_value=frames):
        scope._export_clip()
    text = scope.win.clipboard_append.call_args.args[0]
    assert 'First digital word: 0x0002' in text
    assert 'A1: min=0 max=200 avg=100' in text


def test_export_clip_digital_includes_uart_printable_and_binary():
    scope = export_scope()
    Byte = namedtuple('Byte', 'value')
    scope.decoded_uart = [Byte(65), Byte(1)]
    with patch.object(console, 'samples_to_channels', return_value=([[0, 1]], 2)):
        scope._export_clip()
    text = scope.win.clipboard_append.call_args.args[0]
    assert "0x41 'A'" in text
    assert "0x01 '.'" in text

    scope.win.reset_mock()
    scope.decoded_uart = []
    with patch.object(console, 'samples_to_channels', return_value=([[0, 1]], 2)):
        scope._export_clip()
    assert 'UART' not in scope.win.clipboard_append.call_args.args[0]


def test_marker_export_swaps_markers_cancel_and_handles_short_channels(tmp_path):
    scope = export_scope()
    scope.wave.marker1 = 2
    scope.wave.marker2 = 0
    with patch('tkinter.filedialog.asksaveasfilename', return_value=''):
        scope._export_marker_range()

    target = tmp_path / 'range.ols'
    scope.wave.marker1 = 1
    scope.wave.marker2 = 2
    scope.captured_bytes = bytes(range(12))
    channels = [[1, 0], [1]]
    with patch('tkinter.filedialog.asksaveasfilename', return_value=str(target)), \
         patch.object(console, 'samples_to_channels', return_value=(channels, 2)):
        scope._export_marker_range()
    assert ['0003@0', '0000@1'] == [line for line in target.read_text().splitlines() if '@' in line]


def logger_scope(tmp_path):
    scope = OLScope.__new__(OLScope)
    scope.dev = MagicMock()
    scope.logger_running = False
    scope.logger_count = 0
    scope.logger_stop_evt = Stop()
    scope.log_csv_path_v = Var(str(tmp_path / 'log.csv'))
    scope.log_arm_btn = MagicMock()
    scope.log_stop_btn = MagicMock()
    scope.log_count_label = {}
    scope.log_out = MagicMock()
    scope.log_status = {}
    scope.log_rate = Var('1MHz')
    scope.log_nsamp = Var('128')
    scope.log_trig_mode = Var('Off')
    scope.proto_match = Var('57')
    scope.proto_ch = Var('0')
    scope.proto_baud = Var('115200')
    scope.win = MagicMock()
    scope.wave = MagicMock()
    scope.wave.winfo_width.return_value = 500
    scope.wave.LABEL_WIDTH = 40
    return scope


def test_log_browse_sets_only_selected_filename():
    scope = OLScope.__new__(OLScope)
    scope.log_csv_path_v = Var('old')
    with patch('tkinter.filedialog.asksaveasfilename', return_value=''):
        scope._log_browse()
    assert scope.log_csv_path_v.value == 'old'
    with patch('tkinter.filedialog.asksaveasfilename', return_value='new.csv'):
        scope._log_browse()
    assert scope.log_csv_path_v.value == 'new.csv'


def test_logger_arm_requires_device_writes_header_clamps_samples_and_starts(tmp_path):
    scope = logger_scope(tmp_path)
    scope.dev = None
    scope._logger_arm()
    assert scope.log_status['text'] == 'Not connected'

    scope = logger_scope(tmp_path)
    scope.log_csv_path_v = Var('')
    scope.log_nsamp = Var('bad')
    scope._logger_thread = MagicMock()
    with patch('builtins.open', mock_open()), patch.object(console.threading, 'Thread', ImmediateThread):
        scope._logger_arm()
    assert scope.logger_csv_path == 'data_log.csv'
    assert scope.logger_nsamp == 1024
    scope._logger_thread.assert_called_once_with()

    scope = logger_scope(tmp_path)
    scope.log_nsamp = Var('999999')
    scope._logger_thread = MagicMock()
    with patch.object(console.threading, 'Thread', ImmediateThread):
        scope._logger_arm()
    assert scope.logger_nsamp == 500000
    assert (tmp_path / 'log.csv').read_text().startswith('timestamp,trigger_num')


def test_logger_arm_restores_controls_when_csv_creation_fails(tmp_path):
    scope = logger_scope(tmp_path)
    with patch('builtins.open', side_effect=OSError('readonly')), patch.object(console.threading, 'Thread') as thread:
        scope._logger_arm()
    assert scope.logger_running is False
    scope.log_arm_btn.configure.assert_called_with(state='normal')
    scope.log_stop_btn.configure.assert_called_with(state='disabled')
    assert 'readonly' in scope.log_status['text']
    thread.assert_not_called()


def test_logger_stop_trigger_mapping_and_csv_append(tmp_path):
    scope = logger_scope(tmp_path)
    scope.logger_running = True
    scope.logger_count = 4
    scope._logger_stop()
    assert scope.logger_stop_evt.flag is True
    assert scope.log_status['text'] == 'Stopped - 4 captures'

    for value, expected in [('Off', None), ('Rising', 'rising'), ('Falling', 'falling'), ('Protocol', None)]:
        scope.log_trig_mode = Var(value)
        assert scope._build_trigger_from_logger_ui() == expected

    scope.logger_csv_path = ''
    scope._append_csv_row([1])
    scope.logger_csv_path = str(tmp_path / 'rows.csv')
    scope._append_csv_row([1, 'x'])
    assert (tmp_path / 'rows.csv').read_text() == '1,x\n'
    with patch('builtins.open', side_effect=OSError('full')):
        scope._append_csv_row([2])
    assert 'CSV append error: full' == scope.log_status['text']


@pytest.mark.parametrize('mode', ['Protocol', 'Off', 'Rising'])
def test_logger_thread_captures_rows_for_trigger_modes(mode):
    scope = OLScope.__new__(OLScope)
    scope.dev = MagicMock()
    scope.logger_stop_evt = Stop([False, False, True])
    scope.logger_rate_hz = 1_000_000
    scope.logger_nsamp = 64
    scope.logger_count = 0
    scope.log_trig_mode = Var(mode)
    scope.proto_match = Var('xyz')
    scope.proto_ch = Var('bad')
    scope.proto_baud = Var('bad')
    scope.dev.capture.return_value = b'1234'
    scope._append_csv_row = MagicMock()
    scope.win = MagicMock()
    with patch.object(console, 'samples_to_channels', return_value=([[0, 1], [1, 1]], 2)), \
         patch.object(console.time, 'strftime', return_value='now'):
        scope._logger_thread()
    assert scope.logger_count == 1
    row = scope._append_csv_row.call_args.args[0]
    assert row[:3] == ['now', 1, 2]
    assert len(row) == 3 + NUM_CHANNELS + 1
    if mode == 'Protocol':
        scope.dev.trigger_decode.assert_called_with(match_byte=0x57, channel=0, baud=115200, enable=True)
    elif mode == 'Off':
        scope.dev.trigger_decode.assert_called_with(match_byte=0, enable=False)
    else:
        scope.dev.trigger_decode.assert_not_called()


def test_logger_thread_handles_capture_error_and_short_or_stopped_data():
    scope = OLScope.__new__(OLScope)
    scope.dev = MagicMock()
    scope.logger_rate_hz = 1
    scope.logger_nsamp = 64
    scope.logger_count = 0
    scope.log_trig_mode = Var('Rising')
    scope.win = MagicMock()
    scope.dev.capture.side_effect = OSError('device')
    scope.logger_stop_evt = Stop([False, False])
    scope._logger_thread()
    callback = scope.win.after.call_args.args[1]
    scope.log_status = MagicMock()
    callback()
    scope.log_status.configure.assert_called_once_with(text='Error: device')

    scope.dev.capture.side_effect = OSError('stopped')
    scope.logger_stop_evt = Stop([False, True])
    scope.win.reset_mock()
    scope._logger_thread()
    scope.win.after.assert_not_called()

    for values, data in [([False, True, True], b'1234'), ([False, False, True], b''), ([False, False, True], b'123')]:
        scope.dev.capture.side_effect = None
        scope.dev.capture.return_value = data
        scope.logger_stop_evt = Stop(values)
        scope.win.reset_mock()
        scope._logger_thread()
        scope.win.after.assert_not_called()


def test_log_update_ui_and_run_mainloop():
    scope = OLScope.__new__(OLScope)
    scope.log_count_label = {}
    scope.log_status = {}
    scope.wave = MagicMock()
    scope.wave.winfo_width.return_value = 500
    scope.wave.LABEL_WIDTH = 40
    scope.log_out = MagicMock()
    with patch.object(console, 'samples_to_channels', return_value=([[0, 1]], 2)):
        scope._log_update_ui(b'data', 3)
    assert scope.wave.px_scale == 230
    scope.wave.redraw.assert_called_once_with()
    scope.log_out.see.assert_called_once_with('end')

    scope.win = MagicMock()
    with patch.object(console, 'HAS_TK', True): scope.run()
    scope.win.mainloop.assert_called_once_with()
    scope.win.reset_mock()
    with patch.object(console, 'HAS_TK', False): scope.run()
    scope.win.mainloop.assert_not_called()


def cli_args(command, **updates):
    values = dict(
        command=command, input=None, output=None, rate=1_000_000, samples=4,
        timeout=None, format='raw4', protocol='uart', channel=0, baud=115200,
        data=None, tx_pin=3, scl_pin=1, addr='0x28', func='0x03', capture=False,
    )
    values.update(updates)
    return SimpleNamespace(**values)


def test_cli_validates_decode_input_backend_and_open_failure(capsys):
    assert console.cli_mode(cli_args('decode')) == 1
    with patch.object(console, 'HAS_SPI', False):
        assert console.cli_mode(cli_args('capture')) == 1
    dev = MagicMock()
    dev.open.side_effect = OSError('USB')
    with patch.object(console, 'OLSDeviceSPI', return_value=dev):
        assert console.cli_mode(cli_args('capture')) == 1
    assert 'Cannot open' in capsys.readouterr().out


def test_cli_capture_success_output_and_all_decode_failure_terms(tmp_path, capsys):
    dev = MagicMock()
    dev.capture.return_value = b'raw'
    target = tmp_path / 'raw.bin'
    with patch.object(console, 'OLSDeviceSPI', return_value=dev), \
         patch.object(console, 'samples_to_channels', return_value=([[0, 1]], 2)):
        assert console.cli_mode(cli_args('capture', output=str(target))) == 0
    assert target.read_bytes() == b'raw'
    dev.close.assert_called_once_with()
    assert '1 transitions' in capsys.readouterr().out

    for decoded in [((), 0), ([], 1), ([[]], 1)]:
        dev = MagicMock(); dev.capture.return_value = b'x'
        with patch.object(console, 'OLSDeviceSPI', return_value=dev), patch.object(console, 'samples_to_channels', return_value=decoded):
            assert console.cli_mode(cli_args('capture')) == 1
        dev.close.assert_called_once_with()


def test_cli_decode_raw_uart_i2c_and_modbus(tmp_path, capsys):
    target = tmp_path / 'input.bin'; target.write_bytes(b'raw')
    Byte = namedtuple('Byte', 'value')
    Frame = namedtuple('Frame', 'addr func data crc crc_ok')
    common = patch.object(console, 'samples_to_channels', return_value=([[0, 1]], 2))
    with common, patch.object(console, 'decode_uart', return_value=[Byte(65), Byte(1)]):
        assert console.cli_mode(cli_args('decode', input=str(target), protocol='uart')) == 0
    with patch.object(console, 'samples_to_channels', return_value=([[0, 1]], 2)), \
         patch.object(console, 'decode_i2c', return_value=[('START', None), ('BYTE', 0x55)]):
        assert console.cli_mode(cli_args('decode', input=str(target), protocol='i2c')) == 0
    frames = [Frame(1, 3, b'\x00\x01', 0x1234, True), Frame(2, 4, b'', 0, False)]
    with patch.object(console, 'samples_to_channels', return_value=([[0, 1]], 2)), \
         patch.object(console, 'decode_modbus', return_value=frames):
        assert console.cli_mode(cli_args('decode', input=str(target), protocol='modbus')) == 0
    output = capsys.readouterr().out
    assert "0x41  'A'" in output and 'START' in output and 'CRC=0x1234 OK' in output and 'BAD' in output


def test_cli_decode_sigrok_archive_missing_and_present_logic(tmp_path):
    empty = tmp_path / 'empty.sr'
    with zipfile.ZipFile(empty, 'w') as archive: archive.writestr('metadata', 'x')
    assert console.cli_mode(cli_args('decode', input=str(empty), format='sr')) == 1

    valid = tmp_path / 'valid.sr'
    with zipfile.ZipFile(valid, 'w') as archive: archive.writestr('logic-1', b'logic')
    with patch.object(console, 'samples_to_channels', return_value=([[0]], 1)), patch.object(console, 'decode_uart', return_value=[]):
        assert console.cli_mode(cli_args('decode', input=str(valid), format='sr')) == 0


@pytest.mark.parametrize('protocol', ['uart', 'i2c', 'modbus'])
def test_cli_send_data_dispatches_protocol_and_optional_capture(protocol, capsys):
    dev = MagicMock(); dev.sys_clk = 48_000_000; dev.capture_with_gen.return_value = b'cap'
    args = cli_args('send', data='abc', protocol=protocol, capture=True)
    with patch.object(console, 'OLSDeviceSPI', return_value=dev):
        assert console.cli_mode(args) == 0
    dev.start_gen.assert_called_once_with()
    dev.close.assert_called_once_with()
    if protocol == 'i2c':
        dev._load_block.assert_called_once_with(bytes([0x50]) + b'abc')
    elif protocol == 'modbus':
        sent = dev.send_uart.call_args.args[0]
        assert sent[:2] == b'\x28\x03'
        assert sent[-2:] == struct.pack('<H', console.modbus_crc16(sent[:-2]))
    else:
        dev.send_uart.assert_called_once_with(b'abc', baud=115200, tx_pin=3)
    assert 'Captured 3 bytes' in capsys.readouterr().out


def test_cli_send_reads_file_and_rejects_missing_payload(tmp_path):
    dev = MagicMock()
    with patch.object(console, 'OLSDeviceSPI', return_value=dev):
        assert console.cli_mode(cli_args('send')) == 1
    dev.close.assert_called_once_with()

    source = tmp_path / 'send.bin'; source.write_bytes(b'file')
    dev = MagicMock()
    with patch.object(console, 'OLSDeviceSPI', return_value=dev):
        assert console.cli_mode(cli_args('send', input=str(source))) == 0
    dev.send_uart.assert_called_once_with(b'file', baud=115200, tx_pin=3)


@pytest.mark.parametrize('has_spi,found,expected', [(True, True, 'SPI'), (True, False, None), (False, True, None)])
def test_splash_choose_is_safe_without_detected_device(has_spi, found, expected):
    with patch.object(console, 'HAS_SPI', has_spi), patch.object(console, 'find_spi_device', return_value=found):
        assert console.splash_choose() == expected


def test_main_cli_delegates_parsed_args():
    with patch.object(console.sys, 'argv', ['prog', 'decode', '--input', 'x']), \
         patch.object(console, 'cli_mode', return_value=7) as cli:
        with pytest.raises(SystemExit) as exc: console.main()
    assert exc.value.code == 7
    assert cli.call_args.args[0].command == 'decode'


@pytest.mark.parametrize('decoded,exit_code', [(([[0, 1]], 2), 0), (([], 0), 1)])
def test_main_direct_spi_capture_path(decoded, exit_code, tmp_path, capsys):
    dev = MagicMock(); dev.capture.return_value = b'data'
    target = tmp_path / f'{exit_code}.bin'
    argv = ['prog', 'capture', '--backend', 'SPI', '--output', str(target)]
    with patch.object(console.sys, 'argv', argv), patch.object(console, 'HAS_SPI', True), \
         patch.object(console, 'OLSDeviceSPI', return_value=dev), patch.object(console, 'samples_to_channels', return_value=decoded):
        with pytest.raises(SystemExit) as exc: console.main()
    assert exc.value.code == exit_code
    dev.close.assert_called_once_with()
    assert target.read_bytes() == b'data'


def test_main_direct_spi_capture_without_output(tmp_path):
    dev = MagicMock(); dev.capture.return_value = b'data'
    argv = ['prog', 'capture', '--backend', 'SPI']
    with patch.object(console.sys, 'argv', argv), patch.object(console, 'HAS_SPI', True), \
         patch.object(console, 'OLSDeviceSPI', return_value=dev), \
         patch.object(console, 'samples_to_channels', return_value=([[0, 1]], 2)):
        with pytest.raises(SystemExit) as exc: console.main()
    assert exc.value.code == 0


@pytest.mark.parametrize('backend', [None, 'SPI'])
def test_main_gui_builds_scope_autoconnects_and_runs(backend, capsys):
    root = MagicMock()
    app = MagicMock(); app.win = root
    with patch.object(console.sys, 'argv', ['prog']), patch.object(console, 'splash_choose', return_value=backend), \
         patch.object(console.tk, 'Tk', return_value=root), patch.object(console, 'OLScope', return_value=app):
        console.main()
    root.withdraw.assert_called_once_with()
    root.deiconify.assert_called_once_with()
    root.after.assert_called_once_with(100, app._auto_connect)
    app.run.assert_called_once_with()
    if backend is None:
        assert 'disconnected state' in capsys.readouterr().out


def _import_failing_on(blocked):
    original = builtins.__import__

    def selective(name, globals=None, locals=None, fromlist=(), level=0):
        if name == blocked:
            raise ImportError(f'blocked {name}')
        return original(name, globals, locals, fromlist, level)

    return selective


def test_module_spi_and_tk_import_fallbacks_are_executable():
    with patch('builtins.__import__', side_effect=_import_failing_on('driver.ols_spi_device')):
        values = runpy.run_path(console.__file__, run_name='console_spi_fallback')
    assert values['HAS_SPI'] is False
    assert values['analog_wire_stride'](99) == 2
    assert values['wire_to_payload'](bytes(range(8))) == b'\x00\x01\x04\x05'

    with patch('builtins.__import__', side_effect=_import_failing_on('tkinter')):
        values = runpy.run_path(console.__file__, run_name='console_tk_fallback')
    assert values['HAS_TK'] is False
    scope = values['OLScope'](root=None)
    assert scope.win is None


def test_module_serial_import_failure_exits_with_install_hint(capsys):
    with patch('builtins.__import__', side_effect=_import_failing_on('serial')):
        with pytest.raises(SystemExit) as exc:
            runpy.run_path(console.__file__, run_name='console_serial_fallback')
    assert exc.value.code == 1
    assert 'Install pyserial' in capsys.readouterr().out


def test_module_entrypoint_guard_runs_version_command():
    with patch.object(console.sys, 'argv', ['OLS_Console.py', '--version']):
        with pytest.raises(SystemExit) as exc:
            runpy.run_path(console.__file__, run_name='__main__')
    assert exc.value.code == 0
