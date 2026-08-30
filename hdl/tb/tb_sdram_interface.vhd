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
      Capture_Stream_Valid => '0',
      Capture_Stream_Ready => open,
      Capture_Stream_Address => (others => '0'),
      Capture_Stream_Data => (others => '0'),
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
    wait_until(clk, rd_valid, '1', 10 us, "Read valid timeout");
    rd_en <= '0';
    check(rd_data = x"CAFE", "Test 2: addr 0 readback must equal xCAFE");
    report "Read data: " & to_hstring(rd_data);
    wait_cycles(clk, 2);
    report "Test 2: PASS";

    -- Test 3: Sequential single-word writes to DISTINCT addresses
    -- (The SDRAM_Interface has no burst port: 'burst' was a TB-side signal
    -- that never reached the DUT, and the old test wrote all 4 words to the
    -- same address 16, so each write overwrote the previous one and the
    -- read-backs were vacuous. Each word now goes to its own address and is
    -- read back independently.)
    report "Test 3: 4 sequential single-word writes at distinct addresses";
    for i in 1 to 4 loop
      addr <= std_logic_vector(to_unsigned(15 + i, 22));   -- 16, 17, 18, 19
      wr_data <= std_logic_vector(to_unsigned(i, 16));
      wr_en <= '1';
      wait_cycles(clk, 1);
      wr_en <= '0';
      wait_until(clk, idle, '1', 50 us, "Write idle timeout");
      wait_cycles(clk, 5);
      rd_en <= '1';
      wait_until(clk, rd_valid, '1', 10 us, "Read-back valid timeout");
      rd_en <= '0';
      check(rd_data = std_logic_vector(to_unsigned(i, 16)),
            "Test 3: word " & integer'image(i) & " readback at addr " &
            integer'image(15 + i) & " mismatch");
      wait_cycles(clk, 2);
    end loop;
    report "Test 3: PASS";

    -- Test 4: Write then immediate read
    report "Test 4: Read-after-write (addr 0)";
    addr <= (others => '0');
    rd_en <= '1';
    wait_until(clk, rd_valid, '1', 10 us, "Read-after-write valid timeout");
    rd_en <= '0';
    check(rd_data = x"CAFE", "Test 4: read-after-write must return xCAFE");
    report "Read value: " & to_hstring(rd_data);
    report "Test 4: PASS";

    -- Test 5: busy/idle signaling
    report "Test 5: Busy/idle signaling";
    addr <= std_logic_vector(to_unsigned(32, 22));
    wr_data <= x"1234";
    wr_en <= '1';
    wait_cycles(clk, 1);
    wr_en <= '0';
    wait_until(clk, busy, '0', 10 us, "Busy should clear");
    wait_until(clk, idle, '1', 10 us, "Idle should reassert after write");
    report "Test 5: PASS";

    report "=== ALL SDRAM INTERFACE TESTS PASSED ===";
    std.env.finish;
    wait;
  end process;

end bench;
