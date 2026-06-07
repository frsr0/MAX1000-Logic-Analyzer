library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;
use work.spi_protocol_pkg.all;

entity tb_spi_protocol is
end tb_spi_protocol;

architecture sim of tb_spi_protocol is
  type byte_array is array(natural range <>) of std_logic_vector(7 downto 0);

  constant CLK_PERIOD : time := 6.67 ns;  -- 150 MHz

  signal clk : std_logic := '0';
  signal rst : std_logic := '1';

  -- RX side
  signal rx_byte        : std_logic_vector(7 downto 0) := (others => '0');
  signal rx_valid       : std_logic := '0';
  signal rx_cmd         : std_logic_vector(7 downto 0);
  signal rx_seq         : std_logic_vector(7 downto 0);
  signal rx_payload_len : natural range 0 to MAX_RX_PAYLOAD_BYTES;
  signal rx_payload_byte : std_logic_vector(7 downto 0);
  signal rx_payload_valid : std_logic;
  signal rx_payload_last : std_logic;
  signal rx_ok          : std_logic;
  signal rx_err         : std_logic;

  signal rx_obs         : byte_array(0 to MAX_RX_PAYLOAD_BYTES - 1) := (others => (others => '0'));
  signal rx_obs_len     : natural range 0 to MAX_RX_PAYLOAD_BYTES := 0;
  signal rx_obs_last    : std_logic := '0';
  signal rx_ok_seen     : std_logic := '0';
  signal rx_err_seen    : std_logic := '0';

  -- TX side
  signal tx_req_seq     : std_logic_vector(7 downto 0) := (others => '0');
  signal tx_build       : std_logic := '0';
  signal tx_status      : std_logic_vector(7 downto 0) := (others => '0');
  signal tx_rsp_len     : natural range 0 to MAX_TX_PAYLOAD_BYTES := 0;
  signal tx_payload_in  : std_logic_vector(7 downto 0) := (others => '0');
  signal tx_payload_vld : std_logic := '0';
  signal tx_payload_ready : std_logic;
  signal tx_byte        : std_logic_vector(7 downto 0);
  signal tx_valid       : std_logic;
  signal tx_done        : std_logic;
  signal tx_obs         : byte_array(0 to 31) := (others => (others => '0'));
  signal tx_obs_len     : natural range 0 to 32 := 0;

  procedure send_byte(
    signal b : out std_logic_vector(7 downto 0);
    signal v : out std_logic;
    val : std_logic_vector(7 downto 0)
  ) is
  begin
    b <= val;
    v <= '1';
    wait until rising_edge(clk);
    wait for 0 ns;
    v <= '0';
    wait until rising_edge(clk);
    wait for 0 ns;
  end procedure;

  procedure expect_pulse(signal s : in std_logic; what : string) is
  begin
    wait until s = '1' for CLK_PERIOD * 20;
    assert s = '1' report what severity failure;
    wait for 0 ns;
  end procedure;

  procedure clear_capture is
  begin
    wait until rising_edge(clk);
    wait for 0 ns;
  end procedure;

begin
  clk <= not clk after CLK_PERIOD / 2;

  rx_inst : entity work.spi_packet_rx
    port map (
      clk => clk,
      rst => rst,
      rx_byte => rx_byte,
      rx_valid => rx_valid,
      cs_rise => '0',
      cmd_active => rx_cmd,
      seq => rx_seq,
      payload_len => rx_payload_len,
      payload_byte => rx_payload_byte,
      payload_valid => rx_payload_valid,
      payload_last => rx_payload_last,
      packet_ok => rx_ok,
      packet_err => rx_err,
      err_bad_crc => open,
      err_bad_sync => open,
      err_oversize => open
    );

  tx_inst : entity work.spi_packet_tx
    port map (
      clk => clk,
      rst => rst,
      req_seq => tx_req_seq,
      build => tx_build,
      rsp_status => tx_status,
      rsp_len => tx_rsp_len,
      payload_byte_in => tx_payload_in,
      payload_valid_in => tx_payload_vld,
      payload_ready => tx_payload_ready,
      tx_ready => '1',
      tx_byte => tx_byte,
      tx_valid => tx_valid,
      tx_done => tx_done,
      idle_byte => open
    );

  rx_monitor : process(clk)
    variable idx : natural;
  begin
    if rising_edge(clk) then
      if rst = '1' then
        rx_obs_len <= 0;
        rx_obs_last <= '0';
        rx_ok_seen <= '0';
        rx_err_seen <= '0';
      else
        if rx_payload_valid = '1' then
          idx := rx_obs_len;
          if idx < MAX_RX_PAYLOAD_BYTES then
            rx_obs(idx) <= rx_payload_byte;
            rx_obs_len <= idx + 1;
          end if;
          rx_obs_last <= rx_payload_last;
        end if;
        if rx_ok = '1' or rx_err = '1' then
          rx_obs_len <= rx_obs_len;
        end if;
        if rx_ok = '1' then
          rx_ok_seen <= '1';
        end if;
        if rx_err = '1' then
          rx_err_seen <= '1';
        end if;
      end if;
    end if;
  end process;

  tx_monitor : process(clk)
    variable idx : natural;
  begin
    if rising_edge(clk) then
      if rst = '1' then
        tx_obs_len <= 0;
      else
        if tx_valid = '1' then
          idx := tx_obs_len;
          if idx < tx_obs'length then
            tx_obs(idx) <= tx_byte;
            tx_obs_len <= idx + 1;
          end if;
        end if;
      end if;
    end if;
  end process;

  process
    variable crc_i   : integer;
    variable crc_slv  : std_logic_vector(15 downto 0);
  begin
    rst <= '1';
    wait for CLK_PERIOD * 3;
    rst <= '0';
    wait for CLK_PERIOD;

    -- Test 1: zero-length PING
    crc_i := 65535;
    crc_i := crc16_int(16#01#, crc_i);
    crc_i := crc16_int(16#42#, crc_i);
    crc_i := crc16_int(0, crc_i);
    crc_i := crc16_int(0, crc_i);
    crc_slv := std_logic_vector(to_unsigned(crc_i, 16));

    send_byte(rx_byte, rx_valid, x"55");
    send_byte(rx_byte, rx_valid, x"AA");
    send_byte(rx_byte, rx_valid, x"01");
    send_byte(rx_byte, rx_valid, x"42");
    send_byte(rx_byte, rx_valid, x"00");
    send_byte(rx_byte, rx_valid, x"00");
    send_byte(rx_byte, rx_valid, crc_slv(7 downto 0));
    send_byte(rx_byte, rx_valid, crc_slv(15 downto 8));
    wait until rising_edge(clk);
    assert rx_ok_seen = '1' report "PING packet not accepted" severity failure;
    assert rx_cmd = x"01" severity failure;
    assert rx_seq = x"42" severity failure;
    assert rx_payload_len = 0 severity failure;

    wait until rising_edge(clk);
    rst <= '1';
    wait for CLK_PERIOD * 2;
    rst <= '0';
    wait for CLK_PERIOD;

    -- Test 2: packet payload streaming
    crc_i := 65535;
    crc_i := crc16_int(16#10#, crc_i);
    crc_i := crc16_int(16#46#, crc_i);
    crc_i := crc16_int(3, crc_i);
    crc_i := crc16_int(0, crc_i);
    crc_i := crc16_int(16#A1#, crc_i);
    crc_i := crc16_int(16#A2#, crc_i);
    crc_i := crc16_int(16#A3#, crc_i);
    crc_slv := std_logic_vector(to_unsigned(crc_i, 16));

    send_byte(rx_byte, rx_valid, x"55");
    send_byte(rx_byte, rx_valid, x"AA");
    send_byte(rx_byte, rx_valid, x"10");
    send_byte(rx_byte, rx_valid, x"46");
    send_byte(rx_byte, rx_valid, x"03");
    send_byte(rx_byte, rx_valid, x"00");
    send_byte(rx_byte, rx_valid, x"A1");
    send_byte(rx_byte, rx_valid, x"A2");
    send_byte(rx_byte, rx_valid, x"A3");
    send_byte(rx_byte, rx_valid, crc_slv(7 downto 0));
    send_byte(rx_byte, rx_valid, crc_slv(15 downto 8));
    wait until rising_edge(clk);
    assert rx_ok_seen = '1' report "Payload packet not accepted" severity failure;
    assert rx_cmd = x"10" severity failure;
    assert rx_seq = x"46" severity failure;
    assert rx_payload_len = 3 severity failure;
    assert rx_obs_len >= 3 severity failure;
    assert rx_obs(0) = x"A1" severity failure;
    assert rx_obs(1) = x"A2" severity failure;
    assert rx_obs(2) = x"A3" severity failure;
    assert rx_obs_last = '1' severity failure;

    wait until rising_edge(clk);
    rst <= '1';
    wait for CLK_PERIOD * 2;
    rst <= '0';
    wait for CLK_PERIOD;

    -- Test 3: bad CRC rejected
    send_byte(rx_byte, rx_valid, x"55");
    send_byte(rx_byte, rx_valid, x"AA");
    send_byte(rx_byte, rx_valid, x"01");
    send_byte(rx_byte, rx_valid, x"43");
    send_byte(rx_byte, rx_valid, x"00");
    send_byte(rx_byte, rx_valid, x"00");
    send_byte(rx_byte, rx_valid, x"00");
    send_byte(rx_byte, rx_valid, x"00");
    wait until rising_edge(clk);
    assert rx_err_seen = '1' report "Bad CRC not rejected" severity failure;

    wait until rising_edge(clk);
    rst <= '1';
    wait for CLK_PERIOD * 2;
    rst <= '0';
    wait for CLK_PERIOD;

    -- Test 4: back-to-back packets
    crc_i := 65535;
    crc_i := crc16_int(16#01#, crc_i);
    crc_i := crc16_int(16#50#, crc_i);
    crc_i := crc16_int(0, crc_i);
    crc_i := crc16_int(0, crc_i);
    crc_slv := std_logic_vector(to_unsigned(crc_i, 16));

    send_byte(rx_byte, rx_valid, x"55");
    send_byte(rx_byte, rx_valid, x"AA");
    send_byte(rx_byte, rx_valid, x"01");
    send_byte(rx_byte, rx_valid, x"50");
    send_byte(rx_byte, rx_valid, x"00");
    send_byte(rx_byte, rx_valid, x"00");
    send_byte(rx_byte, rx_valid, crc_slv(7 downto 0));
    send_byte(rx_byte, rx_valid, crc_slv(15 downto 8));

    crc_i := 65535;
    crc_i := crc16_int(16#02#, crc_i);
    crc_i := crc16_int(16#51#, crc_i);
    crc_i := crc16_int(0, crc_i);
    crc_i := crc16_int(0, crc_i);
    crc_slv := std_logic_vector(to_unsigned(crc_i, 16));

    send_byte(rx_byte, rx_valid, x"55");
    send_byte(rx_byte, rx_valid, x"AA");
    send_byte(rx_byte, rx_valid, x"02");
    send_byte(rx_byte, rx_valid, x"51");
    send_byte(rx_byte, rx_valid, x"00");
    send_byte(rx_byte, rx_valid, x"00");
    send_byte(rx_byte, rx_valid, crc_slv(7 downto 0));
    send_byte(rx_byte, rx_valid, crc_slv(15 downto 8));
    wait until rising_edge(clk);
    assert rx_ok_seen = '1' report "Back-to-back packet not accepted" severity failure;
    assert rx_cmd = x"02" severity failure;
    assert rx_seq = x"51" severity failure;

    wait until rising_edge(clk);
    rst <= '1';
    wait for CLK_PERIOD * 2;
    rst <= '0';
    wait for CLK_PERIOD;

    -- Test 5: TX builder, zero-length response
    tx_req_seq <= x"42";
    tx_status <= x"00";
    tx_rsp_len <= 0;
    tx_build <= '1';
    wait for CLK_PERIOD;
    tx_build <= '0';
    wait until tx_done = '1' for CLK_PERIOD * 40;
    assert tx_done = '1' report "TX zero-length response did not finish" severity failure;
    wait for 0 ns;
    assert tx_obs_len >= 8 severity failure;
    assert tx_obs(0) = x"AA" severity failure;
    assert tx_obs(1) = x"55" severity failure;
    assert tx_obs(2) = x"00" severity failure;
    assert tx_obs(3) = x"42" severity failure;

    wait until rising_edge(clk);
    rst <= '1';
    wait for CLK_PERIOD * 2;
    rst <= '0';
    wait for CLK_PERIOD;

    -- Test 6: TX builder with payload streaming
    tx_req_seq <= x"46";
    tx_status <= x"00";
    tx_rsp_len <= 3;
    tx_build <= '1';
    wait for CLK_PERIOD;
    tx_build <= '0';

    wait until tx_payload_ready = '1' for CLK_PERIOD * 40;
    assert tx_payload_ready = '1' report "TX never requested payload byte 0" severity failure;
    tx_payload_in <= x"01";
    tx_payload_vld <= '1';
    wait for CLK_PERIOD;
    tx_payload_vld <= '0';

    wait until tx_payload_ready = '1' for CLK_PERIOD * 40;
    assert tx_payload_ready = '1' report "TX never requested payload byte 1" severity failure;
    tx_payload_in <= x"02";
    tx_payload_vld <= '1';
    wait for CLK_PERIOD;
    tx_payload_vld <= '0';

    wait until tx_payload_ready = '1' for CLK_PERIOD * 40;
    assert tx_payload_ready = '1' report "TX never requested payload byte 2" severity failure;
    tx_payload_in <= x"03";
    tx_payload_vld <= '1';
    wait for CLK_PERIOD;
    tx_payload_vld <= '0';

    wait until tx_done = '1' for CLK_PERIOD * 40;
    assert tx_done = '1' report "TX payload response did not finish" severity failure;
    wait for 0 ns;
    assert tx_obs_len >= 11 severity failure;
    assert tx_obs(0) = x"AA" severity failure;
    assert tx_obs(1) = x"55" severity failure;
    assert tx_obs(2) = x"00" severity failure;
    assert tx_obs(3) = x"46" severity failure;
    assert tx_obs(4) = x"03" severity failure;
    assert tx_obs(5) = x"00" severity failure;
    assert tx_obs(6) = x"01" severity failure;
    assert tx_obs(7) = x"02" severity failure;
    assert tx_obs(8) = x"03" severity failure;

    report "SPI protocol TB complete";
    wait;
  end process;
end sim;
