# SDRAM Controller: `SDRAM_Controller_Custom`

**File:** `hdl/rtl/SDRAM_Controller_Custom.vhd` (35.6 KB, ~800 lines)

## Purpose

Custom SDRAM controller for the 64 Mbit x16 SDRAM on the MAX1000 board. Implements open-page streaming writes for the capture write pump, read/write/burst cycles, page management, and single-shot completion signalling.

## SDRAM Device

| Property | Value |
|---|---|
| Type | 64 Mbit (4M × 16) synchronous DRAM |
| Organisation | 4 banks × 1024 rows × 256 columns × 16 bits |
| Address bus | 12-bit (row), 8-bit (column), 2-bit bank |
| Clock | 167 MHz (from PLL c2) |

## Key Features

- **Open-page streaming writes**: keeps the row open across multiple write bursts for capture data
- **Read/write/burst**: supports single-cycle commands and pipelined bursts
- **Capture stream handshake**: interfaces with the async FIFO and write pump
- **Single-shot completion**: signals when all written data has drained to SDRAM
- **Refresh**: auto-refresh compliant with JEDEC SDRAM spec at 167 MHz

## Controller Architecture

### Signal Interface

| Port | Width | Direction | Description |
|---|---|---|---|
| `clk` | 1 | IN | SDRAM core clock (167 MHz) |
| `addr` | 22 | IN | Word address (4M addressable) |
| `wr` | 1 | IN | Write strobe |
| `wdata` | 16 | IN | Write data |
| `rd` | 1 | IN | Read strobe |
| `rdata` | 16 | OUT | Read data (registered) |
| `rvalid` | 1 | OUT | Read data valid |
| `burst` | 1 | IN/OUT | Burst mode control |
| `sdram_*` | various | OUT | Physical SDRAM interface |

### State Machine

The controller implements a multi-state FSM for SDRAM command sequencing:
- `IDLE` → wait for command
- `ACTIVE` → open row (RAS)
- `READ` / `WRITE` → column access with CAS
- `READ_WAIT` → read latency (CL=3)
- `PRECHARGE` → close row
- `REFRESH` → auto-refresh cycle
- `MODE` → load mode register

### Timing Parameters

| Parameter | Value | Description |
|---|---|---|
| `Write_Latency` | 10 | Cycles from CAS to write completion |
| `Read_Latency` | 3 | CAS latency (CL) |
| `Page_Latency` | 3 | Page miss penalty |
| Refresh interval | 64 ms / 8192 rows | ~7.8 µs per row |

## Key Implementation Details

### Open-Page Policy

The controller keeps the row open after a write burst. Subsequent writes to the same row only issue CAS commands (no RAS). This is critical for the capture write pump which delivers contiguous samples to consecutive addresses.

### Producer-Done Completion

When the FLA asserts `producer_done_q`, the controller finishes draining any in-flight FIFO data and asserts `completion` to the FLA. This replaced the old fixed-count completion that could hang when the write pump's pace fluctuated.

### Burst Handling

- Single-shot captures use the streaming write pump which sends data in bursts
- Block reads use burst reads of the configured block size
- CAS-before-RAS refresh scheduling ensures no refresh collision during active capture

## Dependencies

| Component | File |
|---|---|
| `SDRAM_Interface` | `SDRAM_Interface.vhd` |
| `Fast_Logic_Analyzer_SDRAM` | `Fast_Logic_Analyzer_SDRAM.vhd` |

## Testing

| Testbench | What it covers |
|---|---|
| `tb_sdram_controller.vhd` | Command sequencing, refresh, bursts |
| `tb_sdram_interface.vhd` | Signal timing, IO pad model |
| `tb_pump_tput.vhd` | Write pump throughput |
| `tb_stream_tput.vhd` | Streaming throughput |

## Known Limitations

- Single port (no simultaneous read/write)
- No ECC
- Page size is 256 words (512 bytes); crossing a page boundary adds page miss penalty
