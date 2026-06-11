library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;

package spi_protocol_pkg is

  -- ── Packet framing ──────────────────────────────────────────────
  constant SYNC_REQ : std_logic_vector(15 downto 0) := x"AA55";  -- host→FPGA: wire order = 0x55, 0xAA
  constant SYNC_RSP : std_logic_vector(15 downto 0) := x"55AA";  -- FPGA→host: wire order = 0xAA, 0x55 (MSB first)

  -- RX: max payload we accept from host (GEN_LOAD needs up to 256).
  -- Keep small to avoid wasting registers on a wide parallel bus.
  constant MAX_RX_PAYLOAD_BYTES : natural := 256;
  -- TX: max payload we send back (block reads = 1024 bytes).
  -- This is only used for the byte counter in the streaming TX — no wide bus.
  constant MAX_TX_PAYLOAD_BYTES : natural := 1024;

  constant HEADER_BYTES      : natural := 6;  -- SYNC(2) + CMD(1) + SEQ(1) + LEN(2)
  constant FOOTER_BYTES      : natural := 2;  -- CRC16
  constant PACKET_OVERHEAD   : natural := HEADER_BYTES + FOOTER_BYTES;  -- 8

  -- ── Commands ────────────────────────────────────────────────────
  subtype cmd_t is std_logic_vector(7 downto 0);
  constant CMD_PING             : cmd_t := x"01";
  constant CMD_GET_STATUS       : cmd_t := x"02";
  constant CMD_GET_METADATA     : cmd_t := x"03";
  constant CMD_ARM_CAPTURE      : cmd_t := x"10";
  constant CMD_ABORT_CAPTURE    : cmd_t := x"11";
  constant CMD_READ_CAPTURE     : cmd_t := x"12";
  constant CMD_START_STREAM     : cmd_t := x"13";
  constant CMD_READ_STREAM_BLOCK : cmd_t := x"14";
  constant CMD_WRITE_REG        : cmd_t := x"20";
  constant CMD_READ_REG         : cmd_t := x"21";
  constant CMD_GEN_CONFIG       : cmd_t := x"30";
  constant CMD_GEN_START        : cmd_t := x"31";
  constant CMD_GEN_STOP         : cmd_t := x"32";
  constant CMD_GEN_LOAD         : cmd_t := x"33";
  constant CMD_GEN_CAPTURE      : cmd_t := x"34";
  constant CMD_GEN_STATUS       : cmd_t := x"35";

  -- ── Status codes ────────────────────────────────────────────────
  subtype status_t is std_logic_vector(7 downto 0);
  constant ST_OK             : status_t := x"00";
  constant ST_BAD_CRC        : status_t := x"01";
  constant ST_BAD_CMD        : status_t := x"02";
  constant ST_BAD_LEN        : status_t := x"03";
  constant ST_OVERSIZE       : status_t := x"04";
  constant ST_BUSY           : status_t := x"05";
  constant ST_CAPTURE_ARMED  : status_t := x"10";
  constant ST_CAPTURE_BUSY   : status_t := x"11";
  constant ST_CAPTURE_DONE   : status_t := x"12";
  constant ST_CAPTURE_IDLE   : status_t := x"13";
  constant ST_STREAM_ACTIVE  : status_t := x"20";
  constant ST_GEN_BUSY       : status_t := x"30";

  -- ── Fixed block sizes ───────────────────────────────────────────
  constant BLOCK_SIZE : natural := 1024;  -- bytes per capture read block

  -- ── Registers (for CMD_WRITE_REG / CMD_READ_REG) ────────────────
  subtype reg_addr_t is std_logic_vector(7 downto 0);
  constant REG_DIVIDER      : reg_addr_t := x"00";
  constant REG_SAMPLE_COUNT : reg_addr_t := x"01";
  constant REG_DELAY_COUNT  : reg_addr_t := x"02";
  constant REG_TRIGGER_MASK : reg_addr_t := x"10";
  constant REG_TRIGGER_VALUE : reg_addr_t := x"11";
  constant REG_FLAGS        : reg_addr_t := x"20";
  constant REG_FAST_MODE    : reg_addr_t := x"21";
  constant REG_CONT_MODE    : reg_addr_t := x"22";
  constant REG_GEN_PROTO    : reg_addr_t := x"30";
  constant REG_GEN_BAUD     : reg_addr_t := x"31";
  constant REG_GEN_PINS     : reg_addr_t := x"32";
  constant REG_GEN_DATA     : reg_addr_t := x"33";
  constant REG_IFACE_MODE   : reg_addr_t := x"F0";
  constant REG_DEBUG_CH0_ENABLE : reg_addr_t := x"40";
  constant REG_DEBUG_CH0_PERIOD : reg_addr_t := x"43";
  constant REG_DEBUG_CH0_DUTY   : reg_addr_t := x"44";
  constant REG_SCHMITT_ENABLE    : reg_addr_t := x"41";
  constant REG_SCHMITT_THRESHOLD : reg_addr_t := x"42";

  -- ── Helper: CRC-16-IBM ──────────────────────────────────────────
  function crc16(data : std_logic_vector; init : std_logic_vector(15 downto 0) := x"FFFF")
    return std_logic_vector;

  function crc16_int(data : integer; init : integer := 65535) return integer;

end spi_protocol_pkg;

package body spi_protocol_pkg is

  function crc16(data : std_logic_vector; init : std_logic_vector(15 downto 0) := x"FFFF")
    return std_logic_vector is
    variable crc : integer := to_integer(unsigned(init));
    variable by : integer;
  begin
    for i in 0 to data'length / 8 - 1 loop
      by := 0;
      for j in 0 to 7 loop
        if data'low + i * 8 + j <= data'high then
          if data(data'low + i * 8 + j) = '1' then
            by := by + 2 ** j;
          end if;
        end if;
      end loop;
      crc := crc16_int(by, crc);
    end loop;
    return std_logic_vector(to_unsigned(crc, 16));
  end crc16;

  -- Integer CRC helper (same algorithm, byte at a time)
  function crc16_int(data : integer; init : integer := 65535) return integer is
    variable crc : std_logic_vector(15 downto 0) := std_logic_vector(to_unsigned(init, 16));
    variable bv  : std_logic_vector(7 downto 0)  := std_logic_vector(to_unsigned(data, 8));
  begin
    crc := crc xor (x"00" & bv);
    for j in 0 to 7 loop
      if crc(0) = '1' then
        crc := '0' & crc(15 downto 1);
        crc := crc xor x"A001";
      else
        crc := '0' & crc(15 downto 1);
      end if;
    end loop;
    return to_integer(unsigned(crc));
  end crc16_int;

end spi_protocol_pkg;
