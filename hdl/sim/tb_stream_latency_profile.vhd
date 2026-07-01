library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;
use work.spi_protocol_pkg.all;

entity tb_stream_latency_profile is
end tb_stream_latency_profile;

architecture rtl of tb_stream_latency_profile is
  constant CLK_PERIOD : time := 10 ns;  -- 100 MHz
  constant SPI_CLK_PERIOD : time := 33.3 ns;  -- 30 MHz

  signal clk : std_logic := '0';
  signal spi_clk : std_logic := '0';
  signal rst : std_logic := '1';

  -- Timeline tracking (all in ns)
  signal cmd_rx_start : integer := 0;
  signal cmd_rx_end : integer := 0;
  signal ack_tx_start : integer := 0;
  signal ack_tx_end : integer := 0;
  signal data_tx_start : integer := 0;

  signal cycle_count : integer := 0;

begin

  clk <= not clk after CLK_PERIOD / 2;
  spi_clk <= not spi_clk after SPI_CLK_PERIOD / 2;

  process
  begin
    rst <= '1';
    wait for CLK_PERIOD * 10;
    rst <= '0';
    wait;
  end process;

  -- Cycle counter (simulated wall-clock time)
  process(clk)
  begin
    if rising_edge(clk) then
      if rst = '0' then
        cycle_count <= cycle_count + 1;
      end if;
    end if;
  end process;

  -- Simulate command reception
  process
  begin
    wait until rst = '0';
    wait for CLK_PERIOD * 10;

    report "===== STREAMING LATENCY PROFILE =====" severity note;
    report "Measuring time from START_STREAM command to first sample data" severity note;
    report "" severity note;

    -- Phase 1: Command reception (12 bytes at 30 MHz SPI)
    cmd_rx_start <= cycle_count * 10;
    report "t=" & integer'image(cmd_rx_start) & " ns: START_STREAM command begins (12 bytes)" severity note;
    wait for 400 ns;  -- 12 bytes × 33.3 ns/byte
    cmd_rx_end <= cycle_count * 10;
    report "t=" & integer'image(cmd_rx_end) & " ns: Command RX complete (+" &
            integer'image(cmd_rx_end - cmd_rx_start) & " ns)" severity note;
    report "" severity note;

    -- Phase 2: ACK response (16 bytes at 30 MHz SPI)
    ack_tx_start <= cycle_count * 10;
    report "t=" & integer'image(ack_tx_start) & " ns: ACK response begins (16 bytes)" severity note;
    wait for 530 ns;  -- 16 bytes × 33.3 ns/byte
    ack_tx_end <= cycle_count * 10;
    report "t=" & integer'image(ack_tx_end) & " ns: ACK response complete (+" &
            integer'image(ack_tx_end - ack_tx_start) & " ns)" severity note;
    report "" severity note;

    -- Phase 3: The BLACK BOX - measure in stages
    report "===== STREAMING PIPELINE STAGES =====" severity note;

    -- Stage 1: CDC crossing (streaming_active to pclk domain)
    report "Stage 1: CDC crossing (CLK 100MHz -> pclk 166.7MHz)" severity note;
    report "  Input: streaming_active <= '1' at t=" & integer'image(cmd_rx_end) & " ns" severity note;
    report "  2-stage FF synchronizer: ~20-30 ns" severity note;
    wait for 30 ns;
    report "  Output: available in pclk domain at t+30ns" severity note;
    report "" severity note;

    -- Stage 2: Toggle edge detection (block-read FSM sync)
    report "Stage 2: Toggle edge sync in FLA (blk_req_s0/s1)" severity note;
    report "  2-stage FF @ 166.7 MHz: 2 cycles = ~12 ns" severity note;
    wait for 12 ns;
    report "  Edge detected, SDRAM READ issued" severity note;
    report "" severity note;

    -- Stage 3: SDRAM read latency
    report "Stage 3: SDRAM pipeline (Read_Latency=3 + access)" severity note;
    report "  CAS latency: 3 cycles @ 166.7 MHz = ~18 ns" severity note;
    report "  Row/col decode + DRAM access: ~30-40 ns" severity note;
    report "  Data on DQ bus: ~50 ns total" severity note;
    wait for 50 ns;
    report "" severity note;

    -- Stage 4: FIFO and CDC back to CLK domain
    report "Stage 4: FIFO CDC (pclk -> CLK domain)" severity note;
    report "  FIFO input latch: ~10 ns" severity note;
    report "  CDC crossing + output reg: ~50 ns" severity note;
    wait for 60 ns;
    report "  Rd_Fifo_Q valid in CLK domain" severity note;
    report "" severity note;

    -- Stage 5: Block-read FSM drain + dispatch
    report "Stage 5: Block-read FSM pop (states 2-4)" severity note;
    report "  FIFO read request: cycle N" severity note;
    report "  Data valid: cycle N+1" severity note;
    report "  Store in block_buf: cycle N+2" severity note;
    report "  Latency: 3 cycles @ 100 MHz = ~30 ns" severity note;
    wait for 30 ns;
    report "" severity note;

    report "Stage 6: Dispatch build response + TX pipeline" severity note;
    report "  Data ready for dispatch: state WAIT_BLOCK exit" severity note;
    report "  Build TX packet: ~50-100 ns" severity note;
    report "  spi_packet_tx shifts out: ~1-2 cycles = ~10 ns" severity note;
    wait for 75 ns;
    report "" severity note;

    data_tx_start <= cycle_count * 10;
    report "===== LATENCY BREAKDOWN =====" severity note;
    report "" severity note;
    report "Stage 1 (CDC):           30 ns  (2-stage FF)" severity note;
    report "Stage 2 (Toggle sync):   12 ns  (2 cycles @ 166 MHz)" severity note;
    report "Stage 3 (SDRAM):         50 ns  (CAS + access)" severity note;
    report "Stage 4 (FIFO CDC):      60 ns  (crossing + output reg)" severity note;
    report "Stage 5 (FSM pop):       30 ns  (3 cycles @ 100 MHz)" severity note;
    report "Stage 6 (Dispatch TX):   75 ns  (build + shift)" severity note;
    report "" severity note;
    report "Total pipeline latency:  257 ns" severity note;
    report "In SPI bytes @ 30 MHz:   ~8-9 bytes" severity note;
    report "" severity note;

    report "But hardware measures ack_pad breaking point at 88 bytes!" severity note;
    report "This means actual latency is ~88 bytes * 33.3 ns = ~2930 ns = 2.93 µs" severity note;
    report "" severity note;
    report "Gap between theory (257 ns) and measured (2930 ns): ~2700 ns" severity note;
    report "This 2.7 µs is unaccounted for - likely:" severity note;
    report "  - Multiple SDRAM reads pipelined" severity note;
    report "  - Block buffer pipelining delays" severity note;
    report "  - FIFO fill time waiting for data" severity note;
    report "  - Dispatch state machine gating" severity note;
    report "" severity note;
    report "Current ack_pad = 96 bytes, measured safe = 93 bytes" severity note;
    report "Theory predicts bottleneck: SDRAM/FIFO pipeline, not SPI" severity note;

    std.env.stop;
  end process;

end rtl;
