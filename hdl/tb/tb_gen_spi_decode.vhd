-- Decode-level test for the Signal_Gen SPI master.
--
-- tb_signal_gen only checks that SCLK produces one high pulse; it never
-- verifies the clock toggles 8 times per byte or that MOSI carries the right
-- bits. This testbench drives a known payload, samples MOSI on every SCLK
-- rising edge (MSB-first), and asserts the decoded bytes — isolating the SPI
-- FSM from the capture/loopback integration.
library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;
use work.sim_pkg.all;

entity tb_gen_spi_decode is
  generic (
    CLK_FREQ : natural := 100000000;
    BAUD_DIV : natural := 50
  );
end tb_gen_spi_decode;

architecture bench of tb_gen_spi_decode is
  constant CLK_PERIOD : time := 1 sec / real(CLK_FREQ);
  signal clk       : std_logic := '0';
  signal load_byte : std_logic_vector(7 downto 0) := (others => '0');
  signal load_we   : std_logic := '0';
  signal start     : std_logic := '0';
  signal baud_div_s : std_logic_vector(15 downto 0) := std_logic_vector(to_unsigned(BAUD_DIV, 16));
  signal tx_out    : std_logic;
  signal scl_out   : std_logic;
  signal busy      : std_logic;
  signal active    : std_logic;

  constant N : natural := 2;
  type pl_t is array (0 to N-1) of std_logic_vector(7 downto 0);
  constant PAYLOAD : pl_t := (x"A5", x"3C");
begin
  gen_clk(clk, CLK_PERIOD / 2);

  DUT : entity work.Signal_Gen
    generic map (FIFO_DEPTH => 256)
    port map (
      CLK => clk, Load_Byte => load_byte, Load_We => load_we,
      Start => start, Baud_Div => baud_div_s, Proto => '0', SPI_Mode => '1',
      Tx_Out => tx_out, Scl_Out => scl_out, Busy => busy, Active => active,
      I2C_Rd_Len => 0, I2C_Dev_R => (others=>'0'), Sda_In => '1',
      CRC_En => '0', CRC_Poly => x"A001"
    );

  process
    variable rxb   : std_logic_vector(7 downto 0);
    variable nbits : natural := 0;
    variable nby   : natural := 0;
    variable fails : natural := 0;
    variable edges : natural := 0;
  begin
    report "=== tb_gen_spi_decode ===";
    for i in 0 to N-1 loop
      wait until rising_edge(clk);
      load_byte <= PAYLOAD(i); load_we <= '1';
      wait until rising_edge(clk);
      load_we <= '0';
    end loop;
    wait_cycles(clk, 5);
    start <= '1'; wait_cycles(clk, 1); start <= '0';
    wait_until(clk, busy, '1', 200 us, "SPI should go busy");

    -- Sample MOSI at the MIDDLE of each SCLK-high plateau (as the real
    -- hardware decoder does), MSB-first. Mid-plateau sampling is what exposes
    -- merged byte-boundary plateaus; a rising-edge sampler would miss them.
    while nby < N loop
      wait until rising_edge(scl_out) for 100 us;
      exit when scl_out /= '1';            -- timed out -> clock stopped
      wait for (BAUD_DIV / 2) * CLK_PERIOD; -- advance to mid high-plateau
      exit when scl_out /= '1';            -- plateau ended early
      edges := edges + 1;
      rxb := rxb(6 downto 0) & tx_out;
      nbits := nbits + 1;
      if nbits = 8 then
        report "byte " & integer'image(nby) & ": sent=0x" & to_hstring(PAYLOAD(nby)) &
               " decoded=0x" & to_hstring(rxb);
        if rxb /= PAYLOAD(nby) then fails := fails + 1; end if;
        nbits := 0; nby := nby + 1;
      end if;
    end loop;

    report "total SCLK rising edges seen = " & integer'image(edges) &
           " (expect " & integer'image(8 * N) & ")";
    if edges < 8 * N then
      report "=== FAIL: SCLK did not clock all bits ===" severity error;
    elsif fails = 0 then
      report "=== PASS: SPI generator clocks + data correct ===" severity note;
    else
      report "=== FAIL: " & integer'image(fails) & " byte mismatches ===" severity error;
    end if;
    wait;
  end process;
end bench;
