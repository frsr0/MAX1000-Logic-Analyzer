library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;

entity tb_digital_rle_exactly_once is
end tb_digital_rle_exactly_once;

architecture bench of tb_digital_rle_exactly_once is
  constant CLK_PERIOD : time := 5 ns;

  signal clk          : std_logic := '0';
  signal rst          : std_logic := '1';
  signal digital_in   : std_logic_vector(15 downto 0) := (others => '0');
  signal packet_out   : std_logic_vector(15 downto 0);
  signal packet_valid : std_logic;
  signal packet_ready : std_logic := '1';
  signal overflow     : std_logic;

  type count_array is array(0 to 3) of natural;
  signal slice_packets : count_array := (others => 0);
  signal total_packets : natural := 0;
begin
  clk <= not clk after CLK_PERIOD / 2;

  dut : entity work.digital_rle
    port map (
      clk          => clk,
      rst          => rst,
      clk_en       => '1',
      digital_in   => digital_in,
      packet_out   => packet_out,
      packet_valid => packet_valid,
      packet_ready => packet_ready,
      overflow     => overflow
    );

  sink : process(clk)
    variable slice_id : natural range 0 to 3;
  begin
    if rising_edge(clk) then
      if packet_valid = '1' and packet_ready = '1' then
        slice_id := to_integer(unsigned(packet_out(14 downto 13)));
        slice_packets(slice_id) <= slice_packets(slice_id) + 1;
        total_packets <= total_packets + 1;
        assert packet_out(12 downto 9) = "0000"
          report "static-zero slice emitted the wrong value" severity failure;
        assert packet_out(8 downto 0) = std_logic_vector(to_unsigned(511, 9))
          report "static-zero slice emitted the wrong dwell" severity failure;
      end if;
    end if;
  end process;

  stim : process
  begin
    wait for 4 * CLK_PERIOD;
    wait until rising_edge(clk);
    rst <= '0';

    -- One saturation interval plus enough cycles to drain all four pending
    -- slices, but well short of the next saturation interval.
    for i in 0 to 539 loop
      wait until rising_edge(clk);
    end loop;

    assert total_packets = 4
      report "expected exactly four packets, got "
             & integer'image(total_packets) severity failure;
    for i in 0 to 3 loop
      assert slice_packets(i) = 1
        report "slice " & integer'image(i) & " emitted "
               & integer'image(slice_packets(i)) & " packets"
        severity failure;
    end loop;
    assert overflow = '0'
      report "static input unexpectedly overflowed" severity failure;

    report "digital_rle exactly-once handshake passed";
    std.env.finish;
    wait;
  end process;
end bench;
