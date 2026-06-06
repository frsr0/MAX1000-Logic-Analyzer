library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;

entity ADC_Controller is
  port (
    sys_clk        : in  std_logic;
    sys_clk_locked : in  std_logic := '1';
    reset          : in  std_logic := '0';
    ch0_sel        : in  natural range 0 to 15 := 0;
    ch0_start      : in  std_logic := '0';
    ch0_busy       : out std_logic := '0';
    ch0_result     : out std_logic_vector(11 downto 0) := (others => '0');
    ch0_valid      : out std_logic := '0';
    ch1_sel        : in  natural range 0 to 15 := 1;
    ch1_start      : in  std_logic := '0';
    ch1_busy       : out std_logic := '1';
    ch1_result     : out std_logic_vector(11 downto 0) := (others => '0');
    ch1_valid      : out std_logic := '0'
  );
end ADC_Controller;

architecture rtl of ADC_Controller is

  -- ADC control core interface
  signal cmd_valid     : std_logic := '0';
  signal cmd_channel   : std_logic_vector(4 downto 0) := (others => '0');
  signal cmd_sop       : std_logic := '0';
  signal cmd_eop       : std_logic := '0';
  signal cmd_ready     : std_logic := '0';
  signal rsp_valid     : std_logic := '0';
  signal rsp_data      : std_logic_vector(11 downto 0) := (others => '0');

  type state_t is (INIT, IDLE, SEND_CMD, WAIT_RSP, DONE0, DONE1);
  signal state : state_t := INIT;
  signal init_cnt : natural range 0 to 4095 := 0;
  signal ch0_busy_i  : std_logic := '0';
  signal ch1_busy_i  : std_logic := '1';
  signal ch0_r       : std_logic_vector(11 downto 0) := (others => '0');
  signal ch1_r       : std_logic_vector(11 downto 0) := (others => '0');

  component altera_modular_adc_control is
    generic (
      clkdiv                          : integer;
      tsclkdiv                        : integer;
      tsclksel                        : integer;
      hard_pwd                        : integer;
      prescalar                       : integer;
      refsel                          : integer;
      device_partname_fivechar_prefix : string;
      is_this_first_or_second_adc     : integer;
      analog_input_pin_mask           : integer;
      dual_adc_mode                   : integer;
      enable_usr_sim                  : integer;
      reference_voltage_sim           : integer
    );
    port (
      clk               : in  std_logic;
      rst_n             : in  std_logic;
      cmd_valid         : in  std_logic;
      cmd_channel       : in  std_logic_vector(4 downto 0);
      cmd_sop           : in  std_logic;
      cmd_eop           : in  std_logic;
      cmd_ready         : out std_logic;
      rsp_valid         : out std_logic;
      rsp_channel       : out std_logic_vector(4 downto 0);
      rsp_data          : out std_logic_vector(11 downto 0);
      rsp_sop           : out std_logic;
      rsp_eop           : out std_logic;
      clk_in_pll_c0     : in  std_logic;
      clk_in_pll_locked : in  std_logic;
      sync_valid        : out std_logic;
      sync_ready        : in  std_logic
    );
  end component;

begin

  adc_control : altera_modular_adc_control
    generic map (
      clkdiv                          => 4,
      tsclkdiv                        => 1,
      tsclksel                        => 1,
      hard_pwd                        => 0,
      prescalar                       => 0,
      refsel                          => 1,
      device_partname_fivechar_prefix => "10M08",
      is_this_first_or_second_adc     => 1,
      analog_input_pin_mask           => 65791,
      dual_adc_mode                   => 0,
      enable_usr_sim                  => 0,
      reference_voltage_sim           => 65536
    )
    port map (
      clk               => sys_clk,
      rst_n             => not reset,
      cmd_valid         => cmd_valid,
      cmd_channel       => cmd_channel,
      cmd_sop           => cmd_sop,
      cmd_eop           => cmd_eop,
      cmd_ready         => cmd_ready,
      rsp_valid         => rsp_valid,
      rsp_channel       => open,
      rsp_data          => rsp_data,
      rsp_sop           => open,
      rsp_eop           => open,
      clk_in_pll_c0     => sys_clk,
      clk_in_pll_locked => sys_clk_locked,
      sync_valid        => open,
      sync_ready        => '0'
    );

  process(sys_clk)
  begin
    if rising_edge(sys_clk) then
      if reset = '1' then
        state <= INIT;
        init_cnt <= 0;
        cmd_valid <= '0';
        cmd_sop <= '0';
        cmd_eop <= '0';
        ch0_busy_i <= '0';
        ch1_busy_i <= '1';
        ch0_r <= (others => '0');
        ch1_r <= (others => '0');
      else
        cmd_valid <= '0';
        cmd_sop <= '0';
        cmd_eop <= '0';

        case state is

          when INIT =>
            if init_cnt < 4095 then
              init_cnt <= init_cnt + 1;
            else
              ch1_busy_i <= '0';
              state <= IDLE;
            end if;

          when IDLE =>
            ch0_busy_i <= '0';
            ch1_busy_i <= '0';
            if ch0_start = '1' then
              ch0_busy_i <= '1';
              cmd_channel <= std_logic_vector(to_unsigned(ch0_sel, 5));
              cmd_valid <= '1';
              cmd_sop <= '1';
              cmd_eop <= '1';
              state <= SEND_CMD;
            elsif ch1_start = '1' then
              ch1_busy_i <= '1';
              cmd_channel <= std_logic_vector(to_unsigned(ch1_sel, 5));
              cmd_valid <= '1';
              cmd_sop <= '1';
              cmd_eop <= '1';
              state <= SEND_CMD;
            end if;

          when SEND_CMD =>
            if cmd_ready = '1' then
              state <= WAIT_RSP;
            end if;

          when WAIT_RSP =>
            if rsp_valid = '1' then
              if ch0_busy_i = '1' then
                ch0_r <= rsp_data;
                state <= DONE0;
              else
                ch1_r <= rsp_data;
                state <= DONE1;
              end if;
            end if;

          when DONE0 =>
            ch0_busy_i <= '0';
            state <= IDLE;

          when DONE1 =>
            ch1_busy_i <= '0';
            state <= IDLE;

        end case;
      end if;
    end if;
  end process;

  ch0_busy  <= ch0_busy_i;
  ch1_busy  <= ch1_busy_i;
  ch0_result <= ch0_r;
  ch1_result <= ch1_r;
  ch0_valid <= '1' when state = DONE0 else '0';
  ch1_valid <= '1' when state = DONE1 else '0';

end rtl;
