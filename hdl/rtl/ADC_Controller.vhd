library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;

entity ADC_Controller is
  port (
    sys_clk        : in  std_logic;
    sys_clk_locked : in  std_logic := '1';
    -- Dedicated ADC conversion clock (12 MHz from SDRAM_PLL c3). The MAX10 ADC
    -- hard IP requires a clean PLL clock here at its rated frequency; the
    -- 100 MHz sys_clk violates the adcblock minimum-pulse-width spec.
    adc_clk        : in  std_logic := '0';
    adc_clk_locked : in  std_logic := '1';
    reset          : in  std_logic := '0';
    ch0_sel        : in  natural range 0 to 31 := 0;
    ch0_start      : in  std_logic := '0';
    ch0_result     : out std_logic_vector(11 downto 0) := (others => '0');
    ch0_valid      : out std_logic := '0';
    ch1_sel        : in  natural range 0 to 31 := 1;
    ch1_start      : in  std_logic := '0';
    ch1_result     : out std_logic_vector(11 downto 0) := (others => '0');
    ch1_valid      : out std_logic := '0';
    -- Channels 2 and 3 extend the round-robin to 4 analog inputs for the
    -- bit-packing capture pipeline. They default off/unconnected so existing
    -- 2-channel instantiations are unaffected. NOTE: ch2_sel/ch3_sel must name
    -- analog pins that are enabled in analog_input_pin_mask below, and those
    -- board pins must be wired -- otherwise these channels return no valid ADC.
    ch2_sel        : in  natural range 0 to 31 := 2;
    ch2_start      : in  std_logic := '0';
    ch2_result     : out std_logic_vector(11 downto 0) := (others => '0');
    ch2_valid      : out std_logic := '0';
    ch3_sel        : in  natural range 0 to 31 := 3;
    ch3_start      : in  std_logic := '0';
    ch3_result     : out std_logic_vector(11 downto 0) := (others => '0');
    ch3_valid      : out std_logic := '0'
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

  type state_t is (INIT, IDLE, SEND_CMD, WAIT_RSP);
  signal state : state_t := INIT;
  signal init_cnt : natural range 0 to 4095 := 0;
  signal ch_req    : std_logic_vector(3 downto 0) := "0000";
  signal ch_index  : natural range 0 to 3 := 0;

  -- Per-channel state as 4-wide arrays so the round-robin is index-driven.
  type   result_arr is array(0 to 3) of std_logic_vector(11 downto 0);
  type   sel_arr_t  is array(0 to 3) of natural range 0 to 31;
  signal ch_r      : result_arr := (others => (others => '0'));
  signal ch_valid_i : std_logic_vector(3 downto 0) := (others => '0');
  signal sel_arr   : sel_arr_t;
  signal start_v   : std_logic_vector(3 downto 0);

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
      clk_in_pll_c0     => adc_clk,
      clk_in_pll_locked => adc_clk_locked,
      sync_valid        => open,
      sync_ready        => '0'
    );

  -- Pack the per-channel select/start inputs into index-addressable vectors.
  sel_arr <= (0 => ch0_sel, 1 => ch1_sel, 2 => ch2_sel, 3 => ch3_sel);
  start_v <= ch3_start & ch2_start & ch1_start & ch0_start;

  process(sys_clk)
  begin
    if rising_edge(sys_clk) then
      if reset = '1' then
        state <= INIT;
        init_cnt <= 0;
        cmd_valid <= '0';
        cmd_sop <= '0';
        cmd_eop <= '0';
        ch_req <= (others => '0');
        ch_r <= (others => (others => '0'));
      else
        cmd_valid <= '0';
        cmd_sop <= '0';
        cmd_eop <= '0';
        ch_valid_i <= (others => '0');

        case state is

          when INIT =>
            if init_cnt < 4095 then
              init_cnt <= init_cnt + 1;
            else
              state <= IDLE;
            end if;

          when IDLE =>
            -- Latch which channels were requested; sweep them low index first.
            if start_v /= "0000" then
              ch_req   <= start_v;
              ch_index <= 0;
              state <= SEND_CMD;
            end if;

          when SEND_CMD =>
            -- Issue a single-sample command for the current channel if it was
            -- requested; otherwise skip to the next, ending the sweep after 3.
            if ch_req(ch_index) = '1' then
              cmd_channel <= std_logic_vector(to_unsigned(sel_arr(ch_index), 5));
              cmd_valid <= '1';
              cmd_sop <= '1';
              cmd_eop <= '1';
              if cmd_ready = '1' then
                state <= WAIT_RSP;
              end if;
            else
              if ch_index < 3 then
                ch_index <= ch_index + 1;
              else
                state <= IDLE;
              end if;
            end if;

          when WAIT_RSP =>
            if rsp_valid = '1' then
              ch_r(ch_index)      <= rsp_data;
              ch_valid_i(ch_index) <= '1';
              if ch_index < 3 then
                ch_index <= ch_index + 1;
                state <= SEND_CMD;
              else
                state <= IDLE;
              end if;
            end if;

        end case;
      end if;
    end if;
  end process;

  ch0_result <= ch_r(0);  ch0_valid <= ch_valid_i(0);
  ch1_result <= ch_r(1);  ch1_valid <= ch_valid_i(1);
  ch2_result <= ch_r(2);  ch2_valid <= ch_valid_i(2);
  ch3_result <= ch_r(3);  ch3_valid <= ch_valid_i(3);

end rtl;
