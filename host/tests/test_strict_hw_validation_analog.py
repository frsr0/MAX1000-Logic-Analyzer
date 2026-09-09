from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app import hw_validation as hv


def rows(ns=128, active=True):
    digital = [i & 1 for i in range(ns)] if active else [0] * ns
    return [digital[:] for _ in range(16)], ns


def dev():
    value = MagicMock()
    value.sys_clk = 100_000_000
    value.spi = MagicMock()
    value.capture.return_value = b'digital'
    return value


@pytest.fixture(autouse=True)
def quiet_hardware():
    with patch.object(hv, 'save_result'), patch.object(hv, 'check') as check, \
         patch.object(hv, 'log'), patch.object(hv, 'print_header'), \
         patch.object(hv, 'log_floating_channel_activity'), \
         patch.object(hv, 'check_channels_clean'), patch.object(hv.time, 'sleep'):
        yield check


@pytest.mark.parametrize('debug,active', [(False, True), (True, True), (True, False)])
def test_divider_accuracy_paths(debug, active):
    value = dev()
    with patch.object(hv, 'samples_to_channels', return_value=rows(1024, active)):
        hv.test_divider_accuracy(value, debug_on=debug)


def test_divider_accuracy_empty(quiet_hardware):
    value = dev(); value.capture.return_value = b''
    hv.test_divider_accuracy(value)
    quiet_hardware.assert_called_with(False, 'divider test returned no data')


@pytest.mark.parametrize('active', [True, False])
def test_full_width_capture_data(active):
    value = dev()
    with patch.object(hv, 'samples_to_channels', return_value=rows(512, active)):
        hv.test_23ch_capture(value)


def test_full_width_capture_empty(quiet_hardware):
    value = dev(); value.capture.return_value = b''
    hv.test_23ch_capture(value)
    quiet_hardware.assert_any_call(False, 'full-width capture returned no data')


@pytest.mark.parametrize('frames', [
    [{'digital': 1, 'adc': [10, 20]}],
    [{'digital': 0, 'adc': [0, 0]}],
    [],
])
def test_mixed_analog_frame_shapes(frames):
    baseline = [{'digital': None, 'adc': [10]} for _ in range(8)]
    value = dev(); value.capture_analog.side_effect = [
        (b'b', baseline), (b'b', baseline),
        (b'x' * (len(frames) * 5), frames),
    ]
    hv.test_mixed_analog_mode(value, debug_on=True)
    value.set_analog_enable.assert_called_with(False)


def test_mixed_lane_comparison_rejects_stale_rail_value():
    """A legal 12-bit value is still invalid when it disagrees with its pin."""
    mixed = [{'digital': 0, 'adc': [4095, 88]} for _ in range(8)]
    baselines = {
        1: [{'digital': None, 'adc': [82]} for _ in range(8)],
        2: [{'digital': None, 'adc': [91]} for _ in range(8)],
    }

    result = hv.compare_mixed_analog_lanes(mixed, baselines)

    assert result["ok"] is False
    assert result["lanes"][0]["label"] == "ADC1/AIN3"
    assert result["lanes"][0]["mixed_median"] == 4095
    assert "disagrees" in result["lanes"][0]["reason"]


def test_mixed_lane_comparison_rejects_missing_lane_samples():
    result = hv.compare_mixed_analog_lanes([], {1: [], 2: []})
    assert result["ok"] is False
    assert all(lane["reason"] == "missing samples for comparison"
               for lane in result["lanes"])


@pytest.mark.parametrize('frames', [[{'digital': None, 'adc': [123]}], [{'digital': None, 'adc': []}], []])
def test_high_speed_analog_frame_shapes(frames):
    value = dev(); value.capture_analog.return_value = (b'x' * (len(frames) * 2), frames)
    hv.test_high_speed_analog_mode(value)


def test_maximum_analog_retries_then_succeeds_and_exhausts():
    frame = {'digital': None, 'adc': list(range(8))}
    value = dev()
    value.capture_analog.side_effect = [(b'', []), (b'x' * 12, [frame])]
    hv.test_maximum_analog_mode(value)
    assert value.capture_analog.call_count == 2

    value = dev(); value.capture_analog.return_value = (b'', [])
    hv.test_maximum_analog_mode(value)
    assert value.capture_analog.call_count == 6


def test_mixed_frame_alignment_success_empty_and_cleanup_on_error(quiet_hardware):
    value = dev()
    frames = [{'digital': 1, 'adc': [1, 2]}] * 10
    value.capture_analog.return_value = (b'data', frames)
    hv.test_mixed_frame_alignment(value)

    value.capture_analog.return_value = (b'', [])
    hv.test_mixed_frame_alignment(value)
    quiet_hardware.assert_any_call(False, 'mixed-frame capture returned frames')

    value.capture_analog.side_effect = OSError('adc')
    with pytest.raises(OSError): hv.test_mixed_frame_alignment(value)
    value.set_debug_ch0.assert_called_with(False)
    value.set_analog_enable.assert_called_with(False)


@pytest.mark.parametrize('active', [True, False])
def test_mixed_digital_mixed_back_to_back_success(active):
    value = dev()
    frames = [{'digital': 1, 'adc': [1, 2]}]
    value.capture_analog.side_effect = [(b'a', frames), (b'b', frames)]
    with patch.object(hv, 'samples_to_channels', return_value=rows(128, active)):
        hv.test_mixed_digital_mixed_back_to_back(value)


def test_mixed_digital_mixed_back_to_back_empty():
    value = dev(); value.capture_analog.return_value = (b'', [])
    value.capture.return_value = b''
    hv.test_mixed_digital_mixed_back_to_back(value)


def test_mixed_codec_rolling_multiple_buffers_and_no_frames():
    value = dev()
    value.rolling_capture.return_value = iter([(b'a' * 10, 10, 10), (b'b' * 10, 20, 20), (b'c' * 10, 30, 30)])
    frames = [{'digital': 1, 'adc': [1, 2]}] * 300
    with patch.object(hv, 'decode_analog_frames', return_value=frames), \
         patch.object(hv, 'compress_mixed_stream', side_effect=lambda data: b'z' + data), \
         patch.object(hv, 'decompress_mixed_stream', side_effect=lambda data: data[1:]):
        hv.test_mixed_compressed_rolling(value)

    value.rolling_capture.return_value = iter([])
    with patch.object(hv, 'decode_analog_frames', return_value=[]), \
         patch.object(hv, 'compress_mixed_stream', return_value=b''), \
         patch.object(hv, 'decompress_mixed_stream', return_value=b''):
        hv.test_mixed_compressed_rolling(value)


@pytest.mark.parametrize('active', [True, False])
def test_analog_profiles_recover_digital_after_retry(active):
    value = dev()
    fast = [{'digital': None, 'adc': [1]}]
    all_frames = [{'digital': None, 'adc': list(range(8))}]
    value.capture_analog.side_effect = [(b'f', fast), (b'', []), (b'a', all_frames)]
    with patch.object(hv, 'samples_to_channels', return_value=rows(128, active)):
        hv.test_analog_profiles_digital_recovery(value)


def test_analog_profiles_empty_all_paths():
    value = dev(); value.capture_analog.return_value = (b'', []); value.capture.return_value = b''
    hv.test_analog_profiles_digital_recovery(value)
