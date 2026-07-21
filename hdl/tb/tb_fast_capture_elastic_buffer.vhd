library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.NUMERIC_STD.ALL;

entity tb_fast_capture_elastic_buffer is
end tb_fast_capture_elastic_buffer;

architecture sim of tb_fast_capture_elastic_buffer is
  signal clk : std_logic := '0';
  signal rst : std_logic := '1';
  signal in_data : std_logic_vector(7 downto 0) := (others => '0');
  signal in_valid, in_ready : std_logic := '0';
  signal out_data : std_logic_vector(7 downto 0);
  signal out_valid, out_ready : std_logic := '0';
begin
  clk <= not clk after 5 ns;

  dut: entity work.fast_capture_elastic_buffer
    generic map (DATA_WIDTH => 8)
    port map (clk, rst, in_data, in_valid, in_ready,
              out_data, out_valid, out_ready);

  process
    procedure tick is
    begin
      wait until rising_edge(clk);
      wait for 1 ns;
    end procedure;
    procedure push(value : natural) is
      variable accepted : std_logic;
    begin
      in_data  <= std_logic_vector(to_unsigned(value, 8));
      in_valid <= '1';
      loop
        accepted := in_ready;
        tick;
        exit when accepted = '1';
      end loop;
      in_valid <= '0';
    end procedure;
    procedure pop_expect(value : natural) is
    begin
      assert out_valid = '1' report "expected buffered word" severity failure;
      assert out_data = std_logic_vector(to_unsigned(value, 8))
        report "buffer reordered or corrupted data" severity failure;
      out_ready <= '1';
      tick;
      out_ready <= '0';
    end procedure;
  begin
    tick;
    rst <= '0';

    -- Fill both entries, then stall the consumer: head data must remain.
    push(16#11#);
    push(16#22#);
    assert in_ready = '0' report "buffer should be full" severity failure;
    assert out_valid = '1' and out_data = x"11"
      report "head changed while full" severity failure;

    -- Pop while pushing: the second word is delivered, new word is retained.
    in_data <= x"33";
    in_valid <= '1';
    out_ready <= '1';
    tick;
    in_valid <= '0';
    out_ready <= '0';
    assert out_valid = '1' and out_data = x"22"
      report "simultaneous pop/push lost ordering" severity failure;
    pop_expect(16#22#);
    pop_expect(16#33#);

    assert out_valid = '0' report "buffer not empty after final pop" severity failure;
    report "=== TB PASSED: fast_capture_elastic_buffer invariants ===" severity note;
    wait;
  end process;
end sim;
