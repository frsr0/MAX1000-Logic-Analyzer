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
  signal tx_done : std_logic;

  -- The current spi_packet_tx entity has no tx_valid output: tx_byte is
  -- presented for one or more cycles per byte (header/CRC: one cycle per
  -- byte; payload: held across the payload_ready/payload_valid handshake).
  -- The monitor samples tx_byte on every rising edge while the framer runs
  -- and the checker removes consecutive duplicates (a held byte) before
  -- comparing against the expected stream.
  type byte_mem_t is array(0 to 8191) of std_logic_vector(7 downto 0);
  signal sampled : byte_mem_t := (others => (others => '0'));
  signal samp_count : natural := 0;
  signal running : std_logic := '0';
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
      tx_done => tx_done
    );

  -- Payload pump: present one payload byte per payload_ready pulse.
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
    end if;
  end process;

  -- Sample every tx_byte while the framer is running (build high until
  -- tx_done). The framer presents SYNC0 one cycle after build, so sampling
  -- starts one cycle later.
  process(clk)
    variable armed : std_logic := '0';
  begin
    if rising_edge(clk) then
      if build = '1' then
        running <= '1';
        samp_count <= 0;
        armed := '0';
      elsif running = '1' then
        if armed = '1' then
          if samp_count < sampled'length then
            sampled(samp_count) <= tx_byte;
            samp_count <= samp_count + 1;
          end if;
        else
          armed := '1';
        end if;
        if tx_done = '1' then
          running <= '0';
        end if;
      end if;
    end if;
  end process;

  process
    -- Build the expected byte stream and compare against the de-duplicated
    -- sampled stream.
    variable expected : byte_mem_t;
    variable n_expected : natural;
    variable e, s : natural;
    variable crc : std_logic_vector(15 downto 0);
    variable k : natural;

    procedure check_stream is
    begin
      k := 0;
      expected(k) := SYNC_RSP(7 downto 0);  k := k + 1;   -- 0xAA
      expected(k) := SYNC_RSP(15 downto 8); k := k + 1;   -- 0x55
      expected(k) := ST_OK;                 k := k + 1;
      expected(k) := x"5A";                 k := k + 1;
      expected(k) := std_logic_vector(to_unsigned(BLOCK_SIZE mod 256, 8));
      k := k + 1;
      expected(k) := std_logic_vector(to_unsigned(BLOCK_SIZE / 256, 8));
      k := k + 1;
      for i in 0 to BLOCK_SIZE - 1 loop
        expected(k) := std_logic_vector(to_unsigned(i mod 256, 8));
        k := k + 1;
      end loop;
      n_expected := k;

      crc := crc16(expected(2), x"FFFF");
      for i in 3 to n_expected - 1 loop
        crc := crc16(expected(i), crc);
      end loop;
      expected(n_expected) := crc(7 downto 0);
      expected(n_expected + 1) := crc(15 downto 8);
      n_expected := n_expected + 2;

      -- De-duplicate both streams by collapsing consecutive identical bytes.
      -- The framer holds each payload byte across the payload_ready/valid
      -- handshake (several cycles per byte) while header/CRC bytes advance
      -- one per cycle, so collapsing runs yields the presented byte stream.
      -- Any legitimate consecutive duplicate in expected (e.g. crc_l=crc_h)
      -- is collapsed identically on both sides.
      s := 0;
      e := 0;
      while e < n_expected loop
        if s >= samp_count then
          check(false, "TX stream ended early at expected byte " &
                integer'image(e) & " (" & to_hstring(expected(e)) & ")");
          return;
        end if;
        if sampled(s) = expected(e) then
          -- consume the full run on both sides
          while s + 1 < samp_count and sampled(s + 1) = sampled(s) loop
            s := s + 1;
          end loop;
          while e + 1 < n_expected and expected(e + 1) = expected(e) loop
            e := e + 1;
          end loop;
          s := s + 1;
          e := e + 1;
        else
          -- leading/trailing non-matching samples (idle FF before start):
          -- skip them only before the stream begins
          if e = 0 then
            s := s + 1;
          else
            check(false, "TX byte mismatch at " & integer'image(e) & ": got " &
                  to_hstring(sampled(s)) & " expected " & to_hstring(expected(e)));
            return;
          end if;
        end if;
      end loop;
    end procedure;

  begin
    wait_cycles(clk, 5);
    build <= '1';
    wait_cycles(clk, 1);
    build <= '0';

    wait until tx_done = '1' for 50 us;
    check(tx_done = '1', "packet TX should complete 1024-byte response");
    wait_cycles(clk, 2);

    check_stream;

    for i in 0 to 15 loop report "SAMP[" & integer'image(i) & "]=" & to_hstring(sampled(i)); end loop;
    report "=== SPI PACKET TX 1024-BYTE PAYLOAD TEST PASSED ===";
    std.env.finish;
    wait;
  end process;
end bench;
