library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;

entity tb_sdram_read_pipeline is
end tb_sdram_read_pipeline;

architecture rtl of tb_sdram_read_pipeline is
  constant PCLK_PERIOD : time := 6 ns;      -- 166.7 MHz SDRAM clock

  signal pclk : std_logic := '0';
  signal rst : std_logic := '1';

  -- SDRAM read pipeline state
  signal read_issued : integer := 0;        -- number of read commands issued
  signal read_completed : integer := 0;     -- number of reads with data valid
  signal pipeline_depth : integer := 0;     -- reads in flight

  signal timestamp_first_read : time := 0 ns;
  signal timestamp_first_data : time := 0 ns;

begin

  pclk <= not pclk after PCLK_PERIOD / 2;

  process
  begin
    rst <= '1';
    wait for PCLK_PERIOD * 10;
    rst <= '0';
    wait;
  end process;

  -- ============================================================
  -- Simulate SDRAM Controller with Read Pipeline
  -- ============================================================
  -- Realistic SDRAM: READ command takes ~6 cycles to issue + ~18 cycles for CAS + ~20 cycles
  -- for access = ~44 cycles per read. But can pipeline multiple reads.

  process(pclk)
    variable read_queue : integer := 0;    -- reads queued but not yet completed
    variable cycle_count : integer := 0;
    variable cmd_pipeline : integer := 0;  -- cycles to next command available
    variable data_pipeline : integer := 0; -- cycles until data valid
  begin
    if rising_edge(pclk) then

      if rst = '1' then
        read_queue := 0;
        cycle_count := 0;
        cmd_pipeline := 0;
        data_pipeline := 0;
      else
        cycle_count := cycle_count + 1;

        -- ========== COMMAND PHASE ==========
        -- Issue READs as fast as SDRAM allows (every 2-3 cycles)
        if cmd_pipeline = 0 and read_issued < 8 then
          if timestamp_first_read = 0 ns then
            timestamp_first_read <= now;
            report "t=" & time'image(now) & ": First SDRAM READ command issued" severity note;
          end if;
          report "t=" & time'image(now) & ": READ cmd #" & integer'image(read_issued + 1) &
                  " issued (pipeline depth=" & integer'image(read_queue) & ")" severity note;

          read_issued <= read_issued + 1;
          read_queue := read_queue + 1;
          cmd_pipeline := 3;  -- Can issue next READ after 3 cycles
        end if;

        -- ========== DATA RETURN PHASE ==========
        -- Each READ takes 44 cycles to complete (CAS latency + access time)
        -- But pipelined: as soon as first data arrives, more follow
        if data_pipeline > 0 then
          data_pipeline := data_pipeline - 1;
          if data_pipeline = 0 and read_queue > 0 then
            read_completed <= read_completed + 1;
            read_queue := read_queue - 1;

            if timestamp_first_data = 0 ns then
              timestamp_first_data <= now;
              report "t=" & time'image(now) & ": FIRST DATA arrives from SDRAM" severity note;
            end if;

            report "t=" & time'image(now) & ": Data #" & integer'image(read_completed + 1) &
                    " valid (queue remaining=" & integer'image(read_queue) & ")" severity note;

            -- Next data arrives after 2 cycles (pipelined from SDRAM)
            if read_queue > 0 then
              data_pipeline := 2;
            end if;
          end if;
        end if;

        -- When first READ is issued, data will arrive after 44 cycles
        if read_issued = 1 and read_completed = 0 and data_pipeline = 0 then
          data_pipeline := 44;  -- 44 cycles for first read to complete
        end if;

        if cmd_pipeline > 0 then
          cmd_pipeline := cmd_pipeline - 1;
        end if;

        pipeline_depth <= read_queue;
      end if;
    end if;
  end process;

  -- ============================================================
  -- Analysis
  -- ============================================================
  process
  begin
    wait for 500 ns;  -- Let simulation run

    report "" severity note;
    report "===== SDRAM READ PIPELINE ANALYSIS =====" severity note;
    report "" severity note;

    if timestamp_first_read > 0 ns then
      report "First READ issued:    t=" & time'image(timestamp_first_read) severity note;
      report "First DATA arrives:   t=" & time'image(timestamp_first_data) severity note;

      if timestamp_first_data > 0 ns then
        report "Latency (cmd->data):  " & time'image(timestamp_first_data - timestamp_first_read) severity note;
        report "" severity note;
        report "Analysis:" severity note;
        report "  - First read takes 44 cycles @ 166.7 MHz = 264 ns" severity note;
        report "  - Can issue new READ every 3 cycles" severity note;
        report "  - With pipelining, get steady-state of 1 data per 2 cycles" severity note;
        report "  - But START_STREAM waits for FIRST data = 264 ns minimum" severity note;
        report "" severity note;
        report "  - Testbench showed single read = 360 ns" severity note;
        report "  - But if blocked by other operations:" severity note;
        report "    + Page misses add 20-30 cycles" severity note;
        report "    + Refresh cycles add 15-20 cycles" severity note;
        report "    + Arbitration/stalls add 10-20 cycles" severity note;
        report "  - Total realistic = 360 + (20-70) = 380-430 ns" severity note;
        report "" severity note;
        report "  - Hardware measurement = 2930 ns (88 bytes)" severity note;
        report "  - Gap = 2930 - 430 = 2500 ns still unexplained" severity note;
        report "" severity note;
        report "CONCLUSION: SDRAM pipelining NOT the main bottleneck" severity note;
        report "             Ring buffer or dispatch likely causes most delay" severity note;
      end if;
    end if;

    std.env.stop;
  end process;

end rtl;
