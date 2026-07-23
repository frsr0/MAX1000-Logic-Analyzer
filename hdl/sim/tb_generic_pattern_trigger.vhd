library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use STD.ENV.ALL;

entity tb_generic_pattern_trigger is
end tb_generic_pattern_trigger;

architecture sim of tb_generic_pattern_trigger is
  signal clk     : std_logic := '0';
  signal inputs  : std_logic_vector(31 downto 0) := (others => '0');
  signal trigger : std_logic;
begin
  clk <= not clk after 5 ns;

  dut : entity work.Generic_Pattern_Trigger
    port map (
      CLK => clk, Inputs => inputs(15 downto 0), Enable => '1',
      Clock_Source => '1', Clock_Edge => '0',
      Start_Mode => '0', Start_Channel => 0, Start_Polarity => '0',
      Clock_Channel => 1, Data_Lane_Count => 2,
      Data_Channel_0 => 0, Data_Channel_1 => 2,
      Baud_Div => 1, Frame_Width => 4,
      -- LSB-first value A is normalized to 5 by the host before writing.
      Match_Value => x"00000005", Match_Mask => x"0000000F",
      Bit_Order => '0', Trigger => trigger
    );

  process
    procedure sample_pair(first_value, second_value : std_logic) is
    begin
      inputs(0) <= first_value;
      inputs(2) <= second_value;
      wait for 7 ns;
      inputs(1) <= '1';
      wait for 10 ns;
      inputs(1) <= '0';
      wait for 3 ns;
    end procedure;
  begin
    sample_pair('0', '1');
    sample_pair('0', '1');
    wait for 1 ns;
    assert trigger = '1' report "generic pattern trigger did not pulse" severity failure;
    wait for 10 ns;
    report "tb complete" severity note;
    stop;
  end process;
end sim;
