# Measurements

**Directory:** `backend/app/measurements/`

Measurements are session analysis objects. They compute over the whole
capture, a saved region, or the current cursor pair without mutating waveform
data. Results are saved on the `MeasurementInstance`.

## Model

```python
class MeasurementInstance(BaseModel):
    id: str
    type: str
    channels: list[str]
    scope: Literal["capture", "cursors", "region"]
    region: list[int] | None
    settings: dict
    result: dict | None
    error: str | None
```

## Catalog

| Category | Representative IDs |
|---|---|
| Digital basics | `frequency`, `period`, `duty_cycle`, `pulse_width`, `edge_count`, `min_pulse`, `max_pulse` |
| Digital statistics | `dig_period_stats`, `dig_pulse_stats`, `dig_pulse_histogram`, `dig_glitch_count`, `dig_transition_rate`, `dig_jitter`, `dig_period_histogram`, `dig_setup_hold`, `dig_channel_skew` |
| Analog basics | `min`, `max`, `peak_to_peak`, `mean`, `rms`, `frequency` |
| Analog timing/quality | `ana_period`, `ana_duty`, `ana_rise_time`, `ana_fall_time`, `ana_overshoot`, `ana_undershoot`, `ana_noise`, `ana_crest` |
| Bus | `bus_value` |
| Protocol | `proto_packet_count`, `proto_error_count`, `proto_nack_count`, `proto_uart_framing`, `proto_uart_parity`, `proto_byte_rate`, `proto_utilisation`, `proto_inter_packet`, `proto_response_latency` |

Protocol measurements require completed decoder events. Multi-channel digital
measurements such as setup/hold and skew validate their channel count before
computing.

## API

```text
GET    /api/measurements/types
POST   /api/sessions/{id}/measurements
PATCH  /api/sessions/{id}/measurements/{measurement_id}
DELETE /api/sessions/{id}/measurements/{measurement_id}
GET    /api/sessions/{id}/measurements/results?cursor_a=&cursor_b=
```

The session detail response is the source of truth for the current measurement
instances; there is no separate collection GET endpoint under a session.

## UI and testing

The Measurement panel creates instances, selects scope/channels, and requests
fresh results when cursors change. Backend tests cover digital, analog, bus,
protocol, empty-window, and invalid-channel behavior. Playwright covers the
panel against a captured fixture.

![Measurement panel](../../../frontend/test-results/screenshots/measurements.png)

Trigger search and waveform query behavior are documented separately in
[Triggers](triggers.md) and [Waveform Service](waveform-service.md).
