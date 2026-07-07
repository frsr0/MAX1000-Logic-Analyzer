# Side Panels

**Directory:** `frontend/src/panels/`

## Purpose

Collapsible side panel components on the Capture page. One panel per tab.

## TriggerPanel

Trigger configuration: type (rising/falling/any edge, pattern, uart_byte, immediate, none), channel mask, pattern value, UART byte. Shows hardware vs post-capture classification.

## ChannelPanel

Channel list with colour dots, enable/disable toggle, editable label, colour picker, physical pin assignment display.

## DecoderPanel

Add decoder (type + channel assignment + settings), list instances with status (idle/running/complete/error), run/rerun/cancel/remove actions, progress bar, event/warning counts.

## MeasurementPanel

Add measurement (type + channel), list with computed results, refresh with cursor positions, remove.

## MarkerPanel

Marker A/B positions with sample index and time, Δ values (samples, time, frequency), set/clear, colour picker.

## ExportPanel

Export session: format selector (CSV/JSON/VCD/NPZ/report), window option, channel select, download button.

## RawInspector

Sample range and channel select, raw value display (binary for digital, V for analog), hex dump.
