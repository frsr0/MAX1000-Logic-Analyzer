library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;
use work.sim_pkg.all;

entity tb_protocol_trigger is
  generic (
    CLK_FREQ : natural := 96000000;
    BAUD_DIV_VAL : natural := 834  -- ~115200 @ 96 MHz
  );
end tb_protocol_trigger;

architecture bench of tb_protocol_trigger is
  constant CLK_PERIOD : time := 1 sec / real(CLK_FREQ);
  constant BAUD_TIME  : time := 1 sec / real(CLK_FREQ / BAUD_DIV_VAL);

  signal clk      : std_logic := '0';
  signal inputs   : std_logic_vector(7 downto 0) := (others => '1');
  signal enable   : std_logic := '0';
  signal protocol : std_logic_vector(1 downto 0) := "00";
  signal match_val : std_logic_vector(7 downto 0) := x"00";
  signal baud_div : natural range 1 to 65535 := BAUD_DIV_VAL;
  signal uart_ch  : natural range 0 to 7 := 0;
  signal trigger  : std_logic;
  signal trig_flag : std_logic := '0';
  signal trig_clear : std_logic := '0';
begin

  process(clk)
  begin
    if rising_edge(clk) then
      if trig_clear = '1' then
        trig_flag <= '0';
      elsif trigger = '1' then
        trig_flag <= '1';
      end if;
    end if;
  end process;

  gen_clk(clk, CLK_PERIOD / 2);

  DUT : entity work.Protocol_Trigger
    port map (
      CLK          => clk,
      Inputs       => inputs,
      Enable       => enable,
      Protocol     => protocol,
      Match_Value  => match_val,
      Baud_Div     => baud_div,
      UART_Channel => uart_ch,
      Trigger      => trigger
    );

  process
  begin
    report "=== Protocol Trigger tests ===";

    -- Test 1: Match on CH0
    report "Test 1: Match byte 0xA5 on CH0";
    enable <= '1';
    match_val <= x"A5";
    wait_cycles(clk, 10);

    trig_clear <= '1'; wait_cycles(clk, 1); trig_clear <= '0';
    uart_send_byte(inputs(0), BAUD_TIME, x"A5");
    wait_cycles(clk, 50);
    check(trig_flag = '1', "Trigger should fire for A5");
    report "Test 1: PASS";

    -- Test 2: No match on wrong byte
    report "Test 2: No match on 0x5A";
    trig_clear <= '1'; wait_cycles(clk, 1); trig_clear <= '0';
    wait_cycles(clk, 10);
    uart_send_byte(inputs(0), BAUD_TIME, x"5A");
    wait_cycles(clk, 50);
    check(trig_flag = '0', "Trigger should NOT fire for 5A");
    report "Test 2: PASS";

    -- Test 3: Disable trigger
    report "Test 3: Disabled trigger";
    trig_clear <= '1'; wait_cycles(clk, 1); trig_clear <= '0';
    enable <= '0';
    wait_cycles(clk, 10);
    uart_send_byte(inputs(0), BAUD_TIME, x"A5");
    wait_cycles(clk, 50);
    check(trig_flag = '0', "Trigger should not fire when disabled");
    report "Test 3: PASS";
    enable <= '1';

    report "=== ALL PROTOCOL TRIGGER TESTS PASSED ===";
    wait;
  end process;

end bench;
