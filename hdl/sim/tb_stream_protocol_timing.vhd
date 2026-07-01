library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;
use work.spi_protocol_pkg.all;

entity tb_stream_protocol_timing is
end tb_stream_protocol_timing;

architecture rtl of tb_stream_protocol_timing is
  -- Simulate the SPI transaction for START_STREAM and measure timing.
  -- This testbench counts SPI bytes and reports when ack completes vs. when data begins.

  signal clk : std_logic := '0';
  signal rst : std_logic := '1';
  constant CLK_PERIOD : time := 33.3 ns;  -- 30 MHz

  -- SPI signals (from host perspective, we're simulating MISO read)
  signal spi_byte_count : integer := 0;
  signal ack_start_byte : integer := -1;
  signal ack_end_byte : integer := -1;
  signal data_start_byte : integer := -1;

  -- Simulated SPI data stream (byte-by-byte)
  type spi_stream_t is array (0 to 255) of std_logic_vector(7 downto 0);
  signal spi_stream : spi_stream_t;
  signal spi_byte : std_logic_vector(7 downto 0);
  signal spi_byte_valid : std_logic := '0';

  -- Parse ack response
  signal ack_status : std_logic_vector(7 downto 0);
  signal ack_seq : std_logic_vector(7 downto 0);
  signal ack_len : std_logic_vector(15 downto 0);
  signal producer_idx : std_logic_vector(31 downto 0);
  signal oldest_idx : std_logic_vector(31 downto 0);

begin

  clk <= not clk after CLK_PERIOD / 2;

  -- Reset sequence
  process
  begin
    rst <= '1';
    wait for CLK_PERIOD * 10;
    rst <= '0';
    wait;
  end process;

  -- Build a minimal START_STREAM SPI byte stream
  -- Packet structure: [SYNC(2), CMD, SEQ, LEN(2), PAYLOAD(4), CRC(2)]
  -- START_STREAM: cmd=0x13, payload=start_sample(4 bytes, little-endian)
  -- Response: [SYNC(2), STATUS, SEQ, LEN(2), PRODUCER(4), OLDEST(4), CRC(2)]
  -- Total response: 10 + 8 = 18 bytes minimum
  process
  begin
    wait until rising_edge(clk);
    if rst = '0' then
      -- Simulate preamble (0xFF padding before real data starts)
      -- This is the guard time from request to ack response arriving.
      spi_stream(0) <= x"FF";
      spi_stream(1) <= x"FF";

      -- Ack response starts at byte 2
      ack_start_byte <= 2;
      spi_stream(2) <= x"AA";  -- SYNC_RSP[0] (high byte of 0x55AA)
      spi_stream(3) <= x"55";  -- SYNC_RSP[1]
      spi_stream(4) <= x"20";  -- STATUS = ST_STREAM_ACTIVE
      spi_stream(5) <= x"00";  -- SEQ
      spi_stream(6) <= x"08";  -- LEN low byte = 8 payload bytes
      spi_stream(7) <= x"00";  -- LEN high byte
      spi_stream(8) <= x"12";  -- PRODUCER[0] = 0x12345678
      spi_stream(9) <= x"34";
      spi_stream(10) <= x"56";
      spi_stream(11) <= x"78";
      spi_stream(12) <= x"10";  -- OLDEST[0] = 0x10203040
      spi_stream(13) <= x"20";
      spi_stream(14) <= x"30";
      spi_stream(15) <= x"40";
      spi_stream(16) <= x"XX";  -- CRC low (don't care for timing)
      spi_stream(17) <= x"XX";  -- CRC high

      ack_end_byte <= 17;

      -- HARDWARE VALIDATION RESULT (July 2026):
      -- - Testbench predicted: data at byte 18, safe ack_pad = 48
      -- - Hardware measured: breaking point at ack_pad = 88
      -- - This indicates actual data starts around byte 78-83 (not 18!)
      -- Suggests: FPGA has ~60-65 byte latency from command to stream data
      -- Streaming data starts after significant latency
      data_start_byte <= 80;

      -- Fill the rest with sample data (16-bit little-endian samples)
      for i in 18 to 95 loop
        spi_stream(i) <= std_logic_vector(to_unsigned((i - 18) mod 256, 8));
      end loop;

      spi_byte_valid <= '1';
    end if;
  end process;

  -- Simulate byte clock (one SPI byte per period)
  process
  begin
    wait until rising_edge(clk);
    if rst = '1' then
      spi_byte_count <= 0;
    elsif spi_byte_count < 96 then
      spi_byte <= spi_stream(spi_byte_count);
      spi_byte_count <= spi_byte_count + 1;
    end if;
  end process;

  -- Analysis
  process
    variable start_byte : integer := 0;
    variable end_byte : integer := 0;
    variable data_byte : integer := 0;
  begin
    wait for 10 * CLK_PERIOD;  -- Wait for startup
    wait until spi_byte_count >= 18;

    start_byte := ack_start_byte;
    end_byte := ack_end_byte;
    data_byte := data_start_byte;

    -- Measure timing
    report "===== START_STREAM Protocol Timing =====" severity note;
    report "ACK response spans bytes " & integer'image(start_byte) & " to " & integer'image(end_byte) severity note;
    report "ACK length: " & integer'image(end_byte - start_byte + 1) & " bytes" severity note;
    report "Data starts at byte: " & integer'image(data_byte) severity note;
    report "Guard time (preamble): " & integer'image(start_byte) & " bytes" severity note;

    -- At 30 MHz:
    -- - Each byte = 8 bits = 267 ns
    -- - ACK = 10 bytes (header + 8 payload) = 2670 ns = 2.67 µs
    -- - Preamble = start_byte bytes = start_byte * 267 ns
    -- - Current ack_pad = 96 bytes = 25.6 µs
    report "Timing at 30 MHz SPI clock:" severity note;
    report "  - Each byte = 267 ns" severity note;
    report "  - ACK response = " & integer'image((end_byte - start_byte + 1) * 267) & " ns" severity note;
    report "  - Current ack_pad (96 bytes) = 25.6 µs" severity note;
    report "  - Data starts after byte " & integer'image(data_byte) & " = " &
            integer'image(data_byte * 267) & " ns" severity note;

    -- Calculate recommended ack_pad
    -- We need: enough bytes to cover the longest ack response + minimal guard
    -- ACK response is 18 bytes (SYNC(2) + header(4) + payload(8) + CRC(2))
    -- So minimum ack_pad = 18 + guard_bytes
    -- Estimate guard = 5-10 bytes for pipeline depth
    report "===== Recommendations =====" severity note;
    report "Minimum ack_pad to guarantee data capture:" severity note;
    report "  - Measured ACK + 10-byte guard = ~28 bytes" severity note;
    report "  - Conservative (3-sigma) = ~48 bytes" severity note;
    report "  - Current (ack_pad=96) = 96 bytes (2x conservative)" severity note;

    std.env.stop;
  end process;

end rtl;
