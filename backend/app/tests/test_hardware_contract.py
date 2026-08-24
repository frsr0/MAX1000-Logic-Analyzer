import types

import numpy as np
import pytest

from app.hardware.base import CaptureResult, HardwareError, validate_capture_result
from app.hardware.existing_host_adapter import ExistingHostAdapter, hardware_available


def test_capture_result_contract_accepts_aligned_digital_and_analog_channels():
    result = CaptureResult(
        sample_rate=1_000_000,
        digital=np.arange(4, dtype=np.uint16),
        analog={"a0": np.linspace(0.0, 1.0, 4, dtype=np.float32)},
        trigger_sample=2,
    )

    assert validate_capture_result(result) == 4


@pytest.mark.parametrize(
    "result, message",
    [
        (CaptureResult(sample_rate=0, digital=np.zeros(2, dtype=np.uint16)), "sample rate"),
        (CaptureResult(sample_rate=1, digital=np.zeros(2, dtype=np.uint16),
                       analog={"a0": np.zeros(1)}), "sample count"),
        (CaptureResult(sample_rate=1, digital=np.array([-1, 0])), "uint16"),
        (CaptureResult(sample_rate=1, digital=np.zeros(2, dtype=np.uint16),
                       trigger_sample=3), "trigger sample"),
        (CaptureResult(sample_rate=1), "no samples"),
    ],
)
def test_capture_result_contract_rejects_malformed_results(result, message):
    with pytest.raises(HardwareError, match=message):
        validate_capture_result(result)


def test_existing_host_adapter_accepts_injected_driver_loader():
    class FakeDevice:
        sample_clk = 200_000_000
        sys_clk = 100_000_000

        def open(self):
            pass

        def close(self):
            pass

        def reset(self):
            pass

        def set_analog_config(self, *_args, **_kwargs):
            pass

        def set_schmitt(self, *_args, **_kwargs):
            pass

        def get_metadata(self):
            return b"\x02"

        class spi:
            @staticmethod
            def flush():
                pass

    driver = types.SimpleNamespace(OLSDeviceSPI=FakeDevice)
    adapter = ExistingHostAdapter(lambda: (driver, object()))

    metadata = adapter.connect()

    assert metadata.driver == "ols_spi"
    assert adapter.is_connected()
    adapter.disconnect()


def test_hardware_available_accepts_injected_driver_loader():
    driver = types.SimpleNamespace(find_spi_device=lambda: True)

    assert hardware_available(lambda: (driver, object())) is True
