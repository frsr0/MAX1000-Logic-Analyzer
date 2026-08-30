import pytest

from app.hardware.base import HardwareError
from app.hardware.device_models import GeneratorConfig
from app.hardware.mock_device import MockDevice


def test_mock_routes_accept_every_advertised_optional_wire():
    """The mock device's route descriptors advertise cs/miso/de_pin/
    transaction_capture — validate_generator_config must accept requests that
    use them (a tautological field read-back would not prove this)."""
    device = MockDevice()
    device.connect()
    device.validate_generator_config(
        GeneratorConfig(protocol="spi", extra={"cs_pin": 7}))
    device.validate_generator_config(
        GeneratorConfig(protocol="spi", extra={"miso_pin": 6}))
    device.validate_generator_config(
        GeneratorConfig(protocol="rs485", extra={"de_pin": 2}))
    device.validate_generator_config(
        GeneratorConfig(protocol="swd"))


def test_route_validation_rejects_wire_not_advertised_by_firmware():
    """A requested optional wire must be rejected when the route descriptor
    does not list it as a feature (the capability boundary the tautological
    feature-set read-backs used to stand in for)."""

    class NoCsMisoMockDevice(MockDevice):
        def get_capabilities(self):
            caps = super().get_capabilities()
            caps = caps.model_copy(deep=True)
            for route in caps.generator_routes:
                if route.protocol == "spi":
                    route.features = [f for f in route.features
                                      if f not in ("cs", "miso")]
            return caps

    device = NoCsMisoMockDevice()
    device.connect()
    with pytest.raises(HardwareError,
                       match="SPI CS output is not routed by the connected "
                             "device firmware"):
        device.validate_generator_config(
            GeneratorConfig(protocol="spi", extra={"cs_pin": 7}))
    with pytest.raises(HardwareError,
                       match="SPI MISO output is not routed by the connected "
                             "device firmware"):
        device.validate_generator_config(
            GeneratorConfig(protocol="spi", extra={"miso_pin": 6}))


def test_route_validation_rejects_unrouted_swd_transaction_capture():
    """SWD requests assert transaction capture by default; without the route
    feature the config must be rejected rather than silently accepted."""

    class NoCaptureMockDevice(MockDevice):
        def get_capabilities(self):
            caps = super().get_capabilities()
            caps = caps.model_copy(deep=True)
            for route in caps.generator_routes:
                if route.protocol == "swd":
                    route.features = [f for f in route.features
                                      if f != "transaction_capture"]
            return caps

    device = NoCaptureMockDevice()
    device.connect()
    with pytest.raises(HardwareError,
                       match="SWD transaction capture is not routed by the "
                             "connected device firmware"):
        device.validate_generator_config(GeneratorConfig(protocol="swd"))
    # explicitly declining target capture still validates
    device.validate_generator_config(
        GeneratorConfig(protocol="swd",
                        extra={"capture_target_response": False}))


def test_route_validation_preserves_mock_only_protocols_without_physical_routes():
    device = MockDevice()
    device.connect()
    device.validate_generator_config(GeneratorConfig(protocol="pwm"))
    device.validate_generator_config(GeneratorConfig(protocol="square"))
