# Decoder UI

**Files:** `frontend/src/decoders/DecoderTable.tsx` (imported by CapturePage)

## Purpose

UI integration for protocol decoders: annotation overlay on waveforms, packet table display, severity filtering, and event search.

## DecoderTable

Rendered at the bottom of `CapturePage`. Shows decoded events in a sortable, filterable table:

| Column | Description |
|---|---|
| # | Event index |
| Time | Timestamp relative to capture start |
| Type | Event type (colour-coded icon) |
| Channel | Source channel |
| Label | Human-readable summary (e.g. `0x48 'H'`) |
| Severity | normal / warning / error (colour-coded) |

### Interactions

| Action | Effect |
|---|---|
| Click row | Jump waveform to event position |
| Severity filter | Toggle visibility of normal/warning/error events |
| Search | Filter events by text match |
| Column sort | Sort by time, type, or channel |

## Annotation Overlay

Decoder events rendered as coloured markers above the waveform canvas:

- Annotation colour determined by severity and decoder type
- Hover shows tooltip with full event details (fields, timing)
- Click selects event and highlights corresponding row in DecoderTable
- Stacked decoders show nested annotations (e.g. Modbus events inside UART frame)
- Zoomed-out view collapses annotations into density indicators

## Event Colours

| Severity | Hex Colour | Meaning |
|---|---|---|
| normal | `#4CAF50` (green) | Expected behaviour |
| warning | `#FFC107` (amber) | Marginal condition |
| error | `#F44336` (red) | Protocol error (parity, framing, CRC) |

## Dependencies

| Module | File |
|---|---|
| `DecoderEvent` type | `api/types.ts` |
| `WaveformView` | `state/waveformStore.ts` |
| `WaveformCanvas` | `waveform/WaveformCanvas.tsx` |
