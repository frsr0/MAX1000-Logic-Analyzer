import threading
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
from pydantic import ValidationError

from app.hardware.existing_host_adapter import ExistingHostAdapter
from app.hardware.mock_device import MockDevice
from app.hardware import mock_signals
from app.hardware.base import HardwareError
from app.capture.session_store import SessionStore
from app.capture.session import DeviceMetadata
from app.api import mil as mil_api
from fastapi import HTTPException
import app.mil.mcp4725 as dac
from app.mil.mcp4725 import (Mcp4725TestRequest, Mcp4725WaveformRequest,
                              run_waveform, test_mcp4725 as run_dac_test,
                              waveform_codes)


class Mcp4725Tests(unittest.TestCase):
    def test_mil_routes_enforce_control_and_report_dac_conflicts(self):
        calls = []
        with patch.object(mil_api, "require_control",
                          side_effect=lambda client: calls.append(client)), \
                patch.object(mil_api.accelerometer, "status",
                             return_value={"running": False}):
            with patch.object(mil_api.capture_manager, "capture_state", "armed"):
                with self.assertRaises(HTTPException) as raised:
                    mil_api.mil_mcp4725_test(Mcp4725TestRequest(), "owner")
                self.assertEqual(raised.exception.status_code, 409)
            with patch.object(mil_api.capture_manager, "capture_state", "idle"), \
                    patch.object(mil_api.capture_manager,
                                 "_accelerometer_reserved", True):
                with self.assertRaises(HTTPException) as raised:
                    mil_api.mil_mcp4725_waveform(
                        Mcp4725WaveformRequest(), "owner")
                self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(calls, ["owner", "owner"])

    def test_mil_dac_routes_map_hardware_failures_and_running_accelerometer(self):
        with patch.object(mil_api, "require_control"), \
                patch.object(mil_api.accelerometer, "status",
                             return_value={"running": True}):
            with self.assertRaises(HTTPException) as raised:
                mil_api.mil_mcp4725_test(Mcp4725TestRequest(), "owner")
            self.assertEqual(raised.exception.status_code, 409)
            with self.assertRaises(HTTPException) as raised:
                mil_api.mil_mcp4725_waveform(
                    Mcp4725WaveformRequest(), "owner")
            self.assertEqual(raised.exception.status_code, 409)

        with patch.object(mil_api, "require_control"), \
                patch.object(mil_api.accelerometer, "status",
                             return_value={"running": False}), \
                patch.object(mil_api.capture_manager, "capture_state", "idle"), \
                patch.object(mil_api.capture_manager,
                             "_accelerometer_reserved", False), \
                patch.object(mil_api.capture_manager, "require_device",
                             side_effect=HardwareError("offline")):
            for handler, request in (
                    (mil_api.mil_mcp4725_test, Mcp4725TestRequest()),
                    (mil_api.mil_mcp4725_waveform,
                     Mcp4725WaveformRequest())):
                with self.assertRaises(HTTPException) as raised:
                    handler(request, "owner")
                self.assertEqual(raised.exception.status_code, 502)
                self.assertEqual(raised.exception.detail, "offline")

        device = Mock()
        device.get_metadata.return_value = SimpleNamespace(mock=False)
        device.mcp4725_write_and_measure.side_effect = RuntimeError(
            "FTDI read failed")
        with patch.object(mil_api, "require_control"), \
                patch.object(mil_api.accelerometer, "status",
                             return_value={"running": False}), \
                patch.object(mil_api.capture_manager, "capture_state", "idle"), \
                patch.object(mil_api.capture_manager,
                             "_accelerometer_reserved", False), \
                patch.object(mil_api.capture_manager, "require_device",
                             return_value=device):
            with self.assertRaises(HTTPException) as raised:
                mil_api.mil_mcp4725_test(
                    Mcp4725TestRequest(codes=[2048]), "owner")
        self.assertEqual(raised.exception.status_code, 502)
        self.assertIn("MCP4725 I2C capture failed", raised.exception.detail)
        self.assertIn("FTDI read failed", raised.exception.detail)

    def test_mil_dac_routes_return_results_and_publish_created_waveform(self):
        session = Mock()
        session.summary.return_value = {"id": "session-1"}
        result = {"session_id": "session-1", "passed": True}
        with patch.object(mil_api, "require_control"), \
                patch.object(mil_api.accelerometer, "status",
                             return_value={"running": False}), \
                patch.object(mil_api.capture_manager, "capture_state", "idle"), \
                patch.object(mil_api.capture_manager,
                             "_accelerometer_reserved", False), \
                patch.object(mil_api.capture_manager, "require_device",
                             return_value=Mock()), \
                patch.object(mil_api, "test_mcp4725",
                             return_value={"passed": True}) as run_test:
            response = mil_api.mil_mcp4725_test(Mcp4725TestRequest(), "owner")
            self.assertTrue(response["passed"])
            run_test.assert_called_once()

        with patch.object(mil_api, "require_control"), \
                patch.object(mil_api.accelerometer, "status",
                             return_value={"running": False}), \
                patch.object(mil_api.capture_manager, "capture_state", "idle"), \
                patch.object(mil_api.capture_manager,
                             "_accelerometer_reserved", False), \
                patch.object(mil_api.capture_manager, "require_device",
                             return_value=Mock()), \
                patch.object(mil_api.capture_manager, "store") as store, \
                patch.object(mil_api.capture_manager, "last_session_id", None), \
                patch.object(mil_api, "run_waveform",
                             return_value=result) as run_waveform, \
                patch.object(mil_api.websocket_manager,
                             "publish_threadsafe") as publish:
            store.get.return_value = session
            response = mil_api.mil_mcp4725_waveform(
                Mcp4725WaveformRequest(), "owner")
            self.assertEqual(response, result)
            run_waveform.assert_called_once()
            store.get.assert_called_once_with("session-1")
            publish.assert_called_once_with(
                "status", "session_created", {"id": "session-1"})

    def test_mil_waveform_does_not_publish_when_session_lookup_misses(self):
        result = {"session_id": "expired-session", "passed": True}
        with patch.object(mil_api, "require_control"), \
                patch.object(mil_api.accelerometer, "status",
                             return_value={"running": False}), \
                patch.object(mil_api.capture_manager, "capture_state", "idle"), \
                patch.object(mil_api.capture_manager,
                             "_accelerometer_reserved", False), \
                patch.object(mil_api.capture_manager, "require_device",
                             return_value=Mock()), \
                patch.object(mil_api.capture_manager, "store") as store, \
                patch.object(mil_api, "run_waveform", return_value=result), \
                patch.object(mil_api.websocket_manager,
                             "publish_threadsafe") as publish:
            store.get.return_value = None
            response = mil_api.mil_mcp4725_waveform(
                Mcp4725WaveformRequest(), "owner")
        self.assertEqual(response, result)
        publish.assert_not_called()

    def test_dac_test_rejects_unsupported_device_and_short_or_missing_acks(self):
        unsupported = Mock()
        unsupported.get_metadata.return_value = SimpleNamespace(mock=False)
        del unsupported.mcp4725_write_and_measure
        with self.assertRaises(HardwareError):
            run_dac_test(unsupported, Mcp4725TestRequest(codes=[2048]))

        class AckDevice:
            def __init__(self, acks):
                self.acks = acks

            def get_metadata(self):
                return SimpleNamespace(mock=False)

            def mcp4725_write_and_measure(self, *_args):
                return np.full(32, 1.65)

            def mcp4725_last_acknowledgements(self):
                return self.acks

        short = run_dac_test(AckDevice((True, True)),
                             Mcp4725TestRequest(codes=[2048]))
        self.assertFalse(short["bus_passed"])
        class NoAckMethod(AckDevice):
            mcp4725_last_acknowledgements = None

        no_ack_method = run_dac_test(NoAckMethod(None),
                                     Mcp4725TestRequest(codes=[2048]))
        self.assertTrue(no_ack_method["bus_passed"])

    def test_three_point_check_uses_measured_analog_levels(self):
        device = Mock()
        device.get_metadata.return_value = SimpleNamespace(mock=False)
        device.mcp4725_write_and_measure.side_effect = [
            np.full(32, 0.02), np.full(32, 1.65), np.full(32, 3.28),
        ]
        result = run_dac_test(device, Mcp4725TestRequest(adc_channel=4))
        self.assertTrue(result["passed"])
        self.assertEqual([step["code"] for step in result["steps"]],
                         [0, 2048, 4095])
        self.assertEqual(device.mcp4725_write_and_measure.call_count, 3)
        self.assertEqual(result["analog_input"], "AIN6 (J1/8)")
        self.assertTrue(all(call.args[-1] == 4
                            for call in device.mcp4725_write_and_measure.call_args_list))

    def test_mock_three_point_check_is_explicitly_simulated_and_validated(self):
        with self.assertRaises(ValidationError):
            Mcp4725TestRequest(codes=[4096])
        with self.assertRaises(ValidationError):
            Mcp4725TestRequest(sda_pin=7, scl_pin=7)
        device = MockDevice()
        device.connect()
        result = run_dac_test(device, Mcp4725TestRequest())
        self.assertTrue(result["mock"])
        self.assertTrue(result["passed"])
        self.assertEqual((result["sda_pin"], result["scl_pin"]), (5, 4))
        self.assertEqual((result["sda"], result["scl"]),
                         ("D5 (J1/14)", "D4 (J1/13)"))

    def test_wrong_voltage_fails_even_when_i2c_send_returns(self):
        device = Mock()
        device.get_metadata.return_value = SimpleNamespace(mock=False)
        device.mcp4725_write_and_measure.return_value = np.zeros(32)
        result = run_dac_test(device, Mcp4725TestRequest(codes=[2048]))
        self.assertFalse(result["passed"])
        self.assertFalse(result["steps"][0]["passed"])

    def test_missing_physical_ack_fails_a_correct_voltage_check(self):
        class NackDevice:
            def get_metadata(self):
                return SimpleNamespace(mock=False)

            def mcp4725_write_and_measure(self, *_args):
                return np.full(32, 1.65)

            def mcp4725_last_acknowledgements(self):
                return (False, False, False)

        result = run_dac_test(NackDevice(), Mcp4725TestRequest(codes=[2048]))
        self.assertTrue(result["steps"][0]["voltage_passed"])
        self.assertFalse(result["steps"][0]["bus_passed"])
        self.assertFalse(result["passed"])

    def test_adapter_writes_dac_frame_then_samples_ain3(self):
        adapter = object.__new__(ExistingHostAdapter)
        adapter._lock = threading.RLock()
        adapter._dev = Mock()
        scl, sda = mock_signals.i2c_signal(
            2048, 1_000_000, 100_000, 0x60, True,
            bytes([0x08, 0x00]), start_sample=100)
        digital = ((sda.astype(np.uint16) << 2)
                   | (scl.astype(np.uint16) << 1))
        adapter._dev.capture_with_gen.return_value = digital.tobytes()
        adapter.capture = Mock(return_value=SimpleNamespace(
            analog={"a3": np.full(256, 1.65)}))
        samples = adapter.mcp4725_write_and_measure(0x60, 2048, 22, 18, 3)
        self.assertEqual(len(samples), 256)
        self.assertEqual(adapter.mcp4725_last_acknowledgements(), (True, True, True))
        self.assertEqual(
            adapter._dev.capture_with_gen.call_args.kwargs["i2c_frame"],
            bytes([0xC0, 0x08, 0x00]))
        self.assertEqual(adapter._dev.capture_with_gen.call_args.kwargs["i2c_read_len"], 0)
        self.assertEqual(adapter._dev.capture_with_gen.call_args.kwargs["i2c_tx_pin"], 22)
        self.assertEqual(adapter._dev.capture_with_gen.call_args.kwargs["i2c_scl_pin"], 18)
        adapter._dev.raw_mode.assert_called_once_with(False)
        self.assertEqual(adapter.capture.call_args.args[0].mode, "analog_all")
        self.assertEqual(adapter.capture.call_args.args[0].num_samples, 64)

    def test_adapter_measures_output_after_write_even_when_loopback_has_nack(self):
        adapter = object.__new__(ExistingHostAdapter)
        adapter._lock = threading.RLock()
        adapter._dev = Mock()
        scl, sda = mock_signals.i2c_signal(
            2048, 1_000_000, 100_000, 0x60, True,
            bytes([0x08, 0x00]), start_sample=100,
            ack_per_byte=[False, False, False])
        digital = ((sda.astype(np.uint16) << 2)
                   | (scl.astype(np.uint16) << 1))
        adapter._dev.capture_with_gen.return_value = digital.tobytes()
        adapter.capture = Mock(return_value=SimpleNamespace(
            analog={"a3": np.full(256, 1.65)}))
        samples = adapter.mcp4725_write_and_measure(0x60, 2048, 22, 18)
        self.assertEqual(len(samples), 256)
        self.assertEqual(adapter.mcp4725_last_acknowledgements(), (False, False, False))
        adapter.capture.assert_called_once()

    def test_all_selectable_i2c_pin_pairs_route_to_physical_ack_capture(self):
        adapter = object.__new__(ExistingHostAdapter)
        adapter._lock = threading.RLock()
        adapter._dev = Mock()
        adapter.capture = Mock(return_value=SimpleNamespace(
            analog={f"a{channel}": np.full(32, channel * 0.1)
                    for channel in range(1, 5)}))
        scl, sda = mock_signals.i2c_signal(
            2048, 1_000_000, 100_000, 0x60, True,
            bytes([0x08, 0x00]), start_sample=100)
        for sda_pin in range(23):
            for scl_pin in range(23):
                if sda_pin == scl_pin:
                    with self.assertRaises(ValidationError):
                        Mcp4725TestRequest(sda_pin=sda_pin, scl_pin=scl_pin)
                    continue
                occupied = {pin for pin in (sda_pin, scl_pin) if pin < 16}
                spare = [pin for pin in range(16) if pin not in occupied]
                # capture_with_gen routes SCL echo and physical SDA onto its
                # spare capture channels, regardless of the selected pins.
                scl_capture = spare[1]
                sda_capture = spare[2]
                digital = ((sda.astype(np.uint16) << sda_capture)
                           | (scl.astype(np.uint16) << scl_capture))
                adapter._dev.capture_with_gen.return_value = digital.tobytes()
                analog_channel = 1 + (sda_pin % 4)
                samples = adapter.mcp4725_write_and_measure(
                    0x60, 2048, sda_pin, scl_pin, analog_channel)
                self.assertEqual(adapter.mcp4725_last_acknowledgements(),
                                 (True, True, True), (sda_pin, scl_pin))
                self.assertAlmostEqual(float(samples[0]), analog_channel * 0.1)
                kwargs = adapter._dev.capture_with_gen.call_args.kwargs
                self.assertEqual((kwargs["i2c_tx_pin"], kwargs["i2c_scl_pin"]),
                                 (sda_pin, scl_pin))

    def test_adapter_fast_write_uses_external_i2c_rx_ack_path(self):
        adapter = object.__new__(ExistingHostAdapter)
        adapter._lock = threading.RLock()
        adapter._dev = Mock()
        adapter._dev.external_i2c_write.return_value = (True, True, True)
        adapter.mcp4725_prepare_fast(4, 3)
        acks = adapter.mcp4725_write_fast(0x60, 2048)
        adapter._dev.external_i2c_prepare.assert_called_once_with(4, 3)
        adapter._dev.external_i2c_write.assert_called_once_with(
            bytes([0xC0, 0x08, 0x00]))
        self.assertEqual(acks, (True, True, True))
        self.assertEqual(adapter.mcp4725_last_acknowledgements(), acks)

    def test_waveform_code_shapes_and_limits(self):
        request = Mcp4725WaveformRequest(
            waveform="triangle", frequency_hz=1, steps_per_cycle=8,
            cycles=1, min_v=0, max_v=3.3)
        codes = waveform_codes(request)
        self.assertEqual(len(codes), 8)
        self.assertEqual(codes[0], 0)
        self.assertEqual(codes[4], 4095)
        self.assertEqual(codes[2], codes[6])
        with self.assertRaises(ValidationError):
            Mcp4725WaveformRequest(frequency_hz=2, steps_per_cycle=24)
        sawtooth = waveform_codes(Mcp4725WaveformRequest(
            waveform="sawtooth", frequency_hz=0.25,
            steps_per_cycle=8, cycles=1, min_v=0, max_v=3.3))
        self.assertEqual(sawtooth[0], 0)
        self.assertLess(sawtooth[-1], 4095)
        with self.assertRaises(ValidationError):
            Mcp4725WaveformRequest(min_v=2, max_v=1)
        # The request bounds cap a run at 3 cycles / 0.25 Hz = 12 seconds.
        maximum_duration = Mcp4725WaveformRequest(
            frequency_hz=0.25, cycles=3)
        self.assertEqual(maximum_duration.cycles / maximum_duration.frequency_hz,
                         12)

    def test_mock_scope_persists_quantized_dac_waveform(self):
        device = MockDevice()
        device.connect()
        request = Mcp4725WaveformRequest(
            waveform="square", frequency_hz=1, steps_per_cycle=8,
            cycles=2, min_v=0, max_v=3.3, sda_pin=22, scl_pin=0)
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(Path(directory))
            result = run_waveform(device, request, store)
            session = store.get(result["session_id"])
            scope = store.load_waveform(result["session_id"])
            self.assertTrue(result["mock"])
            self.assertTrue(result["passed"])
            self.assertEqual(result["sda"], "PIO_08 (PMOD/10)")
            self.assertTrue(session.device.mock)
            self.assertEqual(session.channels[0].id, "a3")
            self.assertEqual(len(scope.analog["a3"]), 128)
            self.assertAlmostEqual(float(scope.analog["a3"][0]), 0, places=3)
            self.assertAlmostEqual(float(scope.analog["a3"][40]), 3.3, places=2)

    def test_hardware_waveform_sends_each_code_on_selected_pins(self):
        device = Mock()
        device.get_metadata.return_value = DeviceMetadata(
            driver="ols_spi", device_name="test MAX1000", mock=False)
        clock = [0.0]

        def measure(address, code, sda_pin, scl_pin, adc_channel):
            self.assertEqual((address, sda_pin, scl_pin, adc_channel), (0x61, 22, 0, 3))
            clock[0] += 0.005
            return np.full(32, code * 3.3 / 4096)

        device.mcp4725_write_and_measure.side_effect = measure
        device.mcp4725_write_fast.return_value = (True, True, True)
        request = Mcp4725WaveformRequest(
            address=0x61, sda_pin=22, scl_pin=0, waveform="sine",
            frequency_hz=1, steps_per_cycle=8, cycles=1)
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(Path(directory))
            with patch.object(dac.time, "monotonic", side_effect=lambda: clock[0]), \
                    patch.object(dac.time, "sleep", side_effect=lambda seconds: clock.__setitem__(0, clock[0] + seconds)):
                result = run_waveform(device, request, store)
            self.assertFalse(result["mock"])
            self.assertTrue(result["passed"])
            self.assertEqual(result["actual_frequency_hz"], 1.0)
            self.assertEqual(result["trace_source"], "preflight_measured_levels")
            self.assertEqual(device.mcp4725_write_and_measure.call_count,
                             len(set(waveform_codes(request))))
            device.mcp4725_prepare_fast.assert_called_once_with(22, 0)
            self.assertEqual(device.mcp4725_write_fast.call_count, 8)
            scope = store.load_waveform(result["session_id"])
            self.assertEqual(len(scope.analog["a3"]), 64)
            self.assertTrue(np.all(scope.analog["a3"][:8] == scope.analog["a3"][0]))

    def test_slow_hardware_waveform_fails_timing_even_with_correct_voltage(self):
        device = Mock()
        device.get_metadata.return_value = DeviceMetadata(mock=False)
        clock = [0.0]

        def slow_write(_address, _code):
            clock[0] += 0.25
            return (True, True, True)

        device.mcp4725_write_and_measure.side_effect = lambda _address, code, *_pins: (
            np.full(32, code * 3.3 / 4096))
        device.mcp4725_write_fast.side_effect = slow_write
        with patch.object(dac.time, "monotonic", side_effect=lambda: clock[0]), \
                patch.object(dac.time, "sleep", side_effect=lambda seconds: clock.__setitem__(0, clock[0] + seconds)):
            result = run_waveform(device, Mcp4725WaveformRequest(
                frequency_hz=1, steps_per_cycle=8, cycles=1), Mock())
        self.assertTrue(result["voltage_passed"])
        self.assertFalse(result["timing_passed"])
        self.assertFalse(result["passed"])

    def test_slow_adc_preflight_does_not_reduce_timed_output_rate(self):
        device = Mock()
        device.get_metadata.return_value = DeviceMetadata(mock=False)
        clock = [0.0]

        def slow_measure(_address, code, _sda, _scl, _adc_channel):
            clock[0] += 0.4
            return np.full(32, code * 3.3 / 4096)

        def fast_write(_address, _code):
            clock[0] += 0.01
            return (True, True, True)

        device.mcp4725_write_and_measure.side_effect = slow_measure
        device.mcp4725_write_fast.side_effect = fast_write
        request = Mcp4725WaveformRequest(
            waveform="sine", frequency_hz=0.5, steps_per_cycle=8,
            cycles=1)
        with patch.object(dac.time, "monotonic", side_effect=lambda: clock[0]), \
                patch.object(dac.time, "sleep", side_effect=lambda seconds: clock.__setitem__(0, clock[0] + seconds)):
            result = run_waveform(device, request, Mock())
        self.assertTrue(result["passed"])
        self.assertEqual(result["actual_frequency_hz"], 0.5)
        self.assertEqual(device.mcp4725_write_and_measure.call_count,
                         len(set(waveform_codes(request))))
        self.assertEqual(device.mcp4725_write_fast.call_count, 8)

    def test_waveform_rejects_missing_timed_write_support_and_reports_nack(self):
        unsupported = Mock()
        unsupported.get_metadata.return_value = DeviceMetadata(mock=False)
        del unsupported.mcp4725_write_fast
        with self.assertRaises(HardwareError):
            run_waveform(unsupported, Mcp4725WaveformRequest(), Mock())

        device = Mock()
        device.get_metadata.return_value = DeviceMetadata(mock=False)
        device.mcp4725_write_and_measure.side_effect = lambda _address, code, *_: (
            np.full(32, code * 3.3 / 4096))
        device.mcp4725_write_fast.return_value = (True, False, True)
        clock = [0.0]
        def advance_sleep(seconds):
            clock[0] += seconds
        with patch.object(dac.time, "monotonic", side_effect=lambda: clock[0]), \
                patch.object(dac.time, "sleep", side_effect=advance_sleep):
            result = run_waveform(device, Mcp4725WaveformRequest(
                frequency_hz=0.5, steps_per_cycle=8, cycles=1), Mock())
        self.assertTrue(result["voltage_passed"])
        self.assertTrue(result["timing_passed"])
        self.assertFalse(result["bus_passed"])
        self.assertFalse(result["passed"])

    def test_waveform_checks_preflight_acknowledgements_when_supported(self):
        class AckHardware:
            def get_metadata(self):
                return DeviceMetadata(mock=False)

            def mcp4725_write_and_measure(self, _address, code, *_pins):
                return np.full(32, code * 3.3 / 4096)

            def mcp4725_last_acknowledgements(self):
                return (True, False, True)

            def mcp4725_prepare_fast(self, *_pins):
                pass

            def mcp4725_write_fast(self, _address, _code):
                return (True, True, True)

        clock = [0.0]
        def advance_sleep(seconds):
            clock[0] += seconds
        with patch.object(dac.time, "monotonic", side_effect=lambda: clock[0]), \
                patch.object(dac.time, "sleep", side_effect=advance_sleep):
            result = run_waveform(AckHardware(), Mcp4725WaveformRequest(
                frequency_hz=0.5, steps_per_cycle=8, cycles=1), Mock())
        self.assertTrue(result["voltage_passed"])
        self.assertTrue(result["timing_passed"])
        self.assertFalse(result["bus_passed"])


if __name__ == "__main__":
    unittest.main()
