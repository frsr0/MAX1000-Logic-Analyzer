library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;
use work.sim_pkg.all;

entity tb_adc_controller is
  generic (
    CLK_FREQ : natural := 96000000
  );
end tb_adc_controller;

architecture bench of tb_adc_controller is
  constant CLK_PERIOD : time := 1 sec / real(CLK_FREQ);

  signal clk    : std_logic := '0';
  signal reset  : std_logic := '0';
  signal start  : std_logic := '0';
  signal busy   : std_logic_vector(3 downto 0);
  signal results : std_logic_vector(47 downto 0);
  signal valid  : std_logic_vector(3 downto 0);
begin

  gen_clk(clk, CLK_PERIOD / 2);

  DUT : entity work.ADC_Controller
    port map (
      sys_clk        => clk,
      sys_clk_locked => '1',
      reset          => reset,
      ch0_sel        => 0,
      ch0_start      => start,
      ch0_busy       => busy(0),
      ch0_result     => results(11 downto 0),
      ch0_valid      => valid(0),
      ch1_sel        => 1,
      ch1_start      => start,
      ch1_busy       => busy(1),
      ch1_result     => results(23 downto 12),
      ch1_valid      => valid(1),
      ch2_sel        => 2,
      ch2_start      => start,
      ch2_busy       => busy(2),
      ch2_result     => results(35 downto 24),
      ch2_valid      => valid(2),
      ch3_sel        => 3,
      ch3_start      => start,
      ch3_busy       => busy(3),
      ch3_result     => results(47 downto 36),
      ch3_valid      => valid(3)
    );

  process
  begin
    wait_cycles(clk, 5000);
    report "=== ADC Controller tests ===";

    -- Test 1: Sequence 4 channels
    report "Test 1: 4-channel sequence";
    start <= '1';
    wait_cycles(clk, 1);
    start <= '0';
    wait_until(clk, busy(0), '1', 10 us, "CH0 should go busy");
    -- Wait for all 4 results (each valid pulses for 1 cycle)
    wait_cycles(clk, 200);
    check(busy = "0000", "All channels idle after sequence: " &
          integer'image(to_integer(unsigned(busy))));
    report "Test 1: PASS";

    -- Test 2: Back-to-back sequences
    report "Test 2: Back-to-back sequences";
    for i in 0 to 4 loop
      start <= '1';
      wait_cycles(clk, 1);
      start <= '0';
      wait_cycles(clk, 200);
      check(busy = "0000", "All idle after seq " & integer'image(i));
    end loop;
    report "Test 2: PASS";

    -- Test 3: Reset during conversion
    report "Test 3: Reset during conversion";
    start <= '1';
    wait_cycles(clk, 1);
    start <= '0';
    wait_until(clk, busy(0), '1', 10 us, "ADC should go busy");
    reset <= '1';
    wait_cycles(clk, 5);
    check(busy = "0000", "Busy cleared after reset");
    reset <= '0';
    wait_cycles(clk, 5000);
    check(valid = "0000", "Valids cleared after reset");
    -- Normal conversion after reset
    start <= '1';
    wait_cycles(clk, 1);
    start <= '0';
    wait_cycles(clk, 200);
    check(busy = "0000", "All idle after reset conversion");
    report "Test 3: PASS";

    report "=== ALL ADC CONTROLLER TESTS PASSED ===";
    wait;
  end process;

end bench;
