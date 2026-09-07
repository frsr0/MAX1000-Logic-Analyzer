from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import app.OLS_Console as console
from app.OLS_Console import MODE_DIGITAL, MODE_MIXED, NUM_CHANNELS, OLScope, analog_frame_stride


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


class Event:
    def __init__(self, set_after_clear=False):
        self.flag = False
        self.set_after_clear = set_after_clear

    def clear(self):
        self.flag = self.set_after_clear

    def set(self):
        self.flag = True

    def is_set(self):
        return self.flag


class ImmediateThread:
    def __init__(self, target, daemon):
        self.target = target
        self.daemon = daemon

    def start(self):
        self.target()


class Device:
    def __init__(self):
        self._stride = 2
        self._raw_flags = 7
        self.fast_mode_enabled = False
        self._pending_gen = None
        self.capture_data = bytes(range(12))
        self.rolling_items = []
        self.calls = []

    def reset(self): self.calls.append(('reset',))
    def fast_mode(self, enabled): self.calls.append(('fast_mode', enabled))
    def raw_mode(self, enabled): self.calls.append(('raw_mode', enabled))
    def trigger_decode(self, **kw): self.calls.append(('trigger_decode', kw))
    def set_compression_enabled(self, enabled): self.calls.append(('compression', enabled))
    def set_analog_config(self, mode): self.calls.append(('analog', mode))
    def rolling_capture(self, **kw):
        self.calls.append(('rolling', kw))
        return iter(self.rolling_items)
    def capture(self, **kw):
        self.calls.append(('capture', kw))
        return self.capture_data
    def apply_protocol_trigger(self, data, rate, stride):
        self.calls.append(('protocol', rate, stride))
        return b'filtered', 1
    def protocol_trigger(self):
        return {'enabled': True}


def capture_scope(*, kind='single', mode=MODE_DIGITAL, nsamp=8, device=None):
    scope = OLScope.__new__(OLScope)
    scope.dev = device or Device()
    scope._backend = 'SPI'
    scope.capture_running = False
    scope.capture_type = Var(kind)
    scope.mode_cb = Var('16 Dig + 2 Ana' if mode == MODE_MIXED else '16 Digital')
    scope.raw_mode_var = Var(False)
    scope.compress_enabled = False
    scope.rolling_buf_var = Var('100 ms')
    scope.capture_window = 100
    scope._nsamp = nsamp
    scope._get_rate = MagicMock(return_value=1_000_000)
    scope._get_max_rate = MagicMock(return_value=200_000_000)
    scope.trig_mode = Var('Off')
    scope.trig_ch_vars = [Var(False) for _ in range(NUM_CHANNELS)]
    scope.wave = SimpleNamespace(
        ch_data=[], num_samples=0, _drawn_to=0, delete=MagicMock(),
        winfo_width=MagicMock(return_value=500), LABEL_WIDTH=40,
    )
    scope.stop_evt = Event()
    scope.stop_btn = MagicMock()
    scope.status = {}
    scope.win = MagicMock()
    scope.proto_trig_var = Var(False)
    scope.proto_match = Var('57')
    scope.proto_ch = Var('0')
    scope.proto_baud = Var('115200')
    scope.capture_result = None
    scope.capture_partial = None
    scope.capture_progress = (0, 0)
    scope.captured_bytes = b''
    scope._pending_restart = False
    scope._apply_schmitt = MagicMock()
    scope._poll_capture = MagicMock()
    return scope


def run_capture(scope):
    with patch.object(console.threading, 'Thread', ImmediateThread):
        scope._capture()


def test_capture_rejects_disconnected_and_waits_for_stopping_worker():
    scope = capture_scope()
    scope.dev = None
    with patch.object(console, 'messagebox') as mb:
        scope._capture()
    mb.showerror.assert_called_once_with('Error', 'Not connected')

    scope.dev = Device()
    scope.capture_running = True
    scope.stop_evt.flag = False
    scope._capture()
    scope.win.after.assert_not_called()

    scope.stop_evt.flag = True
    scope._capture()
    assert 'Waiting' in scope.status['text']
    scope.win.after.assert_called_once_with(500, scope._capture)

    scope.stop_evt.flag = False
    scope._pending_restart = True
    scope._capture()
    assert scope.win.after.call_count == 2


def test_capture_works_with_device_without_optional_reset():
    scope = capture_scope()
    delattr(scope.dev.__class__, 'reset')
    try:
        run_capture(scope)
        assert not isinstance(scope.capture_result, Exception)
    finally:
        Device.reset = lambda self: self.calls.append(('reset',))


@pytest.mark.parametrize('trigger_mode,selected,expected', [('Rising', [], (1 << 30) | 1), ('Falling', [3], (2 << 30) | 8)])
def test_single_digital_capture_builds_trigger_and_filters_protocol(trigger_mode, selected, expected, capsys):
    scope = capture_scope(kind='single', nsamp=5000)
    scope.trig_mode = Var(trigger_mode)
    for index in selected:
        scope.trig_ch_vars[index] = Var(True)
    scope.proto_trig_var = Var(True)
    run_capture(scope)
    assert scope.capture_result == (b'filtered', 1_000_000, 5000)
    capture_call = next(call for call in scope.dev.calls if call[0] == 'capture')
    assert capture_call[1]['trigger'] == expected
    assert ('compression', False) in scope.dev.calls
    assert 'first 8 bytes' in capsys.readouterr().out


def test_protocol_inputs_fall_back_and_protocol_filter_failure_keeps_raw_capture():
    scope = capture_scope(kind='single', nsamp=4)
    scope.proto_trig_var = Var(True)
    scope.proto_match = Var('invalid')
    scope.proto_ch = Var('2')
    scope.proto_baud = Var('invalid')
    scope.dev.apply_protocol_trigger = MagicMock(side_effect=RuntimeError('decode'))
    run_capture(scope)
    assert scope.capture_result == (scope.dev.capture_data, 1_000_000, 4)
    trigger_call = next(call for call in scope.dev.calls if call[0] == 'trigger_decode')
    assert trigger_call[1] == {'match_byte': 0x57, 'channel': 2, 'baud': 115200, 'enable': True}
    assert ('fast_mode', True) in scope.dev.calls
    assert ('fast_mode', False) in scope.dev.calls


def test_packet_style_device_uses_stride_flags_instead_of_raw_mode():
    scope = capture_scope(kind='single')
    scope.raw_mode_var = Var(True)
    scope.dev.pkt = object()
    run_capture(scope)
    assert scope.dev._stride == 1
    assert scope.dev._raw_flags == 0
    assert not any(call[0] == 'raw_mode' for call in scope.dev.calls)


def test_short_single_capture_skips_debug_hex_preview():
    scope = capture_scope(kind='single', nsamp=2)
    scope.dev.capture_data = b'\x00\x01'
    run_capture(scope)
    assert scope.capture_result == (b'\x00\x01', 1_000_000, 2)


def test_rolling_digital_passes_pending_generator_and_updates_each_chunk():
    scope = capture_scope(kind='rolling', nsamp=200_000)
    scope.capture_window = 200_000
    scope.rolling_buf_var = Var('200 ms')
    scope.proto_trig_var = Var(True)
    scope.compress_enabled = True
    scope.dev._pending_gen = {'data': b'abc', 'baud': 9600, 'tx_pin': 5}
    scope.dev.rolling_items = [(b'raw', 3, 10)]
    run_capture(scope)
    assert scope.capture_result == (b'filtered', 1_000_000, 3, 2)
    assert scope.capture_partial == b'filtered'
    call = next(call for call in scope.dev.calls if call[0] == 'rolling')[1]
    assert call['chunk_nsamp'] == 65536
    assert (call['gen_data'], call['gen_baud'], call['gen_tx_pin']) == (b'abc', 9600, 5)
    assert scope.dev._pending_gen is None
    assert ('compression', True) in scope.dev.calls


def test_rolling_protocol_filter_failure_preserves_chunk():
    scope = capture_scope(kind='rolling')
    scope.proto_trig_var = Var(True)
    scope.dev.rolling_items = [(b'raw', 2, 5)]
    scope.dev.apply_protocol_trigger = MagicMock(side_effect=RuntimeError('bad frame'))
    run_capture(scope)
    assert scope.capture_result == (b'raw', 1_000_000, 2, 2)


def test_rolling_mixed_capture_uses_wire_and_payload_strides():
    scope = capture_scope(kind='rolling', mode=MODE_MIXED)
    scope.rolling_buf_var = Var(error=RuntimeError('disposed'))
    scope.dev.rolling_items = [(b'\x01\x00\x02\x00', 1, 3)]
    with patch.object(console, 'decode_analog_frames', return_value=[{'digital': 1, 'adc': [2]}]) as decode:
        run_capture(scope)
    result = scope.capture_result
    assert len(result) == 6
    assert result[3] == analog_frame_stride(MODE_MIXED)
    decode.assert_called_once()
    call = next(call for call in scope.dev.calls if call[0] == 'rolling')[1]
    assert call['chunk_nsamp'] == 128
    assert call['payload_stride'] == analog_frame_stride(MODE_MIXED)


def test_single_mixed_capture_converts_wire_payload_and_frames():
    scope = capture_scope(kind='single', mode=MODE_MIXED, nsamp=2)
    scope.dev.capture_data = b'\x01\x00\xaa\xbb\x02\x00\xcc\xdd'
    with patch.object(console, 'wire_to_payload', return_value=b'\x01\x00\x02\x00') as convert, \
         patch.object(console, 'decode_analog_frames', return_value=[{'digital': 1}, {'digital': 2}]) as decode:
        run_capture(scope)
    assert scope.capture_result == (
        b'\x01\x00\x02\x00', 1_000_000, 2, analog_frame_stride(MODE_MIXED),
        [{'digital': 1}, {'digital': 2}], MODE_MIXED,
    )
    convert.assert_called_once()
    decode.assert_called_once()


def test_capture_worker_records_errors_and_tolerates_cleanup_failures():
    scope = capture_scope(kind='single', nsamp=4)
    scope.stop_evt = Event(set_after_clear=True)
    scope.dev.capture = MagicMock(side_effect=OSError('capture failed'))
    scope.dev.fast_mode = MagicMock(side_effect=lambda enabled: None if enabled else (_ for _ in ()).throw(RuntimeError('off')))
    scope.dev.reset = MagicMock(side_effect=[None, RuntimeError('reset failed')])
    run_capture(scope)
    assert isinstance(scope.capture_result, OSError)
    assert str(scope.capture_result) == 'capture failed'
    assert scope.capture_running is False


def test_capture_tolerates_device_rejecting_fast_mode_attribute():
    scope = capture_scope(kind='single', nsamp=2)
    dev = scope.dev
    type(dev).fast_mode_enabled = property(
        lambda self: False,
        lambda self, value: (_ for _ in ()).throw(RuntimeError('readonly')),
    )
    try:
        run_capture(scope)
    finally:
        del type(dev).fast_mode_enabled
    assert scope.capture_result == (dev.capture_data, 1_000_000, 2)


def test_capture_progress_protocol_trigger_success_disabled_and_failures():
    scope = capture_scope()
    scope.capture_mode = MODE_DIGITAL
    scope.samplerate = 2_000_000
    scope._capture_progress(b'raw', 1, 2)
    assert scope.capture_partial == b'filtered'
    assert scope.capture_progress == (1, 2)

    scope.dev.protocol_trigger = MagicMock(return_value=None)
    scope._capture_progress(b'raw2', 2, 3)
    assert scope.capture_partial == b'raw2'

    scope.dev.protocol_trigger = MagicMock(side_effect=RuntimeError('query'))
    scope._capture_progress(b'raw3', 3, 4)
    assert scope.capture_partial == b'raw3'

    scope.dev.protocol_trigger = MagicMock(return_value={'enabled': True})
    scope.dev.apply_protocol_trigger = MagicMock(side_effect=RuntimeError('apply'))
    scope._capture_progress(b'raw4', 4, 5)
    assert scope.capture_partial == b'raw4'

    scope.capture_mode = MODE_MIXED
    scope._capture_progress(b'mixed', 5, 6)
    assert scope.capture_partial == b'mixed'
    scope._capture_progress(b'', 6, 7)
    assert scope.capture_partial == b''


@pytest.mark.parametrize('running,kind,disabled', [(True, 'rolling', True), (True, 'single', False), (False, 'rolling', False)])
def test_generator_button_state_tracks_rolling_capture(running, kind, disabled):
    scope = capture_scope(kind=kind)
    scope.capture_running = running
    scope.gen_send_cap_btn = MagicMock()
    scope.gen_send_btn = MagicMock()
    scope._update_gen_buttons()
    expected_state = 'disabled' if disabled else 'normal'
    scope.gen_send_cap_btn.configure.assert_called_once_with(state=expected_state)


def test_stop_capture_is_idempotent_and_only_signals_worker():
    scope = capture_scope()
    scope.capture_running = False
    scope._stop_capture()
    assert scope.stop_evt.is_set() is False

    scope.capture_running = True
    scope.capture_nsamp = 100
    scope._stop_capture()
    assert scope.stop_evt.is_set() is True
    assert scope.capture_nsamp == 0
    scope.stop_btn.configure.assert_called_once_with(state='disabled')
    assert scope.status['text'] == 'Stopping capture...'


def poll_scope():
    scope = capture_scope()
    scope._update_gen_buttons = MagicMock()
    scope._live_waveform = MagicMock()
    scope._update_export_size_label = MagicMock()
    scope._load_capture = MagicMock()
    scope._load_analog_capture = MagicMock()
    scope._capture = MagicMock()
    scope._poll_capture = OLScope._poll_capture.__get__(scope)
    return scope


@pytest.mark.parametrize('kind,status_fragment', [('rolling', 'Rolling:'), ('single', 'Capturing...')])
def test_poll_running_capture_updates_status_live_view_and_schedules(kind, status_fragment):
    scope = poll_scope()
    scope.capture_running = True
    scope.capture_type = Var(kind)
    scope.capture_progress = (15, 10)
    scope.captured_bytes = b'x' * 1024
    scope._poll_capture()
    assert status_fragment in scope.status['text']
    scope._live_waveform.assert_called_once_with(15)
    scope.win.after.assert_called_once_with(150, scope._poll_capture)
    assert scope._update_export_size_label.called is (kind == 'rolling')


def test_poll_running_zero_total_only_reschedules():
    scope = poll_scope()
    scope.capture_running = True
    scope.capture_progress = (0, 0)
    scope._poll_capture()
    scope._live_waveform.assert_not_called()
    scope.win.after.assert_called_once()


def test_poll_handles_error_and_all_result_shapes_with_pending_restart():
    scope = poll_scope()
    scope.capture_result = RuntimeError('bad')
    scope._poll_capture()
    assert 'Capture error: bad' in scope.status['text']

    scope = poll_scope()
    scope.capture_result = (b'd', 1, 2, 4)
    scope._poll_capture()
    scope._load_capture.assert_called_once_with(b'd', 1, 4)

    scope = poll_scope()
    frames = [{'digital': 0}]
    scope.capture_result = (b'a', 2, 1, 6, frames, MODE_MIXED)
    scope._poll_capture()
    scope._load_analog_capture.assert_called_once_with(b'a', 2, frames, MODE_MIXED, 6)

    scope = poll_scope()
    scope.dev._stride = 1
    scope.capture_result = (b'n', 3, 1)
    scope._pending_restart = True
    scope._poll_capture()
    scope._load_capture.assert_called_once_with(b'n', 3, 1)
    scope._capture.assert_called_once_with()
    assert scope._pending_restart is False

    scope = poll_scope()
    scope.dev = None
    scope.capture_result = (b'n', 3, 1)
    scope._poll_capture()
    scope._load_capture.assert_called_once_with(b'n', 3, 4)

    scope = poll_scope()
    scope.capture_result = None
    scope._poll_capture()
    scope._load_capture.assert_not_called()


def live_scope(partial=b'\x00\x00\x01\x00'):
    scope = capture_scope()
    scope.capture_partial = partial
    scope.capture_mode = MODE_DIGITAL
    scope.capture_stride = 2
    scope.wave = SimpleNamespace(
        num_samples=0, ch_data=[], px_scale=2, scroll_x=0,
        winfo_width=MagicMock(return_value=100), redraw=MagicMock(),
    )
    scope.capture_running = False
    scope.capture_type = Var('single')
    scope._process_decoders = MagicMock()
    scope.live_bar = MagicMock()
    scope._last_live_redraw = 0
    return scope


def test_live_waveform_rejects_missing_short_empty_and_unchanged_data():
    scope = live_scope(None)
    scope._live_waveform(0)
    scope.capture_partial = b'123'
    scope._live_waveform(0)

    scope.capture_partial = b'1234'
    with patch.object(console, 'samples_to_channels', return_value=([], 1)):
        scope._live_waveform(1)
    scope.wave.num_samples = 2
    with patch.object(console, 'samples_to_channels', return_value=([[0, 1]], 2)):
        scope._live_waveform(2)
    scope._process_decoders.assert_not_called()


def test_live_waveform_mixed_builds_digital_defaults_and_analog_channels():
    scope = live_scope(b'1234')
    scope.capture_mode = MODE_MIXED
    frames = [
        {'digital': None, 'adc': [10, 20]},
        {'digital': 3, 'adc': [11, 21]},
    ]
    with patch.object(console, 'decode_analog_frames', return_value=frames), patch.object(console.time, 'time', return_value=1):
        scope._live_waveform(2)
    assert scope.wave.ch_data[0] == [0, 1]
    assert scope.wave.ch_data[1] == [0, 1]
    assert scope.wave.ch_data[-2:] == [[10, 11], [20, 21]]
    scope.wave.redraw.assert_called_once_with()

    scope.wave.redraw.reset_mock()
    with patch.object(console, 'decode_analog_frames', return_value=[]):
        scope._live_waveform(0)
    scope.wave.redraw.assert_not_called()

    scope.wave.num_samples = 0
    no_adc = [{'digital': 0}, {'digital': 1}]
    with patch.object(console, 'decode_analog_frames', return_value=no_adc), patch.object(console.time, 'time', return_value=2):
        scope._live_waveform(2)
    assert len(scope.wave.ch_data) == NUM_CHANNELS


def test_live_waveform_raw_rolling_trims_memory_autoscrolls_and_forces_redraw():
    partial = bytes(range(24))
    scope = live_scope(partial)
    scope.capture_stride = 4
    scope.raw_mode_var = Var(True)
    scope.capture_running = True
    scope.capture_type = Var('rolling')
    scope.capture_window = 1
    scope.wave.px_scale = 0
    scope.force_redraw = True
    with patch.object(console, 'samples_to_channels', return_value=([[0, 1, 0, 1, 0]], 5)) as convert, \
         patch.object(console.time, 'time', return_value=0.1):
        scope._live_waveform(5)
    assert convert.call_args.kwargs['stride'] == 1
    assert convert.call_args.args[0] == bytes([0, 4, 8, 12, 16, 20])
    assert scope.wave.num_samples == 2
    assert scope.wave.ch_data == [[1, 0]]
    scope.wave.redraw.assert_called_once_with()
    assert scope.force_redraw is False


def test_live_waveform_throttle_updates_progress_without_redraw():
    scope = live_scope()
    scope._last_live_redraw = 1
    with patch.object(console, 'samples_to_channels', return_value=([[0, 1]], 2)), \
         patch.object(console.time, 'time', return_value=1.1):
        scope._live_waveform(2)
    scope.wave.redraw.assert_not_called()
    assert scope.live_bar.__setitem__.call_args.args[0] == 'value'


def test_live_waveform_rolling_positive_scale_and_default_memory_limit():
    scope = live_scope()
    scope.capture_running = True
    scope.capture_type = Var('rolling')
    scope.capture_window = 0
    scope.wave.px_scale = 2
    with patch.object(console, 'samples_to_channels', return_value=([[0, 1, 0]], 3)), \
         patch.object(console.time, 'time', return_value=1):
        scope._live_waveform(3)
    assert scope.wave.scroll_x == 0
    assert scope.wave.num_samples == 3
