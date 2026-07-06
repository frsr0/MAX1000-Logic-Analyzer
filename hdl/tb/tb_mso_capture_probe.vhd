library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;

entity tb_mso_capture_probe is
end tb_mso_capture_probe;

architecture bench of tb_mso_capture_probe is
  constant FAST_PERIOD : time := 5 ns;
  constant ADC_PERIOD  : time := 10 ns;

  signal fast_clk : std_logic := '0';
  signal adc_clk  : std_logic := '0';
  signal rst      : std_logic := '1';

  signal adc_ch0       : std_logic_vector(11 downto 0) := (others => '0');
  signal adc_ch1       : std_logic_vector(11 downto 0) := (others => '0');
  signal adc_ch2       : std_logic_vector(11 downto 0) := (others => '0');
  signal adc_ch3       : std_logic_vector(11 downto 0) := (others => '0');
  signal adc_ch0_valid : std_logic := '0';
  signal adc_ch1_valid : std_logic := '0';
  signal adc_ch2_valid : std_logic := '0';
  signal adc_ch3_valid : std_logic := '0';

  signal digital_in : std_logic_vector(15 downto 0) := (others => '0');
  signal out_data   : std_logic_vector(15 downto 0);
  signal out_valid  : std_logic;
  signal out_ready  : std_logic := '1';
  signal overflow   : std_logic;

  signal analog_words : natural := 0;
  signal digital_words : natural := 0;
begin
  fast_clk <= not fast_clk after FAST_PERIOD / 2;
  adc_clk  <= not adc_clk  after ADC_PERIOD / 2;

  dut : entity work.mso_capture
    port map (
      fast_clk      => fast_clk,
      adc_clk       => adc_clk,
      rst           => rst,
      adc_ch0       => adc_ch0,
      adc_ch0_valid => adc_ch0_valid,
      adc_ch1       => adc_ch1,
      adc_ch1_valid => adc_ch1_valid,
      adc_ch2       => adc_ch2,
      adc_ch2_valid => adc_ch2_valid,
      adc_ch3       => adc_ch3,
      adc_ch3_valid => adc_ch3_valid,
      digital_in    => digital_in,
      out_data      => out_data,
      out_valid     => out_valid,
      out_ready     => out_ready,
      dig_overflow  => overflow
    );

  adc_stim : process
    variable cnt : natural := 0;
    variable sample : natural;
  begin
    wait until rising_edge(adc_clk);
    if rst = '0' then
      adc_ch0_valid <= '0';
      adc_ch1_valid <= '0';
      adc_ch2_valid <= '0';
      adc_ch3_valid <= '0';
      case cnt mod 4 is
        when 0 =>
          sample := (16#100# + cnt) mod 4096;
          adc_ch0 <= std_logic_vector(to_unsigned(sample, 12));
          adc_ch0_valid <= '1';
        when 1 =>
          sample := (16#200# + cnt) mod 4096;
          adc_ch1 <= std_logic_vector(to_unsigned(sample, 12));
          adc_ch1_valid <= '1';
        when 2 =>
          sample := (16#300# + cnt) mod 4096;
          adc_ch2 <= std_logic_vector(to_unsigned(sample, 12));
          adc_ch2_valid <= '1';
        when others =>
          sample := (16#400# + cnt) mod 4096;
          adc_ch3 <= std_logic_vector(to_unsigned(sample, 12));
          adc_ch3_valid <= '1';
      end case;
      cnt := cnt + 1;
    end if;
  end process;

  digital_stim : process(fast_clk)
  begin
    if rising_edge(fast_clk) then
      digital_in <= std_logic_vector(unsigned(digital_in) + 1);
    end if;
  end process;

  sink : process(fast_clk)
  begin
    if rising_edge(fast_clk) then
      if out_valid = '1' then
        if out_data(15) = '0' then
          analog_words <= analog_words + 1;
        else
          digital_words <= digital_words + 1;
        end if;
      end if;
    end if;
  end process;

  stim : process
  begin
    wait for 200 ns;
    rst <= '0';
    wait for 200 us;
    assert analog_words > 0
      report "expected packed analog words" severity failure;
    assert digital_words > 0
      report "expected packed digital words" severity failure;
    report "analog_words=" & integer'image(analog_words)
           & " digital_words=" & integer'image(digital_words);
    std.env.finish;
    wait;
  end process;
end bench;
