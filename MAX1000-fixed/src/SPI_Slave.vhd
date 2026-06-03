library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;

entity SPI_Slave2 is
  port (
    sys_clk    : in  std_logic;
    reset      : in  std_logic := '0';
    SCK        : in  std_logic := '0';
    MOSI       : in  std_logic := '0';
    MISO       : out std_logic := 'Z';
    CS_n       : in  std_logic := '1';
    TX_Data    : in  std_logic_vector(7 downto 0) := (others => '0');
    SPI_Preamble   : in  std_logic_vector(7 downto 0) := (others => '0');
    TX_Ready   : out std_logic := '0';
    RX_Data    : out std_logic_vector(7 downto 0) := (others => '0');
    RX_Valid   : out std_logic := '0';
    PipeDepth  : in  natural range 2 to 8 := 8
  );
end SPI_Slave2;

architecture rtl of SPI_Slave2 is
  signal sck_sync : std_logic := '0';
  signal cs_sync  : std_logic := '0';
  signal sck_prev : std_logic := '0';
  signal cs_prev  : std_logic := '0';
  signal sck_rise : std_logic := '0';
  signal sck_fall : std_logic := '0';
  signal cs_fall  : std_logic := '0';
  signal cs_rise  : std_logic := '0';

  signal rx_shift : std_logic_vector(7 downto 0) := (others => '0');
  signal tx_shift : std_logic_vector(7 downto 0) := (others => '0');
  signal bit_cnt  : natural range 0 to 7 := 0;
  signal rx_byte  : std_logic_vector(7 downto 0) := (others => '0');
  signal rx_valid_i : std_logic := '0';

  -- FIX (Bug 1): reload is registered on the cycle RX_Valid fires, then applied
  -- on the very next sck_fall.  No multi-stage pipeline needed because TX_Data
  -- is already stable before that fall edge (the OLS_Interface FSM updates
  -- UART_TX_Data before asserting UART_TX_Enable, and effective_TX_Busy
  -- is now cleared on TX_Data stable, not on RX_Valid).
  signal reload_pending : std_logic := '0';
begin

  sync_proc: process(sys_clk)
  begin
    if rising_edge(sys_clk) then
      sck_sync <= SCK;
      cs_sync  <= CS_n;
      sck_prev <= sck_sync;
      cs_prev  <= cs_sync;

      if sck_sync = '1' and sck_prev = '0' then sck_rise <= '1';
      else sck_rise <= '0'; end if;

      if sck_sync = '0' and sck_prev = '1' then sck_fall <= '1';
      else sck_fall <= '0'; end if;

      if cs_sync = '0' and cs_prev = '1' then cs_fall <= '1';
      else cs_fall <= '0'; end if;

      if cs_sync = '1' and cs_prev = '0' then cs_rise <= '1';
      else cs_rise <= '0'; end if;
    end if;
  end process;

  main_proc: process(sys_clk)
  begin
    if rising_edge(sys_clk) then
      rx_valid_i <= '0';

      if reset = '1' then
        bit_cnt <= 0;
        rx_shift <= (others => '0');
        tx_shift <= (others => '0');
        rx_valid_i <= '0';
        reload_pending <= '0';

      elsif cs_fall = '1' then
        bit_cnt <= 0;
        tx_shift <= SPI_Preamble;
        reload_pending <= '0';

      elsif cs_rise = '1' then
        bit_cnt <= 0;
        reload_pending <= '0';

      elsif cs_sync = '0' then
        if sck_rise = '1' then
          rx_shift <= rx_shift(6 downto 0) & MOSI;
          if bit_cnt = 7 then
            rx_byte    <= rx_shift(6 downto 0) & MOSI;
            rx_valid_i <= '1';
            bit_cnt    <= 0;
            -- FIX (Bug 1): set reload_pending here; it will be consumed on the
            -- NEXT sck_fall (bit 0 of the following byte).  TX_Data is latched
            -- by the OLS_Interface FSM before that fall edge arrives because
            -- effective_TX_Busy clears as soon as TX_Data is written (see
            -- spi_adapter fix in OLS_Interface.vhd).
            reload_pending <= '1';
          else
            bit_cnt <= bit_cnt + 1;
          end if;
        end if;

        -- Reload TX_Data onto MISO shift register on the first sck_fall after
        -- a byte boundary.  No extra pipeline delay: TX_Data must be stable
        -- within one SCK half-period after RX_Valid, which is guaranteed by the
        -- corrected effective_TX_Busy handshake in OLS_Interface.vhd.
        if sck_fall = '1' then
          if reload_pending = '1' then
            tx_shift       <= TX_Data;
            reload_pending <= '0';
          else
            tx_shift <= tx_shift(6 downto 0) & '0';
          end if;
        end if;
      end if;
    end if;
  end process;

  MISO     <= tx_shift(7) when cs_sync = '0' else 'Z';
  TX_Ready <= '1';
  RX_Data  <= rx_byte;
  RX_Valid <= rx_valid_i;

end rtl;
