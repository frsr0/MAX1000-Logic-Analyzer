-- Single-shot captures of 1, 2 and 3 samples must store exactly that many words:
-- the committed count (Producer_Index) equals the request and the read-back
-- progression is exact. Uses pump_integrity_case (declared in
-- tb_pump_integrity.vhd, analysed before this file by run_all_tbs.sh).
library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use work.sim_pkg.all;

entity tb_samples_edge is
end tb_samples_edge;

architecture bench of tb_samples_edge is
  signal clk, fastclk, pclk, pclk_model : std_logic := '0';
  signal done : std_logic_vector(3 downto 0);
begin
  clk    <= not clk    after 4990 ps;
  fastclk <= not fastclk after 2495 ps;
  pclk   <= not pclk   after 2994 ps;
  pclk_model <= transport pclk after 1.5 ns;

  c1 : entity work.pump_integrity_case
    generic map (NAME => "one", RATE_DIV => 5, SAMPLES => 1)
    port map (clk, fastclk, pclk, pclk_model, done(0));
  c2 : entity work.pump_integrity_case
    generic map (NAME => "two", RATE_DIV => 5, SAMPLES => 2)
    port map (clk, fastclk, pclk, pclk_model, done(1));
  c3 : entity work.pump_integrity_case
    generic map (NAME => "three", RATE_DIV => 5, SAMPLES => 3)
    port map (clk, fastclk, pclk, pclk_model, done(2));
  c4 : entity work.pump_integrity_case
    generic map (NAME => "one_fast", RATE_DIV => 1, SAMPLES => 1)
    port map (clk, fastclk, pclk, pclk_model, done(3));

  finish : process
  begin
    wait until done = "1111" for 25 ms;
    assert done = "1111" report "not every case completed" severity failure;
    report "tb_samples_edge: ALL PASS";
    std.env.finish;
    wait;
  end process;
end bench;
