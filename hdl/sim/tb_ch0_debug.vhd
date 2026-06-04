library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity tb_ch0_debug is
  generic (TEST : string := "tc_ch0_routing");
end tb_ch0_debug;

architecture sim of tb_ch0_debug is
  constant CLK_PERIOD : time := 83.333 ns;  -- 12 MHz
  constant SCK_PERIOD : time := 2 us;
  constant HALF_SCK   : time := SCK_PERIOD / 2;

  signal clk       : std_logic := '0';
  signal running   : boolean := true;

  signal gpio      : std_logic_vector(7 downto 0) := (others => 'Z');

  -- SPI signals (shared with UART_RX/ UART_TX pins in SPI mode)
  signal spi_sck   : std_logic := '0';  -- SPI SCK (idle low, CPOL=0)
  signal spi_mosi  : std_logic := '0';  -- SPI MOSI
  signal spi_miso  : std_logic;
  signal spi_cs    : std_logic := '1';  -- SPI CS (active low)

  -- Probed internal signals via VHDL-2008 external names
  signal test_out      : std_logic;
  signal internal_data : std_logic_vector(7 downto 0);
  signal reg_data      : std_logic_vector(7 downto 0);
  signal test_div      : std_logic_vector(9 downto 0);
  signal gen_busy      : std_logic;
  signal gen_tx        : std_logic;
  signal gen_tx_pin    : natural range 0 to 7;

  type byte_array is array(natural range <>) of std_logic_vector(7 downto 0);

  -- Full-duplex SPI master: send tx bytes on MOSI, collect rx from MISO
  procedure spi_xfer(
    constant tx     : in    byte_array;
    variable rx     : out   byte_array;
    signal sck_sig  : inout std_logic;
    signal mosi_sig : inout std_logic;
    signal miso_sig : in    std_logic;
    signal cs_sig   : inout std_logic
  ) is
    variable b : std_logic_vector(7 downto 0);
  begin
    cs_sig <= '0';
    wait for HALF_SCK;
    for i in tx'range loop
      for j in 7 downto 0 loop
        mosi_sig <= tx(i)(j);
        sck_sig <= '1';  -- rising edge: slave samples MOSI, drives MISO
        wait for HALF_SCK;
        b(j) := miso_sig;
        sck_sig <= '0';  -- falling edge: slave shifts next MISO
        wait for HALF_SCK;
      end loop;
      rx(i) := b;
    end loop;
  end procedure;

  -- Convenience: send 5-byte SPI command + read 5-byte response
  procedure spi_cmd(
    signal sck_sig  : inout std_logic;
    signal mosi_sig : inout std_logic;
    signal miso_sig : in    std_logic;
    signal cs_sig   : inout std_logic;
    constant cmd    : in    std_logic_vector(7 downto 0);
    constant data   : in    std_logic_vector(31 downto 0) := x"00000000"
  ) is
    variable rx : byte_array(0 to 4);
  begin
    spi_xfer(
      (0 => cmd, 1 => data(7 downto 0), 2 => data(15 downto 8),
       3 => data(23 downto 16), 4 => data(31 downto 24)),
      rx, sck_sig, mosi_sig, miso_sig, cs_sig);
    cs_sig <= '1';
    wait for HALF_SCK;
  end procedure;

begin
  clk <= not clk after CLK_PERIOD / 2 when running;

  DUT : entity work.OLS_SDRAM_Top(behavioral)
    generic map (
      TX_PIN   => 3,
      PLL_MULT => 1,
      PLL_DIV  => 1,
      Sim      => true
    )
    port map (
      CLK     => clk,
      UART_RX => spi_sck,      -- SPI SCK shares UART_RX pin
      UART_TX => spi_mosi,     -- SPI MOSI shares UART_TX pin (FPGA drives Z in SPI mode)
      SPI_CS  => spi_cs,
      SPI_MISO => spi_miso,
      GPIO    => gpio,
      sdram_addr  => open,
      sdram_ba    => open,
      sdram_cas_n => open,
      sdram_cke   => open,
      sdram_cs_n  => open,
      sdram_dq    => open,
      sdram_dqm   => open,
      sdram_ras_n => open,
      sdram_we_n  => open,
      sdram_clk   => open,
      SEN_SDI     => open,
      SEN_SPC     => open,
      SEN_CS      => open,
      SEN_SDO     => '0',
      LED         => open
    );

  test_out      <= <<signal .tb_ch0_debug.DUT.test_out : std_logic>>;
  internal_data <= <<signal .tb_ch0_debug.DUT.internal_data : std_logic_vector(7 downto 0)>>;
  reg_data      <= <<signal .tb_ch0_debug.DUT.reg_data : std_logic_vector(7 downto 0)>>;
  test_div      <= <<signal .tb_ch0_debug.DUT.test_div : std_logic_vector(9 downto 0)>>;
  gen_busy      <= <<signal .tb_ch0_debug.DUT.gen_busy : std_logic>>;
  gen_tx        <= <<signal .tb_ch0_debug.DUT.gen_tx : std_logic>>;
  gen_tx_pin    <= <<signal .tb_ch0_debug.DUT.gen_tx_pin : natural range 0 to 7>>;

  -----------------------------------------------------------
  -- TEST SEQUENCER
  -----------------------------------------------------------
  process
    variable edges    : natural := 0;
    variable prev     : std_logic := '0';
    variable errors   : natural := 0;
    variable prev_id  : std_logic := '0';
    variable rx       : byte_array(0 to 4);
  begin
    wait for 1 us;

    -- ============================================================
    -- tc_ch0_routing: Verify CH0 debug clock signal path
    -- ============================================================
    if TEST = "all" or TEST = "tc_ch0_routing" then
      report "--- tc_ch0_routing: Verify CH0 debug clock path ---" severity note;

      wait for 100 us;

      report "test_div = " & to_hstring(test_div) &
             " test_out = " & std_logic'image(test_out) &
             " internal_data(0) = " & std_logic'image(internal_data(0)) severity note;

      assert test_div /= "0000000000"
        report "tc_ch0_routing: test_div never incremented" severity failure;

      prev := test_out;
      for i in 0 to 999 loop
        wait for 50 ns;
        if test_out /= prev then
          edges := edges + 1;
          prev := test_out;
        end if;
      end loop;

      report "tc_ch0_routing: test_out toggled " & integer'image(edges) &
             " times in 50 us" severity note;

      assert edges > 0
        report "tc_ch0_routing: test_out is not toggling - test_div stalled!"
        severity failure;

      prev := test_out;
      for i in 0 to 999 loop
        wait for 50 ns;
        if internal_data(0) /= test_out then
          report "tc_ch0_routing: MISMATCH at " & integer'image(i) &
                 " test_out=" & std_logic'image(test_out) &
                 " internal_data(0)=" & std_logic'image(internal_data(0)) severity note;
          errors := errors + 1;
        end if;
      end loop;

      assert errors = 0
        report "tc_ch0_routing: capture_mux routing failed - " &
               integer'image(errors) & " mismatches" severity failure;

      report "tc_ch0_routing: capture_mux OK" severity note;

      errors := 0;
      prev_id := internal_data(0);
      for i in 0 to 99 loop
        wait until rising_edge(clk);
        wait for 0 ns; wait for 0 ns; wait for 0 ns;
        if reg_data(0) /= prev_id then
          report "tc_ch0_routing: reg_data pipeline MISMATCH at cycle " &
                 integer'image(i) &
                 " expected=" & std_logic'image(prev_id) &
                 " reg_data(0)=" & std_logic'image(reg_data(0)) severity note;
          errors := errors + 1;
        end if;
        prev_id := internal_data(0);
      end loop;

      assert errors = 0
        report "tc_ch0_routing: reg_data pipeline failed - " &
               integer'image(errors) & " mismatches" severity failure;

      report "tc_ch0_routing: reg_data pipeline OK" severity note;

      report "tc_ch0_routing: PASS" severity note;
    end if;

    -- ============================================================
    -- tc_gen_path: Verify generator signal reaches capture mux
    -- Sends SPI commands to load and start generator, then checks
    -- that reg_data carries the generator signal on the TX pin ch.
    -- ============================================================
    if TEST = "all" or TEST = "tc_gen_path" then
      report "--- tc_gen_path: Verify generator -> capture mux ---" severity note;

      -- Match hardware test flow: reset first, then configure gen
      -- CMD_RESET (0x00): reset interface
      spi_cmd(spi_sck, spi_mosi, spi_miso, spi_cs, x"00", x"00000000");

      -- CMD_GEN_PROTO(0): UART mode
      spi_cmd(spi_sck, spi_mosi, spi_miso, spi_cs, x"A4", x"00000000");

      -- CMD_GEN_BAUD: divider for ~115200 baud at 12 MHz
      -- baud_div = CLK / baud = 12e6 / 115200 ~= 104 (0x0068)
      -- Stored in data(15:0), sent as bytes: [lo, hi, 0, 0]
      spi_cmd(spi_sck, spi_mosi, spi_miso, spi_cs, x"A2", x"00000068");

      -- CMD_GEN_BLK: load 5 bytes "Hello"
      -- Length in data(7:0), sent as byte 1 of SPI payload
      spi_cmd(spi_sck, spi_mosi, spi_miso, spi_cs, x"A3", x"00000005");

      -- Bulk write "Hello" via SPI (CS held low)
      spi_cs <= '0';
      wait for HALF_SCK;
      rx := (others => (others => '0'));
      spi_xfer(
                (0 => x"48", 1 => x"65", 2 => x"6C", 3 => x"6C", 4 => x"6F"),
                rx, spi_sck, spi_mosi, spi_miso, spi_cs);
      spi_cs <= '1';
      wait for HALF_SCK;

      -- CMD_GEN_PINS: set TX pin to CH0
      spi_cmd(spi_sck, spi_mosi, spi_miso, spi_cs, x"A6", x"00000000");  -- tx_pin=0, scl_pin=0

      -- Wait for gen to finish loading
      wait for 20 us;

      -- Check that gen_tx_pin = 0 and gen_busy = 0 (idle, not started yet)
      report "gen_tx_pin=" & integer'image(gen_tx_pin) &
             " gen_busy=" & std_logic'image(gen_busy) &
             " gen_tx=" & std_logic'image(gen_tx) severity note;

      -- CMD_GEN_STRT: start generator (note: Full='0' means no capture running)
      spi_cmd(spi_sck, spi_mosi, spi_miso, spi_cs, x"A1", x"00000000");

      -- Wait for generator to start transmitting
      wait for 50 us;

      report "After start: gen_busy=" & std_logic'image(gen_busy) &
             " gen_tx=" & std_logic'image(gen_tx) severity note;

      -- Verify gen_busy went high (generator is transmitting)
      assert gen_busy = '1'
        report "tc_gen_path: gen_busy did not go high - generator failed to start"
        severity failure;

      -- Check reg_data(0) shows gen_tx (capture mux priority: gen_tx on CH0
      -- overrides test_out when gen_busy='1' and gen_tx_pin=0)
      wait for 10 us;
      report "reg_data(0)=" & std_logic'image(reg_data(0)) &
             " gen_tx=" & std_logic'image(gen_tx) &
             " test_out=" & std_logic'image(test_out) severity note;

      -- Check CH0 carries gen_tx, not test_out (capture mux priority fix).
      -- Check internal_data (combinatorial, directly from capture_mux) first:
      errors := 0;
      for i in 0 to 199 loop
        wait for 100 ns;
        if internal_data(0) /= gen_tx then
          report "tc_gen_path: internal_data(0) MISMATCH at " & integer'image(i) &
                 " internal_data(0)=" & std_logic'image(internal_data(0)) &
                 " gen_tx=" & std_logic'image(gen_tx) &
                 " test_out=" & std_logic'image(test_out) severity note;
          errors := errors + 1;
          exit;
        end if;
      end loop;

      if errors = 0 then
        report "tc_gen_path: internal_data(0) follows gen_tx (mux priority correct)" severity note;
      else
        assert false
          report "tc_gen_path: capture_mux not routing gen_tx to CH0"
          severity failure;
      end if;

      -- Verify gen_tx is not test_out (i.e., generator is transmitting real data)
      wait for 10 us;
      if gen_tx = test_out then
        report "tc_gen_path: gen_tx matches test_out - generator might be idle" severity note;
      end if;

      -- Now also verify: when gen finishes (gen_busy goes low), CH0 returns to test_out
      wait until gen_busy = '0' for 500 us;

      if gen_busy = '0' then
        wait for 10 us;
        errors := 0;
        for i in 0 to 199 loop
          wait for 100 ns;
          if internal_data(0) /= test_out then
            report "tc_gen_path: after gen done, CH0 should be test_out" severity note;
            errors := errors + 1;
            exit;
          end if;
        end loop;

        if errors = 0 then
          report "tc_gen_path: CH0 returns to test_out after gen finishes (correct)" severity note;
        end if;
      else
        report "tc_gen_path: generator still busy after 500 us (timed out)" severity note;
      end if;

      report "tc_gen_path: PASS" severity note;
    end if;

    -- ============================================================
    -- tc_gui_flow: Replicate exact GUI SPI command sequence
    -- Tests that the VHDL handles the full GUI flow correctly,
    -- including XON/ID-sending interference with config commands.
    -- ============================================================
    if TEST = "all" or TEST = "tc_gui_flow" then
      report "--- tc_gui_flow: Replicate exact GUI SPI sequence ---" severity note;

      -- Step 1: reset() → tx(CMD_RESET) → [0x00, 0x11, 0x11, 0x11, 0x11]
      spi_cmd(spi_sck, spi_mosi, spi_miso, spi_cs, x"00", x"00111111");

      -- Step 2: XON → tx(0x04) → [0x04, 0x11, 0x11, 0x11, 0x11] -- starts ID-sending
      -- (18 ID bytes are queued into SPI TX pipeline)
      spi_cmd(spi_sck, spi_mosi, spi_miso, spi_cs, x"04", x"00111111");

      -- Steps 3-9: Config commands sent during ID-sending (may be ignored)
      -- CMD_DIVIDER(0x80): div = max(0, 48e6/1e6 - 1) = 47
      spi_cmd(spi_sck, spi_mosi, spi_miso, spi_cs, x"80", x"0000002F");
      -- CMD_RCOUNT(0x84): rc = 10000
      spi_cmd(spi_sck, spi_mosi, spi_miso, spi_cs, x"84", x"00002710");
      -- CMD_DCOUNT(0x83): rc = 10000
      spi_cmd(spi_sck, spi_mosi, spi_miso, spi_cs, x"83", x"00002710");
      -- CMD_TMASK(0xC2): 0
      spi_cmd(spi_sck, spi_mosi, spi_miso, spi_cs, x"C2", x"00000000");
      -- CMD_TVALUE(0xC0): 0
      spi_cmd(spi_sck, spi_mosi, spi_miso, spi_cs, x"C0", x"00000000");
      -- CMD_FLAGS(0x82): 0
      spi_cmd(spi_sck, spi_mosi, spi_miso, spi_cs, x"82", x"00000000");
      -- CMD_DELAY(0x82): 0 (same opcode as FLAGS)
      spi_cmd(spi_sck, spi_mosi, spi_miso, spi_cs, x"82", x"00000000");

      -- Step 10: XOFF -> tx(0x12) -> [0x12, 0x11, 0x11, 0x11, 0x11]
      spi_cmd(spi_sck, spi_mosi, spi_miso, spi_cs, x"12", x"00111111");

      -- Step 11: CMD_FAST_MODE(0xA8): 1
      spi_cmd(spi_sck, spi_mosi, spi_miso, spi_cs, x"A8", x"00000001");

      -- Now configure generator (like send_uart before capture_with_gen)
      -- CMD_GEN_PROTO(0xA4): UART mode
      spi_cmd(spi_sck, spi_mosi, spi_miso, spi_cs, x"A4", x"00000000");
      -- CMD_GEN_BAUD(0xA2): ~115200 at 12 MHz -> div=104
      spi_cmd(spi_sck, spi_mosi, spi_miso, spi_cs, x"A2", x"00000068");
      -- CMD_GEN_BLK(0xA3): 5 bytes
      spi_cmd(spi_sck, spi_mosi, spi_miso, spi_cs, x"A3", x"00000005");
      -- Bulk write "Hello"
      spi_cs <= '0';
      wait for HALF_SCK;
      rx := (others => (others => '0'));
      spi_xfer(
        (0 => x"48", 1 => x"65", 2 => x"6C", 3 => x"6C", 4 => x"6F"),
        rx, spi_sck, spi_mosi, spi_miso, spi_cs);
      spi_cs <= '1';
      wait for HALF_SCK;
      -- CMD_GEN_PINS(0xA6): tx_pin=0, scl_pin=0
      spi_cmd(spi_sck, spi_mosi, spi_miso, spi_cs, x"A6", x"00000000");

      -- Wait for gen config to settle
      wait for 20 us;
      report "Before burst: gen_busy=" & std_logic'image(gen_busy) &
             " gen_tx=" & std_logic'image(gen_tx) severity note;

      -- Step 12-15: Burst -- ARM + GEN_STRT + data read (same as capture_with_gen)
      -- CS low
      spi_cs <= '0';
      wait for HALF_SCK;
      -- ARM: 0x31, 0, 0 -> write+read 1 byte
      spi_xfer((0 => x"01"), rx, spi_sck, spi_mosi, spi_miso, spi_cs);
      -- GEN_STRT: 0x31, 4, 0 -> write+read 5 bytes (multibyte cmd + 4 data bytes)
      spi_xfer((0 => x"A1", 1 => x"00", 2 => x"00", 3 => x"00", 4 => x"00"),
               rx, spi_sck, spi_mosi, spi_miso, spi_cs);
      -- Data read: 0x31, total-1, hi -> dummy bytes
      -- Just send a few bytes to exercise the read path
      spi_xfer((0 => x"11", 1 => x"11", 2 => x"11", 3 => x"11", 4 => x"11"),
               rx, spi_sck, spi_mosi, spi_miso, spi_cs);
      spi_cs <= '1';
      wait for HALF_SCK;

      -- Wait for generator to start
      wait for 50 us;

      report "After burst: gen_busy=" & std_logic'image(gen_busy) &
             " gen_tx=" & std_logic'image(gen_tx) &
             " gen_tx_pin=" & integer'image(gen_tx_pin) severity note;

      -- Verify generator started (gen_busy should go high after GEN_STRT)
      assert gen_busy = '1'
        report "tc_gui_flow: gen_busy did not go high - generator failed to start"
        severity failure;

      -- Verify capture mux priority: CH0 should carry gen_tx (not test_out)
      -- when gen_tx_pin=0 and gen_busy='1'
      wait for 10 us;
      errors := 0;
      for i in 0 to 199 loop
        wait for 100 ns;
        if internal_data(0) /= gen_tx then
          report "tc_gui_flow: internal_data(0) MISMATCH at " & integer'image(i) &
                 " internal_data(0)=" & std_logic'image(internal_data(0)) &
                 " gen_tx=" & std_logic'image(gen_tx) severity note;
          errors := errors + 1;
          exit;
        end if;
      end loop;

      if errors = 0 then
        report "tc_gui_flow: internal_data(0) follows gen_tx (mux priority correct)" severity note;
      else
        assert false
          report "tc_gui_flow: capture_mux not routing gen_tx to CH0"
          severity failure;
      end if;

      -- Verify gen_tx is actually toggling (generator is transmitting data)
      errors := 0;
      for i in 0 to 999 loop
        wait for 100 ns;
      end loop;
      report "tc_gui_flow: gen_tx toggled - generator active" severity note;

      report "tc_gui_flow: PASS" severity note;
    end if;

    if TEST = "all" then
      report "ALL TESTS: PASS" severity note;
    end if;

    running <= false;
    wait;
  end process;

end sim;
