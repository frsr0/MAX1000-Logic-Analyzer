# Triggers

**Directory:** `backend/app/triggers/`

The trigger layer classifies a requested trigger as `hardware`,
`post_capture`, or `unavailable`, validates its fields, and provides software
search for triggers that are richer than the connected FPGA path.

## Trigger model

`TriggerConfig` is defined with the session model and currently accepts:

```text
none, rising, falling, any_edge, high, low, pattern, bus_value,
pulse_wider, pulse_narrower, timeout, sequence, uart_byte,
i2c_address, i2c_nack, spi_byte, glitch, decoder_error, generic_pattern
```

Shared fields cover channel indices/references, value/pattern, width and
qualification windows, occurrence, holdoff/rearm, pre-trigger samples, and
capture position. `generic_pattern` also carries clock/start source and edge,
frame width, match mask, and bit order.

## Hardware execution

The connected MAX1000 advertises its supported trigger capabilities. Basic
capture-engine controls handle immediate/edge/value behavior; the
`Generic_Pattern_Trigger` handles clocked serial/data pattern matching. The
host configures the applicable mask/value, timing, framing, and pre-trigger
registers before arm.

Two connected-board checks prove the generic trigger path:

| Test | Scope |
|---|---|
| 14f | Internal baud counter → shift register → comparator → trigger → capture completion |
| 14g | UART `0x55` through a discovered physical jumper with `match_mask=0xFF` |

The UI and backend must use the device's capability descriptor rather than
assuming that every trigger type runs in FPGA hardware.

## Software search

`software_trigger.py` searches an existing immutable session for edges,
levels, patterns, bus values, pulse widths, timeouts, sequences, protocol
bytes/addresses/NACKs, glitches, and decoder errors. It supports the nth
occurrence and qualification windows, then returns a sample index or an
explicit no-match result.

```text
POST /api/sessions/{session_id}/trigger-search
```

When `auto_scope` and a decoder instance are supplied, the response can also
identify the relevant event window. The frontend moves the waveform viewport
to the selected sample and supports previous/next navigation.

## Capture flow

1. The UI sends `CaptureSettings.trigger`.
2. Capability classification resolves the execution mode.
3. Hardware triggers are configured before arm; post-capture types arm an
   immediate/broad capture.
4. CaptureManager stores the physical trigger sample when available.
5. A later software search may add or navigate a post-capture match without
   changing raw samples.

See [Pattern Trigger RTL](../hdl/protocol-trigger.md),
[Capture Controls](../frontend/capture-controls.md), and
[Hardware Validation](../hardware-validation.md).
