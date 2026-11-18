library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;

entity SPI_Slave2 is
  port (
    sys_clk    : in  std_logic;
    fast_clk   : in  std_logic := '0';
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
    CS_Rise    : out std_logic := '0'
  );
end SPI_Slave2;

architecture rtl of SPI_Slave2 is
  signal sck_meta    : std_logic := '0';
  signal sck_sync    : std_logic := '0';
  signal cs_meta     : std_logic := '1';
  signal cs_sync     : std_logic := '1';
  signal sck_prev    : std_logic := '0';
  signal cs_prev     : std_logic := '0';
  signal sck_rise    : std_logic := '0';
  signal cs_fall     : std_logic := '0';
  signal cs_rise_int : std_logic := '0';
  signal cs_active   : std_logic := '0';

  signal rx_shift    : std_logic_vector(7 downto 0) := (others => '0');
  signal bit_cnt     : natural range 0 to 7 := 0;
  signal rx_byte_f   : std_logic_vector(7 downto 0) := (others => '0');
  signal rx_valid_f  : std_logic := '0';
  signal rx_valid_cnt : natural range 0 to 127 := 0;

  -- Pipeline MOSI one fast_clk cycle so sck_rise (~17 ns after real SCK
  -- edge) reads the value driven on the rising edge, before the SCK
  -- falling edge changes MOSI.  Fixes sampling at 30 MHz SCK.
  signal mosi_d : std_logic := '0';

  -- Source-synchronous MISO shifter, clocked by the real SCK RISING edge -
  -- the edge on which the FT2232H samples MISO (MPSSE 0x31). The bit the
  -- host samples at rising edge k was launched at rising edge k-1, a full
  -- SCK period earlier. At 30 MHz the FT2232H needs 11 ns setup (datasheet
  -- table 4.3) and the SCK-pin -> MISO-pin path is about 8 ns, which a
  -- half-period falling-edge launch (16.7 ns) cannot cover; a full period
  -- covers it with ~13 ns to spare, and the 0 ns host hold time is met by
  -- the same ~8 ns path delay. The bit sequence on the wire is unchanged.
  -- Byte loads from tx_data_f at byte boundaries (sck_tx_cnt = "111").
  signal sck_tx_shift : std_logic_vector(7 downto 0) := (others => '0');
  signal sck_tx_cnt   : unsigned(2 downto 0) := (others => '0');

  signal tx_data_s1  : std_logic_vector(7 downto 0) := (others => '0');
  signal tx_data_f   : std_logic_vector(7 downto 0) := (others => '0');
  signal preamble_s1 : std_logic_vector(7 downto 0) := (others => '0');
  signal preamble_f  : std_logic_vector(7 downto 0) := (others => '0');

  signal rx_byte_q   : std_logic_vector(7 downto 0) := (others => '0');
  signal tx_ready_q  : std_logic := '0';
  signal rx_valid_s1 : std_logic := '0';
  signal rx_valid_s2 : std_logic := '0';
  signal rx_valid_s3 : std_logic := '0';
  signal rx_valid_pending : std_logic := '0';
  signal rx_valid_q  : std_logic := '0';
  -- CS rise crossing: toggle CDC. The old scheme synchronized a single
  -- fast_clk-wide (5 ns) cs_rise pulse into the 100 MHz domain with plain
  -- FF sampling — the pulse was caught or missed at random, so consumers
  -- (packet parser resync, dispatcher streaming_active clear) raced.
  signal cs_rise_tog : std_logic := '0';
  signal cs_rise_t1  : std_logic := '0';
  signal cs_rise_t2  : std_logic := '0';
  signal cs_rise_t3  : std_logic := '0';
begin

  -- TX_Data CDC: sys_clk -> fast_clk
  tx_cdc: process(fast_clk)
  begin
    if rising_edge(fast_clk) then
      tx_data_s1  <= TX_Data;
      tx_data_f   <= tx_data_s1;
      preamble_s1 <= SPI_Preamble;
      preamble_f  <= preamble_s1;
    end if;
  end process;

  -- Source-synchronous MISO shifter: real SCK rising edge (see above).
  miso_sck: process(SCK)
  begin
    if rising_edge(SCK) then
      if cs_active = '0' then
        sck_tx_shift <= preamble_f;
      elsif sck_tx_cnt = "111" then
        sck_tx_shift <= tx_data_f;
      else
        sck_tx_shift <= sck_tx_shift(6 downto 0) & '0';
      end if;
    end if;
  end process;

  -- MISO bit counter: held at zero while the raw CS_n pin is high (async
  -- clear), so a transaction truncated mid-byte cannot leave later
  -- transactions framed on the wrong bit boundary.  CS_n is a false path in
  -- the timing constraints.  The counter wraps 7 -> 0 on its own.
  miso_cnt: process(SCK, CS_n)
  begin
    if CS_n = '1' then
      sck_tx_cnt <= (others => '0');
    elsif rising_edge(SCK) then
      sck_tx_cnt <= sck_tx_cnt + 1;
    end if;
  end process;

  -- Full SPI engine on fast_clk (120 MHz)
  fast_proc: process(fast_clk)
  begin
    if rising_edge(fast_clk) then
      sck_meta <= SCK;    sck_sync <= sck_meta;
      cs_meta  <= CS_n;   cs_sync  <= cs_meta;
      sck_prev <= sck_sync;
      cs_prev  <= cs_sync;

      if sck_sync = '1' and sck_prev = '0' then sck_rise <= '1';
      else sck_rise <= '0'; end if;
      if cs_sync = '0' and cs_prev = '1' then cs_fall <= '1';
      else cs_fall <= '0'; end if;
      if cs_sync = '1' and cs_prev = '0' then cs_rise_int <= '1';
                                              cs_rise_tog <= not cs_rise_tog;
      else cs_rise_int <= '0'; end if;
      if cs_sync = '0' then cs_active <= '1';
      else cs_active <= '0'; end if;

      if rx_valid_cnt > 0 then
        rx_valid_cnt <= rx_valid_cnt - 1;
        rx_valid_f <= '1';
      else
        rx_valid_f <= '0';
      end if;

      -- Pipeline MOSI so sck_rise samples the correct rising-edge value
      mosi_d <= MOSI;

      if reset = '1' then
        bit_cnt <= 0;
        rx_shift <= (others => '0');
        rx_valid_cnt <= 0;
        rx_valid_f <= '0';
      elsif cs_fall = '1' then
        bit_cnt <= 0;
        rx_valid_cnt <= 0;
      elsif cs_rise_int = '1' then
        bit_cnt <= 0;
      elsif cs_active = '1' then
        if sck_rise = '1' then
          rx_shift <= rx_shift(6 downto 0) & mosi_d;
          if bit_cnt = 7 then
            rx_byte_f  <= rx_shift(6 downto 0) & mosi_d;
            rx_valid_cnt <= 24;
            bit_cnt    <= 0;
          else
            bit_cnt <= bit_cnt + 1;
          end if;
        end if;
      end if;
    end if;
  end process;

  -- Source-synchronous MISO output: zero synchronizer latency on SCK rising edge
  -- cs_active is registered on fast_clk from the 2-FF CS synchroniser (cs_meta,
  -- cs_sync). It is used asynchronously in the SCK domain on purpose: it changes
  -- only while SCK is idle (CS edges), so it never changes inside an SCK cycle.
  MISO <= sck_tx_shift(7) when cs_active = '1' else 'Z';

  rx_cdc: process(sys_clk)
  begin
    if rising_edge(sys_clk) then
      rx_valid_s1 <= rx_valid_f;
      rx_valid_s2 <= rx_valid_s1;
      rx_valid_s3 <= rx_valid_s2;
      cs_rise_t1  <= cs_rise_tog;
      cs_rise_t2  <= cs_rise_t1;
      cs_rise_t3  <= cs_rise_t2;
      rx_valid_q <= rx_valid_pending;
      rx_valid_pending <= '0';
      if rx_valid_s2 = '1' and rx_valid_s3 = '0' then
        rx_byte_q <= rx_byte_f;
        rx_valid_pending <= '1';
      end if;
    end if;
  end process;

  RX_Data  <= rx_byte_q;
  RX_Valid <= rx_valid_q;
  CS_Rise  <= cs_rise_t2 xor cs_rise_t3;

  tx_ready_delay: process(sys_clk)
  begin
    if rising_edge(sys_clk) then
      tx_ready_q <= rx_valid_s2 and not rx_valid_s3;
    end if;
  end process;
  TX_Ready <= tx_ready_q;

end rtl;
