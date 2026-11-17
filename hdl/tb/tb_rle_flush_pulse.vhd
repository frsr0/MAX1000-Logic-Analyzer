-- Regression for the RLE stream-tail bug: the raw-stream drain (OLS_Interface
-- STATE 4) pulses `flush` for ONE cycle right after the last sample. When that
-- sample starts a fresh run, the compressor is busy emitting the previous run
-- (EMIT_CNT / EMIT_VAL) and used to ignore the pulse, so the final single-sample
-- run was never sent.
--
-- The bench feeds A,A,A,B and applies a one-cycle flush pulse D cycles after
-- the last sample (D = 0..5, covering every compressor state). Exactly the four
-- words (3,A),(1,B) must come out for every D.
library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;

entity tb_rle_flush_pulse is
end tb_rle_flush_pulse;

architecture sim of tb_rle_flush_pulse is
  signal clk : std_logic := '0';
  signal rst : std_logic := '0';
  signal sample_in : std_logic_vector(15 downto 0) := (others => '0');
  signal sample_valid : std_logic := '0';
  signal flush : std_logic := '0';
  signal comp_data : std_logic_vector(15 downto 0);
  signal comp_valid : std_logic;
  signal busy : std_logic;
  signal in_ready : std_logic;
  signal col_rst : std_logic := '0';
  type mem_t is array (0 to 15) of std_logic_vector(15 downto 0);
  signal col_mem : mem_t := (others => (others => '0'));
  signal col_n : integer := 0;
  signal done : boolean := false;
begin
  dut : entity work.rle_compressor
    port map (clk => clk, rst => rst, sample_in => sample_in,
              sample_valid => sample_valid, compression_enable => '1',
              flush => flush, comp_data => comp_data, comp_valid => comp_valid,
              busy => busy, in_ready => in_ready);

  clk <= not clk after 5 ns when not done;

  collector : process(clk)
  begin
    if rising_edge(clk) then
      if col_rst = '1' then
        col_n <= 0;
      elsif comp_valid = '1' and col_n < 16 then
        col_mem(col_n) <= comp_data;
        col_n <= col_n + 1;
      end if;
    end if;
  end process;

  stim : process
    variable fails : integer := 0;
    procedure feed(w : std_logic_vector(15 downto 0)) is
    begin
      wait until falling_edge(clk);
      while in_ready = '0' loop wait until falling_edge(clk); end loop;
      sample_in <= w; sample_valid <= '1';
      wait until falling_edge(clk);
      sample_valid <= '0';
    end procedure;
  begin
    for d in 0 to 5 loop
      rst <= '1'; col_rst <= '1'; flush <= '0'; sample_valid <= '0';
      wait until rising_edge(clk);
      rst <= '0'; col_rst <= '0';
      feed(x"AAAA"); feed(x"AAAA"); feed(x"AAAA"); feed(x"BBBB");
      -- feed() returns one falling edge after the last sample_valid cycle began
      -- (the sample was consumed at the previous rising edge)
      for i in 1 to d loop wait until falling_edge(clk); end loop;
      flush <= '1';
      wait until falling_edge(clk);
      flush <= '0';
      for i in 1 to 12 loop wait until falling_edge(clk); end loop;
      if col_n /= 4 or col_mem(0) /= x"0003" or col_mem(1) /= x"AAAA"
         or col_mem(2) /= x"0001" or col_mem(3) /= x"BBBB" then
        report "FAIL: flush delay " & integer'image(d) & ": got " & integer'image(col_n) &
               " words, w0=" & to_hstring(col_mem(0)) & " w1=" & to_hstring(col_mem(1)) &
               " w2=" & to_hstring(col_mem(2)) & " w3=" & to_hstring(col_mem(3))
          severity error;
        fails := fails + 1;
      else
        report "flush delay " & integer'image(d) & ": final run emitted";
      end if;
    end loop;
    assert fails = 0 report "tb_rle_flush_pulse: " & integer'image(fails) & " delay(s) lost the final run"
      severity failure;
    report "tb_rle_flush_pulse: ALL PASS";
    done <= true;
    std.env.finish;
    wait;
  end process;
end sim;
