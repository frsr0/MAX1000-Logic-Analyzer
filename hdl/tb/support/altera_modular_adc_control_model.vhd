library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity altera_modular_adc_control is
  generic (
    clkdiv                          : integer := 4;
    tsclkdiv                        : integer := 1;
    tsclksel                        : integer := 1;
    hard_pwd                        : integer := 0;
    prescalar                       : integer := 0;
    refsel                          : integer := 1;
    device_partname_fivechar_prefix : string := "10M08";
    is_this_first_or_second_adc     : integer := 1;
    analog_input_pin_mask           : integer := 65791;
    dual_adc_mode                   : integer := 0;
    enable_usr_sim                  : integer := 0;
    reference_voltage_sim           : integer := 65536
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
end altera_modular_adc_control;

architecture sim of altera_modular_adc_control is
  constant CONV_CYCLES : natural := 20;
  signal busy : std_logic := '0';
  signal ch_r : std_logic_vector(4 downto 0) := (others => '0');
begin

  cmd_ready <= not busy;

  process(clk)
    variable cnt : natural range 0 to CONV_CYCLES := 0;
  begin
    if rising_edge(clk) then
      if rst_n = '0' then
        busy <= '0';
        cnt := 0;
        rsp_valid <= '0';
        rsp_data <= (others => '0');
        rsp_channel <= (others => '0');
        rsp_sop <= '0';
        rsp_eop <= '0';
        sync_valid <= '0';
      else
        rsp_valid <= '0';
        rsp_sop <= '0';
        rsp_eop <= '0';

        if cmd_valid = '1' and busy = '0' then
          busy <= '1';
          ch_r <= cmd_channel;
          cnt := 0;
        end if;

        if busy = '1' then
          if cnt < CONV_CYCLES then
            cnt := cnt + 1;
          else
            busy <= '0';
            rsp_valid <= '1';
            rsp_channel <= ch_r;
            rsp_data <= x"AAA";
            rsp_sop <= '1';
            rsp_eop <= '1';
          end if;
        end if;
      end if;
    end if;
  end process;

end sim;
