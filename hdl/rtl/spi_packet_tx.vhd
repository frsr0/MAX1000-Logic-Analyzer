library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;
use work.spi_protocol_pkg.all;

entity spi_packet_tx is
  port (
    clk         : in  std_logic;
    rst         : in  std_logic := '0';

    -- Control / header
    req_seq     : in  std_logic_vector(7 downto 0);
    build       : in  std_logic;  -- pulse to start building response
    rsp_status  : in  std_logic_vector(7 downto 0);
    rsp_len     : in  natural range 0 to MAX_TX_PAYLOAD_BYTES;

    -- Streaming payload input (push bytes after build, when payload_ready=1)
    payload_byte_in  : in  std_logic_vector(7 downto 0);
    payload_valid_in : in  std_logic;
    payload_ready    : out std_logic;  -- '1' when TX is ready for next payload byte

    -- Flow control / output
    tx_ready    : in  std_logic := '1';
    tx_byte     : out std_logic_vector(7 downto 0);
    tx_valid    : out std_logic;  -- strobe when tx_byte valid
    tx_done     : out std_logic;  -- strobe when entire response sent
    idle_byte   : out std_logic   -- '1' when outputting IDLE (0xFF)
  );
end spi_packet_tx;

architecture rtl of spi_packet_tx is
  type state_t is (IDLE, SEND_SYNC0, SEND_SYNC1, SEND_STATUS, SEND_SEQ,
                   SEND_LEN_L, SEND_LEN_H,
                   SEND_PAYLOAD, SEND_CRC_L, SEND_CRC_H, SEND_DONE);
  signal state : state_t := IDLE;
  signal crc_acc : std_logic_vector(15 downto 0) := (others => '1');
  signal cnt     : natural range 0 to MAX_TX_PAYLOAD_BYTES := 0;
  signal busy    : std_logic := '0';
  signal payload_pending : std_logic := '0';
  signal payload_hold    : std_logic_vector(7 downto 0) := (others => '0');
begin

  process(clk)
  begin
    if rising_edge(clk) then
      if rst = '1' then
        state <= IDLE;
        tx_byte <= x"FF";
        tx_valid <= '0';
        tx_done <= '0';
        idle_byte <= '0';
        busy <= '0';
        payload_ready <= '0';
        payload_pending <= '0';
      else
        tx_valid <= '0';
        tx_done <= '0';
        idle_byte <= '0';
        payload_ready <= '0';

        if build = '1' and (busy = '0' or state = SEND_DONE) then
          busy <= '1';
          cnt <= 0;
          crc_acc <= (others => '1');
          payload_pending <= '0';
          state <= SEND_SYNC0;
        end if;

        case state is
          when IDLE =>
            null;  -- Hold last byte; SPI slave consumes TX_Data one byte later.

          when SEND_SYNC0 =>
            if tx_ready = '1' then
              tx_byte <= SYNC_RSP(7 downto 0);
              tx_valid <= '1';
              state <= SEND_SYNC1;
            end if;

          when SEND_SYNC1 =>
            if tx_ready = '1' then
              tx_byte <= SYNC_RSP(15 downto 8);
              tx_valid <= '1';
              state <= SEND_STATUS;
              crc_acc <= (others => '1');
            end if;

          when SEND_STATUS =>
            if tx_ready = '1' then
              tx_byte <= rsp_status;
              tx_valid <= '1';
              crc_acc <= crc16(rsp_status, crc_acc);
              state <= SEND_SEQ;
            end if;

          when SEND_SEQ =>
            if tx_ready = '1' then
              tx_byte <= req_seq;
              tx_valid <= '1';
              crc_acc <= crc16(req_seq, crc_acc);
              state <= SEND_LEN_L;
            end if;

          when SEND_LEN_L =>
            if tx_ready = '1' then
              tx_byte <= std_logic_vector(to_unsigned(rsp_len mod 256, 8));
              tx_valid <= '1';
              crc_acc <= crc16(
                std_logic_vector(to_unsigned(rsp_len mod 256, 8)), crc_acc);
              state <= SEND_LEN_H;
            end if;

          when SEND_LEN_H =>
            if tx_ready = '1' then
              tx_byte <= std_logic_vector(to_unsigned(rsp_len / 256, 8));
              tx_valid <= '1';
              crc_acc <= crc16(
                std_logic_vector(to_unsigned(rsp_len / 256, 8)), crc_acc);
              if rsp_len = 0 then
                state <= SEND_CRC_L;
              else
                cnt <= 0;
                state <= SEND_PAYLOAD;
              end if;
            end if;

          when SEND_PAYLOAD =>
            if payload_pending = '0' then
              payload_ready <= '1';
              if payload_valid_in = '1' then
                payload_hold <= payload_byte_in;
                payload_pending <= '1';
              end if;
            elsif tx_ready = '1' then
              tx_byte <= payload_hold;
              tx_valid <= '1';
              crc_acc <= crc16(payload_hold, crc_acc);
              payload_pending <= '0';
              if cnt + 1 >= rsp_len then
                state <= SEND_CRC_L;
              else
                cnt <= cnt + 1;
              end if;
            end if;

          when SEND_CRC_L =>
            if tx_ready = '1' then
              tx_byte <= crc_acc(7 downto 0);
              tx_valid <= '1';
              state <= SEND_CRC_H;
            end if;

          when SEND_CRC_H =>
            if tx_ready = '1' then
              tx_byte <= crc_acc(15 downto 8);
              tx_valid <= '1';
              state <= SEND_DONE;
            end if;

          when SEND_DONE =>
            tx_done <= '1';
            busy <= '0';
            state <= IDLE;
        end case;
      end if;
    end if;
  end process;

end rtl;
