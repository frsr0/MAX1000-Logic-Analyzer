library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity Signal_Gen is
  generic (FIFO_DEPTH : natural := 256);
  port (
    CLK       : in  std_logic;
    Load_Byte : in  std_logic_vector(7 downto 0);
    Load_We   : in  std_logic;
    Start     : in  std_logic;
    Start_Ack : out std_logic := '0';
    Start_Reject : out std_logic := '0';
    Done_Pulse   : out std_logic := '0';
    Baud_Div  : in  std_logic_vector(15 downto 0);
    Proto     : in  std_logic := '0';  -- 0=UART, 1=I2C
    SPI_Mode  : in  std_logic := '0';  -- 1=SPI (overrides Proto)
    Tx_Out    : out std_logic := '1';
    Scl_Out   : out std_logic := '1';
    Busy      : out std_logic := '0';
    Active    : out std_logic := '0';
    Fifo_Count : out std_logic_vector(7 downto 0) := (others => '0');
    I2C_Rd_Len : in natural range 0 to 255 := 0;
    I2C_Dev_R  : in std_logic_vector(7 downto 0) := (others => '0');
    Sda_In     : in std_logic := '1';
    CRC_En    : in std_logic := '0';
    CRC_Poly  : in std_logic_vector(15 downto 0) := x"A001"
  );
end Signal_Gen;

architecture rtl of Signal_Gen is
  type fifo_t is array (0 to FIFO_DEPTH-1) of std_logic_vector(7 downto 0);
  constant FIXED_BAUD_DIV : std_logic_vector(15 downto 0) := x"01E0";  -- 480 = 100 kHz I2C @ 96 MHz
  signal fifo  : fifo_t := (others => (others => '0'));
  signal head  : natural range 0 to FIFO_DEPTH-1 := 0;
  signal tail  : natural range 0 to FIFO_DEPTH-1 := 0;
  signal count : natural range 0 to FIFO_DEPTH := 0;
  signal tx_active   : std_logic := '0';
  signal start_d     : std_logic := '0';
  signal done_pulse_i : std_logic := '0';

  -- Registered byte-load stage for SPI/I2C (breaks FIFO read → Tx_Out path)
  signal byte_buf    : std_logic_vector(7 downto 0) := (others => '0');
  signal byte_ready  : std_logic := '0';

  -- UART explicit registered FSM
  type uart_state_t is (
    UART_IDLE,
    UART_START_BIT,
    UART_DATA_BITS,
    UART_STOP_BIT,
    UART_DONE
  );
  signal uart_state      : uart_state_t := UART_IDLE;
  signal uart_baud_cnt   : natural range 0 to 65535 := 0;
  signal uart_baud_limit : natural range 0 to 65535 := 239;
  signal uart_bit_idx    : natural range 0 to 7 := 0;
  signal uart_shift      : std_logic_vector(7 downto 0) := (others => '0');

  -- CRC state for UART
  signal uart_crc       : std_logic_vector(15 downto 0) := (others => '0');
  signal uart_crc_run   : std_logic := '0';
  signal uart_crc_phase : natural range 0 to 2 := 0;

  function crc16_update(
    crc_in : std_logic_vector(15 downto 0);
    data   : std_logic_vector(7 downto 0);
    poly   : std_logic_vector(15 downto 0)
  ) return std_logic_vector is
    variable c : std_logic_vector(15 downto 0);
  begin
    c := crc_in xor (x"00" & data);
    for i in 0 to 7 loop
      if c(0) = '1' then
        c := '0' & c(15 downto 1);
        c := c xor poly;
      else
        c := '0' & c(15 downto 1);
      end if;
    end loop;
    return c;
  end function;

  attribute preserve : boolean;
  attribute preserve of tx_active : signal is true;
  attribute preserve of count : signal is true;
  attribute preserve of byte_buf : signal is true;
  attribute preserve of byte_ready : signal is true;
begin
  Active <= tx_active;
  Busy   <= tx_active;
  Fifo_Count <= std_logic_vector(to_unsigned(count, 8));
  Done_Pulse <= done_pulse_i;

  process(CLK)
    variable start_rise : std_logic := '0';
    variable start_accept_v : std_logic := '0';
    variable baud_limit_v : natural range 0 to 65535 := 0;
    variable baud_cnt   : natural range 0 to 65535 := 0;
    variable bit_cnt  : natural range 0 to 15 := 0;
    variable data_buf : std_logic_vector(7 downto 0) := (others => '0');
    variable byte_active : boolean := false;
    variable crc      : std_logic_vector(15 downto 0) := (others => '0');
    variable crc_run  : boolean := false;
    variable crc_rem  : natural range 0 to 3 := 0;
    variable crc_done : boolean := false;
    variable crc_idx  : natural range 0 to 2 := 0;
    variable i2c_state : natural range 0 to 15 := 0;
    variable i2c_bit  : natural range 0 to 8 := 0;
    variable rd_remain : natural range 0 to 255 := 0;
    variable read_active : boolean := false;
    variable spi_state : natural range 0 to 4 := 0;
    variable spi_bit  : natural range 0 to 8 := 0;
  begin
    if rising_edge(CLK) then
      Start_Ack <= '0';
      Start_Reject <= '0';
      done_pulse_i <= '0';
      start_accept_v := '0';

      -- FIFO write (common to both protocols)
      if Load_We = '1' and count < FIFO_DEPTH then
        fifo(head) <= Load_Byte;
        head <= (head + 1) mod FIFO_DEPTH;
        count <= count + 1;
      end if;

      -- Edge-detect Start
      start_rise := Start and not start_d;
      start_d <= Start;

      -- Start trigger: accept only on rising edge when idle.
      -- Compute baud_limit before setting tx_active to avoid the
      -- signal-order trap where tx_active='0' reset runs on same cycle.
      if start_rise = '1' and tx_active = '0' then
        baud_limit_v := to_integer(unsigned(Baud_Div)) - 1;
        if Baud_Div = x"0000" then
          baud_limit_v := to_integer(unsigned(FIXED_BAUD_DIV)) - 1;
        end if;

        if SPI_Mode = '1' and count > 0 then
          -- SPI start — queue first byte in byte_buf (registered), let FSM handle it
          start_accept_v := '1';
          Start_Ack <= '1';
          tx_active <= '1';
          uart_state <= UART_IDLE;
          uart_baud_cnt <= 0;
          uart_baud_limit <= baud_limit_v;
          byte_buf <= fifo(tail);
          byte_ready <= '1';
          tail <= (tail + 1) mod FIFO_DEPTH;
          count <= count - 1;
          spi_bit := 0;
          spi_state := 3;  -- CS setup, not state 0 (prevents double-load)
          Tx_Out <= '1';  -- Will be updated on next cycle when byte_buf is ready
          Scl_Out <= '1';

        elsif SPI_Mode = '0' and Proto = '0' and count > 0 then
          -- UART start
          start_accept_v := '1';
          Start_Ack <= '1';
          tx_active <= '1';
          uart_baud_limit <= baud_limit_v;
          uart_baud_cnt <= 0;
          uart_shift <= fifo(tail);
          tail <= (tail + 1) mod FIFO_DEPTH;
          count <= count - 1;
          if CRC_En = '1' then
            uart_crc <= crc16_update(x"FFFF", fifo(tail), CRC_Poly);
            uart_crc_run <= '1';
          else
            uart_crc <= (others => '0');
            uart_crc_run <= '0';
          end if;
          uart_crc_phase <= 0;
          uart_bit_idx <= 0;
          uart_state <= UART_START_BIT;
          Tx_Out <= '0';
          Scl_Out <= '1';

        elsif Proto = '1' and (count > 0 or I2C_Rd_Len > 0) then
          -- I2C start
          start_accept_v := '1';
          Start_Ack <= '1';
          tx_active <= '1';
          uart_state <= UART_IDLE;
          uart_baud_cnt <= 0;
          i2c_state := 0;
          Tx_Out <= '1';
          Scl_Out <= '1';

        else
          Start_Reject <= '1';
        end if;
      end if;

      if start_accept_v = '1' then
        -- State already initialized this cycle; skip idle reset.
        -- SPI/UART/I2C engines run from their respective branches.
        null;
      elsif tx_active = '0' then
        -- Idle: reset everything
        uart_state <= UART_IDLE;
        uart_baud_cnt <= 0;
        uart_bit_idx <= 0;
        uart_crc_run <= '0';
        uart_crc_phase <= 0;
        i2c_state := 0; i2c_bit := 0; rd_remain := 0; read_active := false;
        spi_state := 0; spi_bit := 0;
        byte_active := false; bit_cnt := 0;
        byte_ready <= '0';
        crc := (others => '0'); crc_run := false; crc_rem := 0; crc_done := false;
        Tx_Out <= '1'; Scl_Out <= '1';

      elsif SPI_Mode = '1' then
        ----------------------------------------------------
        -- SPI Master (registered byte-load stage)
        ----------------------------------------------------
        if baud_cnt < baud_limit_v then
          baud_cnt := baud_cnt + 1;
        else
          baud_cnt := 0;
          case spi_state is
            when 0 =>  -- Idle / load byte
              if count > 0 then
                byte_buf <= fifo(tail);
                byte_ready <= '1';
                tail <= (tail + 1) mod FIFO_DEPTH;
                count <= count - 1;
                spi_bit := 0;
                spi_state := 3;
              else
                tx_active <= '0'; done_pulse_i <= '1';
              end if;
            when 3 =>  -- CS setup (wait for byte_ready)
              Scl_Out <= '1';
              if byte_ready = '1' then
                Tx_Out <= byte_buf(7);
                byte_ready <= '0';
                spi_state := 1;
              end if;
            when 1 =>  -- SCLK low, output bit
              Scl_Out <= '0';
              Tx_Out <= byte_buf(7 - spi_bit);
              spi_state := 2;
            when 2 =>  -- SCLK high
              Scl_Out <= '1';
              spi_bit := spi_bit + 1;
              if spi_bit >= 8 then
                if count > 0 then
                  byte_buf <= fifo(tail);
                  byte_ready <= '1';
                  tail <= (tail + 1) mod FIFO_DEPTH;
                  count <= count - 1;
                  spi_bit := 0;
                  spi_state := 3;
                else
                  tx_active <= '0'; done_pulse_i <= '1';
                  spi_state := 0;
                end if;
              else
                spi_state := 1;
              end if;
            when others =>
              spi_state := 0;
          end case;
        end if;

      elsif Proto = '0' then
        ----------------------------------------------------
        -- UART TX — explicit registered FSM
        ----------------------------------------------------
        if uart_baud_cnt < uart_baud_limit then
          uart_baud_cnt <= uart_baud_cnt + 1;
        else
          uart_baud_cnt <= 0;

          case uart_state is
            when UART_START_BIT =>
              Tx_Out <= '0';
              uart_bit_idx <= 0;
              uart_state <= UART_DATA_BITS;

            when UART_DATA_BITS =>
              Tx_Out <= uart_shift(uart_bit_idx);
              if uart_bit_idx = 7 then
                uart_state <= UART_STOP_BIT;
              else
                uart_bit_idx <= uart_bit_idx + 1;
              end if;

            when UART_STOP_BIT =>
              Tx_Out <= '1';

              if count > 0 then
                uart_shift <= fifo(tail);
                tail <= (tail + 1) mod FIFO_DEPTH;
                count <= count - 1;
                if uart_crc_run = '1' then
                  uart_crc <= crc16_update(uart_crc, fifo(tail), CRC_Poly);
                end if;
                uart_bit_idx <= 0;
                uart_state <= UART_START_BIT;
                Tx_Out <= '0';

              elsif uart_crc_run = '1' and uart_crc_phase < 2 then
                if uart_crc_phase = 0 then
                  uart_shift <= uart_crc(7 downto 0);
                  uart_crc_phase <= 1;
                else
                  uart_shift <= uart_crc(15 downto 8);
                  uart_crc_phase <= 2;
                end if;
                uart_bit_idx <= 0;
                uart_state <= UART_START_BIT;
                Tx_Out <= '0';

              else
                uart_crc_run <= '0';
                uart_crc_phase <= 0;
                tx_active <= '0';
                done_pulse_i <= '1';
                uart_state <= UART_IDLE;
                Tx_Out <= '1';
              end if;

            when others =>
              uart_state <= UART_IDLE;
              Tx_Out <= '1';
          end case;
        end if;

      elsif Proto = '1' then
        ----------------------------------------------------
        -- I2C Master (registered byte-load stage)
        ----------------------------------------------------
        if baud_cnt < baud_limit_v then
          baud_cnt := baud_cnt + 1;
        else
          baud_cnt := 0;
          case i2c_state is
            when 0 =>  -- START
              Scl_Out <= '1'; Tx_Out <= '0';
              rd_remain := I2C_Rd_Len;
              read_active := false;
              i2c_state := 1;
            when 1 =>
              if byte_active = false then
                if count > 0 then
                  Scl_Out <= '0';
                  byte_buf <= fifo(tail);
                  byte_ready <= '1';
                  tail <= (tail + 1) mod FIFO_DEPTH;
                  count <= count - 1;
                  i2c_bit := 0; byte_active := true;
                elsif rd_remain > 0 and not read_active then
                  read_active := true;
                  Scl_Out <= '0';
                  i2c_state := 13;
                elsif rd_remain > 0 and read_active then
                  Scl_Out <= '0';
                  rd_remain := rd_remain - 1;
                  i2c_bit := 0;
                  i2c_state := 8;
                else
                  Scl_Out <= '0';
                  i2c_state := 5;
                end if;
              else
                Scl_Out <= '0';
              end if;
              if byte_active then
                if i2c_state = 1 and byte_ready = '1' then
                  Tx_Out <= byte_buf(7);
                  byte_ready <= '0';
                  i2c_bit := 0;
                  i2c_state := 2;
                end if;
              end if;
            when 2 =>
              Scl_Out <= '1';
              i2c_state := 3;
            when 3 =>
              Scl_Out <= '0';
              if i2c_bit < 8 then
                Tx_Out <= byte_buf(7 - i2c_bit);
                i2c_bit := i2c_bit + 1;
                i2c_state := 2;
              else
                Tx_Out <= '1';
                i2c_state := 4;
              end if;
            when 4 =>
              Scl_Out <= '1';
              byte_active := false;
              if i2c_state = 4 then
                i2c_state := 1;
              end if;
            when 5 =>
              Scl_Out <= '1'; Tx_Out <= '1';
              tx_active <= '0'; done_pulse_i <= '1';
              i2c_state := 0;
            when 13 =>
              Scl_Out <= '0'; Tx_Out <= '1';
              i2c_state := 6;
            when 6 =>
              Scl_Out <= '1'; Tx_Out <= '1';
              i2c_state := 7;
            when 7 =>
              Scl_Out <= '1'; Tx_Out <= '0';
              byte_buf <= I2C_Dev_R;
              byte_ready <= '1';
              byte_active := true;
              i2c_bit := 0;
              i2c_state := 1;
            when 8 =>
              Scl_Out <= '0'; Tx_Out <= '1';
              i2c_state := 9;
            when 9 =>
              Scl_Out <= '1';
              i2c_state := 10;
            when 10 =>
              Scl_Out <= '1';
              i2c_state := 12;
            when 12 =>
              Scl_Out <= '1';
              i2c_bit := i2c_bit + 1;
              if i2c_bit < 8 then
                i2c_state := 8;
              else
                i2c_state := 14;
              end if;
            when 14 =>
              Scl_Out <= '0';
              if rd_remain = 0 then
                Tx_Out <= '1';
                i2c_state := 11;
              else
                Tx_Out <= '0';
                i2c_state := 15;
              end if;
            when 15 =>
              Scl_Out <= '1';
              rd_remain := rd_remain - 1;
              i2c_bit := 0;
              i2c_state := 8;
            when 11 =>
              Scl_Out <= '1';
              byte_active := false;
              i2c_state := 5;
            when others => i2c_state := 0;
          end case;
        end if;
      end if;
    end if;
  end process;
end rtl;
