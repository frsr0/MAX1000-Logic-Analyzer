library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;
use work.sim_pkg.all;
use work.spi_protocol_pkg.all;
-- NOTE: The streaming opcode path (CMD_START_STREAM / STREAM_TX) bypasses
-- this packet framer entirely — raw bytes flow directly from the FLA
-- response FIFO to the SPI slave via the stream_tx_pump process. This
-- testbench covers only the framed-packet TX path (CMD_READ_CAPTURE etc.).

entity tb_spi_packet_tx is
end tb_spi_packet_tx;

architecture bench of tb_spi_packet_tx is
  signal clk : std_logic := '0';
  signal build : std_logic := '0';
  signal payload_byte : std_logic_vector(7 downto 0) := (others => '0');
  signal payload_valid : std_logic := '0';
  signal payload_ready : std_logic;
  signal tx_ready : std_logic := '1';
  signal tx_byte : std_logic_vector(7 downto 0);
  signal tx_valid : std_logic;
  signal tx_done : std_logic;
  signal idle_byte : std_logic;

  type byte_mem_t is array(0 to 1100) of std_logic_vector(7 downto 0);
  signal captured : byte_mem_t := (others => (others => '0'));
  signal cap_count : natural := 0;
begin
  gen_clk(clk, 5 ns);

  DUT : entity work.spi_packet_tx
    port map (
      clk => clk,
      rst => '0',
      req_seq => x"5A",
      build => build,
      rsp_status => ST_OK,
      rsp_len => BLOCK_SIZE,
      payload_byte_in => payload_byte,
      payload_valid_in => payload_valid,
      payload_ready => payload_ready,
      tx_ready => tx_ready,
      tx_byte => tx_byte,
      tx_valid => tx_valid,
      tx_done => tx_done,
      idle_byte => idle_byte
    );

  process(clk)
    variable next_payload : natural range 0 to BLOCK_SIZE := 0;
    variable wait_ready_low : boolean := false;
  begin
    if rising_edge(clk) then
      payload_valid <= '0';
      if wait_ready_low then
        if payload_ready = '0' then
          wait_ready_low := false;
        end if;
      elsif payload_ready = '1' and next_payload < BLOCK_SIZE then
        payload_byte <= std_logic_vector(to_unsigned(next_payload mod 256, 8));
        payload_valid <= '1';
        next_payload := next_payload + 1;
        wait_ready_low := true;
      end if;

      if tx_valid = '1' then
        captured(cap_count) <= tx_byte;
        cap_count <= cap_count + 1;
      end if;
    end if;
  end process;

  process
  begin
    wait_cycles(clk, 5);
    build <= '1';
    wait_cycles(clk, 1);
    build <= '0';

    wait until tx_done = '1' for 50 us;
    check(tx_done = '1', "packet TX should complete 1024-byte response");
    wait_cycles(clk, 2);

    check(cap_count = BLOCK_SIZE + PACKET_OVERHEAD,
          "unexpected TX byte count: " & integer'image(cap_count));
    check(captured(0) = x"AA", "sync0 mismatch");
    check(captured(1) = x"55", "sync1 mismatch");
    check(captured(2) = ST_OK, "status mismatch");
    check(captured(3) = x"5A", "seq mismatch");
    check(captured(4) = x"00", "len low mismatch for 1024");
    check(captured(5) = x"04", "len high mismatch for 1024");

    for i in 0 to BLOCK_SIZE - 1 loop
      check(captured(6 + i) = std_logic_vector(to_unsigned(i mod 256, 8)),
            "payload byte mismatch at " & integer'image(i));
    end loop;

    report "=== SPI PACKET TX 1024-BYTE PAYLOAD TEST PASSED ===";
    wait;
  end process;
end bench;
