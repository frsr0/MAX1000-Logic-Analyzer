library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;

entity tb_capture_compressor is
end tb_capture_compressor;

architecture sim of tb_capture_compressor is
  constant NSAMP : natural := 16;
  type sample_array is array (0 to NSAMP - 1) of std_logic_vector(15 downto 0);
  type output_array is array (0 to 15) of std_logic_vector(15 downto 0);

  signal clk           : std_logic := '0';
  signal rst           : std_logic := '0';
  signal sample_in     : std_logic_vector(15 downto 0) := (others => '0');
  signal sample_valid  : std_logic := '0';
  signal comp_enable   : std_logic := '1';
  signal comp_data     : std_logic_vector(15 downto 0);
  signal comp_valid    : std_logic;
  signal comp_busy     : std_logic;
  signal comp_ready    : std_logic;
  signal collect_clear : std_logic := '0';
  signal output_count  : natural := 0;
  signal output_words  : output_array := (others => (others => '0'));
  signal done          : boolean := false;
begin
  clk_process : process
  begin
    while not done loop
      clk <= '0';
      wait for 5 ns;
      clk <= '1';
      wait for 5 ns;
    end loop;
    wait;
  end process;

  dut : entity work.capture_compressor
    port map (
      clk                => clk,
      rst                => rst,
      sample_in          => sample_in,
      sample_valid       => sample_valid,
      compression_enable => comp_enable,
      comp_data          => comp_data,
      comp_valid         => comp_valid,
      busy               => comp_busy,
      in_ready           => comp_ready
    );

  collector : process(clk)
  begin
    if rising_edge(clk) then
      if collect_clear = '1' then
        output_count <= 0;
      elsif comp_valid = '1' then
        assert output_count < output_words'length
          report "capture compressor emitted too many words"
          severity failure;
        output_words(output_count) <= comp_data;
        output_count <= output_count + 1;
      end if;
    end if;
  end process;

  stimulus : process
    variable ramp      : sample_array;
    variable overflow  : sample_array;
    variable final_overflow : sample_array;
  begin
    for i in 0 to NSAMP - 1 loop
      ramp(i) := std_logic_vector(to_unsigned(i, 16));
      overflow(i) := std_logic_vector(to_unsigned(i, 16));
      final_overflow(i) := std_logic_vector(to_unsigned(i, 16));
    end loop;
    overflow(1) := x"0100";
    final_overflow(15) := x"0100";

    -- Compression is already enabled when reset is released. The first sample
    -- must become the delta anchor, not an unframed passthrough word.
    rst <= '1';
    collect_clear <= '1';
    wait until rising_edge(clk);
    rst <= '0';
    collect_clear <= '0';
    wait until falling_edge(clk);
    for i in 0 to NSAMP - 1 loop
      sample_in <= ramp(i);
      sample_valid <= '1';
      wait until rising_edge(clk);
      wait until falling_edge(clk);
    end loop;
    sample_valid <= '0';
    for i in 0 to 31 loop
      wait until rising_edge(clk);
    end loop;
    wait for 1 ns;
    assert output_count = 6
      report "startup ramp output count=" & integer'image(output_count) &
             ", expected 6"
      severity failure;
    assert output_words(0) = x"0000"
      report "startup ramp anchor was not the first sample"
      severity failure;

    -- An overflow invalidates the fixed-size delta block. It must not emit a
    -- saturated value that the host could accept as a lossless block.
    rst <= '1';
    collect_clear <= '1';
    wait until rising_edge(clk);
    rst <= '0';
    collect_clear <= '0';
    wait until falling_edge(clk);
    for i in 0 to NSAMP - 1 loop
      sample_in <= overflow(i);
      sample_valid <= '1';
      wait until rising_edge(clk);
      wait until falling_edge(clk);
    end loop;
    sample_valid <= '0';
    for i in 0 to 31 loop
      wait until rising_edge(clk);
    end loop;
    wait for 1 ns;
    assert output_count < 6
      report "early overflow produced a complete-looking delta block"
      severity failure;

    -- The final delta must take the same invalid-block path; checking this
    -- catches the old sample_cnt=15 precedence bug.
    rst <= '1';
    collect_clear <= '1';
    wait until rising_edge(clk);
    rst <= '0';
    collect_clear <= '0';
    wait until falling_edge(clk);
    for i in 0 to NSAMP - 1 loop
      sample_in <= final_overflow(i);
      sample_valid <= '1';
      wait until rising_edge(clk);
      wait until falling_edge(clk);
    end loop;
    sample_valid <= '0';
    for i in 0 to 31 loop
      wait until rising_edge(clk);
    end loop;
    wait for 1 ns;
    assert output_count < 6
      report "final overflow produced a complete-looking delta block"
      severity failure;

    report "=== TB_CAPTURE_COMPRESSOR PASS ===" severity note;
    done <= true;
    wait;
  end process;
end sim;
