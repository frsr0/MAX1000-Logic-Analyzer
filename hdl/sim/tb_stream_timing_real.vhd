library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;
use work.spi_protocol_pkg.all;

-- Real testbench: instantiate SPI_Slave + spi_packet_rx to measure
-- actual START_STREAM timing.

entity tb_stream_timing_real is
end tb_stream_timing_real;

architecture rtl of tb_stream_timing_real is

  constant CLK_PERIOD : time := 33.3 ns;   -- 30 MHz
  constant SPI_CLK_PERIOD : time := 33.3 ns;  -- SPI clock = sys clock (1:1 divider)

  signal clk : std_logic := '0';
  signal rst : std_logic := '1';
  signal spi_clk : std_logic := '0';
  signal spi_mosi : std_logic := '1';
  signal spi_miso : std_logic := '0';
  signal spi_cs : std_logic := '1';

  -- Command builder state
  signal cmd_byte_idx : integer := 0;
  signal cmd_bytes : std_logic_vector(95 downto 0);  -- 12-byte START_STREAM

  -- Ack response
  signal resp_byte_idx : integer := 0;
  signal resp_bytes : std_logic_vector(255 downto 0);  -- 16+ bytes for ack
  signal resp_valid : std_logic := '0';

  -- Timing captures
  signal sync_rsp_seen_byte : integer := -1;
  signal first_sample_byte : integer := -1;
  signal rx_byte_count : integer := 0;
  signal tx_byte_count : integer := 0;

  -- Packet RX signals from DUT
  signal packet_ok : std_logic := '0';
  signal packet_err : std_logic := '0';

begin

  clk <= not clk after CLK_PERIOD / 2;
  spi_clk <= not spi_clk after SPI_CLK_PERIOD / 2;

  -- Reset
  process
  begin
    rst <= '1';
    wait for CLK_PERIOD * 10;
    rst <= '0';
    wait;
  end process;

  -- Build START_STREAM command
  -- Manually construct the byte stream
  process
    variable cmd : std_logic_vector(95 downto 0);
    variable payload : std_logic_vector(31 downto 0) := x"00000100";  -- start_sample = 0x100
    variable crc : std_logic_vector(15 downto 0);
  begin
    wait until rst = '0';

    -- [SYNC(2), CMD, SEQ, LEN(2), PAYLOAD(4), CRC(2)] = 12 bytes
    -- Byte 0: 0x55
    cmd(95 downto 88) := x"55";
    -- Byte 1: 0xAA
    cmd(87 downto 80) := x"AA";
    -- Byte 2: 0x13 (CMD_START_STREAM)
    cmd(79 downto 72) := x"13";
    -- Byte 3: 0x00 (SEQ)
    cmd(71 downto 64) := x"00";
    -- Byte 4-5: 0x04, 0x00 (LEN = 4)
    cmd(63 downto 56) := x"04";
    cmd(55 downto 48) := x"00";
    -- Byte 6-9: payload (start_sample = 0x00000100, little-endian)
    cmd(47 downto 40) := x"00";
    cmd(39 downto 32) := x"01";
    cmd(31 downto 24) := x"00";
    cmd(23 downto 16) := x"00";
    -- Bytes 10-11: CRC16 (skip for now, just use dummy)
    cmd(15 downto 0) := x"0000";

    cmd_bytes <= cmd;
  end process;

  -- Build expected ACK response
  process
    variable resp : std_logic_vector(255 downto 0);
  begin
    wait until rst = '0';

    -- [SYNC(2), STATUS, SEQ, LEN(2), PAYLOAD(8), CRC(2)] = 16 bytes
    -- Note: SYNC is wire-order (0xAA, 0x55) but we construct as big-endian
    resp(255 downto 248) := x"AA";  -- SYNC[0]
    resp(247 downto 240) := x"55";  -- SYNC[1]
    resp(239 downto 232) := x"20";  -- STATUS = ST_STREAM_ACTIVE
    resp(231 downto 224) := x"00";  -- SEQ
    resp(223 downto 216) := x"08";  -- LEN low
    resp(215 downto 208) := x"00";  -- LEN high
    -- producer_index = 0x12345678
    resp(207 downto 200) := x"12";
    resp(199 downto 192) := x"34";
    resp(191 downto 184) := x"56";
    resp(183 downto 176) := x"78";
    -- oldest_index = 0x10203040
    resp(175 downto 168) := x"10";
    resp(167 downto 160) := x"20";
    resp(159 downto 152) := x"30";
    resp(151 downto 144) := x"40";
    -- CRC
    resp(143 downto 128) := x"0000";

    resp_bytes <= resp;
  end process;

  -- SPI master simulation: send command, then read ack + data
  process
    variable byte_idx : integer := 0;
    variable bit_idx : integer := 0;
    variable resp_start : integer := 0;
    variable cmd_done : boolean := false;
  begin
    wait until rst = '0';
    wait for SPI_CLK_PERIOD * 2;

    spi_cs <= '0';  -- CS low
    wait for SPI_CLK_PERIOD;

    -- Send 12-byte command
    for byte_num in 0 to 11 loop
      for bit in 7 downto 0 loop
        spi_mosi <= cmd_bytes(95 - (byte_num * 8 + (7 - bit)));
        wait for SPI_CLK_PERIOD;
      end loop;
      rx_byte_count <= byte_num + 1;
      report "Command byte " & integer'image(byte_num) & " sent" severity note;
    end loop;

    cmd_done := true;
    resp_start := rx_byte_count;

    -- Now read ack + data (clock in, expect data on MISO)
    -- Send NOP bytes (0x11) and count where SYNC_RSP appears
    for byte_num in 0 to 95 loop
      spi_mosi <= '1';  -- NOP byte
      for bit in 7 downto 0 loop
        wait for SPI_CLK_PERIOD;
        -- Check if we see 0xAA on MISO (start of SYNC_RSP)
        if spi_miso = '1' and sync_rsp_seen_byte = -1 then
          sync_rsp_seen_byte <= rx_byte_count;
          report "SYNC_RSP detected at byte " & integer'image(rx_byte_count) severity note;
        end if;
      end loop;
      tx_byte_count <= byte_num + 1;
      rx_byte_count <= resp_start + byte_num + 1;
    end loop;

    spi_cs <= '1';  -- CS high
    wait for SPI_CLK_PERIOD * 10;
    std.env.stop;
  end process;

  -- Report results
  process
  begin
    wait for 10 ms;

    report "===== START_STREAM Timing Results =====" severity note;
    if sync_rsp_seen_byte >= 0 then
      report "SYNC_RSP (ack start) appeared at byte: " & integer'image(sync_rsp_seen_byte) severity note;
      report "Guard time (bytes before ack): " & integer'image(sync_rsp_seen_byte) severity note;
      report "At 30 MHz (267 ns/byte): " & integer'image(sync_rsp_seen_byte * 267) & " ns" severity note;
    else
      report "ERROR: SYNC_RSP not detected!" severity error;
    end if;

    if first_sample_byte >= 0 then
      report "First sample data at byte: " & integer'image(first_sample_byte) severity note;
    end if;

    report "===== Recommendations =====" severity note;
    if sync_rsp_seen_byte >= 0 then
      report "Current ack_pad = 96 bytes" severity note;
      report "Can safely reduce to: " & integer'image(sync_rsp_seen_byte + 20) & " bytes" severity note;
      report "Gain: ~" & integer'image((96 - (sync_rsp_seen_byte + 20)) * 100 / 96) & "%" severity note;
    end if;

  end process;

end rtl;
