-- Decode-level test for the Signal_Gen UART transmitter.
--
-- The existing tb_signal_gen / tb_gen_loopback only check the start-bit width
-- and that a burst of edges appears — they never reconstruct the bytes. This
-- testbench drives a known payload through the generator and *decodes* Tx_Out
-- with a standard 8N1 LSB-first receiver, asserting that the decoded bytes
-- equal what was sent. It mirrors the host configuration (Baud_Div = 868, the
-- value the host writes for 115200 at its 100 MHz clock notion).
library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;
use work.sim_pkg.all;

entity tb_gen_uart_decode is
  generic (
    CLK_FREQ : natural := 100000000;
    BAUD_DIV : natural := 868
  );
end tb_gen_uart_decode;

architecture bench of tb_gen_uart_decode is
  constant CLK_PERIOD : time := 1 sec / real(CLK_FREQ);
  constant BIT_TIME   : time := BAUD_DIV * CLK_PERIOD;

  signal clk       : std_logic := '0';
  signal load_byte : std_logic_vector(7 downto 0) := (others => '0');
  signal load_we   : std_logic := '0';
  signal start     : std_logic := '0';
  signal baud_div_s : std_logic_vector(15 downto 0) := std_logic_vector(to_unsigned(BAUD_DIV, 16));
  signal tx_out    : std_logic;
  signal scl_out   : std_logic;
  signal busy      : std_logic;
  signal active    : std_logic;

  constant N : natural := 14;
  type pl_t is array (0 to N-1) of std_logic_vector(7 downto 0);
  -- "MAX1000 jumper"
  constant PAYLOAD : pl_t := (
    x"4D", x"41", x"58", x"31", x"30", x"30", x"30", x"20",
    x"6A", x"75", x"6D", x"70", x"65", x"72");
begin

  gen_clk(clk, CLK_PERIOD / 2);

  DUT : entity work.Signal_Gen
    generic map (FIFO_DEPTH => 256)
    port map (
      CLK => clk, Load_Byte => load_byte, Load_We => load_we,
      Start => start, Baud_Div => baud_div_s, Proto => '0', SPI_Mode => '0',
      Tx_Out => tx_out, Scl_Out => scl_out, Busy => busy, Active => active,
      I2C_Rd_Len => 0, I2C_Dev_R => (others=>'0'), Sda_In => '1',
      CRC_En => '0', CRC_Poly => x"A001"
    );

  process
    variable rxb   : std_logic_vector(7 downto 0);
    variable stopb : std_logic;
    variable fails : natural := 0;
  begin
    report "=== tb_gen_uart_decode ===";
    -- load payload into the generator FIFO
    for i in 0 to N-1 loop
      wait until rising_edge(clk);
      load_byte <= PAYLOAD(i); load_we <= '1';
      wait until rising_edge(clk);
      load_we <= '0';
    end loop;
    wait_cycles(clk, 5);
    start <= '1'; wait_cycles(clk, 1); start <= '0';

    -- decode N bytes off Tx_Out (idle-high 8N1, LSB first)
    for i in 0 to N-1 loop
      wait until falling_edge(tx_out);     -- leading edge of the start bit
      wait for BIT_TIME * 1.5;             -- advance to the centre of data bit 0
      for b in 0 to 7 loop
        rxb(b) := tx_out;
        wait for BIT_TIME;
      end loop;
      stopb := tx_out;                     -- should be the centre of the stop bit
      report "byte " & integer'image(i) &
             ": sent=0x" & to_hstring(PAYLOAD(i)) &
             " decoded=0x" & to_hstring(rxb) &
             " stop=" & std_logic'image(stopb);
      if rxb /= PAYLOAD(i) then fails := fails + 1; end if;
      if stopb /= '1' then
        report "  -> BAD STOP BIT" severity warning;
        fails := fails + 1;
      end if;
    end loop;

    if fails = 0 then
      report "=== PASS: generator UART round-trips correctly ===" severity note;
    else
      report "=== FAIL: " & integer'image(fails) &
             " byte/stop mismatches ===" severity error;
    end if;
    wait;
  end process;

end bench;
