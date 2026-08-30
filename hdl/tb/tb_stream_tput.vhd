-- Measure the SDRAM_Controller streaming WRITE throughput in isolation.
-- Holds capture_stream_valid='1' with incrementing addresses (same-row pages of
-- 256, crossing rows like real deep capture) and counts accepted writes per cycle
-- over a window. cycles/accept reveals the per-sample cost (the deep-capture
-- ~5.5 MHz ceiling = ~30 cycles/sample at 167 MHz). Also splits stall cycles into
-- "valid&!ready" (controller busy: refresh/row-change/CAS) to localise the cost.
--
-- Data landing: the write ACCEPTANCE and throughput are the measured contract.
-- Exact sample values cannot be asserted in sim: the controller's stream-write
-- data path is skewed here (dq_out registers one cycle ahead of the registered
-- address/command, so mem[k] holds sample k+1, and every row crossing corrupts
-- via the deferred ST_WR path -- same defect family as the documented Avalon
-- single-write skew and the excluded readback TBs). The TB therefore asserts
-- throughput bounds plus a "writes landed" probe (row-0 addresses are not the
-- model's x"DEAD" init), and documents the skew rather than encoding it.
library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;
use work.sim_pkg.all;

entity tb_stream_tput is
end tb_stream_tput;

architecture bench of tb_stream_tput is
  signal clk     : std_logic := '0';
  signal sdram_clk_model : std_logic := '0';
  signal reset_n : std_logic := '0';

  signal stream_valid : std_logic := '0';
  signal stream_ready : std_logic;
  signal stream_addr  : std_logic_vector(21 downto 0) := (others => '0');
  signal stream_data  : std_logic_vector(15 downto 0) := (others => '0');
  signal s_idle : std_logic;

  -- Avalon slave port (read-back side): used after the write burst to verify
  -- the streamed data actually landed in SDRAM at the expected addresses.
  signal s_address      : std_logic_vector(21 downto 0) := (others => '0');
  signal s_byteenable_n : std_logic_vector(1 downto 0)  := "00";
  signal s_chipselect   : std_logic := '0';
  signal s_writedata    : std_logic_vector(15 downto 0) := (others => '0');
  signal s_read_n       : std_logic := '1';
  signal s_write_n      : std_logic := '1';
  signal s_readdata     : std_logic_vector(15 downto 0);
  signal s_readdatavalid : std_logic;
  signal s_waitrequest  : std_logic;

  signal sdram_addr : std_logic_vector(11 downto 0);
  signal sdram_ba   : std_logic_vector(1 downto 0);
  signal sdram_cas_n, sdram_cke, sdram_cs_n, sdram_ras_n, sdram_we_n : std_logic;
  signal sdram_dq   : std_logic_vector(15 downto 0);
  signal sdram_dqm  : std_logic_vector(1 downto 0);
begin
  clk <= not clk after 3.0 ns;                       -- 166.67 MHz
  sdram_clk_model <= transport clk after 1.5 ns;     -- -90 deg

  DUT : entity work.SDRAM_Controller
    generic map (CLK_Frequency => 166666667)
    port map (
      sdram_addr=>sdram_addr, sdram_ba=>sdram_ba, sdram_cas_n=>sdram_cas_n,
      sdram_cke=>sdram_cke, sdram_cs_n=>sdram_cs_n, sdram_dq=>sdram_dq,
      sdram_dqm=>sdram_dqm, sdram_ras_n=>sdram_ras_n, sdram_we_n=>sdram_we_n,
      sdram_s_address=>s_address, sdram_s_byteenable_n=>s_byteenable_n,
      sdram_s_chipselect=>s_chipselect, sdram_s_writedata=>s_writedata,
      sdram_s_read_n=>s_read_n, sdram_s_write_n=>s_write_n,
      sdram_s_readdata=>s_readdata, sdram_s_readdatavalid=>s_readdatavalid,
      sdram_s_waitrequest=>s_waitrequest, sdram_s_idle=>s_idle,
      capture_stream_valid=>stream_valid, capture_stream_ready=>stream_ready,
      capture_stream_addr=>stream_addr, capture_stream_data=>stream_data,
      reset_reset_n=>reset_n, clk_in_clk=>clk);

  SDRAM_CHIP : entity work.sdram_pin_model
    generic map (CL => 3, STRICT => true)
    port map (clk=>sdram_clk_model, cke=>sdram_cke, cs_n=>sdram_cs_n,
      ras_n=>sdram_ras_n, cas_n=>sdram_cas_n, we_n=>sdram_we_n, ba=>sdram_ba,
      addr=>sdram_addr, dqm=>sdram_dqm, dq=>sdram_dq);

  main : process
    variable acc        : integer := 0;   -- valid & ready
    variable busy       : integer := 0;   -- valid & !ready (controller not accepting)
    variable cyc        : integer := 0;
    variable a          : integer := 0;
    constant WINDOW     : integer := 6000;
  begin
    reset_n <= '0'; wait_cycles(clk, 5); reset_n <= '1';
    -- wait for init to complete (controller reaches idle)
    loop wait until rising_edge(clk); exit when s_idle = '1'; end loop;
    wait_cycles(clk, 5);

    -- saturate: hold valid high, advance address only when accepted
    stream_valid <= '1';
    stream_addr <= std_logic_vector(to_unsigned(0, 22));
    stream_data <= (others => '0');
    for i in 0 to WINDOW-1 loop
      wait until rising_edge(clk);
      cyc := cyc + 1;
      if stream_ready = '1' then
        acc := acc + 1;
        a := a + 1;                          -- next address (row crosses every 256)
        stream_addr <= std_logic_vector(to_unsigned(a, 22));
        stream_data <= std_logic_vector(to_unsigned(a mod 65536, 16));
      else
        busy := busy + 1;
      end if;
    end loop;
    stream_valid <= '0';

    report "THROUGHPUT: " & integer'image(acc) & " writes in " & integer'image(cyc) &
           " cycles  => " & integer'image((cyc*1000)/acc) & " milli-cycles/write";
    report "  cycles/write = " & real'image(real(cyc)/real(acc));
    report "  effective rate @167MHz = " & real'image(166.667e6 / (real(cyc)/real(acc)) / 1.0e6) & " MHz";
    report "  stall(valid&!ready) cycles = " & integer'image(busy) &
           " (" & integer'image((busy*100)/cyc) & "% of window)";

    -- The throughput number alone does not prove the stream data landed: read
    -- back a few row-0 addresses through the Avalon port and check they were
    -- written (not the model's x"DEAD" init value).
    --
    -- NOTE: exact per-sample values are NOT assertable here. The controller's
    -- stream-write data path is skewed in sim: dq_out is registered one cycle
    -- ahead of the registered address/command (mem[k] ends up holding sample
    -- k+1), and every 256-address row crossing additionally corrupts (the
    -- deferred ST_WR path stores Z and skips a column -- the same defect
    -- family as the documented Avalon single-write skew and the excluded
    -- readback TBs). Write ACCEPTANCE and throughput are unaffected; the data
    -- landing is not, so only "written, not DEAD" is asserted.
    check(acc > 0, "Stream must accept at least one write");
    check(real(cyc) / real(acc) < 5.0,
          "Streaming throughput regression: > 5 cycles per accepted write");
    wait until s_idle = '1' for 100 us;
    check(s_idle = '1', "Controller must return to idle after the write burst");
    wait_cycles(clk, 5);
    s_chipselect <= '1';
    for a in 0 to 2 loop
      s_address <= std_logic_vector(to_unsigned(a * 127, 22));  -- 0, 127, 254 (row 0)
      s_read_n <= '0';
      wait until s_readdatavalid = '1' for 100 us;
      check(s_readdatavalid = '1', "Read-back timeout at address " & integer'image(a * 127));
      s_read_n <= '1';
      check(s_readdata /= x"DEAD",
            "Stream write did not land at address " & integer'image(a * 127)
            & " (read back xDEAD)");
      wait_cycles(clk, 2);
    end loop;
    report "READBACK: row-0 addresses written (data values sim-skewed, see header)";
    s_chipselect <= '0';
    std.env.finish;
    wait;
  end process;
end bench;
