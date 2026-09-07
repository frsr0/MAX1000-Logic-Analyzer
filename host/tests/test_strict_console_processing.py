from collections import namedtuple
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import app.OLS_Console as console
from app.OLS_Console import MODE_DIGITAL, MODE_MIXED, NUM_CHANNELS, OLScope


class Var:
    def __init__(self, value=None): self.value = value
    def get(self): return self.value
    def set(self, value): self.value = value


def scope_base():
    scope = OLScope.__new__(OLScope)
    scope.status = {}
    scope.raw_mode_var = Var(False)
    scope.ch_names = [f'CH{i}' for i in range(NUM_CHANNELS)]
    scope.ch_data = [[0, 1] for _ in range(NUM_CHANNELS)]
    scope.wave = MagicMock()
    scope.wave.winfo_width.return_value = 500
    scope.wave.LABEL_WIDTH = 40
    scope.wave.num_samples = 2
    scope.wave.channel_visible = [True] * NUM_CHANNELS
    scope.capture_type = Var('single')
    scope.capture_window = 100
    scope.capture_nsamp = 2
    scope.samplerate = 1_000_000
    scope.mode_cb = Var('16 Digital')
    scope.filter_threshold = 3
    scope.filter_enabled = [False] * NUM_CHANNELS
    scope.decoder_slots = []
    scope._process_decoders = MagicMock()
    scope._fit_view = MagicMock()
    return scope


def test_load_capture_empty_and_all_decode_failure_shapes(capsys):
    scope = scope_base()
    scope._load_capture(b'', 1)
    assert '0 bytes' in scope.status['text']
    assert 'data is empty' in capsys.readouterr().out

    failures = [((), 0), ([], 1), ([[]], 1)]
    for decoded in failures:
        with patch.object(console, 'samples_to_channels', return_value=decoded):
            scope._load_capture(b'x', 1)
        assert scope.status['text'] == 'No samples decoded from capture'


def test_load_capture_raw_extracts_first_byte_and_loads_processed_wave(capsys):
    scope = scope_base()
    scope.raw_mode_var = Var(True)
    scope._process_decoders = MagicMock()
    scope._fit_view = MagicMock()
    channels = [[0, 1], [1, 0]]
    data = bytes(range(9))
    with patch.object(console, 'samples_to_channels', return_value=(channels, 2)) as decode:
        scope._load_capture(data, 2_000_000, stride=4)
    decode.assert_called_once_with(bytes([0, 4]), stride=1)
    assert scope.captured_bytes == bytes([0, 4])
    assert scope.samplerate == 2_000_000
    scope._process_decoders.assert_called_once_with()
    scope.wave.load.assert_called_once()
    scope._fit_view.assert_called_once_with()
    assert '1 CH0 transitions' in scope.status['text']
    assert 'first 20' in capsys.readouterr().out


def test_load_analog_empty_decoded_rows_and_mixed_default_channels():
    scope = scope_base()
    scope._load_analog_capture(b'', 1, [], MODE_MIXED, 6)
    assert '0 bytes' in scope.status['text']

    scope._fit_view = MagicMock()
    scope._load_analog_capture(b'data', 2, (), MODE_MIXED, 6)
    assert len(scope.ch_data) == NUM_CHANNELS + 4
    assert scope.ch_names[-4:] == ['A0', 'A1', 'A2', 'A3']
    scope.wave.load.assert_called()


def test_load_analog_decodes_non_list_frames_and_handles_missing_digital_and_extra_adc():
    scope = scope_base()
    rows = [
        {'digital': None, 'adc': [10]},
        {'digital': 3, 'adc': [11, 99]},
    ]
    with patch.object(console, 'decode_analog_frames', return_value=rows) as decode:
        scope._load_analog_capture(b'wire', 4_000_000, object(), MODE_MIXED, 6)
    decode.assert_called_once_with(b'wire', MODE_MIXED)
    assert scope.ch_data[0] == [0, 1]
    assert scope.ch_data[1] == [0, 1]
    assert scope.ch_data[-1] == [10, 11]
    assert scope.ch_names[-1] == 'A0'

    scope._load_analog_capture(b'wire', 1, [], MODE_DIGITAL, 2)
    assert len(scope.ch_data) == NUM_CHANNELS


@pytest.mark.parametrize(
    'kind,window,capture_nsamp,wave_samples,width,draws,total',
    [
        ('rolling', 200, 5, 7, 500, True, 200),
        ('single', 200, 5, 7, 500, True, 5),
        ('single', 200, 0, 7, 500, True, 7),
        ('single', 200, 0, 0, 500, False, 0),
        ('single', 200, 5, 7, 10, False, 5),
    ],
)
def test_fit_view_chooses_capture_extent_and_valid_canvas(kind, window, capture_nsamp, wave_samples, width, draws, total):
    scope = scope_base()
    scope.capture_type = Var(kind)
    scope.capture_window = window
    scope.capture_nsamp = capture_nsamp
    scope.wave.num_samples = wave_samples
    scope.wave.winfo_width.return_value = width
    scope._fit_view = OLScope._fit_view.__get__(scope)
    scope._fit_view()
    assert scope.wave.redraw.called is draws
    if draws:
        assert scope.wave.px_scale == (width - 40) / total
        assert scope.wave.scroll_x == 0


def test_scroll_placeholders_are_safe_callbacks():
    scope = scope_base()
    assert scope._on_scroll('moveto', 0.5) is None
    assert scope._update_scroll(1, 2, 3) is None


def test_sync_channel_visibility_handles_missing_lists_short_wave_and_analog_rows():
    scope = OLScope.__new__(OLScope)
    scope._sync_ch_vis_ui()
    scope.ch_vis_vars = [Var(), Var('unchanged')]
    scope.ana_vis_vars = [Var(), Var('unchanged')]
    scope._sync_ch_vis_ui()
    assert scope.ch_vis_vars[1].value == 'unchanged'

    scope.wave = SimpleNamespace(channel_visible=[False] * 17)
    scope._sync_ch_vis_ui()
    assert scope.ch_vis_vars[0].value is False
    assert scope.ch_vis_vars[1].value is False
    assert scope.ana_vis_vars[0].value is False
    assert scope.ana_vis_vars[1].value == 'unchanged'


def real_process_scope():
    scope = scope_base()
    scope._process_decoders = OLScope._process_decoders.__get__(scope)
    scope.dec_out = MagicMock()
    return scope


def slot(source, proto, **extra):
    value = {
        'enabled': True, 'src_str': source, 'src_idx': int(source[0]),
        'proto': proto, 'baud': 1000, 'thresh': 2, 'sda_idx': 3, 'scl_idx': 1,
    }
    value.update(extra)
    return value


def test_process_decoders_uses_defaults_without_optional_attributes():
    scope = OLScope.__new__(OLScope)
    scope.ch_data = [[0, 1] for _ in range(NUM_CHANNELS)]
    scope.samplerate = 1_000
    scope._process_decoders()
    assert scope.ch_names == [f'CH{i}' for i in range(NUM_CHANNELS)]


def test_process_decoders_uart_clamps_negative_positions_formats_long_text_and_extends_visibility():
    scope = real_process_scope()
    scope.filter_enabled[0] = True
    uart = slot('0_f', 'UART')
    scope.decoder_slots = [uart]
    Byte = namedtuple('Byte', 'pos value')
    decoded = [Byte(-1, 65)] + [Byte(0, 1)] * 50
    with patch.object(console, 'glitch_filter', return_value=[1, 0]) as filt, \
         patch.object(console, 'decode_uart', return_value=decoded) as decode:
        scope._process_decoders()
    filt.assert_called_once_with([0, 1], 3)
    assert decode.call_args.kwargs['filter_threshold'] == 2
    assert scope.ch_names[:3] == ['CH0', 'CH0_f', '0_f_UART']
    assert len(scope.wave.channel_visible) == len(scope.ch_data)
    output = scope.dec_out.insert.call_args.args[1]
    assert '[01]' in output and '...' in output


def test_process_decoders_i2c_formats_start_stop_bytes_and_none():
    scope = real_process_scope()
    i2c = slot('0', 'I2C', thresh=0)
    scope.decoder_slots = [i2c]
    events = [('START', None), ('BYTE', 0x55), ('BYTE', None), ('STOP', None)]
    with patch.object(console, 'decode_i2c', return_value=events) as decode:
        scope._process_decoders()
    assert decode.call_args.kwargs['filter_threshold'] == 0
    assert i2c['sig'][0] == 1
    assert 'S 0x55 P' in scope.dec_out.insert.call_args.args[1]


def test_process_decoders_spi_disabled_mismatch_and_missing_filtered_source():
    scope = real_process_scope()
    disabled = slot('0', 'UART', enabled=False)
    mismatch = slot('2', 'UART')
    missing = slot('9_f', 'SPI')
    spi = slot('1', 'SPI', thresh=0)
    scope.decoder_slots = [disabled, mismatch, missing, spi]
    with patch.object(console, 'decode_spi', return_value=[0x12, 0xAB]):
        scope._process_decoders()
    assert '1_SPI' in scope.ch_names
    assert '9_f_SPI' not in scope.ch_names
    assert '0x12 0xAB' in scope.dec_out.insert.call_args.args[1]


def test_process_decoders_numeric_source_uses_channel_identity_after_inserted_rows():
    scope = real_process_scope()
    scope.filter_enabled[0] = True
    scope.ch_data[2] = [1, 1]
    scope.decoder_slots = [slot('0', 'SPI'), slot('2', 'UART')]
    Byte = namedtuple('Byte', 'pos value')
    with patch.object(console, 'decode_spi', return_value=[]), \
         patch.object(console, 'decode_uart', return_value=[Byte(0, 65)]) as uart:
        scope._process_decoders()
    assert uart.call_args.args[0] == [[1, 1]]


def test_process_decoders_without_wave_or_output_widget_and_no_active_decoders():
    scope = real_process_scope()
    del scope.wave
    del scope.dec_out
    scope._process_decoders()
    assert len(scope.ch_data) == NUM_CHANNELS

    scope = real_process_scope()
    scope._process_decoders()
    scope.dec_out.insert.assert_called_once_with('1.0', 'No decoders active')


def generator_scope(proto='UART', data='abc'):
    scope = scope_base()
    scope.dev = MagicMock()
    scope.gen_proto = Var(proto)
    scope.gen_data = MagicMock()
    scope.gen_data.get.return_value = data
    scope.gen_tx_pin = Var('3')
    scope.gen_scl_pin = Var('1')
    scope.gen_baud = Var('9600')
    scope.gen_addr = Var('2A')
    scope.gen_func = Var('03')
    scope.capture_type = Var('single')
    scope.win = MagicMock()
    scope.rate_cb = Var('1MHz')
    scope._nsamp = 8
    scope.wave = MagicMock()
    scope._process_decoders = MagicMock()
    return scope


def test_generator_send_early_returns_and_rolling_queue_default_pin():
    scope = generator_scope()
    scope.dev = None
    scope._gen_send()

    scope.dev = MagicMock()
    scope.gen_data.get.return_value = ''
    scope._gen_send()
    assert not hasattr(scope.dev, '_pending_gen') or not isinstance(scope.dev._pending_gen, dict)

    scope.gen_data.get.return_value = 'roll'
    scope.capture_type = Var('rolling')
    scope.gen_tx_pin = Var('bad')
    scope._gen_send()
    assert scope.dev._pending_gen == {'data': b'roll', 'baud': 9600, 'tx_pin': 3, 'proto': 'UART'}
    assert 'queued' in scope.status['text']


@pytest.mark.parametrize('proto', ['UART', 'I2C', 'Modbus'])
def test_generator_send_dispatches_every_advertised_protocol(proto):
    scope = generator_scope(proto)
    scope.dev.sys_clk = 48_000_000
    scope._gen_send()
    assert scope.status['text'] == 'Generator started'
    if proto == 'UART':
        scope.dev.send_uart.assert_called_once_with(b'abc', 9600, tx_pin=3)
    elif proto == 'I2C':
        scope.dev._pins.assert_called_once_with(tx_pin=3, scl_pin=1)
        scope.dev._load_block.assert_called_once_with(bytes([0x54]) + b'abc')
        scope.dev.start_gen.assert_called_once_with()
    else:
        scope.dev.send_modbus.assert_called_once_with(0x2A, 3, b'abc', baud=9600, tx_pin=3)


def test_generator_send_reports_invalid_protocol_and_device_errors():
    scope = generator_scope('CAN')
    scope._gen_send()
    assert 'Unsupported generator protocol' in scope.status['text']

    scope = generator_scope('UART')
    scope.dev.send_uart.side_effect = OSError('line busy')
    scope._gen_send()
    assert 'line busy' in scope.status['text']


def test_generator_capture_early_returns_and_rolling_queue():
    scope = generator_scope()
    scope.dev = None
    scope._gen_send_capture()
    scope.dev = MagicMock()
    scope.gen_data.get.return_value = ''
    scope._gen_send_capture()

    scope.gen_data.get.return_value = 'roll'
    scope.capture_type = Var('rolling')
    scope.gen_tx_pin = Var('bad')
    scope._gen_send_capture()
    assert scope.dev._pending_gen['tx_pin'] == 3
    assert 'queued' in scope.status['text']


@pytest.mark.parametrize('proto', ['UART', 'Modbus', 'I2C'])
def test_generator_capture_dispatches_protocol_loads_wave_and_highlights_tx(proto, capsys):
    scope = generator_scope(proto)
    scope.dev.capture_with_gen.return_value = b'capture'
    channels = [[0, 1, 0] for _ in range(NUM_CHANNELS)]
    with patch.object(console, 'samples_to_channels', return_value=(channels, 3)):
        scope._gen_send_capture()
    assert scope.captured_bytes == b'capture'
    scope.wave.load.assert_called_once()
    scope.wave.highlight_channel.assert_called_once_with(3)
    scope._process_decoders.assert_called_once_with()
    assert '2 trans on CH3' in scope.status['text']
    assert 'gen_capture returned' in capsys.readouterr().out
    if proto == 'I2C':
        kwargs = scope.dev.capture_with_gen.call_args.kwargs
        assert kwargs['proto'] == 'I2C'
        assert kwargs['i2c_frame'] == bytes([0x54]) + b'abc'
    else:
        assert scope.dev._gen_data == b'abc'


def test_generator_capture_handles_exception_empty_and_decode_failures():
    scope = generator_scope()
    scope.dev.capture_with_gen.side_effect = OSError('capture')
    scope._gen_send_capture()
    assert 'Capture error' in scope.status['text']

    scope = generator_scope()
    scope.dev.capture_with_gen.return_value = b''
    scope._gen_send_capture()
    assert scope.status['text'] == 'Capture returned 0 bytes'

    for decoded in [((), 0), ([], 1), ([[]], 1)]:
        scope = generator_scope()
        scope.dev.capture_with_gen.return_value = b'x'
        with patch.object(console, 'samples_to_channels', return_value=decoded):
            scope._gen_send_capture()
        assert scope.status['text'] == 'No samples decoded from capture'


def accel_scope():
    scope = scope_base()
    scope.dev = MagicMock()
    scope.dev.spi = MagicMock()
    scope.acc_addr = Var('19')
    scope.acc_speed = Var('400000')
    scope.acc_sda_pin = Var('3')
    scope.acc_scl_pin = Var('1')
    scope.win = MagicMock()
    scope._show_accel_result = MagicMock()
    scope._process_decoders = MagicMock()
    scope.wave = MagicMock()
    scope.ch_names = [f'CH{i}' for i in range(NUM_CHANNELS)]
    return scope


def test_accel_read_requires_device_and_handles_no_capture_data():
    scope = accel_scope()
    scope.dev = None
    scope._accel_read(0x0F, 1)
    assert scope.status['text'] == 'No device'

    scope = accel_scope()
    scope.dev.i2c_capture_with_gen.return_value = b''
    with patch.object(console.time, 'sleep'):
        scope._accel_read(0x0F, 1)
    scope._show_accel_result.assert_called_once_with('No data returned')


@pytest.mark.parametrize(
    'reg,read_len,decoded,payload,fragment',
    [
        (0x01, 1, [], [], 'No I2C data decoded'),
        (0x0F, 1, [('BYTE', 0x33)], [0x33], 'LIS3DH'),
        (0x0F, 1, [('BYTE', 0x10)], [0x10], 'expected 0x33'),
        (0x28, 2, [('BYTE', 1)], [0x00, 0x40], 'X axis'),
        (0x2A, 2, [('BYTE', 1)], [0x00, 0xC0], 'Y axis'),
        (0x2C, 2, [('BYTE', 1)], [0x00, 0x40], 'Z axis'),
        (0x10, 2, [('BYTE', 1)], [1, 2], 'Data: 0x01 0x02'),
        (0x10, 1, [('START', None)], [], 'No read data'),
    ],
)
def test_accel_read_decodes_all_result_forms(reg, read_len, decoded, payload, fragment):
    scope = accel_scope()
    scope.dev.i2c_capture_with_gen.return_value = b'data'
    channels = [[0, 1] for _ in range(NUM_CHANNELS)]
    with patch.object(console.time, 'sleep'), \
         patch.object(console, 'samples_to_channels', return_value=(channels, 2)), \
         patch.object(console, 'decode_i2c', return_value=decoded), \
         patch.object(console, 'parse_i2c_read_payload', return_value=payload):
        scope._accel_read(reg, read_len)
    assert fragment in scope._show_accel_result.call_args.args[0]
    assert scope.status['text'] == 'Accel: 2 samples'
    scope.dev.set_pin_map.assert_any_call(1, 25)
    scope.dev.set_pin_map.assert_any_call(3, 24)


def test_accel_read_reports_parse_errors_and_result_widget_state_cycle():
    scope = accel_scope()
    scope.acc_addr = Var('xyz')
    scope._accel_read(0, 1)
    assert 'Error:' in scope._show_accel_result.call_args.args[0]

    scope.acc_result = MagicMock()
    scope._show_accel_result = OLScope._show_accel_result.__get__(scope)
    scope._show_accel_result('answer')
    assert scope.acc_result.config.call_args_list[0].kwargs == {'state': 'normal'}
    scope.acc_result.insert.assert_called_once_with('1.0', 'answer')
    assert scope.acc_result.config.call_args_list[-1].kwargs == {'state': 'disabled'}


def test_accel_read_zero_decoded_samples_does_not_claim_success():
    scope = accel_scope()
    scope.dev.i2c_capture_with_gen.return_value = b'data'
    with patch.object(console.time, 'sleep'), \
         patch.object(console, 'samples_to_channels', return_value=([[]] * NUM_CHANNELS, 0)), \
         patch.object(console, 'decode_i2c', return_value=[]), \
         patch.object(console, 'parse_i2c_read_payload', return_value=[]):
        scope._accel_read(0, 1)
    assert 'text' not in scope.status or not scope.status['text'].startswith('Accel:')
