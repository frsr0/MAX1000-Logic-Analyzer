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
  signal ch_sel : natural range 0 to 7 := 0;
  signal start  : std_logic := '0';
  signal busy   : std_logic;
  signal result : std_logic_vector(11 downto 0);
  signal valid  : std_logic;
begin

  gen_clk(clk, CLK_PERIOD / 2);

  DUT : entity work.ADC_Controller
    port map (
      sys_clk      => clk,
      reset        => reset,
      channel_sel  => ch_sel,
      start        => start,
      busy         => busy,
      result       => result,
      result_valid => valid
    );

  process
  begin
    report "=== ADC Controller tests ===";

    -- Test 1: Single conversion on CH0
    report "Test 1: Single conversion CH0";
    ch_sel <= 0;
    start <= '1';
    wait_cycles(clk, 1);
    start <= '0';
    wait_until(clk, busy, '1', 10 us, "ADC should go busy");
    wait_until(clk, valid, '1', 100 us, "ADC should produce result");
    check(valid = '1', "Result valid not asserted");
    wait_cycles(clk, 2);
    check(busy = '0', "ADC should be idle after result");
    report "Test 1: PASS";

    -- Test 2: All 8 channels
    report "Test 2: Multi-channel scan";
    for ch in 0 to 7 loop
      ch_sel <= ch;
      start <= '1';
      wait_cycles(clk, 1);
      start <= '0';
      wait_until(clk, valid, '1', 100 us, "ADC CH" & integer'image(ch) & " timeout");
      check(valid = '1', "Valid asserted on CH" & integer'image(ch));
    end loop;
    report "Test 2: PASS";

    -- Test 3: Back-to-back conversions
    report "Test 3: Back-to-back conversions";
    for i in 0 to 15 loop
      ch_sel <= i mod 8;
      start <= '1';
      wait_cycles(clk, 1);
      start <= '0';
      wait_until(clk, valid, '1', 100 us, "ADC back-to-back " & integer'image(i) & " timeout");
      wait_cycles(clk, 2);
    end loop;
    report "Test 3: PASS";

    -- Test 4: Reset during conversion
    report "Test 4: Reset during conversion";
    ch_sel <= 0;
    start <= '1';
    wait_cycles(clk, 1);
    start <= '0';
    wait_until(clk, busy, '1', 10 us, "ADC should go busy");
    reset <= '1';
    wait_cycles(clk, 5);
    check(busy = '0', "ADC should not be busy after reset");
    reset <= '0';
    wait_cycles(clk, 5);
    check(valid = '0', "Valid should be cleared after reset");
    -- Normal conversion after reset
    start <= '1';
    wait_cycles(clk, 1);
    start <= '0';
    wait_until(clk, valid, '1', 100 us, "ADC after reset timeout");
    report "Test 4: PASS";

    report "=== ALL ADC CONTROLLER TESTS PASSED ===";
    wait;
  end process;

end bench;
