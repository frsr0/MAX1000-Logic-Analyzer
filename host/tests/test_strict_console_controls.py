from types import SimpleNamespace
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

import app.OLS_Console as console
from app.OLS_Console import MODE_DIGITAL, MODE_MIXED, NUM_CHANNELS, OLScope


class Var:
    def __init__(self, value=None, *, error=None):
        self.value = value
        self.error = error

    def get(self):
        if self.error:
            raise self.error
        return self.value

    def set(self, value):
        self.value = value


def bare_scope():
    scope = OLScope.__new__(OLScope)
    scope.dev = None
    scope._backend = 'SPI'
    scope.ch_data = []
    scope.compress_enabled = False
    scope.capture_running = False
    scope.capture_window = 1000
    scope.capture_type = Var('single')
    scope.mode_cb = Var('16 Digital')
    scope.raw_mode_var = Var(False)
    scope.status = {}
    return scope


@pytest.mark.parametrize('kind,removed,shown', [('rolling', True, False), ('single', False, True)])
def test_capture_type_updates_trigger_visibility(kind, removed, shown):
    scope = bare_scope()
    scope.capture_type = Var(kind)
    scope.rolling_var = Var()
    scope.trig_frame = MagicMock()
    scope._update_rate_info = MagicMock()
    scope._capture_type_changed()
    assert scope.rolling_var.value is (kind == 'rolling')
    assert scope.trig_frame.grid_remove.called is removed
    assert scope.trig_frame.grid.called is shown
    scope._update_rate_info.assert_called_once_with()


def test_channel_visibility_callbacks_validate_available_rows():
    scope = bare_scope()
    scope.wave = SimpleNamespace(channel_visible=[True] * 18, redraw=MagicMock())
    scope.ch_vis_vars = [Var(False)]
    scope.ana_vis_vars = [Var(False), Var(True)]
    scope._toggle_ch_vis(0)
    scope._toggle_ch_vis(99)
    scope._toggle_ana_vis(0)
    scope._toggle_ana_vis(5)
    assert scope.wave.channel_visible[0] is False
    assert scope.wave.channel_visible[NUM_CHANNELS] is False
    assert scope.wave.redraw.call_count == 2

    del scope.wave
    scope._toggle_ch_vis(0)
    scope._toggle_ana_vis(0)


def test_rate_changed_applies_current_value_then_rebuilds_presets():
    scope = bare_scope()
    scope.rate_cb = Var('2MHz')
    scope._apply_rate = MagicMock()
    scope._update_buf_presets = MagicMock()
    scope._on_rate_changed(object())
    scope._apply_rate.assert_called_once_with('2MHz')
    scope._update_buf_presets.assert_called_once_with()


@pytest.mark.parametrize('raw,expected', [('garbage', 1_000_000), (None, 1_000_000), ('0.5MHz', 500_000)])
def test_apply_rate_defaults_and_parses_fractional_rates(raw, expected):
    scope = bare_scope()
    scope.rate_cb = Var()
    scope._get_max_rate = MagicMock(return_value=100_000_000)
    scope._update_time_display = MagicMock()
    scope._update_rate_info = MagicMock()
    scope._update_buf_estimate = MagicMock()
    assert scope._apply_rate(raw) == expected
    assert scope.rate_cb.value == scope._fmt_rate(expected)


@pytest.mark.parametrize('rate,label', [(120_000_000, '120MHz'), (999, '999Hz')])
def test_format_rate_boundary_labels(rate, label):
    assert bare_scope()._fmt_rate(rate) == label


def test_max_rate_ignores_invalid_device_clock_and_attribute_errors():
    scope = bare_scope()
    scope.dev = SimpleNamespace(sys_clk='fast')
    assert scope._get_max_rate() == 100_000_000

    class Broken:
        @property
        def sys_clk(self):
            raise TypeError('bad clock')

    scope.dev = Broken()
    assert scope._get_max_rate() == 100_000_000


@pytest.mark.parametrize(
    'capture_type,compressed,rate,fragment,color',
    [
        ('rolling', False, 60_000_000, 'Rolling limited', '#c00'),
        ('rolling', False, 1_000_000, 'OK for rolling', '#555'),
        ('rolling', True, 1_000_000, 'OK for rolling', '#555'),
        ('single', False, 1_000_000, 'Single-shot OK', '#555'),
    ],
)
def test_rate_info_reports_capacity_state(capture_type, compressed, rate, fragment, color):
    scope = bare_scope()
    scope.capture_type = Var(capture_type)
    scope.compress_enabled = compressed
    scope.rate_info_var = Var()
    scope.rate_info_lbl = MagicMock()
    scope._get_rate = MagicMock(return_value=rate)
    scope._update_rate_info()
    assert fragment in scope.rate_info_var.value
    scope.rate_info_lbl.configure.assert_called_once_with(foreground=color)


def test_compression_setting_handles_variable_failure_and_absence():
    scope = bare_scope()
    scope.compress_var = Var(True)
    scope._on_compress_changed()
    assert scope.compress_enabled is True

    scope.compress_enabled = False
    scope.compress_var = Var(error=RuntimeError('tk disposed'))
    scope._on_compress_changed()
    assert scope.compress_enabled is False

    scope.compress_var = None
    scope.compress_enabled = 1
    scope._on_compress_changed()
    assert scope.compress_enabled is True


def test_compression_mode_defaults_to_current_capture_mode():
    scope = bare_scope()
    scope.compress_enabled = True
    scope.mode_cb = Var('16 Digital')
    assert scope._should_enable_compression_for_capture(True) is True
    assert scope._should_enable_compression_for_capture(False) is False
    assert scope._should_enable_compression_for_capture(True, raw=True) is False
    assert scope._should_enable_compression_for_capture(True, mode=99) is False


def test_buffer_presets_include_sizes_and_custom_entry():
    scope = bare_scope()
    scope.rolling_buf = {}
    scope._get_rate = MagicMock(return_value=1_000_000)
    scope._update_buf_estimate = MagicMock()
    scope._update_buf_presets()
    labels = scope.rolling_buf['values']
    assert labels[0].startswith('1 ms (')
    assert labels[-1] == 'Custom'
    assert len(labels) == 7


def test_scan_ports_selects_first_only_when_no_selection():
    scope = bare_scope()
    scope.port_cb = MagicMock()
    scope.port_cb.get.return_value = ''
    ports = [SimpleNamespace(device='COM7'), SimpleNamespace(device='COM8')]
    with patch.object(console.serial.tools.list_ports, 'comports', return_value=ports):
        scope._scan_ports()
    scope.port_cb.set.assert_called_once_with('COM7')
    assert scope.status['text'] == 'Found 2 port(s)'

    scope.port_cb.reset_mock()
    scope.port_cb.get.return_value = 'COM8'
    with patch.object(console.serial.tools.list_ports, 'comports', return_value=[]):
        scope._scan_ports()
    scope.port_cb.set.assert_not_called()
    assert scope.status['text'] == 'Found 0 port(s)'


@pytest.mark.parametrize('found', [True, False])
def test_auto_connect_routes_discovery_result(found):
    scope = bare_scope()
    scope._connect = MagicMock()
    scope._update_ui_state = MagicMock()
    with patch.object(console, 'find_spi_device', return_value=found):
        scope._auto_connect()
    if found:
        scope._connect.assert_called_once_with()
        scope._update_ui_state.assert_not_called()
    else:
        assert 'No SPI device' in scope.status['text']
        scope._update_ui_state.assert_called_once_with(connected=False)


class ConnectDevice:
    def __init__(self, metadata=b'meta', *, queue=None, fail=None):
        self.metadata = metadata
        self.fail = fail
        self.debug_ch0_enabled = False
        self.spi = None if queue is None else SimpleNamespace(dev=SimpleNamespace(getQueueStatus=lambda: queue))
        self.closed = False

    def open(self):
        if self.fail:
            raise self.fail

    def reset(self):
        pass

    def get_metadata(self):
        return self.metadata

    def set_debug_ch0(self, *_):
        pass

    def set_bitbang_pwm(self, *_):
        pass

    def close(self):
        self.closed = True


@pytest.mark.parametrize('queue', [None, 7])
def test_connect_success_verifies_metadata_and_updates_ui(queue, capsys):
    scope = bare_scope()
    scope.debug_ch0_var = Var(True)
    scope._update_ui_state = MagicMock()
    dev = ConnectDevice(queue=queue)
    with patch.object(console, 'OLSDeviceSPI', return_value=dev):
        scope._connect()
    assert scope.dev is dev
    assert 'Connected via SPI' in scope.status['text']
    scope._update_ui_state.assert_called_once_with(connected=True)
    output = capsys.readouterr().out
    assert ('queue=7' in output) is (queue == 7)


def test_connect_supports_device_without_optional_debug_method():
    scope = bare_scope()
    scope.debug_ch0_var = Var(False)
    scope._update_ui_state = MagicMock()
    dev = ConnectDevice()
    original = ConnectDevice.set_debug_ch0
    del ConnectDevice.set_debug_ch0
    try:
        with patch.object(console, 'OLSDeviceSPI', return_value=dev):
            scope._connect()
    finally:
        ConnectDevice.set_debug_ch0 = original
    assert 'Connected via SPI' in scope.status['text']


@pytest.mark.parametrize('device,error_text', [(ConnectDevice(metadata=b''), 'FPGA not responding'), (ConnectDevice(fail=OSError('USB')), 'USB')])
def test_connect_failure_reports_and_closes_unresponsive_device(device, error_text):
    scope = bare_scope()
    scope.debug_ch0_var = Var(False)
    scope._update_ui_state = MagicMock()
    with patch.object(console, 'OLSDeviceSPI', return_value=device), patch.object(console, 'messagebox') as mb:
        scope._connect()
    assert error_text in scope.status['text']
    mb.showerror.assert_called_once()
    if device.metadata == b'' and not device.fail:
        assert device.closed is True
        assert scope.dev is None


def test_debug_pwm_handles_absent_device_live_deferral_invalid_values_and_error():
    scope = bare_scope()
    scope.debug_ch0_var = Var(True)
    scope.debug_ch0_freq_var = Var('bad')
    scope.debug_ch0_duty_var = Var('bad')
    scope._debug_ch0_changed()

    dev = SimpleNamespace(_pending_debug_enable=False)
    scope.dev = dev
    scope.capture_running = True
    scope._debug_ch0_changed()
    assert (dev.debug_ch0_enabled, dev._pending_debug_enable, dev._pending_debug_freq, dev._pending_debug_duty) == (
        True, True, 100000, 50)

    scope.capture_running = False
    dev.set_bitbang_pwm = MagicMock(side_effect=RuntimeError('PWM'))
    scope.debug_ch0_freq_var = Var('2000')
    scope.debug_ch0_duty_var = Var('120')
    scope._debug_ch0_changed()
    dev.set_bitbang_pwm.assert_called_once_with(True, freq_hz=2000, duty_pct=99)
    assert 'PWM' in scope.status['text']

    scope.dev = SimpleNamespace()
    scope._debug_ch0_changed()


def test_schmitt_apply_success_missing_capability_and_error():
    scope = bare_scope()
    scope.schmitt_var = Var(True)
    scope.schmitt_thresh_var = Var('4')
    scope._apply_schmitt()

    scope.dev = SimpleNamespace()
    scope._apply_schmitt()

    scope.dev.set_schmitt = MagicMock()
    scope._apply_schmitt()
    scope.dev.set_schmitt.assert_called_once_with(True, 4)

    scope.dev.set_schmitt.side_effect = RuntimeError('filter')
    scope._apply_schmitt()
    assert 'filter' in scope.status['text']


def test_debug_sync_disconnect_and_connection_widgets():
    scope = bare_scope()
    scope.debug_ch0_var = Var(True)
    scope._apply_debug_ch0_setting()
    scope.dev = SimpleNamespace(debug_ch0_enabled=False, close=MagicMock())
    scope._apply_debug_ch0_setting()
    assert scope.dev.debug_ch0_enabled is True

    scope.port_frame = MagicMock()
    scope.port_sep = MagicMock()
    dev = scope.dev
    scope._update_ui_state(True)
    scope._update_ui_state(False)
    scope._disconnect()
    dev.close.assert_called_once_with()
    assert scope.dev is None
    assert scope.status['text'] == 'Disconnected'

    no_widgets = bare_scope()
    no_widgets._update_ui_state(True)
    no_widgets._disconnect()
    assert no_widgets.status['text'] == 'Disconnected'


@pytest.mark.parametrize('label,shown', [('16 Dig + 2 Ana', True), ('16 Digital', False)])
def test_mode_changed_updates_analog_note_and_dependent_labels(label, shown):
    scope = bare_scope()
    scope.mode_cb = Var(label)
    scope._analog_info = MagicMock()
    scope._update_rate_info = MagicMock()
    scope._update_buf_presets = MagicMock()
    scope._mode_changed()
    assert scope._analog_info.grid.called is shown
    assert scope._analog_info.grid_remove.called is (not shown)


def _widget():
    widget = MagicMock()
    widget.get.return_value = ''
    return widget


def test_add_decoder_ui_builds_slot_and_protocol_callback():
    scope = bare_scope()
    scope.decoder_frame = MagicMock()
    scope.decoder_ui = []
    widgets = {name: [] for name in ('LabelFrame', 'Frame', 'Label', 'Checkbutton', 'Combobox', 'Entry')}

    def factory(name):
        def make(*_a, **_kw):
            obj = _widget()
            widgets[name].append(obj)
            return obj
        return make

    fake_ttk = SimpleNamespace(**{name: factory(name) for name in widgets})
    with patch.object(console, 'ttk', fake_ttk), patch.object(console.tk, 'BooleanVar', return_value=Var(False)):
        scope._add_decoder_ui(2)
        _, values = scope.decoder_ui[0]
        proto = values['proto']
        callback = proto.bind.call_args.args[1]
        proto.get.return_value = 'I2C'
        callback()
        values['sda'].pack.assert_called()
        values['baud'].pack_forget.assert_called()
        proto.get.return_value = 'UART'
        callback()
        values['sda'].pack_forget.assert_called()
        values['baud'].pack.assert_called()


def decoder_vars(*, enabled, source='0', proto='UART'):
    return {
        'en': Var(enabled), 'src': Var(source), 'proto': Var(proto),
        'baud': Var('9600'), 'thresh': Var('3'), 'sda': Var('3'), 'scl': Var('1'),
    }


def test_apply_decoders_reads_enabled_slots_and_redraws_existing_capture():
    scope = bare_scope()
    scope.dec_thresh = Var('5')
    scope.filter_vars = [Var(True), Var(False)]
    scope.decoder_ui = [
        (MagicMock(), decoder_vars(enabled=False)),
        (MagicMock(), decoder_vars(enabled=True, source='3_f', proto='I2C')),
        (MagicMock(), decoder_vars(enabled=True, source='2', proto='SPI')),
    ]
    scope.ch_data = [[0, 1]]
    scope._process_decoders = MagicMock()
    scope.wave = SimpleNamespace(redraw=MagicMock())
    scope._apply_decoders()
    assert scope.filter_threshold == 5
    assert scope.filter_enabled == [True, False]
    assert len(scope.decoder_slots) == 2
    assert scope.decoder_slots[0]['src_idx'] == 3
    assert scope.decoder_slots[0]['src_is_filtered'] is True
    assert scope.decoder_slots[0]['baud'] == 0
    assert scope.decoder_slots[1]['baud'] == 9600
    scope._process_decoders.assert_called_once_with()
    scope.wave.redraw.assert_called_once_with()

    scope.ch_data = []
    scope._process_decoders.reset_mock()
    scope._apply_decoders()
    scope._process_decoders.assert_not_called()


def test_rate_buffer_and_time_parsers_cover_invalid_and_plain_values():
    scope = bare_scope()
    scope.rate_cb = Var('nonsense')
    assert scope._get_rate() == 1_000_000
    scope.rate_cb = Var(None)
    assert scope._get_rate() == 1_000_000
    assert scope._parse_buf_ms('25') == 25
    assert scope._parse_buf_ms('bad ms') == 100

    scope.time_var = Var('0.002')
    scope.rate_cb = Var('1MHz')
    scope._update_time_display = MagicMock()
    scope._time_changed()
    assert scope._nsamp == 2000

    scope.time_var = Var()
    scope._nsamp = 2_000_000
    scope.rate_cb = Var('1MHz')
    scope._update_time_display = OLScope._update_time_display.__get__(scope)
    scope._update_time_display()
    assert scope.time_var.value == '2.000 s'


def test_build_capture_tab_falls_back_when_tk_variable_creation_fails():
    scope = bare_scope()
    scope.win = MagicMock()
    scope.decoder_ui = []
    def boolean_var(*args, **kwargs):
        if 'master' in kwargs:
            raise RuntimeError('no Tcl')
        return Var(kwargs.get('value'))

    with patch.object(console.tk, 'BooleanVar', side_effect=boolean_var):
        scope._build_capture_tab(MagicMock())
    assert scope.compress_var is None


def test_trigger_widget_walk_ignores_non_frames_and_non_checkbuttons():
    class Frame:
        def __init__(self, children=()): self.children = list(children)
        def winfo_children(self): return self.children

    class Checkbutton:
        def configure(self, **kwargs): self.state = kwargs['state']

    check = Checkbutton()
    nested = Frame([object(), check])
    scope = bare_scope()
    scope.trig_mode = Var('Rising')
    scope.trig_ch_vars = [Var(True)]
    scope.trig_frame = Frame([object(), nested])
    fake_ttk = SimpleNamespace(Frame=Frame, Checkbutton=Checkbutton)
    with patch.object(console, 'ttk', fake_ttk):
        scope._trig_mode_changed()
    assert check.state == 'normal'


@pytest.mark.parametrize('new_ms,old_ns,stops', [(0, 1000, False), (1, 1000, False), (0.5, 1000, False), (2, 1000, True)])
def test_rolling_buffer_change_restarts_only_for_material_valid_change(new_ms, old_ns, stops):
    scope = bare_scope()
    scope.capture_running = True
    scope.capture_type = Var('rolling')
    scope.rolling_buf_var = Var(str(new_ms))
    scope.capture_window = old_ns
    scope._get_rate = MagicMock(return_value=1_000_000)
    scope._stop_capture = MagicMock()
    scope._capture = MagicMock()
    scope.win = MagicMock()
    scope._on_rolling_buf_change()
    assert scope._stop_capture.called is stops
    assert scope.win.after.called is stops

    scope.capture_running = False
    scope._stop_capture.reset_mock()
    scope._on_rolling_buf_change()
    scope._stop_capture.assert_not_called()
