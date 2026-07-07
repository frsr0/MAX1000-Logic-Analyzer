# Core Wrapper: `OLS_Logic_Analyzer_SDRAM_Core`

**File:** `hdl/rtl/OLS_Logic_Analyzer_SDRAM_Core.vhd` (490 lines)

## Purpose

Structural wrapper that instantiates and wires the three main components of the analyser core: `OLS_Interface` (SPI command interface), `Fast_Logic_Analyzer_SDRAM` (capture engine), and `Signal_Gen` (protocol generator). Also wraps as `OLS_Logic_Analyzer` for compatibility with the top-level OLS_SDRAM_Top.

## Entity: `OLS_Logic_Analyzer`

### Generics

| Generic | Default | Description |
|---|---|---|
| `CLK_Frequency` | 12,000,000 | System clock |
| `SDRAM_CLK_HZ` | 166,666,667 | SDRAM clock |
| `SAMPLE_CLK_HZ` | 200,000,000 | Sample clock |
| `Max_Samples` | 4,194,304 | Maximum capture depth |
| `Channels` | 4 | Number of channels (may differ from FLA) |
| `Sim` | false | Simulation mode |
| `FAST_SPEED` | false | Enable 200 MHz sample path |
| `FAST_RAW_BUILD` | true | Exclude compression at elaboration |

### Ports

All ports are direct pass-throughs from the three sub-components:
- SPI: `CLK`, `SDRAM_CLK_IN`, `FAST_CLK`, `SPI_CS/SCK/MOSI/MISO`, `Interface_Mode`
- Capture: `Inputs_Sys[Ch-1:0]`, `Inputs_Fast[Ch-1:0]`
- SDRAM: `sdram_addr/ba/dq/dqm/cas_n/ras_n/we_n/cke/cs_n/clk`
- Generator: `Gen_Load_Byte/We/Start/Baud_Div/Busy/Fifo_Count/Proto/TX_Pin/SCL_Pin/Clear/RX_Data/RX_Used/RX_Re`, plus protocol flags
- Analog: `Analog_Frame_Data/Len/Stream_Mode/Toggle`, `Packed_Mode/Data/Valid/Ready`
- Control: `Armed`, `Fast_Mode`, `Narrow_Enable/Channel`, `Analog_Enable/Only/Profile/Channel`, `Continuous_Mode`, `Status`, `Buffer_Full/Ack`
- Pin mapping: `Pin_Map_Write/Channel/Pin`
- Debug: `Debug_Ch0_Enable/Channel/Period/Duty`
- Generator capture: `Gen_Start_Ack/Reject`, `Gen_Done_Pulse`, `Gen_Capture_Active`
- Diagnostics: `Pump_*_Cycles` counters

## Component Wiring

### OLS_Interface to Fast_Logic_Analyzer_SDRAM

| OLS_Interface | Signal | FLA_SDRAM |
|---|---|---|
| `Rate_Div` | → | `Rate_Div` |
| `Samples` | → | `Samples` |
| `Start_Offset` | → | `Start_Offset` |
| `Run` | → | `Run` |
| `Full` | ← | `Full` |
| `Address` | → | `Address` |
| `Outputs` | ← | `Outputs` (16-bit → 32-bit) |
| `Blk_Rd_Req_Tog` | → | `Blk_Rd_Req_Tog` |
| `Blk_Rd_Base` | → | `Blk_Rd_Base` |
| `Blk_Rd_Count` | → | `Blk_Rd_Count` |
| `Auto_Renew` | → | `Auto_Renew` |
| `Rd_Fifo_Q` | ← | `Rd_Fifo_Q` |
| `Rd_Fifo_Empty` | ← | `Rd_Fifo_Empty` |
| `Rd_Fifo_RdReq` | → | `Rd_Fifo_RdReq` |
| `Producer_Index` | ← | `Producer_Index` |
| `Oldest_Index` | ← | `Oldest_Index` |
| `Newest_Index` | ← | `Newest_Index` |
| `Overrun_Count` | ← | `Overrun_Count` |
| `Pump_*` | ← | `Pump_*` counters |

### OLS_Interface to Signal_Gen

| OLS_Interface | Signal | Signal_Gen |
|---|---|---|
| `Gen_Load_Byte` | → | `Gen_Load_Byte` |
| `Gen_Load_We` | → | `Gen_Load_We` |
| `Gen_Start` | → | `Gen_Start` |
| `Gen_Baud_Div` | → | `Gen_Baud_Div` |
| `Gen_Proto` | → | `Gen_Proto` |
| `Gen_TX_Pin` | → | `Gen_TX_Pin` |
| `Gen_SCL_Pin` | → | `Gen_SCL_Pin` |
| `Gen_Clear` | → | `Gen_Clear` |
| `Gen_I2C_Rd_Len` | → | `Gen_I2C_Rd_Len` |
| `Gen_I2C_Dev_R` | → | `Gen_I2C_Dev_R` |
| `Gen_I2C_Test` | → | `Gen_I2C_Test` |
| `Gen_SPI_Test` | → | `Gen_SPI_Test` |
| `Gen_Repeat` | → | `Gen_Repeat` |
| `Gen_RS485_Pair` | → | `Gen_RS485_Pair` |
| `Gen_Accel_Attach` | → | `Gen_Accel_Attach` |
| `Gen_Busy` | ← | `Gen_Busy` |
| `Gen_Fifo_Count` | ← | `Gen_Fifo_Count` |
| `Gen_RX_Data` | ← | `Gen_RX_Data` |
| `Gen_RX_Used` | ← | `Gen_RX_Used` |
| `Gen_RX_Re` | → | `Gen_RX_Re` |

## Modes

The core multiplexes between component data sources based on mode signals:
- `Armed`, `Fast_Mode` → FLA fast/normal mode
- `Analog_Enable`, `Analog_Only`, `Analog_Profile` → analog capture routing
- `Continuous_Mode` → enables triple-buffer handshake (Buffer_Full/Ack)
- `Packed_Mode` → mso_capture path drives FIFO
- `Narrow_Enable` → narrow packed mode

## Dependencies

| Component | File |
|---|---|
| `OLS_Interface` | `OLS_Interface.vhd` |
| `Fast_Logic_Analyzer_SDRAM` | `Fast_Logic_Analyzer_SDRAM.vhd` |
| `Signal_Gen` | `Signal_Gen.vhd` |

## Testing

Covered by:
- `tb_top.vhd` — full-core simulation through wrapper
- `tb_ols_interface.vhd` — interface-to-engine interaction
- `tb_core_stream.vhd` — streaming through core
- `tb_core_batched_reads.vhd` — batched block reads through core
