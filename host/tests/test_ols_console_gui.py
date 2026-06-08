import sys, struct, os, json, zipfile, io
from unittest.mock import MagicMock, patch, ANY, PropertyMock

sys.modules['serial'] = MagicMock()
sys.modules['serial.tools'] = MagicMock()
sys.modules['serial.tools.list_ports'] = MagicMock()

from app.OLS_Console import OLScope, WaveformDisplay, NUM_CHANNELS
from app.OLS_Console import ANALOG_MODE_DIGITAL8, ANALOG_MODE_MIXED1, ANALOG_MODE_MIXED2
from app.OLS_Console import ANALOG_MODE_ANALOG1, ANALOG_MODE_ANALOG2
from app.OLS_Console import CMD_DEBUG_CH0_ON, CMD_DEBUG_CH0_OFF


def _make_scope(backend='UART'):
    root = MagicMock()
    scope = OLScope(backend=backend, root=root)
    scope.ch_data = [[0, 1, 0, 1, 0] * 40 for _ in range(NUM_CHANNELS)]
    scope.ch_names = [f"CH{i}" for i in range(NUM_CHANNELS)]
    scope.samplerate = 1_000_000
    return scope


# ====================================================================
# WaveformDisplay
# ====================================================================

class TestWaveformDisplay:
    def _make_wave(self):
        parent = MagicMock()
        wave = WaveformDisplay(parent)
        wave.winfo_width = MagicMock(return_value=500)
        wave.ch_data = [[0, 1, 0, 1, 0] * 20 for _ in range(8)]
        wave.ch_names = [f"CH{i}" for i in range(8)]
        wave.samplerate = 1_000_000
        wave.num_samples = 100
        return wave

    def test_set_scale_clamps_min(self):
        w = self._make_wave()
        w.set_scale(0.1)
        assert w.px_scale == w.MIN_PX_PER_SAMPLE

    def test_set_scale_clamps_max(self):
        w = self._make_wave()
        w.set_scale(100)
        assert w.px_scale == w.MAX_PX_PER_SAMPLE

    def test_set_scale_passthrough(self):
        w = self._make_wave()
        w.set_scale(3.0)
        assert w.px_scale == 3.0

    def test_set_scale_adjusts_scroll(self):
        w = self._make_wave()
        w.scroll_x = 50
        w.set_scale(4.0)
        assert w.scroll_x >= 0

    def test_total_height(self):
        w = self._make_wave()
        h = w.total_height()
        assert h > 0
        assert isinstance(h, int)

    def test_draw_incremental_noop_upto_le_drawn(self):
        w = self._make_wave()
        w._drawn_to = 50
        result = w.draw_incremental(30)
        assert result is None

    def test_draw_incremental_noop_no_data(self):
        w = self._make_wave()
        w.ch_data = []
        result = w.draw_incremental(10)
        assert result is None

    def test_draw_incremental_extends(self):
        w = self._make_wave()
        w._drawn_to = 10
        w.num_samples = 100
        w.draw_incremental(50)
        assert w._drawn_to == 50

    def test_load_resets_state(self):
        w = self._make_wave()
        w.marker1 = 5
        w.load([[0] * 30], ["CH0"], samplerate=500000)
        assert w.marker1 is None
        assert w.num_samples == 30
        assert w.samplerate == 500000

    def test_get_decode_y(self):
        w = self._make_wave()
        assert w.get_decode_y() == w.total_height()


# ====================================================================
# OLScope UI parsers
# ====================================================================

class TestOLScopeGetters:
    def test_get_rate_khz(self):
        scope = _make_scope()
        scope.rate_cb = MagicMock()
        scope.rate_cb.get.return_value = '500kHz'
        assert scope._get_rate() == 500000

    def test_get_rate_mhz(self):
        scope = _make_scope()
        scope.rate_cb = MagicMock()
        scope.rate_cb.get.return_value = '1MHz'
        assert scope._get_rate() == 1000000

    def test_get_rate_12mhz(self):
        scope = _make_scope()
        scope.rate_cb = MagicMock()
        scope.rate_cb.get.return_value = '12MHz'
        assert scope._get_rate() == 12000000

    def test_get_samples(self):
        scope = _make_scope()
        scope.samp_cb = MagicMock()
        scope.samp_cb.get.return_value = '5000'
        assert scope._get_samples() == 5000

    def test_get_capture_mode_digital(self):
        scope = _make_scope()
        scope.mode_cb = MagicMock()
        scope.mode_cb.get.return_value = 'Digital'
        assert scope._get_capture_mode() == ANALOG_MODE_DIGITAL8

    def test_get_capture_mode_mixed1(self):
        scope = _make_scope()
        scope.mode_cb = MagicMock()
        scope.mode_cb.get.return_value = 'Mixed 1'
        assert scope._get_capture_mode() == ANALOG_MODE_MIXED1

    def test_get_capture_mode_mixed2(self):
        scope = _make_scope()
        scope.mode_cb = MagicMock()
        scope.mode_cb.get.return_value = 'Mixed 2'
        assert scope._get_capture_mode() == ANALOG_MODE_MIXED2

    def test_get_capture_mode_analog1(self):
        scope = _make_scope()
        scope.mode_cb = MagicMock()
        scope.mode_cb.get.return_value = 'Analogue 1'
        assert scope._get_capture_mode() == ANALOG_MODE_ANALOG1

    def test_get_capture_mode_analog2(self):
        scope = _make_scope()
        scope.mode_cb = MagicMock()
        scope.mode_cb.get.return_value = 'Analogue 2'
        assert scope._get_capture_mode() == ANALOG_MODE_ANALOG2

    def test_get_capture_mode_unknown_defaults_to_digital(self):
        scope = _make_scope()
        scope.mode_cb = MagicMock()
        scope.mode_cb.get.return_value = 'Foobar'
        assert scope._get_capture_mode() == ANALOG_MODE_DIGITAL8


class TestOLScopeUpdateTimeDisplay:
    def test_us(self):
        scope = _make_scope()
        scope.rate_cb = MagicMock()
        scope.rate_cb.get.return_value = '12MHz'
        scope.samp_cb = MagicMock()
        scope.samp_cb.get.return_value = '500'
        scope.time_var = MagicMock()
        scope._update_time_display()
        args = scope.time_var.set.call_args[0][0]
        assert 'us' in args

    def test_ms(self):
        scope = _make_scope()
        scope.rate_cb = MagicMock()
        scope.rate_cb.get.return_value = '1MHz'
        scope.samp_cb = MagicMock()
        scope.samp_cb.get.return_value = '5000'
        scope.time_var = MagicMock()
        scope._update_time_display()
        args = scope.time_var.set.call_args[0][0]
        assert 'ms' in args

    def test_s(self):
        scope = _make_scope()
        scope.rate_cb = MagicMock()
        scope.rate_cb.get.return_value = '1MHz'
        scope.samp_cb = MagicMock()
        scope.samp_cb.get.return_value = '500000'
        scope.time_var = MagicMock()
        scope._update_time_display()
        args = scope.time_var.set.call_args[0][0]
        assert 's' in args


class TestOLScopeUpdateBufEstimate:
    def test_with_valid_value(self):
        scope = _make_scope()
        scope.rolling_buf_var = MagicMock()
        scope.rolling_buf_var.get.return_value = '50000'
        scope.rate_cb = MagicMock()
        scope.rate_cb.get.return_value = '1MHz'
        scope.buf_estimate_lbl = MagicMock()
        scope._update_buf_estimate()
        assert scope.buf_estimate_lbl.__setitem__.called

    def test_with_invalid_value(self):
        scope = _make_scope()
        scope.rolling_buf_var = MagicMock()
        scope.rolling_buf_var.get.return_value = 'abc'
        scope.buf_estimate_lbl = MagicMock()
        scope._update_buf_estimate()
        assert scope.buf_estimate_lbl.__setitem__.called

    def test_with_zero(self):
        scope = _make_scope()
        scope.rolling_buf_var = MagicMock()
        scope.rolling_buf_var.get.return_value = '0'
        scope.buf_estimate_lbl = MagicMock()
        scope._update_buf_estimate()
        scope.buf_estimate_lbl.__setitem__.assert_called_with('text', "")


class TestOLScopeTimeChanged:
    def test_us_conversion(self):
        scope = _make_scope()
        scope.time_var = MagicMock()
        scope.time_var.get.return_value = '500us'
        scope.rate_cb = MagicMock()
        scope.rate_cb.get.return_value = '1MHz'
        scope.samp_cb = MagicMock()
        scope._time_changed()
        assert scope.samp_cb.set.called

    def test_ms_conversion(self):
        scope = _make_scope()
        scope.time_var = MagicMock()
        scope.time_var.get.return_value = '5ms'
        scope.rate_cb = MagicMock()
        scope.rate_cb.get.return_value = '1MHz'
        scope.samp_cb = MagicMock()
        scope._time_changed()
        assert scope.samp_cb.set.called

    def test_s_conversion(self):
        scope = _make_scope()
        scope.time_var = MagicMock()
        scope.time_var.get.return_value = '1s'
        scope.rate_cb = MagicMock()
        scope.rate_cb.get.return_value = '1MHz'
        scope.samp_cb = MagicMock()
        scope._time_changed()
        assert scope.samp_cb.set.called

    def test_bad_input_reverts(self):
        scope = _make_scope()
        scope.time_var = MagicMock()
        scope.time_var.get.return_value = 'not-a-number'
        scope.rate_cb = MagicMock()
        scope.rate_cb.get.return_value = '1MHz'
        scope.samp_cb = MagicMock()
        scope.time_var = MagicMock()
        scope._time_changed()


# ====================================================================
# OLScope UI toggles
# ====================================================================

class TestOLScopeTrigModeChanged:
    def test_off_disables_checks(self):
        scope = _make_scope()
        scope.trig_mode = MagicMock()
        scope.trig_mode.get.return_value = 'Off'
        scope.trig_ch_vars = [MagicMock() for _ in range(16)]
        scope.trig_frame = MagicMock()
        scope.trig_frame.winfo_children.return_value = []
        scope._trig_mode_changed()
        for v in scope.trig_ch_vars:
            v.set.assert_called_with(False)

    def test_rising_enables_checks(self):
        scope = _make_scope()
        scope.trig_mode = MagicMock()
        scope.trig_mode.get.return_value = 'Rising'
        scope.trig_ch_vars = [MagicMock() for _ in range(16)]
        scope.trig_frame = MagicMock()
        scope.trig_frame.winfo_children.return_value = []
        scope._trig_mode_changed()
        assert scope.trig_frame.winfo_children.called

    def test_debug_ch0_changed_updates_device(self):
        scope = _make_scope()
        scope.dev = MagicMock()
        scope.debug_ch0_var = MagicMock()

        scope.debug_ch0_var.get.return_value = True
        scope._debug_ch0_changed()
        scope.dev.set_debug_ch0.assert_called_once_with(True)

        scope.dev.reset_mock()
        scope.debug_ch0_var.get.return_value = False
        scope._debug_ch0_changed()
        scope.dev.set_debug_ch0.assert_called_once_with(False)

    def test_apply_debug_ch0_setting_syncs_to_device(self):
        scope = _make_scope()
        scope.dev = MagicMock()
        scope.dev.debug_ch0_enabled = True
        scope.debug_ch0_var = MagicMock()

        scope.debug_ch0_var.get.return_value = False
        scope._apply_debug_ch0_setting()
        assert scope.dev.debug_ch0_enabled is False

        scope.debug_ch0_var.get.return_value = True
        scope._apply_debug_ch0_setting()
        assert scope.dev.debug_ch0_enabled is True


class TestOLScopeGenShowProtoFields:
    def test_uart(self):
        scope = _make_scope()
        scope.gen_proto = MagicMock()
        scope.gen_proto.get.return_value = 'UART'
        scope.gen_func_lbl = MagicMock()
        scope.gen_func = MagicMock()
        scope.gen_addr_lbl = MagicMock()
        scope.gen_addr = MagicMock()
        scope.gen_scl_lbl = MagicMock()
        scope.gen_scl_pin = MagicMock()
        scope.gen_tx_lbl = MagicMock()
        scope._gen_show_proto_fields()
        scope.gen_func_lbl.grid_remove.assert_called_once()
        scope.gen_addr_lbl.grid_remove.assert_called_once()
        scope.gen_scl_lbl.grid_remove.assert_called_once()
        scope.gen_tx_lbl.configure.assert_called_once()

    def test_i2c(self):
        scope = _make_scope()
        scope.gen_proto = MagicMock()
        scope.gen_proto.get.return_value = 'I2C'
        scope.gen_func_lbl = MagicMock()
        scope.gen_func = MagicMock()
        scope.gen_addr_lbl = MagicMock()
        scope.gen_addr = MagicMock()
        scope.gen_scl_lbl = MagicMock()
        scope.gen_scl_pin = MagicMock()
        scope.gen_tx_lbl = MagicMock()
        scope._gen_show_proto_fields()
        scope.gen_func_lbl.grid_remove.assert_called_once()
        scope.gen_addr_lbl.grid.assert_called_once()
        scope.gen_scl_lbl.grid.assert_called_once()
        scope.gen_tx_lbl.configure.assert_called_with(text='TX Pin (SDA):')

    def test_modbus(self):
        scope = _make_scope()
        scope.gen_proto = MagicMock()
        scope.gen_proto.get.return_value = 'Modbus'
        scope.gen_func_lbl = MagicMock()
        scope.gen_func = MagicMock()
        scope.gen_addr_lbl = MagicMock()
        scope.gen_addr = MagicMock()
        scope.gen_scl_lbl = MagicMock()
        scope.gen_scl_pin = MagicMock()
        scope.gen_tx_lbl = MagicMock()
        scope._gen_show_proto_fields()
        scope.gen_func_lbl.grid.assert_called_once()
        scope.gen_addr_lbl.grid.assert_called_once()
        scope.gen_scl_lbl.grid_remove.assert_called_once()


# ====================================================================
# OLScope _process_decoders
# ====================================================================

class TestOLScopeProcessDecoders:
    def test_empty_ch_data_returns_early(self):
        scope = _make_scope()
        scope.ch_data = []
        scope._process_decoders()
        assert scope.ch_data == []

    def test_no_filters_no_slots_leaves_data_unchanged(self):
        scope = _make_scope()
        orig_len = len(scope.ch_data)
        scope.filter_enabled = [False] * 16
        scope.decoder_slots = []
        scope._process_decoders()
        assert len(scope.ch_data) == orig_len

    def test_filter_appends_filtered_channel(self):
        scope = _make_scope()
        scope.filter_enabled = [True] + [False] * 15
        scope.decoder_slots = []
        scope._process_decoders()
        assert len(scope.ch_data) > 16
        assert scope.ch_names[16].endswith('_f')

    def test_uart_decoder_appends_signal(self):
        scope = _make_scope()
        data = [0] * 200 + [0, 1, 0, 1, 0, 1, 0, 1, 0, 0] * 20
        scope.ch_data = [data[:] for _ in range(16)]
        scope.filter_enabled = [False] * 16
        scope.decoder_slots = [{
            'enabled': True,
            'src_str': '0',
            'src_idx': 0,
            'src_is_filtered': False,
            'proto': 'UART',
            'baud': 115200,
            'thresh': 0,
            'sda_idx': 3,
            'scl_idx': 1,
        }]
        scope.samplerate = 1000000
        scope._process_decoders()
        assert len(scope.ch_data) == 17

    def test_i2c_decoder_appends_signal(self):
        scope = _make_scope()
        data = [1] * 200 + [1, 0, 1, 0, 1, 0] * 20
        scope.ch_data = [data[:] for _ in range(16)]
        scope.filter_enabled = [False] * 16
        scope.decoder_slots = [{
            'enabled': True,
            'src_str': '0',
            'src_idx': 0,
            'src_is_filtered': False,
            'proto': 'I2C',
            'baud': 0,
            'thresh': 0,
            'sda_idx': 3,
            'scl_idx': 1,
        }]
        scope.samplerate = 1000000
        scope._process_decoders()
        assert len(scope.ch_data) == 17

    def test_spi_decoder_appends_signal(self):
        scope = _make_scope()
        data = [0] * 200 + [0, 0, 1, 1, 0, 0, 1, 1, 0] * 20
        scope.ch_data = [data[:] for _ in range(16)]
        scope.filter_enabled = [False] * 16
        scope.decoder_slots = [{
            'enabled': True,
            'src_str': '0',
            'src_idx': 0,
            'src_is_filtered': False,
            'proto': 'SPI',
            'baud': 0,
            'thresh': 0,
            'sda_idx': 3,
            'scl_idx': 1,
        }]
        scope.samplerate = 1000000
        scope._process_decoders()
        assert len(scope.ch_data) == 17

    def test_disabled_slot_skipped(self):
        scope = _make_scope()
        scope.filter_enabled = [False] * 16
        scope.decoder_slots = [{
            'enabled': False,
            'src_str': '0',
            'src_idx': 0,
            'src_is_filtered': False,
            'proto': 'UART',
            'baud': 115200,
            'thresh': 0,
            'sda_idx': 3,
            'scl_idx': 1,
        }]
        scope._process_decoders()
        assert len(scope.ch_data) == 16

    def test_filtered_channel_as_decoder_source(self):
        scope = _make_scope()
        scope.filter_enabled = [True] + [False] * 15
        scope.decoder_slots = [{
            'enabled': True,
            'src_str': 'CH0_f',
            'src_idx': 16,
            'src_is_filtered': True,
            'proto': 'UART',
            'baud': 115200,
            'thresh': 0,
            'sda_idx': 3,
            'scl_idx': 1,
        }]
        scope.samplerate = 1000000
        scope._process_decoders()
        assert len(scope.ch_data) >= 17


# ====================================================================
# OLScope exports
# ====================================================================

class TestOLScopeExports:
    def test_export_size_label_with_data(self):
        scope = _make_scope()
        scope.captured_bytes = b'\x00' * 400
        scope.export_size_lbl = MagicMock()
        scope._update_export_size_label()
        args = scope.export_size_lbl.__setitem__.call_args
        assert args is not None
        assert 'samples' in str(args)

    def test_export_size_label_without_data(self):
        scope = _make_scope()
        scope.captured_bytes = b''
        scope.export_size_lbl = MagicMock()
        scope._update_export_size_label()
        scope.export_size_lbl.__setitem__.assert_called_with(
            'text', "Captured: 0 samples (0 MB)")

    def test_export_ols_no_data(self):
        scope = _make_scope()
        scope.captured_bytes = b''
        scope._export_ols()
        tk = sys.modules['tkinter']
        tk.messagebox.showinfo.assert_called_once()

    def test_export_ols_writes_file(self, tmpdir):
        scope = _make_scope()
        scope.captured_bytes = bytes(range(64))
        scope.samplerate = 1000000
        fpath = str(tmpdir.join('test.ols'))
        tk = sys.modules['tkinter']
        tk.filedialog.asksaveasfilename.return_value = fpath
        scope._export_ols()
        assert os.path.exists(fpath)
        with open(fpath) as f:
            content = f.read()
        assert 'Rate: 1000000' in content
        assert 'Channels: 16' in content

    def test_export_sr_no_data(self):
        scope = _make_scope()
        scope.captured_bytes = b''
        scope._export_sr()
        assert True

    def test_export_sr_writes_zip(self, tmpdir):
        scope = _make_scope()
        scope.captured_bytes = bytes(range(80))
        scope.samplerate = 1000000
        fpath = str(tmpdir.join('test.sr'))
        tk = sys.modules['tkinter']
        tk.filedialog.asksaveasfilename.return_value = fpath
        scope._export_sr()
        assert os.path.exists(fpath)
        with zipfile.ZipFile(fpath, 'r') as zf:
            names = zf.namelist()
        assert 'metadata' in names
        assert 'logic-1' in names

    def test_export_clip_no_data(self):
        scope = _make_scope()
        scope.captured_bytes = b''
        scope._export_clip()
        assert True

    def test_export_clip_with_data(self):
        scope = _make_scope()
        scope.captured_bytes = bytes(range(80))
        scope.samplerate = 1000000
        scope.decoded_uart = []
        scope.win = MagicMock()
        scope._export_clip()
        assert scope.win.clipboard_clear.called
        assert scope.win.clipboard_append.called

    def test_export_marker_range_no_markers(self):
        scope = _make_scope()
        scope.wave = MagicMock()
        scope.wave.marker1 = None
        scope.wave.marker2 = None
        scope._export_marker_range()
        tk = sys.modules['tkinter']
        tk.messagebox.showinfo.assert_called()

    def test_export_marker_range_no_data(self):
        scope = _make_scope()
        scope.wave = MagicMock()
        scope.wave.marker1 = 10
        scope.wave.marker2 = 20
        scope.captured_bytes = b''
        scope._export_marker_range()
        tk = sys.modules['tkinter']
        tk.messagebox.showinfo.assert_called()

    def test_export_marker_range_too_small(self):
        scope = _make_scope()
        scope.wave = MagicMock()
        scope.wave.marker1 = 0
        scope.wave.marker2 = 0
        scope.captured_bytes = bytes(range(80))
        scope.samplerate = 1000000
        scope._export_marker_range()
        tk = sys.modules['tkinter']
        tk.messagebox.showinfo.assert_called()

    def test_export_marker_range_writes_file(self, tmpdir):
        scope = _make_scope()
        scope.wave = MagicMock()
        scope.wave.marker1 = 5
        scope.wave.marker2 = 15
        scope.captured_bytes = bytes(range(80))
        scope.samplerate = 1000000
        scope.capture_stride = 4
        fpath = str(tmpdir.join('range.ols'))
        tk = sys.modules['tkinter']
        tk.filedialog.asksaveasfilename.return_value = fpath
        scope._export_marker_range()
        assert os.path.exists(fpath)
