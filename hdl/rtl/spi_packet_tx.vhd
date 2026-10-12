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
    tx_done     : out std_logic   -- strobe when entire response sent
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
  signal len_lo          : std_logic_vector(7 downto 0) := (others => '0');
  signal len_hi          : std_logic_vector(7 downto 0) := (others => '0');
  signal crc_in          : std_logic_vector(7 downto 0) := (others => '0');
  signal crc_nxt         : std_logic_vector(15 downto 0) := (others => '1');
begin

  -- Length bytes are emitted as payload and also folded into the CRC; derive
  -- them once instead of re-evaluating the division at each use.
  len_lo <= std_logic_vector(to_unsigned(rsp_len mod 256, 8));
  len_hi <= std_logic_vector(to_unsigned(rsp_len / 256, 8));

  -- One shared CRC-16 cone.  The FSM folds exactly one byte per CRC state, so
  -- selecting the input byte by state is equivalent to the five separate
  -- crc16() calls it replaces -- one cone plus a byte mux instead of five
  -- unshared cones, which is a material area saving at 96% device fill.
  with state select crc_in <=
    rsp_status   when SEND_STATUS,
    req_seq      when SEND_SEQ,
    len_lo       when SEND_LEN_L,
    len_hi       when SEND_LEN_H,
    payload_hold when SEND_PAYLOAD,
    (others => '0') when others;

  crc_nxt <= crc16(crc_in, crc_acc);

  process(clk)
  begin
    if rising_edge(clk) then
      if rst = '1' then
        state <= IDLE;
        tx_byte <= x"FF";
        tx_done <= '0';
        busy <= '0';
        payload_ready <= '0';
        payload_pending <= '0';
      else
        tx_done <= '0';
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
              state <= SEND_SYNC1;
            end if;

          when SEND_SYNC1 =>
            if tx_ready = '1' then
              tx_byte <= SYNC_RSP(15 downto 8);
              state <= SEND_STATUS;
              crc_acc <= (others => '1');
            end if;

          when SEND_STATUS =>
            if tx_ready = '1' then
              tx_byte <= rsp_status;
              crc_acc <= crc_nxt;
              state <= SEND_SEQ;
            end if;

          when SEND_SEQ =>
            if tx_ready = '1' then
              tx_byte <= req_seq;
              crc_acc <= crc_nxt;
              state <= SEND_LEN_L;
            end if;

          when SEND_LEN_L =>
            if tx_ready = '1' then
              tx_byte <= len_lo;
              crc_acc <= crc_nxt;
              state <= SEND_LEN_H;
            end if;

          when SEND_LEN_H =>
            if tx_ready = '1' then
              tx_byte <= len_hi;
              crc_acc <= crc_nxt;
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
              crc_acc <= crc_nxt;
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
              state <= SEND_CRC_H;
            end if;

          when SEND_CRC_H =>
            if tx_ready = '1' then
              tx_byte <= crc_acc(15 downto 8);
              state <= SEND_DONE;
            end if;

          when SEND_DONE =>
            -- SPI_Slave2 reloads TX_Data on the *next* byte boundary after the
            -- producer presents a byte. Hold the final CRC byte until one more
            -- tx_ready pulse confirms that boundary has passed; otherwise the
            -- upper layer can hand off to RAW_STREAM one byte too early and the
            -- slave reuses CRC_L/CRC_H as the first stream byte(s).
            if tx_ready = '1' then
              tx_done <= '1';
              busy <= '0';
              state <= IDLE;
            end if;
        end case;
      end if;
    end if;
  end process;

end rtl;
