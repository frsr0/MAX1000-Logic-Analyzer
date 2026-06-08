library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;
use work.spi_protocol_pkg.all;

entity spi_packet_rx is
  port (
    clk         : in  std_logic;
    rst         : in  std_logic := '0';

    -- Byte-level SPI input
    rx_byte     : in  std_logic_vector(7 downto 0);
    rx_valid    : in  std_logic;
    cs_rise     : in  std_logic := '0';  -- abort: CS deassert mid-packet

    -- Decoded header (valid when packet_ok strobes)
    cmd_active  : out std_logic_vector(7 downto 0);
    seq         : out std_logic_vector(7 downto 0);
    payload_len : out natural range 0 to MAX_RX_PAYLOAD_BYTES;

    -- Streaming payload output (each byte as it arrives from SPI)
    payload_byte   : out std_logic_vector(7 downto 0);
    payload_valid  : out std_logic;  -- strobes once per payload byte
    payload_last   : out std_logic;  -- strobes on the last payload byte

    -- Packet completion flags
    packet_ok      : out std_logic;  -- pulse on valid complete packet
    packet_err     : out std_logic;  -- pulse on CRC mismatch / bad frame

    -- Error diagnostics
    err_bad_crc  : out std_logic;
    err_bad_sync : out std_logic;
    err_oversize : out std_logic
  );
end spi_packet_rx;

architecture rtl of spi_packet_rx is
  type state_t is (WAIT_SYNC0, WAIT_SYNC1, GET_CMD, GET_SEQ,
                   GET_LEN_L, GET_LEN_H, GET_PAYLOAD, GET_CRC_L, GET_CRC_H);
  signal state : state_t := WAIT_SYNC0;
  signal cmd_reg  : std_logic_vector(7 downto 0) := (others => '0');
  signal seq_reg  : std_logic_vector(7 downto 0) := (others => '0');
  signal len_vec  : std_logic_vector(15 downto 0) := (others => '0');
  signal cnt      : natural range 0 to MAX_RX_PAYLOAD_BYTES := 0;
  signal plen     : natural range 0 to MAX_RX_PAYLOAD_BYTES := 0;
  signal crc_rx   : std_logic_vector(15 downto 0) := (others => '0');
  signal packet_ok_int  : std_logic := '0';
  signal packet_err_int : std_logic := '0';
  signal last_int : std_logic := '0';
begin

  process(clk)
    variable crc_int  : integer range 0 to 65535 := 65535;
    variable sync_low : std_logic_vector(7 downto 0) := (others => '0');
  begin
    if rising_edge(clk) then
      if rst = '1' then
        state <= WAIT_SYNC0;
        packet_ok_int <= '0'; packet_err_int <= '0';
        err_bad_crc <= '0'; err_bad_sync <= '0'; err_oversize <= '0';
        len_vec <= (others => '0');
        cnt <= 0; plen <= 0;
        crc_int := 65535;
        last_int <= '0';
      else
        payload_valid <= '0';
        payload_last <= '0';
        packet_ok_int <= '0';
        packet_err_int <= '0';
        err_bad_crc <= '0'; err_bad_sync <= '0'; err_oversize <= '0';
        last_int <= '0';

        if rx_valid = '1' then
          case state is
            when WAIT_SYNC0 =>
              sync_low := rx_byte;
              state <= WAIT_SYNC1;

            when WAIT_SYNC1 =>
              if rx_byte & sync_low = SYNC_REQ then
                state <= GET_CMD;
                crc_int := 65535;
              else
                state <= WAIT_SYNC0;
                err_bad_sync <= '1';
              end if;

            when GET_CMD =>
              cmd_reg <= rx_byte;
              cmd_active <= rx_byte;
              state <= GET_SEQ;
              crc_int := crc16_int(to_integer(unsigned(rx_byte)), crc_int);

            when GET_SEQ =>
              seq_reg <= rx_byte;
              state <= GET_LEN_L;
              crc_int := crc16_int(to_integer(unsigned(rx_byte)), crc_int);

            when GET_LEN_L =>
              len_vec(7 downto 0) <= rx_byte;
              state <= GET_LEN_H;
              crc_int := crc16_int(to_integer(unsigned(rx_byte)), crc_int);

            when GET_LEN_H =>
              len_vec(15 downto 8) <= rx_byte;
              cnt <= 0;
              plen <= to_integer(unsigned(rx_byte & len_vec(7 downto 0)));
              crc_int := crc16_int(to_integer(unsigned(rx_byte)), crc_int);
              if to_integer(unsigned(rx_byte & len_vec(7 downto 0))) > MAX_RX_PAYLOAD_BYTES then
                err_oversize <= '1';
                packet_err_int <= '1';
                state <= WAIT_SYNC0;
              elsif to_integer(unsigned(rx_byte & len_vec(7 downto 0))) = 0 then
                state <= GET_CRC_L;
              else
                state <= GET_PAYLOAD;
              end if;

            when GET_PAYLOAD =>
              payload_byte <= rx_byte;
              payload_valid <= '1';
              cnt <= cnt + 1;
              crc_int := crc16_int(to_integer(unsigned(rx_byte)), crc_int);
              if cnt + 1 >= plen then
                last_int <= '1';
                payload_last <= '1';
                state <= GET_CRC_L;
              end if;

            when GET_CRC_L =>
              crc_rx(7 downto 0) <= rx_byte;
              state <= GET_CRC_H;

            when GET_CRC_H =>
              crc_rx(15 downto 8) <= rx_byte;
              crc_int := crc16_int(to_integer(unsigned(crc_rx(7 downto 0))), crc_int);
              crc_int := crc16_int(to_integer(unsigned(rx_byte)), crc_int);
              if crc_int = 0 then
                cmd_active <= cmd_reg;
                seq <= seq_reg;
                payload_len <= plen;
                packet_ok_int <= '1';
              else
                err_bad_crc <= '1';
                packet_err_int <= '1';
              end if;
              state <= WAIT_SYNC0;

            when others => null;
          end case;
        end if;
      end if;
    end if;
  end process;

  packet_ok  <= packet_ok_int;
  packet_err <= packet_err_int;

end rtl;
