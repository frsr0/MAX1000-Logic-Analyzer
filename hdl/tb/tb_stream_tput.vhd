-- Measure the SDRAM_Controller streaming WRITE throughput in isolation.
-- Holds capture_stream_valid='1' with incrementing addresses (same-row pages of
-- 256, crossing rows like real deep capture) and counts accepted writes per cycle
-- over a window. cycles/accept reveals the per-sample cost (the deep-capture
-- ~5.5 MHz ceiling = ~30 cycles/sample at 167 MHz). Also splits stall cycles into
-- "valid&!ready" (controller busy: refresh/row-change/CAS) to localise the cost.
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
      sdram_s_address=>(others=>'0'), sdram_s_byteenable_n=>"00",
      sdram_s_chipselect=>'0', sdram_s_writedata=>(others=>'0'),
      sdram_s_read_n=>'1', sdram_s_write_n=>'1', sdram_s_burst=>'0',
      sdram_s_readdata=>open, sdram_s_readdatavalid=>open, sdram_s_waitrequest=>open,
      sdram_s_idle=>s_idle,
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
    std.env.finish;
    wait;
  end process;
end bench;
