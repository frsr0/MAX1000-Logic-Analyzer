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
    dev.pkt.arm_capture.return_value = 0
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
def test_fast_capture_full_payload_paths(debug, active, isolate_hardware_side_effects):
    dev = device()
    dev.read_capture_range.return_value = b'x' * 1024 + b'y' * 1024
    with patch.object(hv, 'samples_to_channels', return_value=channels(1024, active)), \
         patch.object(hv, 'log_floating_channel_activity') as activity:
        hv.test_fast_capture(dev, debug_on=debug)
    assert activity.called
    dev.read_capture_range.assert_called_once_with(0, 1024)
    if debug:
        dev.set_debug_ch0.assert_called_with(True, freq_hz=pytest.approx(dev.sys_clk / 1024))
        assert any("fast CH0 debug PWM transitions" in call.args[1]
                   for call in isolate_hardware_side_effects.check.call_args_list)


def test_fast_capture_empty_and_partial_blocks(isolate_hardware_side_effects):
    dev = device(); dev.read_capture_range.return_value = b''
    hv.test_fast_capture(dev)
    isolate_hardware_side_effects.check.assert_any_call(False, 'fast mode capture returned data')


def test_max_speed_capture_full_and_empty(isolate_hardware_side_effects):
    dev = device(); dev.read_capture_range.return_value = b'x' * 1024 + b'y' * 1024
    dev.pkt.get_status.return_value = {}
    with patch.object(hv, 'samples_to_channels', return_value=channels(1024)), \
         patch.object(hv, 'log_floating_channel_activity'):
        hv.test_max_speed_capture(dev)
    dev.set_live_gen.assert_called_once()
    dev.clear_live_gen.assert_called_once()
    assert any("max-speed CH0 source is non-static" in call.args[1]
               for call in isolate_hardware_side_effects.check.call_args_list)
    dev = device(); dev.read_capture_range.return_value = b''
    dev.pkt.get_status.return_value = {}
    hv.test_max_speed_capture(dev)
    isolate_hardware_side_effects.check.assert_called_with(False, 'max-speed capture returned no data')


@pytest.mark.parametrize('debug,active', [(False, True), (True, True), (True, False)])
def test_continuous_capture_retries_and_processes_buffer(debug, active, isolate_hardware_side_effects):
    dev = device(); dev.pkt.read_capture_block.side_effect = [b'', b'x' * 1024]
    with patch.object(hv, 'samples_to_channels', return_value=channels(512, active)), \
         patch.object(hv, 'log_floating_channel_activity'), patch.object(hv, 'check_channels_clean'):
        hv.test_continuous_capture(dev, debug_on=debug)
    assert dev.pkt.read_capture_block.call_count == 2
    if debug:
        assert any("continuous CH0 debug PWM transitions" in call.args[1]
                   for call in isolate_hardware_side_effects.check.call_args_list)


def test_continuous_capture_empty_reports_failure(isolate_hardware_side_effects):
    dev = device(); dev.pkt.read_capture_block.return_value = b''
    hv.test_continuous_capture(dev)
    isolate_hardware_side_effects.check.assert_any_call(False, 'continuous capture returned no data')


@pytest.mark.parametrize('debug,active,expected', [
    (False, False, True),
    (False, True, False),
    (True, True, True),
    (True, False, False),
])
def test_trigger_edge_capture_paths(debug, active, expected, isolate_hardware_side_effects):
    dev = device()
    with patch.object(hv, 'samples_to_channels', return_value=channels(512, active)), \
         patch.object(hv, 'check_channels_clean'):
        hv.test_trigger_edge(dev, debug_on=debug)
    assertions = [call.args[0] for call in isolate_hardware_side_effects.check.call_args_list]
    assert expected in assertions


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


def _square_row(cycles=40, half=102, lead=4):
    """1 MHz square wave at 200 MS/s: 100.4-sample half periods."""
    row = [1] * lead
    for _ in range(cycles):
        row += [0] * half + [1] * half
    return row


def test_max_rate_kept_prefix_reports_truncation_and_a_clean_prefix(
        isolate_hardware_side_effects):
    dev = device()
    dev.pkt.read_register.return_value = 3          # pump overflow observed
    dev.last_capture_truncated = True
    row = _square_row()
    with patch.object(hv, "samples_to_channels",
                      return_value=([row[:] for _ in range(hv.NUM_CHANNELS)],
                                    len(row))):
        hv.test_max_rate_kept_prefix(dev)

    dev.set_debug_ch0.assert_any_call(False)        # always released
    verdicts = [c.args[0] for c in
                isolate_hardware_side_effects.check.call_args_list]
    assert verdicts and all(verdicts)
    assert len(verdicts) == 4


def test_max_rate_kept_prefix_aborts_cleanly_without_data(
        isolate_hardware_side_effects):
    dev = device()
    dev.capture.return_value = b""
    hv.test_max_rate_kept_prefix(dev)
    isolate_hardware_side_effects.check.assert_called_once_with(
        False, "200 MS/s capture returned no data")


def test_readback_alignment_checks_exactly_n_changed_words(
        isolate_hardware_side_effects):
    dev = device()
    before = b"\xff\xff" * 4096
    after_two = b"\x01\x00" * 2 + b"\xff\xff" * (4096 - 2)
    after_three_thousand = b"\x02\x00" * 3000 + b"\xff\xff" * (4096 - 3000)
    dev.read_capture_range.side_effect = [before, after_two,
                                          before, after_three_thousand]
    dev.capture.side_effect = [b"\x00\x00" * 2, b"\x00\x00" * 3000]

    hv.test_readback_alignment_exact_word_count(dev)

    verdicts = [c.args[0] for c in
                isolate_hardware_side_effects.check.call_args_list]
    assert verdicts == [True, True, True, True]
    # the pre-fill helper leaves the device back in digital mode
    dev.set_analog_config.assert_any_call(hv.MODE_DIGITAL)


def test_readback_time_oracle_accepts_the_simulated_first_low_index(
        isolate_hardware_side_effects):
    dev = device()
    dev.capture_with_gen.return_value = b"raw"
    rows = []
    for want in hv.ORACLE_FIRST_LOW_INDEX.values():
        row = [1] * 1500
        row[want] = 0
        rows.append(([row[:] for _ in range(hv.NUM_CHANNELS)], 1500))
    with patch.object(hv, "samples_to_channels", side_effect=rows):
        hv.test_readback_absolute_time_oracle(dev)

    verdicts = [c.args[0] for c in
                isolate_hardware_side_effects.check.call_args_list]
    assert verdicts == [True, True, True]
    assert dev.capture_with_gen.call_count == 3


def test_deep_capture_address_uniqueness_flags_aliasing(
        isolate_hardware_side_effects):
    """The payload must appear once and windows a multiple of 32,768 samples
    apart must differ; a readback address that aliases repeats the first
    window and the check fails."""
    dev = device()
    dev.capture_with_gen.return_value = b"deep"
    ns = hv.DEEP_CAPTURE_SAMPLES
    period = 2000
    row = [0] * ns
    for start in range(0, ns, period):
        row[start:start + period // 2] = [1] * len(row[start:start + period // 2])
    rows = [row[:] for _ in range(hv.NUM_CHANNELS)]
    payload = bytes(range(33, 90))
    decoded = [SimpleNamespace(value=b) for b in payload + b"\x00"]

    with patch.object(hv, "_restore_pin_map"), \
         patch.object(hv, "_get_jumper_pair", return_value=(3, 7)), \
         patch.object(hv, "samples_to_channels", return_value=(rows, ns)), \
         patch.object(hv, "decode_uart_safe", return_value=decoded), \
         patch.object(hv.time, "sleep"):
        hv.test_deep_capture_address_uniqueness(dev)

    verdicts = [c.args[0] for c in
                isolate_hardware_side_effects.check.call_args_list]
    # sample count, "appears exactly once", span, and the three window lags
    assert verdicts == [True] * 6
    dev.capture_with_gen.assert_called_once()


def test_deep_capture_address_uniqueness_skips_without_a_wired_pair(
        isolate_hardware_side_effects):
    dev = device()
    with patch.object(hv, "_restore_pin_map"), \
         patch.object(hv, "_get_jumper_pair", return_value=None), \
         patch.object(hv.time, "sleep"):
        hv.test_deep_capture_address_uniqueness(dev)

    isolate_hardware_side_effects.check.assert_not_called()
    dev.capture_with_gen.assert_not_called()


def test_deep_capture_address_uniqueness_aborts_on_an_empty_capture(
        isolate_hardware_side_effects):
    dev = device()
    dev.capture_with_gen.return_value = b""
    with patch.object(hv, "_restore_pin_map"), \
         patch.object(hv, "_get_jumper_pair", return_value=(3, 7)), \
         patch.object(hv.time, "sleep"):
        hv.test_deep_capture_address_uniqueness(dev)

    isolate_hardware_side_effects.check.assert_called_once_with(
        False, "deep capture returned no data")
