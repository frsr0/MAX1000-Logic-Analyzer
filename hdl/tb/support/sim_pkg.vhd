library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;

package sim_pkg is

  -- Clock generation: toggles a std_logic signal at half-period
  procedure gen_clk(signal clk : inout std_logic; constant half_period : in time);

  -- SPI master procedures (CPOL=0, CPHA=0: SCK idle low, sample on rising edge)
  type byte_array is array (natural range <>) of std_logic_vector(7 downto 0);

  -- Full-duplex SPI transfer: while CS asserted, shift MOSI bytes out and capture MISO bytes
  -- Pads with idle pattern (0xFF) if tx bytes run out before rx bytes
  procedure spi_xfer(
    signal    cs_n   : out   std_logic;
    signal    sck    : out   std_logic;
    signal    mosi   : out   std_logic;
    signal    miso   : in    std_logic;
    constant  half_period : in    time;
    constant  tx_data     : in    byte_array;
    variable  rx_data     : out   byte_array
  );

  -- Convenience: single byte SPI transfer
  procedure spi_byte(
    signal    cs_n   : out   std_logic;
    signal    sck    : out   std_logic;
    signal    mosi   : out   std_logic;
    signal    miso   : in    std_logic;
    constant  half_period : in    time;
    constant  tx     : in    std_logic_vector(7 downto 0);
    variable  rx     : out   std_logic_vector(7 downto 0)
  );

  -- Convenience: 5-byte SPI command (opcode + 4 data bytes)
  procedure spi_cmd5(
    signal    cs_n   : out   std_logic;
    signal    sck    : out   std_logic;
    signal    mosi   : out   std_logic;
    signal    miso   : in    std_logic;
    constant  half_period : in    time;
    constant  opcode : in    std_logic_vector(7 downto 0);
    constant  data   : in    std_logic_vector(31 downto 0);
    variable  reply  : out   byte_array(0 to 4)
  );

  -- UART send byte (8N1)
  procedure uart_send_byte(
    signal    tx     : out   std_logic;
    constant  bit_time : in    time;
    constant  data   : in    std_logic_vector(7 downto 0)
  );

  -- UART send 32-bit word (LSB first, 4 bytes 8N1)
  procedure uart_send_word(
    signal    tx     : out   std_logic;
    constant  bit_time : in    time;
    constant  data   : in    std_logic_vector(31 downto 0)
  );

  -- Wait until signal matches value, with timeout
  procedure wait_until(
    signal    clk     : in    std_logic;
    signal    sig     : in    std_logic;
    constant  value   : in    std_logic;
    constant  timeout : in    time;
    constant  msg     : in    string
  );

  -- Wait until rising edge of signal, with timeout
  procedure wait_rising(
    signal    clk     : in    std_logic;
    signal    sig     : in    std_logic;
    constant  timeout : in    time;
    constant  msg     : in    string
  );

  -- Assert check with severity failure
  procedure check(
    constant  cond : in    boolean;
    constant  msg  : in    string
  );

  -- Measure high/low pulse width of a signal in clock cycles
  procedure measure_pulse(
    signal    clk        : in    std_logic;
    signal    sig        : in    std_logic;
    constant  polarity   : in    std_logic;  -- '1' = measure high, '0' = measure low
    constant  max_cycles : in    natural;
    variable  cycles     : out   natural;
    variable  found      : out   boolean
  );

  -- Wait for N rising edges of clk
  procedure wait_cycles(signal clk : in std_logic; constant n : in natural);

end sim_pkg;

package body sim_pkg is

  procedure gen_clk(signal clk : inout std_logic; constant half_period : in time) is
  begin
    loop
      clk <= '0';
      wait for half_period;
      clk <= '1';
      wait for half_period;
    end loop;
  end procedure;

  procedure spi_xfer(
    signal    cs_n   : out   std_logic;
    signal    sck    : out   std_logic;
    signal    mosi   : out   std_logic;
    signal    miso   : in    std_logic;
    constant  half_period : in    time;
    constant  tx_data     : in    byte_array;
    variable  rx_data     : out   byte_array
  ) is
    variable rx_len : natural;
    variable tx_len : natural;
    variable tx_byte : std_logic_vector(7 downto 0);
  begin
    rx_len := rx_data'length;
    tx_len := tx_data'length;

    cs_n <= '0';
    wait for half_period;

    for i in 0 to rx_len - 1 loop
      if i < tx_len then
        tx_byte := tx_data(i);
      else
        tx_byte := x"FF";  -- idle pattern
      end if;

      for b in 7 downto 0 loop
        sck <= '0';
        mosi <= tx_byte(b);
        wait for half_period;
        sck <= '1';
        rx_data(i)(b) := miso;
        wait for half_period;
      end loop;
    end loop;

    sck <= '0';
    cs_n <= '1';
    wait for half_period;
  end procedure;

  procedure spi_byte(
    signal    cs_n   : out   std_logic;
    signal    sck    : out   std_logic;
    signal    mosi   : out   std_logic;
    signal    miso   : in    std_logic;
    constant  half_period : in    time;
    constant  tx     : in    std_logic_vector(7 downto 0);
    variable  rx     : out   std_logic_vector(7 downto 0)
  ) is
    variable tx_arr : byte_array(0 to 0) := (0 => tx);
    variable rx_arr : byte_array(0 to 0);
  begin
    spi_xfer(cs_n, sck, mosi, miso, half_period, tx_arr, rx_arr);
    rx := rx_arr(0);
  end procedure;

  procedure spi_cmd5(
    signal    cs_n   : out   std_logic;
    signal    sck    : out   std_logic;
    signal    mosi   : out   std_logic;
    signal    miso   : in    std_logic;
    constant  half_period : in    time;
    constant  opcode : in    std_logic_vector(7 downto 0);
    constant  data   : in    std_logic_vector(31 downto 0);
    variable  reply  : out   byte_array(0 to 4)
  ) is
    variable tx_arr : byte_array(0 to 4);
  begin
    tx_arr(0) := opcode;
    -- Little-endian byte order to match struct.pack('<I', val) in host library
    tx_arr(1) := data(7 downto 0);
    tx_arr(2) := data(15 downto 8);
    tx_arr(3) := data(23 downto 16);
    tx_arr(4) := data(31 downto 24);
    spi_xfer(cs_n, sck, mosi, miso, half_period, tx_arr, reply);
  end procedure;

  procedure uart_send_byte(
    signal    tx     : out   std_logic;
    constant  bit_time : in    time;
    constant  data   : in    std_logic_vector(7 downto 0)
  ) is
  begin
    tx <= '0';  wait for bit_time;  -- start bit
    for i in 0 to 7 loop
      tx <= data(i);  wait for bit_time;
    end loop;
    tx <= '1';  wait for bit_time;  -- stop bit
  end procedure;

  procedure uart_send_word(
    signal    tx     : out   std_logic;
    constant  bit_time : in    time;
    constant  data   : in    std_logic_vector(31 downto 0)
  ) is
  begin
    uart_send_byte(tx, bit_time, data(7 downto 0));
    uart_send_byte(tx, bit_time, data(15 downto 8));
    uart_send_byte(tx, bit_time, data(23 downto 16));
    uart_send_byte(tx, bit_time, data(31 downto 24));
  end procedure;

  procedure wait_until(
    signal    clk     : in    std_logic;
    signal    sig     : in    std_logic;
    constant  value   : in    std_logic;
    constant  timeout : in    time;
    constant  msg     : in    string
  ) is
  begin
    if sig /= value then
      wait until sig = value for timeout;
      check(sig = value, msg & " (timeout)");
    end if;
  end procedure;

  procedure wait_rising(
    signal    clk     : in    std_logic;
    signal    sig     : in    std_logic;
    constant  timeout : in    time;
    constant  msg     : in    string
  ) is
    variable t_start : time;
  begin
    t_start := now;
    wait until rising_edge(sig) for timeout;
    check(now /= t_start, msg & " (timeout waiting for rising edge)");
  end procedure;

  procedure check(
    constant  cond : in    boolean;
    constant  msg  : in    string
  ) is
  begin
    assert cond report msg severity failure;
  end procedure;

  procedure measure_pulse(
    signal    clk        : in    std_logic;
    signal    sig        : in    std_logic;
    constant  polarity   : in    std_logic;
    constant  max_cycles : in    natural;
    variable  cycles     : out   natural;
    variable  found      : out   boolean
  ) is
    variable t_before : time;
  begin
    cycles := 0;
    found := false;
    t_before := now;

    if polarity = '1' then
      wait until rising_edge(sig) for 1 ms;
    else
      wait until falling_edge(sig) for 1 ms;
    end if;

    if now = t_before then
      found := false;
      return;
    end if;

    found := true;

    if polarity = '1' then
      while sig = '1' and cycles < max_cycles loop
        wait until rising_edge(clk);
        cycles := cycles + 1;
      end loop;
    else
      while sig = '0' and cycles < max_cycles loop
        wait until rising_edge(clk);
        cycles := cycles + 1;
      end loop;
    end if;
  end procedure;

  procedure wait_cycles(signal clk : in std_logic; constant n : in natural) is
  begin
    for i in 0 to n - 1 loop
      wait until rising_edge(clk);
    end loop;
  end procedure;

end sim_pkg;
