-- Measure the SDRAM_Controller capture-burst WRITE throughput in isolation and
-- verify the data it wrote.
--
-- A pump model keeps the controller fed: an endless FIFO model (the popped word
-- is on q one cycle after pop, word k = k mod 65536) and back-to-back 256-word
-- row bursts at consecutive addresses. The SDRAM pin model runs STRICT with
-- FAIL_ON_VIOLATION, so any tRCD/tRP/tRAS/tRC/tWR/tRFC violation, access to a
-- closed bank, or write with an undriven DQ fails the bench.
--
-- Contract measured: words written per clock over a window long enough to
-- include several row changes and refreshes (expected ~0.9: 256 words per
-- ~271 clocks minus refresh), and exactly-once / right-address landing of every
-- word, read back through the Avalon port.
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

  signal b_req    : std_logic := '0';
  signal b_addr   : std_logic_vector(21 downto 0) := (others => '0');
  signal b_len    : std_logic_vector(8 downto 0) := (others => '0');
  signal b_stop   : std_logic := '0';
  signal b_pop_ok : std_logic := '1';
  signal b_q      : std_logic_vector(15 downto 0) := (others => '0');
  signal b_pop    : std_logic;
  signal b_wr     : std_logic;
  signal b_busy   : std_logic;
  signal s_idle : std_logic;

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
  signal popped : natural := 0;
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
      capture_burst_req=>b_req, capture_burst_addr=>b_addr,
      capture_burst_len=>b_len, capture_burst_stop=>b_stop,
      capture_pop_ok=>b_pop_ok, capture_data=>b_q,
      capture_pop=>b_pop, capture_wr=>b_wr, capture_busy=>b_busy,
      reset_reset_n=>reset_n, clk_in_clk=>clk);

  SDRAM_CHIP : entity work.sdram_pin_model
    generic map (CL => 3, STRICT => true, FAIL_ON_VIOLATION => true)
    port map (clk=>sdram_clk_model, cke=>sdram_cke, cs_n=>sdram_cs_n,
      ras_n=>sdram_ras_n, cas_n=>sdram_cas_n, we_n=>sdram_we_n, ba=>sdram_ba,
      addr=>sdram_addr, dqm=>sdram_dqm, dq=>sdram_dq);

  -- Endless FIFO model: pop at cycle c -> word (popped count) on q at c+1.
  fifo_model : process(clk)
    variable n : natural := 0;
  begin
    if rising_edge(clk) then
      if b_pop = '1' then
        b_q <= std_logic_vector(to_unsigned(n mod 65536, 16));
        n := n + 1;
        popped <= n;
      end if;
    end if;
  end process;

  main : process
    variable acc        : integer := 0;
    variable cyc        : integer := 0;
    variable a          : integer := 0;
    variable bursts     : integer := 0;
    variable col        : integer := 0;
    variable done_r, w0 : integer := 0;
    variable rd         : std_logic_vector(15 downto 0);
    constant ROWS       : integer := 24;      -- 6144 words, > 4 refresh intervals
  begin
    reset_n <= '0'; wait_cycles(clk, 5); reset_n <= '1';
    loop wait until rising_edge(clk); exit when s_idle = '1'; end loop;
    wait_cycles(clk, 5);

    -- Back-to-back 256-word row bursts, cycles counted from the first request
    -- to the end of the last burst.
    -- A burst can end early (refresh pre-empts it); the pump then requests the
    -- remainder of the row, exactly as the real pump does.
    for r in 0 to ROWS - 1 loop
      done_r := 0;
      while done_r < 256 loop
        b_addr <= std_logic_vector(to_unsigned(r * 256 + done_r, 22));
        b_len  <= std_logic_vector(to_unsigned(256 - done_r, 9));
        b_req  <= '1';
        w0 := acc;
        loop
          wait until rising_edge(clk);
          cyc := cyc + 1;
          if b_wr = '1' then acc := acc + 1; end if;
          exit when b_busy = '1';
        end loop;
        b_req <= '0';
        loop
          wait until rising_edge(clk);
          cyc := cyc + 1;
          if b_wr = '1' then acc := acc + 1; end if;
          exit when b_busy = '0';
        end loop;
        bursts := bursts + 1;
        done_r := done_r + (acc - w0);
      end loop;
    end loop;

    report "THROUGHPUT: " & integer'image(acc) & " words in " & integer'image(cyc) &
           " cycles over " & integer'image(bursts) & " row bursts";
    report "  words per clock = " & real'image(real(acc) / real(cyc));
    check(acc = ROWS * 256, "Every requested word must be written exactly once: " &
          integer'image(acc));
    check(popped = acc, "pops and writes must match: popped " & integer'image(popped));
    check(real(acc) / real(cyc) > 0.85,
          "Streaming throughput regression: below 0.85 words per clock");
    wait until s_idle = '1' for 100 us;
    check(s_idle = '1', "Controller must return to idle after the write burst");
    wait_cycles(clk, 5);

    -- Exactly-once / right-address: read back every 17th word (and the row
    -- edges) through the Avalon port; word k must equal k mod 65536.
    s_chipselect <= '1';
    for r in 0 to ROWS - 1 loop
      for c in 0 to 5 loop
        case c is
          when 0 => col := 0;
          when 1 => col := 1;
          when 2 => col := 17;
          when 3 => col := 100;
          when 4 => col := 254;
          when others => col := 255;
        end case;
        a := r * 256 + col;
        s_address <= std_logic_vector(to_unsigned(a, 22));
        s_read_n <= '0';
        wait until s_readdatavalid = '1' for 100 us;
        check(s_readdatavalid = '1', "Read-back timeout at address " & integer'image(a));
        s_read_n <= '1';
        check(s_readdata = std_logic_vector(to_unsigned(a mod 65536, 16)),
              "Stream write landed wrong at address " & integer'image(a) &
              ": got " & to_hstring(s_readdata));
        wait_cycles(clk, 2);
      end loop;
    end loop;
    report "READBACK: sampled words of all rows match";
    s_chipselect <= '0';
    std.env.finish;
    wait;
  end process;
end bench;
