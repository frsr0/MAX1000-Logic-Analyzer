library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;
use work.spi_protocol_pkg.all;

entity spi_command_dispatch is
  port (
    clk       : in std_logic;
    rst       : in std_logic := '0';

    -- Decoded packet from RX
    rx_cmd        : in  std_logic_vector(7 downto 0);
    rx_seq        : in  std_logic_vector(7 downto 0);
    rx_payload    : in  std_logic_vector(MAX_PAYLOAD_BYTES * 8 - 1 downto 0);
    rx_payload_len : in natural range 0 to MAX_PAYLOAD_BYTES;
    rx_packet_ok  : in  std_logic;  -- pulse

    -- Response to TX builder
    tx_build      : out std_logic;
    tx_status     : out std_logic_vector(7 downto 0);
    tx_payload    : out std_logic_vector(MAX_PAYLOAD_BYTES * 8 - 1 downto 0);
    tx_len        : out natural range 0 to MAX_PAYLOAD_BYTES;
    tx_seq        : out std_logic_vector(7 downto 0);

    -- TX builder done
    tx_done       : in std_logic;

    -- Hardware hooks (simplified interface to existing logic)
    -- Capture
    arm_capture   : out std_logic;
    abort_capture : out std_logic;
    read_block    : out std_logic;
    block_addr    : out natural range 0 to 1048575;
    block_data    : in  std_logic_vector(MAX_PAYLOAD_BYTES * 8 - 1 downto 0);

    -- Stream
    start_stream  : out std_logic;
    read_stream   : out std_logic;
    stream_data   : in  std_logic_vector(MAX_PAYLOAD_BYTES * 8 - 1 downto 0);

    -- Registers
    reg_write     : out std_logic;
    reg_addr      : out std_logic_vector(7 downto 0);
    reg_wdata     : out std_logic_vector(31 downto 0);
    reg_rdata     : in  std_logic_vector(31 downto 0) := (others => '0');

    -- Generator
    gen_config    : out std_logic;
    gen_start     : out std_logic;
    gen_stop      : out std_logic;

    -- Status
    capture_status : in std_logic_vector(7 downto 0) := ST_CAPTURE_IDLE;
    gen_busy       : in std_logic := '0';
    protocol_ver   : in std_logic_vector(7 downto 0) := x"01"
  );
end spi_command_dispatch;

architecture rtl of spi_command_dispatch is
  type state_t is (IDLE, WAIT_BLOCK, WAIT_STREAM, WAIT_REG, BUILD_RSP, WAIT_TX);
  signal state : state_t := IDLE;
  signal status_reg : std_logic_vector(7 downto 0) := ST_OK;
  signal resp_payload : std_logic_vector(MAX_PAYLOAD_BYTES * 8 - 1 downto 0) := (others => '0');
  signal resp_len : natural range 0 to MAX_PAYLOAD_BYTES := 0;
  signal seq_reg  : std_logic_vector(7 downto 0) := (others => '0');
  signal pkt_pending : std_logic := '0';
  signal pkt_cmd : std_logic_vector(7 downto 0) := (others => '0');
  signal pkt_seq : std_logic_vector(7 downto 0) := (others => '0');
begin

  tx_seq <= seq_reg;

  process(clk)
    variable cmd_byte : integer range 0 to 255;
  begin
    if rising_edge(clk) then
      if rst = '1' then
        state <= IDLE;
        tx_build <= '0';
        arm_capture <= '0';
        abort_capture <= '0';
        read_block <= '0';
        start_stream <= '0';
        read_stream <= '0';
        reg_write <= '0';
        gen_config <= '0';
        gen_start <= '0';
        gen_stop <= '0';
        pkt_pending <= '0';
      else
        tx_build <= '0';
        arm_capture <= '0';
        abort_capture <= '0';
        read_block <= '0';
        start_stream <= '0';
        read_stream <= '0';
        reg_write <= '0';
        gen_config <= '0';
        gen_start <= '0';
        gen_stop <= '0';

        -- Latch incoming packet while waiting for TX to finish
        if rx_packet_ok = '1' and state /= IDLE then
          pkt_pending <= '1';
          pkt_cmd <= rx_cmd;
          pkt_seq <= rx_seq;
        end if;

        cmd_byte := to_integer(unsigned(rx_cmd));

        case state is
          when IDLE =>
            if rx_packet_ok = '1' or pkt_pending = '1' then
              if pkt_pending = '1' then
                seq_reg <= pkt_seq;
                cmd_byte := to_integer(unsigned(pkt_cmd));
                pkt_pending <= '0';
              else
                seq_reg <= rx_seq;
              end if;
              status_reg <= ST_OK;
              resp_payload <= (others => '0');
              resp_len <= 0;

              case cmd_byte is
                when 16#01# =>  -- CMD_PING
                  resp_payload(7 downto 0) <= protocol_ver;
                  resp_payload(15 downto 8) <= x"01";
                  resp_payload(23 downto 16) <= x"00";
                  resp_len <= 3;
                  state <= BUILD_RSP;

                when 16#02# =>  -- CMD_GET_STATUS
                  resp_payload(7 downto 0) <= capture_status;
                  resp_payload(15 downto 8) <= (others => '0');
                  resp_payload(16) <= gen_busy;
                  resp_len <= 3;
                  state <= BUILD_RSP;

                when 16#03# =>  -- CMD_GET_METADATA
                  resp_payload(7 downto 0)  <= x"10";
                  resp_payload(15 downto 8) <= x"10";
                  resp_payload(31 downto 16) <= x"00F0";
                  resp_payload(39 downto 32) <= x"01";
                  resp_len <= 5;
                  state <= BUILD_RSP;

                when 16#10# =>  -- CMD_ARM_CAPTURE
                  arm_capture <= '1';
                  status_reg <= ST_CAPTURE_ARMED;
                  state <= BUILD_RSP;

                when 16#11# =>  -- CMD_ABORT_CAPTURE
                  abort_capture <= '1';
                  status_reg <= ST_CAPTURE_IDLE;
                  state <= BUILD_RSP;

                when 16#12# =>  -- CMD_READ_CAPTURE
                  read_block <= '1';
                  block_addr <= to_integer(unsigned(rx_payload(31 downto 0)));
                  state <= WAIT_BLOCK;

                when 16#13# =>  -- CMD_START_STREAM
                  start_stream <= '1';
                  status_reg <= ST_STREAM_ACTIVE;
                  state <= BUILD_RSP;

                when 16#14# =>  -- CMD_READ_STREAM_BLOCK
                  read_stream <= '1';
                  state <= WAIT_STREAM;

                when 16#20# =>  -- CMD_WRITE_REG
                  if rx_payload_len >= 5 then
                    reg_addr  <= rx_payload(7 downto 0);
                    reg_wdata <= rx_payload(39 downto 8);
                    reg_write <= '1';
                  else
                    status_reg <= ST_BAD_LEN;
                  end if;
                  state <= BUILD_RSP;

                when 16#21# =>  -- CMD_READ_REG
                  if rx_payload_len >= 1 then
                    reg_addr <= rx_payload(7 downto 0);
                    state <= WAIT_REG;
                  else
                    status_reg <= ST_BAD_LEN;
                    state <= BUILD_RSP;
                  end if;

                when 16#30# =>  -- CMD_GEN_CONFIG
                  gen_config <= '1';
                  state <= BUILD_RSP;

                when 16#31# =>  -- CMD_GEN_START
                  gen_start <= '1';
                  state <= BUILD_RSP;

                when 16#32# =>  -- CMD_GEN_STOP
                  gen_stop <= '1';
                  state <= BUILD_RSP;

                when others =>
                  status_reg <= ST_BAD_CMD;
                  state <= BUILD_RSP;
              end case;
            end if;

          when WAIT_BLOCK =>
            resp_payload <= block_data;
            resp_len <= BLOCK_SIZE;
            state <= BUILD_RSP;

          when WAIT_STREAM =>
            resp_payload <= stream_data;
            resp_len <= BLOCK_SIZE;
            state <= BUILD_RSP;

          when WAIT_REG =>
            resp_payload(31 downto 0) <= reg_rdata;
            resp_len <= 4;
            state <= BUILD_RSP;

          when BUILD_RSP =>
            tx_status <= status_reg;
            tx_payload <= resp_payload;
            tx_len <= resp_len;
            tx_build <= '1';
            state <= WAIT_TX;

          when WAIT_TX =>
            if tx_done = '1' then
              state <= IDLE;
            end if;
        end case;
      end if;
    end if;
  end process;

end rtl;
