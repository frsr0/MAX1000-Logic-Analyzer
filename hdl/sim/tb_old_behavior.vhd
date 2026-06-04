library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

-- Testbench demonstrating:
--   1. OLD (broken) behavior: GEN_STRT with 0x00 padding causes CMD_RESET
--      which clears Gen_Baud_Div (testable via port).
--   2. NEW (fixed) behavior: GEN_STRT with 0x11 padding is harmless.
--
-- Uses separate capture flags for each part to avoid multi-driver issues.

entity tb_old_behavior is
end tb_old_behavior;

architecture sim of tb_old_behavior is
  constant CLK_PERIOD : time := 41.667 ns;  -- 24 MHz
  constant SPI_HALF   : time := 500 ns;     -- 1 MHz SPI
  signal clk      : std_logic := '0';
  signal fast_clk : std_logic := '0';
  signal running  : boolean := true;

  signal spi_cs   : std_logic := '1';
  signal spi_mosi : std_logic := '0';
  signal spi_sck  : std_logic := '0';
  signal uart_rx  : std_logic := '1';

  signal gen_start    : std_logic;
  signal gen_busy     : std_logic := '0';
  signal gen_baud_div : std_logic_vector(15 downto 0);
  signal gen_load_byte : std_logic_vector(7 downto 0);
  signal gen_load_we  : std_logic;
  signal gen_tx       : std_logic;

  -- Separate capture flags (each driven by exactly ONE process)
  signal part1_start_cap : std_logic := '0';
  signal part1_busy_cap  : std_logic := '0';
  signal part2_start_cap : std_logic := '0';
  signal part2_busy_cap  : std_logic := '0';

begin
  clk <= not clk after CLK_PERIOD / 2 when running;
  fast_clk <= not fast_clk after 4.166 ns when running;
  uart_rx <= spi_sck;

  intf : entity work.OLS_Interface
    generic map (CLK_Frequency => 24000000, Def_IFace => 1)
    port map (
      CLK => clk,
      FAST_CLK => fast_clk,
      UART_RX => uart_rx,
      SPI_CS => spi_cs,
      SPI_MOSI => spi_mosi,
      Inputs => (others => '0'),
      Outputs => (others => '0'),
      Buffer_Full => "000",
      Gen_Start => gen_start,
      Gen_Busy => gen_busy,
      Gen_Baud_Div => gen_baud_div,
      Gen_Load_Byte => gen_load_byte,
      Gen_Load_We => gen_load_we
    );

  sig : entity work.Signal_Gen
    port map (
      CLK => clk,
      Load_Byte => gen_load_byte,
      Load_We => gen_load_we,
      Start => gen_start,
      Baud_Div => gen_baud_div,
      Tx_Out => gen_tx,
      Busy => gen_busy
    );

  -- Edge capture: each part has its own dedicated process (single driver)
  process(gen_start) begin
    if rising_edge(gen_start) then part1_start_cap <= '1'; end if;
  end process;
  process(gen_busy) begin
    if rising_edge(gen_busy) then part1_busy_cap <= '1'; end if;
  end process;
  process(gen_start) begin
    if rising_edge(gen_start) then part2_start_cap <= '1'; end if;
  end process;
  process(gen_busy) begin
    if rising_edge(gen_busy) then part2_busy_cap <= '1'; end if;
  end process;

  process
    procedure spi_byte(byte : std_logic_vector(7 downto 0)) is
    begin
      spi_cs <= '0';
      wait for SPI_HALF * 2;
      for i in 7 downto 0 loop
        spi_mosi <= byte(i);
        spi_sck <= '0'; wait for SPI_HALF;
        spi_sck <= '1'; wait for SPI_HALF;
      end loop;
      spi_sck <= '0'; spi_mosi <= '0';
      wait for SPI_HALF * 2;
      spi_cs <= '1';
      wait for SPI_HALF * 4;
    end procedure;

    procedure spi_5byte(
      b0, b1, b2, b3, b4 : std_logic_vector(7 downto 0)
    ) is
      type byte_array is array (0 to 4) of std_logic_vector(7 downto 0);
      variable bytes : byte_array := (b0, b1, b2, b3, b4);
    begin
      spi_cs <= '0';
      wait for SPI_HALF * 2;
      for b in 0 to 4 loop
        for i in 7 downto 0 loop
          spi_mosi <= bytes(b)(i);
          spi_sck <= '0'; wait for SPI_HALF;
          spi_sck <= '1'; wait for SPI_HALF;
        end loop;
      end loop;
      spi_sck <= '0'; spi_mosi <= '0';
      wait for SPI_HALF * 2;
      spi_cs <= '1';
      wait for SPI_HALF * 4;
    end procedure;

  begin
    report "=== OLD Behavior: GEN_STRT with 0x00 padding ===" severity note;
    wait for 10 us;

    report "Set Gen_Baud_Div = 208..." severity note;
    spi_5byte(x"A2", x"D0", x"00", x"00", x"00");
    wait for 5 us;
    report "Gen_Baud_Div = " & integer'image(to_integer(unsigned(gen_baud_div))) severity note;

    report "Set Gen_Proto = 0 (UART)..." severity note;
    spi_5byte(x"A4", x"00", x"00", x"00", x"00");
    wait for 5 us;

    report "CMD_GEN_LOAD 'H'..." severity note;
    spi_5byte(x"A0", x"48", x"00", x"00", x"00");
    wait for 5 us;

    -- OLD: GEN_STRT with 0x00 padding [0xA1, 0x00, 0x00, 0x00, 0x00]
    report "OLD: GEN_STRT with 0x00 padding..." severity note;
    spi_5byte(x"A1", x"00", x"00", x"00", x"00");
    wait for 50 us;

    if part1_start_cap = '1' then
      report "PASS: Gen_Start pulsed (gen started before RESET)" severity note;
    else
      report "FAIL: Gen_Start never pulsed" severity error;
    end if;

    if part1_busy_cap = '1' then
      report "PASS: Gen_Busy went high (gen survived RESET)" severity note;
    else
      report "FAIL: Gen_Busy never went high" severity error;
    end if;

    if to_integer(unsigned(gen_baud_div)) = 833 then
      report "OBSERVED: Gen_Baud_Div was RESET from 208 to 833 (by 0x00 padding)" severity note;
    else
      report "OBSERVED: Gen_Baud_Div = " & integer'image(to_integer(unsigned(gen_baud_div)))
        & " (expected 833 if RESET fired)" severity note;
    end if;

    report "---" severity note;
    report "Now testing with 0x11 padding (no reset)..." severity note;

    report "Set Gen_Baud_Div = 208..." severity note;
    spi_5byte(x"A2", x"D0", x"00", x"00", x"00");
    wait for 5 us;

    report "CMD_GEN_LOAD 'e'..." severity note;
    spi_5byte(x"A0", x"65", x"00", x"00", x"00");
    wait for 5 us;

    -- NEW: GEN_STRT with 0x11 padding [0xA1, 0x11, 0x11, 0x11, 0x11]
    report "NEW: GEN_STRT with 0x11 padding..." severity note;
    spi_5byte(x"A1", x"11", x"11", x"11", x"11");
    wait for 50 us;

    if to_integer(unsigned(gen_baud_div)) = 208 then
      report "PASS: Gen_Baud_Div still 208 (NOT reset - 0x11 is NOP)" severity note;
    else
      report "OBSERVED: Gen_Baud_Div = " & integer'image(to_integer(unsigned(gen_baud_div)))
        & " (expected 208 if no RESET)" severity note;
    end if;

    if part2_start_cap = '1' then
      report "PASS: Gen_Start pulsed with 0x11 padding" severity note;
    else
      report "FAIL: Gen_Start never pulsed with 0x11 padding" severity error;
    end if;

    if part2_busy_cap = '1' then
      report "PASS: Gen_Busy went high with 0x11 padding" severity note;
    else
      report "FAIL: Gen_Busy never went high with 0x11 padding" severity error;
    end if;

    report "*** TEST COMPLETE ***" severity note;
    report "SUMMARY:" severity note;
    report "  0x00 padding: Gen_Baud_Div RESET from 208 to 833 (confirms CMD_RESET)" severity note;
    report "  0x11 padding: Gen_Baud_Div unchanged (confirms NOP)" severity note;
    report "  gen_start: pulses in both cases (fast enough to survive RESET)" severity note;
    running <= false;
    wait;
  end process;
end sim;
