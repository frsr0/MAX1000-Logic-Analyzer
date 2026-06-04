library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity tb_send_uart is
end tb_send_uart;

architecture sim of tb_send_uart is
  constant CLK_PERIOD : time := 41.667 ns;  -- 24 MHz
  constant SPI_HALF   : time := 500 ns;     -- 1 MHz SPI
  signal clk      : std_logic := '0';
  signal fast_clk : std_logic := '0';
  signal running  : boolean := true;

  signal spi_cs   : std_logic := '1';
  signal spi_mosi : std_logic := '0';
  signal spi_sck  : std_logic := '0';
  signal uart_rx  : std_logic := '1';

  -- OLS_Interface outputs to Signal_Gen
  signal gen_start    : std_logic;
  signal gen_busy     : std_logic := '0';
  signal gen_baud_div : std_logic_vector(15 downto 0);
  signal gen_load_byte : std_logic_vector(7 downto 0);
  signal gen_load_we  : std_logic;
  signal gen_tx       : std_logic;

  -- Edge capture flags
  signal start_cap : std_logic := '0';
  signal busy_cap  : std_logic := '0';
  signal tx_cap    : std_logic := '0';

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

  process(gen_start) begin
    if rising_edge(gen_start) then start_cap <= '1'; end if;
  end process;
  process(gen_busy) begin
    if rising_edge(gen_busy) then busy_cap <= '1'; end if;
  end process;
  process(gen_tx) begin
    if falling_edge(gen_tx) then tx_cap <= '1'; end if;
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
    report "=== send_uart() Full Command Sequence Test ===" severity note;
    wait for 10 us;

    -- Reset to known state
    report "CMD_RESET..." severity note;
    spi_byte(x"00");
    wait for 5 us;

    -- Step 1: CMD_GEN_PROTO (0xA4) = 0 (UART)
    report "CMD_GEN_PROTO = 0 (UART)..." severity note;
    spi_5byte(x"A4", x"00", x"00", x"00", x"00");
    wait for 5 us;

    -- Step 2: CMD_GEN_BAUD (0xA2) = 208 (115200 @ 24 MHz)
    report "CMD_GEN_BAUD = 208..." severity note;
    spi_5byte(x"A2", x"D0", x"00", x"00", x"00");
    wait for 5 us;

    -- Step 3: CMD_GEN_BLK (0xA3) = 4 bytes ('H','e','l','o')
    report "CMD_GEN_BLK + 4 bytes..." severity note;
    spi_5byte(x"A3", x"04", x"00", x"00", x"00");
    wait for 5 us;
    -- Data bytes (each as a single SPI byte with its own CS-low)
    spi_byte(x"48");  -- 'H'
    spi_byte(x"65");  -- 'e'
    spi_byte(x"6C");  -- 'l'
    spi_byte(x"6F");  -- 'o'
    wait for 5 us;

    -- Step 4: CMD_GEN_PINS (0xA6) = tx_pin=3, scl_pin=1
    report "CMD_GEN_PINS tx=3 scl=1..." severity note;
    spi_5byte(x"A6", x"03", x"00", x"01", x"00");
    wait for 5 us;

    -- Step 5: CMD_GEN_STRT (0xA1) with 0x11 padding (THE FIX)
    --   OLD: [0xA1, 0x00, 0x00, 0x00, 0x00] --> CMD_RESET in padding
    --   NEW: [0xA1, 0x11, 0x11, 0x11, 0x11] --> CMD_XON/NOP in padding
    report "CMD_GEN_STRT with 0x11 padding..." severity note;
    spi_5byte(x"A1", x"11", x"11", x"11", x"11");
    wait for 50 us;

    -- Verify gen started
    if start_cap = '1' then
      report "PASS: Gen_Start pulsed" severity note;
    else
      report "FAIL: Gen_Start never pulsed" severity error;
    end if;

    if busy_cap = '1' then
      report "PASS: Gen_Busy went high (gen running)" severity note;
    else
      report "FAIL: Gen_Busy never went high" severity error;
    end if;

    if tx_cap = '1' then
      report "PASS: Gen_TX toggled (UART data transmitted)" severity note;
    else
      report "FAIL: Gen_TX never toggled" severity error;
    end if;

    -- Wait for gen to finish (4 bytes at 115200 baud = ~347 us)
    wait for 400 us;

    if gen_busy = '0' then
      report "PASS: Gen_Busy went low after transmission complete" severity note;
    else
      report "FAIL: Gen_Busy still high after transmission window" severity error;
    end if;

    report "*** send_uart TEST COMPLETE ***" severity note;
    running <= false;
    wait;
  end process;
end sim;
