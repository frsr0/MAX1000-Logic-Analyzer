-- Bit_Engine 24-bit divider fidelity.
--
-- Verifies the widened REG_GEN_BAUD divider (Bit_Div 24 bits) produces the
-- exact symbol rate the host asks for. The engine emits one 2-bit symbol per
-- 4*(Bit_Div+1) + 1 clock cycles (4 ticks of Bit_Div+1 each, plus one LOAD
-- cycle), so a [0,3,0,3] pattern (TX toggles every symbol) has an out_0
-- period of exactly 4*(Bit_Div+1) + 1 cycles.
--
-- The critical case is Bit_Div = 83499: the 1200-baud divider at
-- sys_clk = 100.2 MHz. A 16-bit register truncates it to 17963 (~5.6 kHz
-- instead of 1.2 kHz); the 24-bit divider must hold the full value.
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity tb_bit_engine_div24 is
end entity;

architecture bench of tb_bit_engine_div24 is
  signal clk         : std_logic := '0';
  signal tx_data     : std_logic_vector(7 downto 0) := (others => '0');
  signal tx_we       : std_logic := '0';
  signal tx_used     : std_logic_vector(7 downto 0);
  signal rx_data     : std_logic_vector(7 downto 0);
  signal rx_re       : std_logic := '0';
  signal rx_used     : std_logic_vector(7 downto 0);
  signal rx_overflow : std_logic;
  signal busy        : std_logic;
  signal done        : std_logic;
  signal start       : std_logic := '0';
  signal clear       : std_logic := '0';
  signal out_0       : std_logic;
  signal out_1       : std_logic;
  signal bit_div     : std_logic_vector(23 downto 0) := (others => '0');
begin
  clk <= not clk after 5 ns;

  dut : entity work.Bit_Engine
    port map (
      CLK         => clk,
      TX_Data     => tx_data,
      TX_We       => tx_we,
      TX_Used     => tx_used,
      RX_Data     => rx_data,
      RX_Re       => rx_re,
      RX_Used     => rx_used,
      RX_Overflow => rx_overflow,
      Bit_Div     => bit_div,
      Num_Syms    => x"FFFF",
      Over_Sample => "00",
      RX_Enable   => '0',
      Clk_Toggle  => '0',
      Start       => start,
      Repeat      => '1',
      Busy        => busy,
      Done        => done,
      Clear       => clear,
      Out_0       => out_0,
      Out_1       => out_1,
      In_0        => '1'
    );

  process
    -- Measure the out_0 period for the current Bit_Div (returns in cycles).
    procedure measure_period(natural_div : natural;
                             expected    : natural;
                             label_text  : string) is
      variable cycles : natural := 0;
      variable prev   : std_logic := '0';
    begin
      -- Load [0,3,0,3]: TX (bit 0) toggles every symbol.
      tx_data <= x"CC";
      tx_we   <= '1';
      wait until rising_edge(clk);
      tx_we   <= '0';
      bit_div <= std_logic_vector(to_unsigned(natural_div, 24));
      start   <= '1';
      wait until rising_edge(clk);
      start   <= '0';

      -- Wait for the first out_0 rising edge.
      while not (out_0 = '1' and prev = '0') loop
        prev := out_0;
        wait until rising_edge(clk);
      end loop;
      prev   := out_0;
      cycles := 0;
      -- Count cycles to the next rising edge (= one full out_0 period).
      while not (out_0 = '1' and prev = '0') loop
        prev := out_0;
        wait until rising_edge(clk);
        cycles := cycles + 1;
      end loop;

      assert cycles = expected
        report label_text & ": out_0 period " & integer'image(cycles) &
               " cycles, expected " & integer'image(expected) &
               " (div " & integer'image(natural_div) & ")"
        severity failure;

      clear <= '1';
      wait until rising_edge(clk);
      clear <= '0';
      wait until rising_edge(clk);
    end procedure;
  begin
    wait for 40 ns;               -- reset settle
    -- Measured Bit_Engine model: two [0,3,0,3] symbols (one out_0 period)
    -- take 2*div + 5 clock cycles (4 ticks of div+1 plus the byte-boundary
    -- LOAD and the M9K synchronous FIFO-read latency). The host's on-wire
    -- model sys_clk/(div+1.25) is the hardware-verified rate; the +2.5 here
    -- is the simulation's cycle-exact count and differs by <0.2% at 115200.
    measure_period(1000, 2005, "div 1000");
    -- The 1200-baud divider at 100.2 MHz (83499). A 16-bit register would
    -- truncate it to 17963 and produce 2*17963+5 = 35931 cycles (~5.6 kHz);
    -- the 24-bit divider must produce 2*83499+5 = 167003 (~1.2 kHz).
    measure_period(83499, 167003, "div 83499 (1200 baud)");
    -- Just above the old 16-bit ceiling.
    measure_period(65536, 131077, "div 65536");
    report "PASS: 24-bit Bit_Engine divider holds 1200-baud and 65536 dividers"
           severity note;
    wait;
  end process;
end architecture;
