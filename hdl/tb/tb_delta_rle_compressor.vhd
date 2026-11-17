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

  -- Collect RLE output for the whole run, including words emitted while input
  -- is still being fed (the first run closes when the zero deltas arrive).
  collector : process(clk)
  begin
    if rising_edge(clk) then
      if comp_valid = '1' then
        assert output_count < 4
          report "delta_rle emitted more words than the expected two RLE pairs"
          severity failure;
        output_words(16 * output_count + 15 downto 16 * output_count) <= comp_data;
        output_count <= output_count + 1;
      end if;
    end if;
  end process;

  process
  begin
    wait for 2 * CLK_PERIOD;
    rst <= '0';

    -- Two constant 16-sample groups. Group 0 emits a verbatim anchor and five
    -- zero delta words; group 1 emits a chained zero anchor and five more, so
    -- RLE sees one 1234 run followed by eleven zero words. Input is held low
    -- across group 0's FLUSH (in_ready low) and resumes once ANCHOR is back.
    for g in 0 to 1 loop
      for i in 0 to 15 loop
        sample_in <= x"1234";
        sample_valid <= '1';
        wait until rising_edge(clk);
        assert in_ready = '1'
          report "delta_rle rejected a sample before the block was complete"
          severity failure;
      end loop;
      sample_valid <= '0';
      if g = 0 then
        loop
          wait until falling_edge(clk);
          exit when in_ready = '0';
        end loop;
        loop
          wait until falling_edge(clk);
          exit when in_ready = '1';
        end loop;
      end if;
    end loop;
    wait until rising_edge(clk);
    wait until rising_edge(clk);

    flush <= '1';
    wait until rising_edge(clk);
    flush <= '0';

    for i in 0 to 300 loop
      wait until rising_edge(clk);
      exit when output_count = 4 and busy = '0';
    end loop;

    assert output_count = 4
      report "delta_rle lost the anchor word or a packed-delta run"
      severity failure;
    assert output_words(15 downto 0) = x"0001"
      report "first RLE count is not one anchor sample"
      severity failure;
    assert output_words(31 downto 16) = x"1234"
      report "delta_rle lost the first anchor sample"
      severity failure;
    assert output_words(47 downto 32) = x"000B"
      report "packed-delta run count is incorrect"
      severity failure;
    assert output_words(63 downto 48) = x"0000"
      report "packed-delta run value is incorrect"
      severity failure;
    report "=== TB_DELTA_RLE_COMPRESSOR PASS ===";
    std.env.finish;
    wait;
  end process;
end bench;
