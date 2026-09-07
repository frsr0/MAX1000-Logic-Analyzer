from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.gui_waveform import WaveformDisplay


def make_wave(*, data=None, names=None, app=None, width=240, height=180):
    wave = WaveformDisplay(MagicMock(), app=app)
    wave.winfo_width = MagicMock(return_value=width)
    wave.winfo_height = MagicMock(return_value=height)
    wave.delete = MagicMock()
    wave.create_line = MagicMock(return_value=1)
    wave.create_rectangle = MagicMock(return_value=2)
    wave.create_text = MagicMock(return_value=3)
    wave.redraw = MagicMock()
    if data is not None:
        wave.ch_data = data
        wave.ch_names = names or []
        wave.num_samples = len(data[0]) if data else 0
        wave.channel_visible = [True] * len(data)
    return wave


def event(x=0, y=0, delta=0):
    return SimpleNamespace(x=x, y=y, delta=delta)


def text_values(wave):
    return [call.kwargs.get('text') for call in wave.create_text.call_args_list]


def test_channel_visibility_height_and_load_contracts():
    wave = make_wave(data=[[0], [1]], names=['A', 'B'], height=100)
    assert wave._visible_indices() == [0, 1]
    assert wave._calc_ch_height() == 30

    wave.channel_visible = [False, False]
    assert wave._calc_ch_height() == wave.CH_HEIGHT
    assert wave.total_height() == wave.RULER_H + wave.DECODE_H

    wave.set_channel_visible(0, True)
    wave.toggle_channel(0)
    assert wave.channel_visible == [False, False]
    assert wave.redraw.call_count == 2

    for invalid in (-1, 2):
        wave.set_channel_visible(invalid, True)
        wave.toggle_channel(invalid)
    assert wave.channel_visible == [False, False]
    assert wave.redraw.call_count == 2

    wave.load([[0, 1, 0]], ['D0'], 2_000_000)
    assert (wave.num_samples, wave.samplerate) == (3, 2_000_000)
    assert wave.channel_visible == [True]
    assert (wave.marker1, wave.marker2, wave.scroll_x, wave._drawn_to) == (None, None, 0, 0)

    wave.load([], [], 1)
    assert wave.num_samples == 0


def test_incremental_draw_covers_digital_analog_transitions_and_bounds():
    wave = make_wave(data=[[0, 1, 1], [0, 2048, 4095]], names=['D', 'A'])
    wave.px_scale = 2
    wave.scroll_x = 0
    wave._drawn_to = 0
    wave.draw_incremental(99)
    assert wave._drawn_to == 99
    assert wave.delete.call_args.args == ('live',)
    assert wave.create_line.call_count == 2
    assert all(call.kwargs['tags'] == 'live' for call in wave.create_line.call_args_list)

    wave.create_line.reset_mock()
    wave._drawn_to = 1
    wave.draw_incremental(2)
    assert wave.create_line.call_count == 2

    wave.create_line.reset_mock()
    wave._drawn_to = wave.num_samples
    wave.draw_incremental(wave.num_samples + 1)
    wave.create_line.assert_not_called()

    wave.draw_incremental(1)
    wave.ch_data = []
    wave.draw_incremental(3)
    wave.create_line.assert_not_called()


@pytest.mark.parametrize('delta,factor', [(1, 1.2), (-1, 0.8)])
def test_wheel_scales_and_consumes_event(delta, factor):
    wave = make_wave()
    wave.set_scale = MagicMock()
    assert wave._on_wheel(event(delta=delta)) == 'break'
    wave.set_scale.assert_called_once_with(2.0 * factor)


def test_scale_clamps_preserves_center_and_handles_zero_old_scale():
    wave = make_wave(width=100)
    wave.scroll_x = 10
    wave.set_scale(100)
    assert wave.px_scale == wave.MAX_PX_PER_SAMPLE
    assert wave.scroll_x == pytest.approx(34)

    wave.px_scale = 0
    wave.set_scale(0.1)
    assert wave.px_scale == wave.MIN_PX_PER_SAMPLE
    assert wave.scroll_x == 0


def test_label_click_toggles_only_hit_channel_and_syncs_app():
    app = SimpleNamespace(_sync_ch_vis_ui=MagicMock())
    wave = make_wave(data=[[0], [1]], names=['A', 'B'], app=app, height=180)
    wave.toggle_channel = MagicMock()
    wave._on_click(event(x=10, y=25))
    wave.toggle_channel.assert_called_once_with(0)
    app._sync_ch_vis_ui.assert_called_once_with()

    wave.toggle_channel.reset_mock()
    wave._on_click(event(x=10, y=179))
    wave.toggle_channel.assert_not_called()

    wave.app = None
    wave._on_click(event(x=10, y=25))
    wave.toggle_channel.assert_called_once_with(0)


def test_marker_click_cycle_orders_markers_and_rejects_outside_samples():
    wave = make_wave(data=[[0] * 20], names=['D'])
    wave.px_scale = 2
    wave.scroll_x = 0

    wave._on_click(event(x=50))
    assert (wave.marker1, wave.marker2, wave.dragging) == (5, None, 'marker1')
    wave._on_click(event(x=44))
    assert (wave.marker1, wave.marker2, wave.dragging) == (2, 5, 'marker1')
    wave._on_click(event(x=48))
    assert (wave.marker1, wave.marker2, wave.dragging) == (4, None, 'marker1')

    before = wave.redraw.call_count
    wave._on_click(event(x=39))
    wave._on_click(event(x=1000))
    assert wave.redraw.call_count == before


def test_marker_click_second_marker_without_swap():
    wave = make_wave(data=[[0] * 20], names=['D'])
    wave.marker1 = 2
    wave._on_click(event(x=50))
    assert (wave.marker1, wave.marker2, wave.dragging) == (2, 5, 'marker2')


def test_drag_clamps_moves_both_markers_and_swaps_drag_identity():
    wave = make_wave(data=[[0] * 10], names=['D'])
    wave.dragging = None
    wave._on_drag(event(x=50))
    wave.dragging = 'marker1'
    wave._on_drag(event(x=40))
    assert wave.marker1 == 0
    wave._on_drag(event(x=1000))
    assert wave.marker1 == 9

    wave.marker1, wave.marker2, wave.dragging = 8, 2, 'marker2'
    wave._on_drag(event(x=42))
    assert (wave.marker1, wave.marker2, wave.dragging) == (1, 8, 'marker1')

    wave.marker1, wave.marker2, wave.dragging = 8, 2, 'marker1'
    wave._on_drag(event(x=50))
    assert (wave.marker1, wave.marker2, wave.dragging) == (2, 5, 'marker2')

    wave.marker1, wave.marker2, wave.dragging = 1, 8, 'marker1'
    wave._on_drag(event(x=44))
    assert (wave.marker1, wave.marker2, wave.dragging) == (2, 8, 'marker1')

    wave.dragging = 'marker1'
    wave._on_drag(event(x=39))
    assert wave.marker1 == 2
    wave._on_release(event())
    assert wave.dragging is None


def test_highlight_draws_selected_visible_row():
    wave = make_wave(data=[[0], [1], [0]], names=['A', 'B', 'C'])
    wave.channel_visible = [True, False, True]
    wave.highlight_channel(2)
    wave.redraw.assert_called_once_with()
    args = wave.create_rectangle.call_args.args
    assert args[1] > wave.RULER_H

    wave.create_rectangle.reset_mock()
    wave.highlight_channel(99)
    wave.create_rectangle.assert_not_called()


def prepare_redraw(wave):
    wave.redraw = WaveformDisplay.redraw.__get__(wave)
    wave.px_scale = 2
    wave.scroll_x = 0
    wave.samplerate = 1_000_000
    return wave


def test_redraw_early_exits_for_tiny_or_empty_canvas():
    tiny = prepare_redraw(make_wave(data=[[0]], names=['D'], width=9))
    tiny.redraw()
    tiny.create_rectangle.assert_not_called()

    empty = prepare_redraw(make_wave(data=[], names=[], width=100))
    empty.redraw()
    empty.create_rectangle.assert_not_called()


@pytest.mark.parametrize(
    'samplerate,scale,suffix',
    [(1_000_000_000, 50, 'ns'), (1_000_000, 50, 'µs'), (1_000, 50, 'ms')],
)
def test_redraw_ruler_uses_all_time_units(samplerate, scale, suffix):
    wave = prepare_redraw(make_wave(data=[[0, 1]], names=['D'], width=100))
    wave.samplerate = samplerate
    wave.px_scale = scale
    wave.redraw()
    assert any(str(value).endswith(suffix) for value in text_values(wave))


def test_redraw_ruler_handles_no_matching_step_negative_scroll_and_disabled_ruler():
    wave = prepare_redraw(make_wave(data=[[0]], names=['D'], width=70))
    wave.samplerate = 1
    wave.px_scale = 0.5
    wave.scroll_x = -1
    wave.redraw()

    wave.create_text.reset_mock()
    wave.px_scale = 0
    wave.redraw()
    wave.px_scale = 0.5
    wave.samplerate = 0
    wave.redraw()
    assert not any(str(value).endswith(('ns', 'µs', 'ms')) for value in text_values(wave))


def test_redraw_digital_filtered_analog_and_empty_visible_ranges():
    wave = prepare_redraw(make_wave(
        data=[[0, 1, 1], [1, 0, 1], [0, 2048, 4095], [0, 1, 0]],
        names=['RAW', 'clean_f', 'ADC0', 'OFF'],
        width=120,
    ))
    wave.redraw()
    colors = [call.kwargs.get('fill') for call in wave.create_line.call_args_list]
    assert '#0066cc' in colors
    assert '#2a7' in colors
    assert '#b05a00' in colors
    assert {'3.3V', '1.65V', '0V', '4095'} <= set(text_values(wave))

    wave.scroll_x = 100
    wave.redraw()


def test_redraw_uart_overlays_printable_hex_and_filters_irrelevant_frames():
    slots = [
        {'enabled': False, 'src_str': 'RX', 'baud': 1000, 'frames': []},
        {'enabled': True, 'src_str': 'OTHER', 'baud': 1000, 'frames': []},
        {'enabled': True, 'src_str': 'RX', 'baud': 1000, 'frames': [
            {'type': 'error', 'pos': 0, 'val': 0},
            {'type': 'byte', 'pos': -100, 'val': 65},
            {'type': 'byte', 'pos': 0, 'val': 65},
            {'type': 'byte', 'pos': 1, 'val': 1},
        ]},
    ]
    app = SimpleNamespace(decoder_slots=slots)
    wave = prepare_redraw(make_wave(data=[[0, 1]], names=['RX_UART'], app=app, width=140))
    wave.samplerate = 1_000
    wave.redraw()
    assert {'A', '[01]'} <= set(text_values(wave))


def test_redraw_spi_and_i2c_decoder_paths_use_frame_positions():
    slots = [
        {'enabled': False, 'src_str': 'BUS', 'frames': [{}]},
        {'enabled': True, 'src_str': 'OTHER', 'frames': [{}]},
        {'enabled': True, 'src_str': 'BUS', 'frames': [{'type': 'byte', 'pos': 1}]},
    ]
    app = SimpleNamespace(decoder_slots=slots)
    spi = prepare_redraw(make_wave(data=[[0]], names=['BUS_SPI'], app=app))
    spi.redraw()

    slots[-1]['frames'] = [
        {'type': 'START', 'pos': 2},
        {'type': 'STOP', 'pos': 5},
        {'type': 'BYTE', 'pos': 7},
    ]
    i2c = prepare_redraw(make_wave(data=[[0]], names=['BUS_I2C'], app=app))
    i2c.redraw()
    assert {'S', 'P'} <= set(text_values(i2c))
    stop_call = next(call for call in i2c.create_text.call_args_list if call.kwargs.get('text') == 'P')
    assert stop_call.args[0] == i2c.LABEL_WIDTH + 5 * i2c.px_scale - 8


def test_redraw_markers_reports_interval_frequency_and_zero_interval():
    wave = prepare_redraw(make_wave(data=[[0, 1, 0]], names=['D']))
    wave.marker1, wave.marker2 = 0, 2
    wave.redraw()
    assert any('Δt = 2.0 µs' in str(value) and 'f = 500.0 kHz' in str(value)
               for value in text_values(wave))

    wave.create_text.reset_mock()
    wave.marker1 = wave.marker2 = 1
    wave.redraw()
    assert any('f = 0.0 kHz' in str(value) for value in text_values(wave))

    wave.create_text.reset_mock()
    wave.marker2 = None
    wave.redraw()
    assert 'M1' in text_values(wave)
    assert not any('Δt' in str(value) for value in text_values(wave))
    assert wave.get_decode_y() == wave.total_height() - wave.DECODE_H
