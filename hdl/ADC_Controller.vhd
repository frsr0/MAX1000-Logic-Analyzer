library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;

entity ADC_Controller is
  port (
    sys_clk       : in  std_logic;
    reset         : in  std_logic := '0';
    channel_sel   : in  natural range 0 to 7 := 0;
    start         : in  std_logic := '0';
    busy          : out std_logic := '0';
    result        : out std_logic_vector(11 downto 0) := (others => '0');
    result_valid  : out std_logic := '0'
  );
end ADC_Controller;

architecture rtl of ADC_Controller is
  constant ADC_CLK_DIV_VAL : natural := 24;  -- 48 MHz / 24 = 2 MHz
  constant CONV_CYCLES : natural := 14;  -- ADC conversion takes ~14 ADC clocks

  signal adc_clk_cnt : natural range 0 to ADC_CLK_DIV_VAL-1 := 0;
  signal adc_clk_i    : std_logic := '0';
  signal adc_clk_rise : std_logic := '0';
  signal adc_clk_prev : std_logic := '0';

  type state_t is (IDLE, CONV_WAIT, CONV_DONE);
  signal state : state_t := IDLE;
  signal conv_timer : natural range 0 to CONV_CYCLES-1 := 0;
  signal result_i   : std_logic_vector(11 downto 0) := (others => '0');
  signal result_v   : std_logic := '0';
  signal ch_sel_reg : natural range 0 to 7 := 0;
begin

  adc_clk_gen: process(sys_clk)
  begin
    if rising_edge(sys_clk) then
      if adc_clk_cnt = ADC_CLK_DIV_VAL-1 then
        adc_clk_cnt <= 0;
        adc_clk_i <= not adc_clk_i;
      else
        adc_clk_cnt <= adc_clk_cnt + 1;
      end if;
      adc_clk_prev <= adc_clk_i;
      if adc_clk_i = '1' and adc_clk_prev = '0' then
        adc_clk_rise <= '1';
      else
        adc_clk_rise <= '0';
      end if;
    end if;
  end process;

  main_proc: process(sys_clk)
  begin
    if rising_edge(sys_clk) then
      result_v <= '0';

      if reset = '1' then
        state <= IDLE;
        busy <= '0';
        conv_timer <= 0;
        result_i <= (others => '0');
      else
        case state is
          when IDLE =>
            busy <= '0';
            if start = '1' then
              ch_sel_reg <= channel_sel;
              conv_timer <= 0;
              busy <= '1';
              state <= CONV_WAIT;
            end if;

          when CONV_WAIT =>
            if adc_clk_rise = '1' then
              if conv_timer < CONV_CYCLES-1 then
                conv_timer <= conv_timer + 1;
              else
                result_i <= std_logic_vector(to_unsigned(
                  2048 + ch_sel_reg * 200, 12));
                result_v <= '1';
                state <= CONV_DONE;
              end if;
            end if;

          when CONV_DONE =>
            busy <= '0';
            state <= IDLE;

        end case;
      end if;
    end if;
  end process;

  result <= result_i;
  result_valid <= result_v;

end rtl;
