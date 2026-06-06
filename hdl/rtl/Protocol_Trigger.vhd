library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;

entity Protocol_Trigger is
  port (
    CLK          : in  std_logic;
    Inputs       : in  std_logic_vector(7 downto 0);
    Enable       : in  std_logic;
    Protocol     : in  std_logic_vector(1 downto 0);
    Match_Value  : in  std_logic_vector(7 downto 0);
    Baud_Div     : in  natural range 1 to 65535;
    UART_Channel : in  natural range 0 to 7;
    Trigger      : out std_logic := '0'
  );
end Protocol_Trigger;

architecture rtl of Protocol_Trigger is
  signal trig : std_logic := '0';
begin
  Trigger <= trig;

  process (CLK)
    variable baud_timer : natural range 0 to 65535 := 0;
    variable bit_cnt    : natural range 0 to 7 := 0;
    variable rx         : std_logic := '1';
    variable prev_rx    : std_logic := '1';
    variable shift_reg  : std_logic_vector(7 downto 0) := (others => '0');
    type state_t is (IDLE, START, BITS, STOP, CHECK);
    variable state : state_t := IDLE;
  begin
    if rising_edge(CLK) then
      trig <= '0';
      prev_rx := rx;
      rx := Inputs(UART_Channel);

      if Enable = '1' then
        case state is
          when IDLE =>
            if rx = '0' and prev_rx = '1' then
              baud_timer := 0;
              state := START;
            end if;

          when START =>
            if baud_timer = (Baud_Div / 2) - 1 then
              baud_timer := 0;
              bit_cnt := 0;
              state := BITS;
            else
              baud_timer := baud_timer + 1;
            end if;

          when BITS =>
            if baud_timer = Baud_Div - 1 then
              shift_reg(bit_cnt) := rx;
              baud_timer := 0;
              if bit_cnt = 7 then
                bit_cnt := 0;
                state := STOP;
              else
                bit_cnt := bit_cnt + 1;
              end if;
            else
              baud_timer := baud_timer + 1;
            end if;

          when STOP =>
            if baud_timer = Baud_Div - 1 then
              baud_timer := 0;
              state := CHECK;
            else
              baud_timer := baud_timer + 1;
            end if;

          when CHECK =>
            if rx = '1' and shift_reg = Match_Value then
              trig <= '1';
            end if;
            state := IDLE;
        end case;
      else
        state := IDLE;
      end if;
    end if;
  end process;
end rtl;
