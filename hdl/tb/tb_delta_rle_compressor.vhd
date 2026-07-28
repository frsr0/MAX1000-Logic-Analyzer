library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;

entity tb_delta_rle_compressor is
end tb_delta_rle_compressor;

architecture bench of tb_delta_rle_compressor is
  constant CLK_PERIOD : time := 10 ns;

  signal clk                : std_logic := '0';
  signal rst                : std_logic := '1';
  signal sample_in          : std_logic_vector(15 downto 0) := (others => '0');
  signal sample_valid       : std_logic := '0';
  signal compression_enable : std_logic := '1';
  signal delta_mode         : std_logic := '1';
  signal flush              : std_logic := '0';
  signal comp_data          : std_logic_vector(15 downto 0);
  signal comp_valid         : std_logic;
  signal busy               : std_logic;
  signal in_ready           : std_logic;
  signal output_count       : natural := 0;
  signal output_words       : std_logic_vector(63 downto 0) := (others => '0');
begin
  clk <= not clk after CLK_PERIOD / 2;

  dut : entity work.delta_rle_compressor
    port map (
      clk                => clk,
      rst                => rst,
      sample_in          => sample_in,
      sample_valid       => sample_valid,
      compression_enable => compression_enable,
      delta_mode         => delta_mode,
      flush              => flush,
      comp_data          => comp_data,
      comp_valid         => comp_valid,
      busy               => busy,
      in_ready           => in_ready
    );

  process
    variable count_v : natural := 0;
    variable words_v : std_logic_vector(63 downto 0) := (others => '0');
  begin
    wait for 2 * CLK_PERIOD;
    rst <= '0';

    -- Six constant samples are emitted by capture_compressor: one anchor and
    -- five identical packed-delta words. RLE must therefore emit two pairs.
    for i in 0 to 15 loop
      sample_in <= x"1234";
      sample_valid <= '1';
      wait until rising_edge(clk);
      assert in_ready = '1'
        report "delta_rle rejected a sample before the block was complete"
        severity failure;
    end loop;
    sample_valid <= '0';
    wait until rising_edge(clk);
    wait until rising_edge(clk);

    flush <= '1';
    wait until rising_edge(clk);
    flush <= '0';

    for i in 0 to 300 loop
      wait until rising_edge(clk);
      if comp_valid = '1' then
        assert count_v < 4
          report "delta_rle emitted more words than the expected two RLE pairs"
          severity failure;
        words_v(16 * count_v + 15 downto 16 * count_v) := comp_data;
        count_v := count_v + 1;
      end if;
      exit when count_v = 4 and busy = '0';
    end loop;

    output_count <= count_v;
    output_words <= words_v;
    assert count_v = 4
      report "delta_rle lost the anchor word or a packed-delta run"
      severity failure;
    assert words_v(15 downto 0) = x"0001"
      report "first RLE count is not one anchor sample"
      severity failure;
    assert words_v(31 downto 16) = x"1234"
      report "delta_rle lost the first anchor sample"
      severity failure;
    assert words_v(47 downto 32) = x"0005"
      report "packed-delta run count is incorrect"
      severity failure;
    assert words_v(63 downto 48) = x"0000"
      report "packed-delta run value is incorrect"
      severity failure;
    report "=== TB_DELTA_RLE_COMPRESSOR PASS ===";
    wait;
  end process;
end bench;
