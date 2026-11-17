library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;
use work.sim_pkg.all;

-- SPI slave MISO re-framing test.
-- The source-synchronous MISO bit counter (sck_tx_cnt) must return to zero
-- whenever CS_n is high.  A transaction truncated mid-byte must not leave the
-- counter misaligned for later (byte-aligned) transactions.
--   (a) normal 3-byte transaction: bytes 1..2 must equal the TX_Data pattern
--   (b) transaction truncated after 13 SCK clocks, then CS_n rises
--   (c) normal 4-byte transaction: bytes 1..3 must be correctly framed
-- Byte 0 of a transaction is whatever the shifter held from the previous
-- transaction (existing, hardware-validated behaviour) and is not checked.
entity tb_spi_slave_realign is
end tb_spi_slave_realign;

architecture bench of tb_spi_slave_realign is
  constant CLK_PERIOD : time := 10 ns;
  constant HALF       : time := 100 ns;  -- 5 MHz SCK

  signal sys_clk  : std_logic := '0';
  signal fast_clk : std_logic := '0';
  signal reset    : std_logic := '0';
  signal sck      : std_logic := '0';
  signal mosi     : std_logic := '0';
  signal miso     : std_logic;
  signal cs_n     : std_logic := '1';
  signal tx_data  : std_logic_vector(7 downto 0) := (others => '0');
  signal tx_ready : std_logic;
  signal rx_data  : std_logic_vector(7 downto 0);
  signal rx_valid : std_logic;
  signal cs_rise  : std_logic;

  -- Distinct, non-shift-related pattern per byte index.
  function pat(j : natural) return std_logic_vector is
  begin
    return std_logic_vector(to_unsigned((16#96# + j * 16#2B#) mod 256, 8));
  end function;
begin

  gen_clk(sys_clk, CLK_PERIOD / 2);
  gen_clk(fast_clk, CLK_PERIOD / 2);

  DUT : entity work.SPI_Slave2
    port map (
      sys_clk      => sys_clk,
      fast_clk     => fast_clk,
      reset        => reset,
      SCK          => sck,
      MOSI         => mosi,
      MISO         => miso,
      CS_n         => cs_n,
      TX_Data      => tx_data,
      SPI_Preamble => x"00",
      TX_Ready     => tx_ready,
      RX_Data      => rx_data,
      RX_Valid     => rx_valid,
      CS_Rise      => cs_rise
    );

  process
    type byte_arr is array (0 to 7) of std_logic_vector(7 downto 0);
    variable got : byte_arr;

    -- Mode-0 master: nclk clocks, MISO sampled on each rising edge.  tx_data
    -- is advanced one byte ahead of the byte being clocked so that the
    -- byte-boundary load on the 8th falling edge picks up pat(j+1).
    procedure txn(nclk : natural) is
      variable byte_i : natural;
      variable bit_i  : natural;
    begin
      tx_data <= pat(1);
      cs_n <= '0';
      wait for 500 ns;
      for k in 0 to nclk - 1 loop
        byte_i := k / 8;
        bit_i  := 7 - (k mod 8);
        if bit_i = 7 then
          tx_data <= pat(byte_i + 1);
        end if;
        wait for HALF;
        sck <= '1';
        got(byte_i)(bit_i) := miso;
        wait for HALF;
        sck <= '0';
      end loop;
      wait for 300 ns;
      cs_n <= '1';
      wait for 1 us;
    end procedure;
  begin
    reset <= '1';
    wait_cycles(sys_clk, 10);
    reset <= '0';
    wait_cycles(sys_clk, 20);

    report "=== (a) byte-aligned 3-byte transaction ===";
    txn(24);
    for j in 1 to 2 loop
      check(got(j) = pat(j), "(a) byte " & integer'image(j) & ": expected "
            & to_hstring(pat(j)) & " got " & to_hstring(got(j)));
    end loop;

    report "=== (b) truncated transaction: 13 clocks ===";
    txn(13);

    report "=== (c) byte-aligned 4-byte transaction after truncation ===";
    txn(32);
    for j in 1 to 3 loop
      check(got(j) = pat(j), "(c) byte " & integer'image(j) & ": expected "
            & to_hstring(pat(j)) & " got " & to_hstring(got(j)));
    end loop;

    report "=== (d) a second aligned transaction stays framed ===";
    txn(24);
    for j in 1 to 2 loop
      check(got(j) = pat(j), "(d) byte " & integer'image(j) & ": expected "
            & to_hstring(pat(j)) & " got " & to_hstring(got(j)));
    end loop;

    report "=== ALL SPI REALIGN TESTS PASSED ===";
    std.env.finish;
    wait;
  end process;

end bench;
