library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;
use work.spi_protocol_pkg.all;
use work.sim_pkg.all;

entity tb_spi_packet_link is end;

architecture sim of tb_spi_packet_link is
  constant SYS_HALF  : time := 3.333 ns;
  constant FAST_HALF : time := 4.167 ns;
  constant SPI_HALF  : time := 500 ns;

  signal sys_clk, fast_clk : std_logic := '0';
  signal sck, mosi : std_logic := '0';
  signal miso : std_logic;
  signal cs_n : std_logic := '1';
  signal rx_data : std_logic_vector(7 downto 0);
  signal rx_valid, tx_ready, cs_rise : std_logic;
  signal cmd, seq : std_logic_vector(7 downto 0);
  signal plen : natural range 0 to MAX_RX_PAYLOAD_BYTES;
  signal pbyte : std_logic_vector(7 downto 0);
  signal pvalid, plast, ok, err : std_logic;
  signal ok_seen, err_seen : std_logic := '0';
begin
  gen_clk(sys_clk, SYS_HALF);
  gen_clk(fast_clk, FAST_HALF);

  slave : entity work.SPI_Slave2
    port map (
      sys_clk => sys_clk, fast_clk => fast_clk, reset => '0',
      SCK => sck, MOSI => mosi, MISO => miso, CS_n => cs_n,
      TX_Data => x"FF", SPI_Preamble => x"10", TX_Ready => tx_ready,
      RX_Data => rx_data, RX_Valid => rx_valid, CS_Rise => cs_rise
    );

  rx : entity work.spi_packet_rx
    port map (
      clk => sys_clk, rst => '0', rx_byte => rx_data, rx_valid => rx_valid,
      cs_rise => cs_rise, cmd_active => cmd, seq => seq, payload_len => plen,
      payload_byte => pbyte, payload_valid => pvalid, payload_last => plast,
      packet_ok => ok, packet_err => err,
      err_bad_crc => open, err_bad_sync => open, err_oversize => open
    );

  process(sys_clk)
  begin
    if rising_edge(sys_clk) then
      if ok = '1' then
        ok_seen <= '1';
      end if;
      if err = '1' then
        err_seen <= '1';
      end if;
    end if;
  end process;

  process
    variable tx : byte_array(0 to 7);
    variable rr : byte_array(0 to 7);
    variable crc_i : integer := 65535;
    variable crc_v : std_logic_vector(15 downto 0);
  begin
    cs_n <= '1';
    wait for 1 us;
    crc_i := 65535;
    crc_i := crc16_int(1, crc_i);
    crc_i := crc16_int(16#55#, crc_i);
    crc_i := crc16_int(0, crc_i);
    crc_i := crc16_int(0, crc_i);
    crc_v := std_logic_vector(to_unsigned(crc_i, 16));
    tx := (x"55", x"AA", x"01", x"55", x"00", x"00", crc_v(7 downto 0), crc_v(15 downto 8));
    spi_xfer(cs_n, sck, mosi, miso, SPI_HALF, tx, rr);
    wait for 5 us;
    assert ok_seen = '1' report "packet ok not seen" severity failure;
    assert err_seen = '0' report "packet error seen" severity failure;
    assert cmd = x"01" report "cmd mismatch" severity failure;
    assert seq = x"55" report "seq mismatch" severity failure;
    report "SPI packet link passed";
    wait;
  end process;
end sim;
