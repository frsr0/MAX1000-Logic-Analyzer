library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity tb_data_accum is
  generic (TEST : string := "tc_gen_baud");
end tb_data_accum;

architecture sim of tb_data_accum is
  constant CLK_PERIOD : time := 41.667 ns;  -- 24 MHz
  constant FCLK_PERIOD : time := 8.333 ns;  -- 120 MHz

  signal clk       : std_logic := '0';
  signal fast_clk  : std_logic := '0';
  signal running   : boolean := true;

  -- SPI interface (OLS_Interface in SPI mode)
  signal spi_sck   : std_logic := '0';
  signal spi_mosi  : std_logic := '0';
  signal spi_miso  : std_logic;
  signal spi_cs    : std_logic := '1';
  signal int_mode  : std_logic;

  -- Probed outputs from OLS_Interface
  signal gen_baud  : std_logic_vector(15 downto 0) := (others => '0');
  signal divider   : natural := 0;
  signal read_cnt  : natural := 0;
  signal gen_proto : std_logic := '0';
  signal status_byte : std_logic_vector(7 downto 0) := (others => '0');
  signal rcount_mod : std_logic_vector(7 downto 0) := (others => '0');
  signal rcount_div : std_logic_vector(7 downto 0) := (others => '0');

  type byte_array is array(natural range <>) of std_logic_vector(7 downto 0);

  -- Full-duplex SPI: send tx bytes, collect rx bytes
  procedure spi_xfer(
    constant tx     : in    byte_array;
    variable rx     : out   byte_array;
    signal sck_sig  : inout std_logic;
    signal mosi_sig : inout std_logic;
    signal miso_sig : in    std_logic;
    signal cs_sig   : inout std_logic;
    constant SCK_PERIOD : in time := 33 ns  -- ~30 MHz
  ) is
    constant HALF : time := SCK_PERIOD / 2;
    variable b : std_logic_vector(7 downto 0);
  begin
    cs_sig <= '0';
    wait for 500 ns;  -- long CS setup for SPI slave detection
    for i in tx'range loop
      for j in 7 downto 0 loop
        mosi_sig <= tx(i)(j);
        sck_sig <= '1'; wait for HALF;
        b(j) := miso_sig;
        sck_sig <= '0'; wait for HALF;
      end loop;
      rx(i) := b;
    end loop;
  end procedure;

begin
  clk <= not clk after CLK_PERIOD / 2 when running;
  fast_clk <= not fast_clk after FCLK_PERIOD / 2 when running;

  DUT : entity work.OLS_Interface(behavioral)
    generic map (
      Baud_Rate    => 921600,
      CLK_Frequency => 24000000,
      Max_Samples  => 1048576,
      OS_Rate      => 13,
      Def_IFace    => 1
    )
    port map (
      CLK => clk,
      FAST_CLK => fast_clk,
      UART_RX => spi_sck,
      UART_TX => open,
      SPI_CS  => spi_cs,
      SPI_MOSI => spi_mosi,
      SPI_MISO => spi_miso,
      Interface_Mode => int_mode,
      Inputs => (others => '0'),
      Rate_Div => open,
      Samples => open,
      Start_Offset => open,
      Run => open,
      Full => '0',
      Address => open,
      Outputs => (others => '0'),
      Gen_Load_Byte => open,
      Gen_Load_We => open,
      Gen_Start => open,
      Gen_Baud_Div => gen_baud,
      Gen_Busy => '0',
      Gen_Proto => gen_proto,
      Gen_TX_Pin => open,
      Buffer_Full => "000",
      Buffer_Ack => open
    );

  process
    variable rx : byte_array(0 to 4) := (others => (others => '0'));
  begin
    wait for 5 us;

    -- ============================================================
    -- tc_gen_baud: CMD_GEN_BAUD (0xA2) with data 0x000000D0 = 208
    -- Expected: Gen_Baud_Div = x"00D0"
    -- ============================================================
    if TEST = "all" or TEST = "tc_gen_baud" then
      report "--- tc_gen_baud: CMD_GEN_BAUD(208) ---" severity note;

      -- Send 5 bytes [0xA2, 0xD0, 0x00, 0x00, 0x00]
      spi_xfer((0 => x"A2", 1 => x"D0", 2 => x"00", 3 => x"00", 4 => x"00"),
               rx, spi_sck, spi_mosi, spi_miso, spi_cs);
      spi_cs <= '1'; wait for 5 us;

      report "Gen_Baud_Div = 0x" & to_hstring(gen_baud) severity note;

      if gen_baud = x"00D0" then
        report "tc_gen_baud: Gen_Baud_Div correctly set to 208 PASS" severity note;
      else
        report "tc_gen_baud: Expected 0x00D0 (208), got 0x" & to_hstring(gen_baud) & " FAIL" severity failure;
      end if;
    end if;

    -- ============================================================
    -- tc_gen_baud_big: CMD_GEN_BAUD with a large value
    -- Expected: Gen_Baud_Div = x"1234" (4660)
    -- ============================================================
    if TEST = "all" or TEST = "tc_gen_baud_big" then
      report "--- tc_gen_baud_big: CMD_GEN_BAUD(4660) ---" severity note;

      spi_xfer((0 => x"A2", 1 => x"34", 2 => x"12", 3 => x"00", 4 => x"00"),
               rx, spi_sck, spi_mosi, spi_miso, spi_cs);
      spi_cs <= '1'; wait for 5 us;

      report "Gen_Baud_Div = 0x" & to_hstring(gen_baud) severity note;

      if gen_baud = x"1234" then
        report "tc_gen_baud_big: Gen_Baud_Div correctly set to 0x1234 PASS" severity note;
      else
        report "tc_gen_baud_big: Expected 0x1234, got 0x" & to_hstring(gen_baud) & " FAIL" severity failure;
      end if;
    end if;

    -- ============================================================
    -- tc_double: Two consecutive multi-byte commands
    -- First sets Gen_Baud_Div, second should overwrite.
    -- ============================================================
    if TEST = "all" or TEST = "tc_double" then
      report "--- tc_double: Two consecutive multi-byte commands ---" severity note;

      -- First: set baud to 0x00D0
      spi_xfer((0 => x"A2", 1 => x"D0", 2 => x"00", 3 => x"00", 4 => x"00"),
               rx, spi_sck, spi_mosi, spi_miso, spi_cs);
      spi_cs <= '1'; wait for 3 us;

      -- Second: set baud to 0x0042
      spi_xfer((0 => x"A2", 1 => x"42", 2 => x"00", 3 => x"00", 4 => x"00"),
               rx, spi_sck, spi_mosi, spi_miso, spi_cs);
      spi_cs <= '1'; wait for 3 us;

      report "After second cmd: Gen_Baud_Div = 0x" & to_hstring(gen_baud) severity note;

      if gen_baud = x"0042" then
        report "tc_double: Second command correctly overwrote to 0x0042 PASS" severity note;
      else
        report "tc_double: Expected 0x0042, got 0x" & to_hstring(gen_baud) & " FAIL" severity failure;
      end if;
    end if;

    -- ============================================================
    -- tc_gen_proto: CMD_GEN_PROTO (0xA4) with data(0)=1
    -- Expected: Gen_Proto = '1'
    -- ============================================================
    if TEST = "all" or TEST = "tc_gen_proto" then
      report "--- tc_gen_proto: CMD_GEN_PROTO(1) ---" severity note;

      wait for 1 us;

      spi_xfer((0 => x"A4", 1 => x"01", 2 => x"00", 3 => x"00", 4 => x"00"),
               rx, spi_sck, spi_mosi, spi_miso, spi_cs);
      spi_cs <= '1'; wait for 5 us;

      report "Gen_Proto = " & std_logic'image(gen_proto) severity note;

      if gen_proto = '1' then
        report "tc_gen_proto: Gen_Proto set to 1 PASS" severity note;
      else
        report "tc_gen_proto: Expected 1, got " & std_logic'image(gen_proto) & " FAIL" severity failure;
      end if;
    end if;

    if TEST = "all" then
      report "ALL TESTS: PASS" severity note;
    end if;

    running <= false;
    wait;
  end process;

end sim;
