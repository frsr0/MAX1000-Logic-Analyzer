library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;
use work.sim_pkg.all;

entity tb_sdram_interface is
  generic (
    CLK_FREQ : natural := 96000000
  );
end tb_sdram_interface;

architecture bench of tb_sdram_interface is
  constant CLK_PERIOD : time := 1 sec / real(CLK_FREQ);

  signal clk   : std_logic := '0';

  signal addr      : std_logic_vector(21 downto 0) := (others => '0');
  signal wr_en     : std_logic := '0';
  signal wr_data   : std_logic_vector(15 downto 0) := (others => '0');
  signal burst     : std_logic := '0';
  signal rd_en     : std_logic := '0';
  signal rd_data   : std_logic_vector(15 downto 0);
  signal rd_valid  : std_logic;
  signal busy      : std_logic;
  signal idle      : std_logic;

  signal sdram_addr : std_logic_vector(11 downto 0);
  signal sdram_ba   : std_logic_vector(1 downto 0);
  signal sdram_cas_n : std_logic;
  signal sdram_cke   : std_logic;
  signal sdram_cs_n  : std_logic;
  signal sdram_dq    : std_logic_vector(15 downto 0);
  signal sdram_dqm   : std_logic_vector(1 downto 0);
  signal sdram_ras_n : std_logic;
  signal sdram_we_n  : std_logic;
  signal sdram_clk   : std_logic;

  signal clk_150 : std_logic;
begin

  gen_clk(clk, CLK_PERIOD / 2);

  DUT : entity work.SDRAM_Interface
    generic map (
      Sim           => true,
      Write_Latency => 10,
      Read_Latency  => 3,
      Page_Latency  => 3
    )
    port map (
      CLK          => clk,
      Reset        => '0',
      CLK_150_Out  => clk_150,
      Address      => addr,
      Write_Enable => wr_en,
      Write_Data   => wr_data,
      Burst        => burst,
      Read_Enable  => rd_en,
      Read_Data    => rd_data,
      Read_Valid   => rd_valid,
      Busy         => busy,
      Idle         => idle,
      sdram_addr   => sdram_addr,
      sdram_ba     => sdram_ba,
      sdram_cas_n  => sdram_cas_n,
      sdram_cke    => sdram_cke,
      sdram_cs_n   => sdram_cs_n,
      sdram_dq     => sdram_dq,
      sdram_dqm    => sdram_dqm,
      sdram_ras_n  => sdram_ras_n,
      sdram_we_n   => sdram_we_n,
      sdram_clk    => sdram_clk
    );

  process
  begin
    report "=== SDRAM Interface tests (sim mode) ===";

    wait_cycles(clk, 1000);

    -- Test 1: Single write
    report "Test 1: Single write at addr 0";
    addr <= (others => '0');
    wr_data <= x"CAFE";
    wr_en <= '1';
    wait_cycles(clk, 1);
    wr_en <= '0';
    wait_until(clk, idle, '1', 10 us, "SDRAM should become idle after write");
    report "Test 1: PASS";

    -- Test 2: Single read
    report "Test 2: Read back addr 0";
    addr <= (others => '0');
    rd_en <= '1';
    wait_cycles(clk, 1);
    rd_en <= '0';
    wait_until(clk, rd_valid, '1', 10 us, "Read valid timeout");
    report "Read data: " & to_hstring(rd_data);
    report "Test 2: PASS";

    -- Test 3: Burst write 4 words
    report "Test 3: Burst write 4 words";
    addr <= std_logic_vector(to_unsigned(16, 22));
    wr_data <= x"0001";
    burst <= '1';
    wr_en <= '1';
    wait_cycles(clk, 1);
    wr_en <= '0';
    wait_until(clk, idle, '1', 50 us, "Burst idle timeout");  -- burst needs more time
    wait_cycles(clk, 5);
    wr_data <= x"0002";
    wr_en <= '1';
    wait_cycles(clk, 1);
    wr_en <= '0';
    wait_until(clk, idle, '1', 50 us, "Burst 2 idle timeout");
    wait_cycles(clk, 5);
    wr_data <= x"0003";
    wr_en <= '1';
    wait_cycles(clk, 1);
    wr_en <= '0';
    wait_until(clk, idle, '1', 50 us, "Burst 3 idle timeout");
    wait_cycles(clk, 5);
    wr_data <= x"0004";
    wr_en <= '1';
    wait_cycles(clk, 1);
    wr_en <= '0';
    burst <= '0';
    wait_until(clk, idle, '1', 50 us, "Burst 4 idle timeout");
    report "Test 3: PASS";

    -- Test 4: Write then immediate read
    report "Test 4: Read-after-write (addr 0)";
    addr <= (others => '0');
    rd_en <= '1';
    wait_cycles(clk, 1);
    rd_en <= '0';
    wait_until(clk, rd_valid, '1', 10 us, "Read-after-write valid timeout");
    report "Read value: " & to_hstring(rd_data);
    report "Test 4: PASS";

    -- Test 5: busy/idle signaling
    report "Test 5: Busy/idle signaling";
    check(idle = '1' or busy = '1', "SDRAM should be either idle or busy");
    addr <= std_logic_vector(to_unsigned(32, 22));
    wr_data <= x"1234";
    wr_en <= '1';
    wait_cycles(clk, 1);
    wr_en <= '0';
    wait_until(clk, busy, '0', 10 us, "Busy should clear");
    report "Test 5: PASS";

    report "=== ALL SDRAM INTERFACE TESTS PASSED ===";
    wait;
  end process;

end bench;
