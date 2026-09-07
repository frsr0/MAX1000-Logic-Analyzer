from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app import hw_validation as hv


def channels(ns=1024, active=True):
    row = [i & 1 for i in range(ns)] if active else [0] * ns
    return [row[:] for _ in range(hv.NUM_CHANNELS)], ns


def device():
    dev = MagicMock()
    dev.sys_clk = 100_000_000
    dev.sample_clk = 200_000_000
    dev._stride = 2
    dev.spi = MagicMock()
    dev.pkt = MagicMock()
    dev.pkt.write_register.return_value = True
    dev.pkt.get_status.return_value = {'capture_status': 1}
    dev.get_metadata.return_value = b'123456789'
    dev.capture.return_value = b'data'
    dev.capture_with_gen.return_value = b'data'
    return dev


@pytest.fixture(autouse=True)
def isolate_hardware_side_effects():
    with patch.object(hv, 'save_result') as save, patch.object(hv.time, 'sleep'), \
         patch.object(hv, 'print_header'), patch.object(hv, 'print_progress'), \
         patch.object(hv, 'log'), patch.object(hv, 'check') as check:
        yield SimpleNamespace(save=save, check=check)


def test_spi_handoff_response_and_no_response(isolate_hardware_side_effects):
    dev = device()
    dev.pkt.transaction.return_value = (hv.ST_OK, 1, b'123456789')
    hv.test_spi_handoff(dev)
    isolate_hardware_side_effects.check.assert_any_call(True, 'GET_METADATA returned ST_OK (0x00)')
    dev.pkt.transaction.return_value = None
    hv.test_spi_handoff(dev)
    isolate_hardware_side_effects.check.assert_called_with(False, 'GET_METADATA returned no response')


def test_spi_commands_success_and_missing_responses(isolate_hardware_side_effects):
    dev = device()
    dev.pkt.transaction.return_value = (hv.ST_OK, 0, b'')
    hv.test_spi_commands(dev)
    assert dev.pkt.write_register.call_count == 12
    dev.pkt.transaction.return_value = None
    dev.pkt.get_status.return_value = None
    hv.test_spi_commands(dev)
    calls = [call.args for call in isolate_hardware_side_effects.check.call_args_list]
    assert (False, 'PING returned no response') in calls
    assert (False, 'GET_STATUS returned no response') in calls


@pytest.mark.parametrize('debug,active', [(False, True), (True, True), (True, False)])
def test_single_capture_data_debug_paths(debug, active):
    dev = device()
    with patch.object(hv, 'samples_to_channels', return_value=channels(256, active)), \
         patch.object(hv, 'check_channels_clean') as clean:
        hv.test_single_capture(dev, debug_on=debug)
    assert clean.called
    dev.capture.assert_called_once_with(rate_hz=1_000_000, nsamples=256, timeout=10)


def test_single_capture_empty_reports_failure(isolate_hardware_side_effects):
    dev = device(); dev.capture.return_value = b''
    hv.test_single_capture(dev)
    isolate_hardware_side_effects.check.assert_called_with(False, 'capture returned data')


@pytest.mark.parametrize('debug,active', [(False, True), (True, True), (True, False)])
def test_fast_capture_full_payload_paths(debug, active):
    dev = device()
    dev.pkt.read_capture_block.side_effect = [b'x' * 1024, b'y' * 1024]
    with patch.object(hv, 'samples_to_channels', return_value=channels(1024, active)), \
         patch.object(hv, 'log_floating_channel_activity') as activity:
        hv.test_fast_capture(dev, debug_on=debug)
    assert activity.called
    assert dev.pkt.read_capture_block.call_count == 2


def test_fast_capture_empty_and_partial_blocks(isolate_hardware_side_effects):
    dev = device(); dev.pkt.read_capture_block.return_value = b''
    hv.test_fast_capture(dev)
    isolate_hardware_side_effects.check.assert_any_call(False, 'fast mode capture returned data')


def test_max_speed_capture_full_and_empty(isolate_hardware_side_effects):
    dev = device(); dev.pkt.read_capture_block.side_effect = [b'x' * 1024, b'y' * 1024]
    with patch.object(hv, 'samples_to_channels', return_value=channels(1024)), \
         patch.object(hv, 'log_floating_channel_activity'):
        hv.test_max_speed_capture(dev)
    dev = device(); dev.pkt.read_capture_block.return_value = b''
    hv.test_max_speed_capture(dev)
    isolate_hardware_side_effects.check.assert_called_with(False, 'max-speed capture returned no data')


@pytest.mark.parametrize('debug,active', [(False, True), (True, True), (True, False)])
def test_continuous_capture_retries_and_processes_buffer(debug, active):
    dev = device(); dev.pkt.read_capture_block.side_effect = [b'', b'x' * 1024]
    with patch.object(hv, 'samples_to_channels', return_value=channels(512, active)), \
         patch.object(hv, 'log_floating_channel_activity'), patch.object(hv, 'check_channels_clean'):
        hv.test_continuous_capture(dev, debug_on=debug)
    assert dev.pkt.read_capture_block.call_count == 2


def test_continuous_capture_empty_reports_failure(isolate_hardware_side_effects):
    dev = device(); dev.pkt.read_capture_block.return_value = b''
    hv.test_continuous_capture(dev)
    isolate_hardware_side_effects.check.assert_any_call(False, 'continuous capture returned no data')


@pytest.mark.parametrize('debug,active', [(False, True), (True, True), (True, False)])
def test_trigger_edge_capture_paths(debug, active):
    dev = device()
    with patch.object(hv, 'samples_to_channels', return_value=channels(512, active)), \
         patch.object(hv, 'check_channels_clean'):
        hv.test_trigger_edge(dev, debug_on=debug)


@pytest.mark.parametrize('debug', [False, True])
def test_trigger_edge_empty_is_expected_only_when_debug_off(debug, isolate_hardware_side_effects):
    dev = device(); dev.capture.return_value = b''
    hv.test_trigger_edge(dev, debug_on=debug)
    if not debug:
        isolate_hardware_side_effects.check.assert_called_with(True, 'trigger capture stayed idle with debug OFF')


@pytest.mark.parametrize('fn,result_name', [(hv.test_i2c_sweep, 'test9_i2c_sweep'), (hv.test_gen_spi_loopback, 'test10_spi_loopback')])
def test_generator_loopback_skips_without_jumper(fn, result_name, isolate_hardware_side_effects):
    dev = device()
    with patch.object(hv, '_get_jumper_pair', return_value=None), patch.object(hv, 'skip') as skip:
        fn(dev)
    skip.assert_called_once()
    assert isolate_hardware_side_effects.save.call_args.args[0] == result_name


def test_i2c_loopback_data_exact_mismatch_and_empty(isolate_hardware_side_effects):
    dev = device()
    events = [('DATA', 0xA6), ('DATA', 0x2D), ('DATA', 0x08)]
    with patch.object(hv, '_get_jumper_pair', return_value=(3, 4)), \
         patch.object(hv, 'samples_to_channels', return_value=channels(3)), \
         patch.object(hv, 'decode_i2c', return_value=events), patch.object(hv, '_restore_pin_map'):
        hv.test_i2c_sweep(dev)
    assert any(call.args[0] is True for call in isolate_hardware_side_effects.check.call_args_list)

    with patch.object(hv, '_get_jumper_pair', return_value=(3, 4)), \
         patch.object(hv, 'samples_to_channels', return_value=channels(3)), \
         patch.object(hv, 'decode_i2c', return_value=[]), patch.object(hv, '_restore_pin_map'):
        hv.test_i2c_sweep(dev)
    dev.capture_with_gen.return_value = b''
    with patch.object(hv, '_get_jumper_pair', return_value=(3, 4)), patch.object(hv, '_restore_pin_map'):
        hv.test_i2c_sweep(dev)
    isolate_hardware_side_effects.check.assert_called_with(False, 'I2C generator capture returned no data')


def test_spi_loopback_exact_mismatch_and_empty(isolate_hardware_side_effects):
    dev = device(); payload = bytes([0xA5, 0x3C, 0xDE, 0xAD])
    with patch.object(hv, '_get_jumper_pair', return_value=(3, 4)), \
         patch.object(hv, 'samples_to_channels', return_value=channels(4)), \
         patch.object(hv, 'decode_spi', return_value=list(payload)), patch.object(hv, '_restore_pin_map'):
        hv.test_gen_spi_loopback(dev)
    with patch.object(hv, '_get_jumper_pair', return_value=(3, 4)), \
         patch.object(hv, 'samples_to_channels', return_value=channels(4)), \
         patch.object(hv, 'decode_spi', return_value=[]), patch.object(hv, '_restore_pin_map'):
        hv.test_gen_spi_loopback(dev)
    dev.capture_with_gen.return_value = b''
    with patch.object(hv, '_get_jumper_pair', return_value=(3, 4)):
        hv.test_gen_spi_loopback(dev)
    isolate_hardware_side_effects.check.assert_called_with(False, 'SPI gen capture returned no data')


def test_accel_whoami_success_no_hit_and_exception(isolate_hardware_side_effects):
    dev = device(); dev.accel_whoami_spi.return_value = {0: 0x33, 1: 0}
    hv.test_accel_who_am_i(dev)
    dev.accel_whoami_spi.return_value = {}
    hv.test_accel_who_am_i(dev)
    dev.accel_whoami_spi.side_effect = OSError('bus')
    hv.test_accel_who_am_i(dev)
    assert isolate_hardware_side_effects.check.call_count == 3


@pytest.mark.parametrize('first_active,second_active', [(True, True), (False, False)])
def test_device_lifecycle_reopens_and_validates_both_captures(first_active, second_active):
    dev = device()
    with patch.object(hv, 'samples_to_channels', side_effect=[channels(256, first_active), channels(256, second_active)]), \
         patch.object(hv, 'log_floating_channel_activity'), patch.object(hv, 'check_channels_clean'):
        hv.test_device_lifecycle_sanity(dev)
    dev.close.assert_called_once_with()
    dev.open.assert_called_once_with()


def test_device_lifecycle_empty_captures(isolate_hardware_side_effects):
    dev = device(); dev.capture.side_effect = [b'', b'']
    hv.test_device_lifecycle_sanity(dev)
    calls = [call.args for call in isolate_hardware_side_effects.check.call_args_list]
    assert (False, 'pre-reopen capture returned no data') in calls
    assert (False, 'post-reopen capture returned no data') in calls
