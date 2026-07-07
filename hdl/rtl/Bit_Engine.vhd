library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity Bit_Engine is
  generic (FIFO_DEPTH : natural := 256);
  port (
    CLK         : in  std_logic;
    TX_Data     : in  std_logic_vector(7 downto 0);
    TX_We       : in  std_logic;
    TX_Used     : out std_logic_vector(7 downto 0);
    RX_Data     : out std_logic_vector(7 downto 0);
    RX_Re       : in  std_logic;
    RX_Used     : out std_logic_vector(7 downto 0);
    RX_Overflow : out std_logic;
    Bit_Div     : in  std_logic_vector(15 downto 0);
    Num_Syms    : in  std_logic_vector(15 downto 0);
    Over_Sample : in  std_logic_vector(1 downto 0);
    RX_Enable   : in  std_logic;
    Clk_Toggle  : in  std_logic;
    Start       : in  std_logic;
    Busy        : out std_logic;
    Done        : out std_logic;
    Clear       : in  std_logic;
    Out_0       : out std_logic;
    Out_1       : out std_logic;
    In_0        : in  std_logic
  );
end Bit_Engine;

architecture rtl of Bit_Engine is
  constant PTR_W : natural := 8;
  subtype ptr_t is unsigned(PTR_W-1 downto 0);

  type tx_ram_t is array(0 to FIFO_DEPTH-1) of std_logic_vector(7 downto 0);
  signal tx_ram : tx_ram_t := (others => (others => '0'));
  attribute ramstyle : string;
  attribute ramstyle of tx_ram : signal is "M9K";

  type rx_ram_t is array(0 to FIFO_DEPTH-1) of std_logic_vector(7 downto 0);
  signal rx_ram : rx_ram_t := (others => (others => '0'));
  attribute ramstyle of rx_ram : signal is "M9K";

  signal tx_wr_ptr : ptr_t := (others => '0');
  signal tx_rd_ptr : ptr_t := (others => '0');
  signal tx_count  : natural range 0 to FIFO_DEPTH := 0;

  signal rx_wr_ptr : ptr_t := (others => '0');
  signal rx_rd_ptr : ptr_t := (others => '0');
  signal rx_count  : natural range 0 to FIFO_DEPTH := 0;
  signal rx_overflow_i : std_logic := '0';

  type state_t is (IDLE, LOAD, SHIFT);
  signal state : state_t := IDLE;

  signal sym_cnt    : unsigned(15 downto 0) := (others => '0');
  signal baud_acc   : unsigned(15 downto 0) := (others => '0');
  signal tick       : std_logic := '0';

  signal tx_buf    : std_logic_vector(7 downto 0) := (others => '0');
  signal tx_sym    : natural range 0 to 3 := 0;
  signal out0_r    : std_logic := '1';
  signal out1_r    : std_logic := '1';
  signal clk_tog_r : std_logic := '0';

  signal os_cnt    : unsigned(1 downto 0) := (others => '0');
  signal os_limit  : unsigned(1 downto 0) := (others => '0');

  signal rx_buf    : std_logic_vector(7 downto 0) := (others => '0');
  signal rx_bits   : natural range 0 to 7 := 0;
  signal rx_push   : std_logic := '0';

  function inc_ptr(p : ptr_t) return ptr_t is
  begin
    if to_integer(p) = FIFO_DEPTH - 1 then
      return (others => '0');
    end if;
    return p + 1;
  end function;

begin
  TX_Used <= std_logic_vector(to_unsigned(tx_count, 8));
  RX_Used <= std_logic_vector(to_unsigned(rx_count, 8));
  RX_Overflow <= rx_overflow_i;
  Out_0 <= out0_r;
  Out_1 <= out1_r;
  RX_Data <= rx_ram(to_integer(rx_rd_ptr));

  process(CLK)
    variable start_rise : std_logic;
  begin
    if rising_edge(CLK) then
      Busy <= '0';
      Done <= '0';
      rx_push <= '0';
      tick    <= '0';

      if Clear = '1' then
        state    <= IDLE;
        tx_wr_ptr <= (others => '0');
        tx_rd_ptr <= (others => '0');
        tx_count  <= 0;
        rx_wr_ptr <= (others => '0');
        rx_rd_ptr <= (others => '0');
        rx_count  <= 0;
        rx_overflow_i <= '0';
        sym_cnt   <= (others => '0');
        baud_acc  <= (others => '0');
        out0_r    <= '1';
        out1_r    <= '1';
        tx_sym    <= 0;
        clk_tog_r <= '0';
        os_cnt    <= (others => '0');
        rx_bits   <= 0;
        rx_push   <= '0';

      else
        -- TX FIFO write (accepted anytime not in clear)
        if TX_We = '1' and tx_count < FIFO_DEPTH then
          tx_ram(to_integer(tx_wr_ptr)) <= TX_Data;
          tx_wr_ptr <= inc_ptr(tx_wr_ptr);
          tx_count  <= tx_count + 1;
        end if;

        -- RX FIFO read (host reads captured data)
        if RX_Re = '1' and rx_count > 0 then
          rx_rd_ptr <= inc_ptr(rx_rd_ptr);
          rx_count  <= rx_count - 1;
        end if;

        -- Push captured byte to RX FIFO
        if rx_push = '1' and rx_count < FIFO_DEPTH then
          rx_ram(to_integer(rx_wr_ptr)) <= rx_buf;
          rx_wr_ptr <= inc_ptr(rx_wr_ptr);
          rx_count  <= rx_count + 1;
        end if;
        if rx_push = '1' and rx_count = FIFO_DEPTH then
          rx_overflow_i <= '1';
        end if;

        -- State machine
        case state is

          when IDLE =>
            out0_r <= '1';
            out1_r <= '1';
            tx_sym <= 0;
            baud_acc <= (others => '0');
            os_cnt   <= (others => '0');
            rx_bits  <= 0;
            clk_tog_r <= '0';

            start_rise := Start;
            if start_rise = '1' and tx_count > 0 then
              sym_cnt   <= unsigned(Num_Syms);
              os_limit  <= unsigned(Over_Sample);
              state     <= LOAD;
            end if;

          when LOAD =>
            if tx_count > 0 then
              tx_buf    <= tx_ram(to_integer(tx_rd_ptr));
              tx_rd_ptr <= inc_ptr(tx_rd_ptr);
              tx_count  <= tx_count - 1;
              tx_sym    <= 0;
              state     <= SHIFT;
              Busy      <= '1';
            else
              Done  <= '1';
              state <= IDLE;
            end if;

          when SHIFT =>
            Busy <= '1';

            if baud_acc >= unsigned(Bit_Div) then
              baud_acc <= (others => '0');
              tick     <= '1';
            else
              baud_acc <= baud_acc + 1;
            end if;

            if tick = '1' then
              if Clk_Toggle = '1' then
                clk_tog_r <= not clk_tog_r;
                out1_r    <= clk_tog_r;
              else
                out1_r <= tx_buf(tx_sym*2 + 1);
              end if;
              out0_r <= tx_buf(tx_sym*2);

              if tx_sym = 3 then
                tx_sym <= 0;
                state  <= LOAD;
              else
                tx_sym <= tx_sym + 1;
              end if;

              if sym_cnt > 0 then
                sym_cnt <= sym_cnt - 1;
              end if;
            end if;

            if sym_cnt = 0 and tick = '1' then
              Done   <= '1';
              out0_r <= '1';
              out1_r <= '1';
              state  <= IDLE;
            end if;

        end case;

        -- Oversample RX capture
        if RX_Enable = '1' and state /= IDLE then
          if os_limit = 0 then
            if tick = '1' then
              rx_buf(rx_bits) <= In_0;
              if rx_bits = 7 then
                rx_bits <= 0;
                rx_push <= '1';
              else
                rx_bits <= rx_bits + 1;
              end if;
            end if;
          else
            if os_cnt >= os_limit then
              os_cnt <= (others => '0');
            else
              os_cnt <= os_cnt + 1;
            end if;
            if os_cnt = 0 then
              rx_buf(rx_bits) <= In_0;
              if rx_bits = 7 then
                rx_bits <= 0;
                rx_push <= '1';
              else
                rx_bits <= rx_bits + 1;
              end if;
            end if;
          end if;
        elsif state = IDLE then
          rx_bits <= 0;
        end if;

      end if;
    end if;
  end process;

end rtl;
