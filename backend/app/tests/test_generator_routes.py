from app.hardware.device_models import DeviceCapabilities, GeneratorRouteCapability


def test_generator_routes_describe_optional_physical_wires_without_overclaiming():
    caps = DeviceCapabilities(
        generator_protocols=["rs485", "spi"],
        generator_routes=[
            GeneratorRouteCapability(
                protocol="rs485",
                physical=True,
                outputs={"a": "d1", "b": "d3"},
                features=["internal_de_timing"],
            ),
            GeneratorRouteCapability(
                protocol="spi",
                physical=True,
                outputs={"mosi": "configurable", "sclk": "configurable"},
                features=["capture_loopback"],
                detail="CS and MISO are not routed by this firmware",
            ),
        ],
    )

    spi = next(route for route in caps.generator_routes if route.protocol == "spi")
    rs485 = next(route for route in caps.generator_routes if route.protocol == "rs485")
    assert "capture_loopback" in spi.features
    assert "cs" not in spi.features
    assert "miso" not in spi.features
    assert "internal_de_timing" in rs485.features


def test_future_routes_can_advertise_cs_miso_and_swd_capture():
    caps = DeviceCapabilities(generator_routes=[
        GeneratorRouteCapability(
            protocol="spi",
            physical=True,
            outputs={"mosi": "d5", "sclk": "d4", "miso": "d6", "cs": "d7"},
            features=["capture_loopback", "cs", "miso"],
        ),
        GeneratorRouteCapability(
            protocol="swd",
            physical=True,
            outputs={"swclk": "d0", "swdio": "d1"},
            features=["transaction_capture"],
        ),
    ])

    assert {route.protocol for route in caps.generator_routes} == {"spi", "swd"}
    assert "cs" in caps.generator_routes[0].features
    assert "transaction_capture" in caps.generator_routes[1].features
