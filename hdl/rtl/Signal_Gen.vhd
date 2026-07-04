library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity Signal_Gen is
  generic (FIFO_DEPTH : natural := 256);
  port (
    CLK       : in  std_logic;
    Load_Byte : in  std_logic_vector(7 downto 0);
    Load_We   : in  std_logic;
    Clear     : in  std_logic := '0';
    Start     : in  std_logic;
    Start_Ack : out std_logic := '0';
    Start_Reject : out std_logic := '0';
    Done_Pulse   : out std_logic := '0';
    Baud_Div  : in  std_logic_vector(15 downto 0);
    Proto     : in  std_logic := '0';  -- 0=UART, 1=I2C
    SPI_Mode  : in  std_logic := '0';  -- 1=SPI (overrides Proto)
    Repeat    : in  std_logic := '0';  -- replay loaded UART FIFO forever
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
  constant PTR_WIDTH : natural := 8;
  constant FIXED_BAUD_DIV : std_logic_vector(15 downto 0) := x"01E0";  -- 480 = 100 kHz I2C @ 96 MHz

  subtype ptr_t is unsigned(PTR_WIDTH-1 downto 0);

  type ram_t is array (0 to FIFO_DEPTH-1) of std_logic_vector(7 downto 0);
  signal fifo_ram : ram_t := (others => (others => '0'));
  attribute ramstyle : string;
  attribute ramstyle of fifo_ram : signal is "M9K";

  type mode_t is (MODE_NONE, MODE_UART, MODE_SPI, MODE_I2C);
  type read_src_t is (READ_NONE, READ_PLAYBACK, READ_I2C_DEV);
  type uart_state_t is (
    UART_IDLE,
    UART_FETCH,
    UART_START_BIT,
    UART_DATA_BITS,
    UART_STOP_BIT
  );
  type spi_state_t is (
    SPI_IDLE,
    SPI_FETCH,
    SPI_LOAD,
    SPI_SCLK_LOW,
    SPI_SCLK_HIGH,
    SPI_TRAIL_LOW
  );
  type i2c_state_t is (
    I2C_IDLE,
    I2C_START,
    I2C_NEXT_BYTE,
    I2C_FETCH_BYTE,
    I2C_BIT_HIGH,
    I2C_SHIFT_LOW,
    I2C_ACK_HIGH,
    I2C_RESTART_LOW,
    I2C_RESTART_HIGH,
    I2C_RESTART_START,
    I2C_READ_LOW,
    I2C_READ_HIGH1,
    I2C_READ_HIGH2,
    I2C_READ_ACK_LOW,
    I2C_READ_ACK_HIGH,
    I2C_STOP_LOW,
    I2C_STOP_HIGH,
    I2C_STOP_DONE
  );

  signal wr_ptr         : ptr_t := (others => '0');
  signal rd_ptr         : ptr_t := (others => '0');
  signal used_count     : natural range 0 to FIFO_DEPTH := 0;

  signal tx_active      : std_logic := '0';
  signal active_mode    : mode_t := MODE_NONE;
  signal start_d        : std_logic := '0';
  signal done_pulse_i   : std_logic := '0';

  signal tx_out_r       : std_logic := '1';
  signal scl_out_r      : std_logic := '1';

  signal repeat_active  : std_logic := '0';
  signal repeat_base    : ptr_t := (others => '0');
  signal repeat_ptr     : ptr_t := (others => '0');
  signal repeat_count   : natural range 0 to FIFO_DEPTH := 0;
  signal repeat_left    : natural range 0 to FIFO_DEPTH := 0;

  signal read_issue_q   : std_logic := '0';
  signal read_src_q     : read_src_t := READ_NONE;
  signal read_addr_q    : ptr_t := (others => '0');
  signal read_valid_q   : std_logic := '0';
  signal read_data_q    : std_logic_vector(7 downto 0) := (others => '0');

  signal byte_buf       : std_logic_vector(7 downto 0) := (others => '0');

  signal baud_cnt_s     : natural range 0 to 65535 := 0;
  signal baud_limit_s   : natural range 0 to 65535 := 0;
  signal baud_tick_r    : std_logic := '0';

  signal uart_state      : uart_state_t := UART_IDLE;
  signal uart_baud_cnt   : natural range 0 to 65535 := 0;
  signal uart_baud_limit : natural range 0 to 65535 := 239;
  signal uart_bit_idx    : natural range 0 to 7 := 0;
  signal uart_shift      : std_logic_vector(7 downto 0) := (others => '0');
  signal uart_crc        : std_logic_vector(15 downto 0) := (others => '0');
  signal uart_crc_run    : std_logic := '0';
  signal uart_crc_phase  : natural range 0 to 2 := 0;

  signal spi_state       : spi_state_t := SPI_IDLE;
  signal spi_bit_idx     : natural range 0 to 7 := 0;

  signal i2c_state       : i2c_state_t := I2C_IDLE;
  signal i2c_bit_idx     : natural range 0 to 7 := 0;
  signal i2c_rd_remain   : natural range 0 to 255 := 0;
  signal i2c_read_phase  : std_logic := '0';

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

  function inc_ptr(p : ptr_t) return ptr_t is
  begin
    if to_integer(p) = FIFO_DEPTH - 1 then
      return (others => '0');
    end if;
    return p + 1;
  end function;
begin
  Active <= tx_active;
  Busy   <= tx_active;
  Tx_Out <= tx_out_r;
  Scl_Out <= scl_out_r;
  Fifo_Count <= std_logic_vector(to_unsigned(used_count, 8));
  Done_Pulse <= done_pulse_i;

  -- Shared baud tick for SPI/I2C. The tick is registered one cycle before the
  -- protocol engines consume it so the comparator does not sit in the control
  -- cone that also drives the playback RAM read addresses.
  process(CLK)
  begin
    if rising_edge(CLK) then
      if tx_active = '0' then
        baud_cnt_s  <= 0;
        baud_tick_r <= '0';
      elsif active_mode = MODE_SPI or active_mode = MODE_I2C then
        if baud_cnt_s >= baud_limit_s then
          baud_cnt_s  <= 0;
          baud_tick_r <= '1';
        else
          baud_cnt_s  <= baud_cnt_s + 1;
          baud_tick_r <= '0';
        end if;
      else
        baud_tick_r <= '0';
      end if;
    end if;
  end process;

  process(CLK)
    variable start_rise    : std_logic;
    variable start_accept  : std_logic;
    variable baud_limit_v  : natural range 0 to 65535;
    variable issue_read_v  : std_logic;
    variable issue_src_v   : read_src_t;
    variable issue_addr_v  : ptr_t;
  begin
    if rising_edge(CLK) then
      Start_Ack <= '0';
      Start_Reject <= '0';
      done_pulse_i <= '0';

      start_accept := '0';
      issue_read_v := '0';
      issue_src_v := READ_NONE;
      issue_addr_v := read_addr_q;

      -- Retire the previous read request through a single shared playback path.
      read_valid_q <= read_issue_q;
      case read_src_q is
        when READ_PLAYBACK =>
          if read_issue_q = '1' then
            read_data_q <= fifo_ram(to_integer(read_addr_q));
          end if;
        when READ_I2C_DEV =>
          if read_issue_q = '1' then
            read_data_q <= I2C_Dev_R;
          end if;
        when others =>
          null;
      end case;

      if Load_We = '1' and used_count < FIFO_DEPTH then
        fifo_ram(to_integer(wr_ptr)) <= Load_Byte;
        wr_ptr <= inc_ptr(wr_ptr);
        used_count <= used_count + 1;
      end if;

      start_rise := Start and not start_d;
      start_d <= Start;

      if start_rise = '1' and tx_active = '0' then
        baud_limit_v := to_integer(unsigned(Baud_Div)) - 1;
        if Baud_Div = x"0000" then
          baud_limit_v := to_integer(unsigned(FIXED_BAUD_DIV)) - 1;
        end if;

        baud_limit_s <= baud_limit_v;
        uart_baud_limit <= baud_limit_v;
        uart_baud_cnt <= 0;

        if SPI_Mode = '1' and used_count > 0 then
          start_accept := '1';
          Start_Ack <= '1';
          tx_active <= '1';
          active_mode <= MODE_SPI;
          spi_state <= SPI_FETCH;
          spi_bit_idx <= 0;
          uart_state <= UART_IDLE;
          i2c_state <= I2C_IDLE;
          tx_out_r <= '1';
          scl_out_r <= '1';

          issue_read_v := '1';
          issue_src_v := READ_PLAYBACK;
          issue_addr_v := rd_ptr;
          rd_ptr <= inc_ptr(rd_ptr);
          used_count <= used_count - 1;

        elsif SPI_Mode = '0' and Proto = '0' and used_count > 0 then
          start_accept := '1';
          Start_Ack <= '1';
          tx_active <= '1';
          active_mode <= MODE_UART;
          uart_state <= UART_FETCH;
          uart_bit_idx <= 0;
          uart_crc_phase <= 0;
          spi_state <= SPI_IDLE;
          i2c_state <= I2C_IDLE;
          tx_out_r <= '1';
          scl_out_r <= '1';

          if Repeat = '1' then
            repeat_active <= '1';
            repeat_base <= rd_ptr;
            repeat_ptr <= inc_ptr(rd_ptr);
            repeat_count <= used_count;
            if used_count > 1 then
              repeat_left <= used_count - 1;
            else
              repeat_left <= used_count;
            end if;
            uart_crc_run <= '0';
            issue_read_v := '1';
            issue_src_v := READ_PLAYBACK;
            issue_addr_v := rd_ptr;
          else
            repeat_active <= '0';
            repeat_count <= 0;
            repeat_left <= 0;
            uart_crc_run <= '0';
            issue_read_v := '1';
            issue_src_v := READ_PLAYBACK;
            issue_addr_v := rd_ptr;
            rd_ptr <= inc_ptr(rd_ptr);
            used_count <= used_count - 1;
          end if;

        elsif Proto = '1' and (used_count > 0 or I2C_Rd_Len > 0) then
          start_accept := '1';
          Start_Ack <= '1';
          tx_active <= '1';
          active_mode <= MODE_I2C;
          i2c_state <= I2C_START;
          i2c_bit_idx <= 0;
          i2c_rd_remain <= I2C_Rd_Len;
          i2c_read_phase <= '0';
          uart_state <= UART_IDLE;
          spi_state <= SPI_IDLE;
          tx_out_r <= '1';
          scl_out_r <= '1';
          repeat_active <= '0';
          repeat_count <= 0;
          repeat_left <= 0;
          uart_crc_run <= '0';
          uart_crc_phase <= 0;
        else
          Start_Reject <= '1';
        end if;
      end if;

      if start_accept = '0' then
        if tx_active = '0' then
          active_mode <= MODE_NONE;
          uart_state <= UART_IDLE;
          spi_state <= SPI_IDLE;
          i2c_state <= I2C_IDLE;
          tx_out_r <= '1';
          scl_out_r <= '1';
          uart_baud_cnt <= 0;
          uart_bit_idx <= 0;
          uart_crc_run <= '0';
          uart_crc_phase <= 0;
          repeat_active <= '0';
          repeat_count <= 0;
          repeat_left <= 0;

        elsif active_mode = MODE_UART then
          if uart_baud_cnt < uart_baud_limit then
            uart_baud_cnt <= uart_baud_cnt + 1;
          else
            uart_baud_cnt <= 0;

            case uart_state is
              when UART_FETCH =>
                if read_valid_q = '1' then
                  uart_shift <= read_data_q;
                  if repeat_active = '0' and CRC_En = '1' then
                    if uart_crc_run = '0' then
                      uart_crc <= crc16_update(x"FFFF", read_data_q, CRC_Poly);
                    else
                      uart_crc <= crc16_update(uart_crc, read_data_q, CRC_Poly);
                    end if;
                    uart_crc_run <= '1';
                  end if;
                  uart_bit_idx <= 0;
                  uart_state <= UART_START_BIT;
                end if;

              when UART_START_BIT =>
                tx_out_r <= '0';
                uart_bit_idx <= 0;
                uart_state <= UART_DATA_BITS;

              when UART_DATA_BITS =>
                tx_out_r <= uart_shift(uart_bit_idx);
                if uart_bit_idx = 7 then
                  uart_state <= UART_STOP_BIT;
                else
                  uart_bit_idx <= uart_bit_idx + 1;
                end if;

              when UART_STOP_BIT =>
                tx_out_r <= '1';

                if repeat_active = '1' then
                  issue_read_v := '1';
                  issue_src_v := READ_PLAYBACK;
                  issue_addr_v := repeat_ptr;
                  if repeat_count <= 1 then
                    repeat_ptr <= repeat_base;
                    repeat_left <= repeat_count;
                  elsif repeat_left <= 1 then
                    repeat_ptr <= repeat_base;
                    repeat_left <= repeat_count;
                  else
                    repeat_ptr <= inc_ptr(repeat_ptr);
                    repeat_left <= repeat_left - 1;
                  end if;
                  uart_state <= UART_FETCH;

                elsif used_count > 0 then
                  issue_read_v := '1';
                  issue_src_v := READ_PLAYBACK;
                  issue_addr_v := rd_ptr;
                  rd_ptr <= inc_ptr(rd_ptr);
                  used_count <= used_count - 1;
                  uart_state <= UART_FETCH;

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

                else
                  tx_active <= '0';
                  active_mode <= MODE_NONE;
                  uart_state <= UART_IDLE;
                  uart_crc_run <= '0';
                  uart_crc_phase <= 0;
                  repeat_active <= '0';
                  repeat_count <= 0;
                  repeat_left <= 0;
                  done_pulse_i <= '1';
                  tx_out_r <= '1';
                end if;

              when others =>
                uart_state <= UART_IDLE;
                tx_out_r <= '1';
            end case;
          end if;

        elsif active_mode = MODE_SPI then
          if baud_tick_r = '1' then
            case spi_state is
              when SPI_FETCH =>
                if read_valid_q = '1' then
                  byte_buf <= read_data_q;
                  spi_bit_idx <= 0;
                  spi_state <= SPI_LOAD;
                end if;

              when SPI_LOAD =>
                scl_out_r <= '0';
                tx_out_r <= byte_buf(7);
                spi_state <= SPI_SCLK_LOW;

              when SPI_SCLK_LOW =>
                scl_out_r <= '0';
                tx_out_r <= byte_buf(7 - spi_bit_idx);
                spi_state <= SPI_SCLK_HIGH;

              when SPI_SCLK_HIGH =>
                scl_out_r <= '1';
                if spi_bit_idx = 7 then
                  if used_count > 0 then
                    issue_read_v := '1';
                    issue_src_v := READ_PLAYBACK;
                    issue_addr_v := rd_ptr;
                    rd_ptr <= inc_ptr(rd_ptr);
                    used_count <= used_count - 1;
                    spi_state <= SPI_FETCH;
                  else
                    spi_state <= SPI_TRAIL_LOW;
                  end if;
                else
                  spi_bit_idx <= spi_bit_idx + 1;
                  spi_state <= SPI_SCLK_LOW;
                end if;

              when SPI_TRAIL_LOW =>
                scl_out_r <= '0';
                tx_active <= '0';
                active_mode <= MODE_NONE;
                spi_state <= SPI_IDLE;
                done_pulse_i <= '1';

              when others =>
                spi_state <= SPI_IDLE;
            end case;
          end if;

        elsif active_mode = MODE_I2C then
          if baud_tick_r = '1' then
            case i2c_state is
              when I2C_START =>
                scl_out_r <= '1';
                tx_out_r <= '0';
                i2c_state <= I2C_NEXT_BYTE;

              when I2C_NEXT_BYTE =>
                if used_count > 0 then
                  issue_read_v := '1';
                  issue_src_v := READ_PLAYBACK;
                  issue_addr_v := rd_ptr;
                  rd_ptr <= inc_ptr(rd_ptr);
                  used_count <= used_count - 1;
                  i2c_state <= I2C_FETCH_BYTE;
                elsif i2c_rd_remain > 0 and i2c_read_phase = '0' then
                  i2c_state <= I2C_RESTART_LOW;
                elsif i2c_rd_remain > 0 then
                  i2c_bit_idx <= 0;
                  i2c_rd_remain <= i2c_rd_remain - 1;
                  i2c_state <= I2C_READ_LOW;
                else
                  i2c_state <= I2C_STOP_LOW;
                end if;

              when I2C_FETCH_BYTE =>
                scl_out_r <= '0';
                if read_valid_q = '1' then
                  byte_buf <= read_data_q;
                  tx_out_r <= read_data_q(7);
                  i2c_bit_idx <= 1;
                  i2c_state <= I2C_BIT_HIGH;
                end if;

              when I2C_BIT_HIGH =>
                scl_out_r <= '1';
                i2c_state <= I2C_SHIFT_LOW;

              when I2C_SHIFT_LOW =>
                scl_out_r <= '0';
                if i2c_bit_idx < 8 then
                  tx_out_r <= byte_buf(7 - i2c_bit_idx);
                  i2c_bit_idx <= i2c_bit_idx + 1;
                  i2c_state <= I2C_BIT_HIGH;
                else
                  tx_out_r <= '1';
                  i2c_state <= I2C_ACK_HIGH;
                end if;

              when I2C_ACK_HIGH =>
                scl_out_r <= '1';
                i2c_state <= I2C_NEXT_BYTE;

              when I2C_RESTART_LOW =>
                scl_out_r <= '0';
                tx_out_r <= '1';
                i2c_state <= I2C_RESTART_HIGH;

              when I2C_RESTART_HIGH =>
                scl_out_r <= '1';
                tx_out_r <= '1';
                i2c_state <= I2C_RESTART_START;

              when I2C_RESTART_START =>
                scl_out_r <= '1';
                tx_out_r <= '0';
                i2c_read_phase <= '1';
                issue_read_v := '1';
                issue_src_v := READ_I2C_DEV;
                issue_addr_v := (others => '0');
                i2c_state <= I2C_FETCH_BYTE;

              when I2C_READ_LOW =>
                scl_out_r <= '0';
                tx_out_r <= '1';
                i2c_state <= I2C_READ_HIGH1;

              when I2C_READ_HIGH1 =>
                scl_out_r <= '1';
                i2c_state <= I2C_READ_HIGH2;

              when I2C_READ_HIGH2 =>
                scl_out_r <= '1';
                if i2c_bit_idx = 7 then
                  i2c_state <= I2C_READ_ACK_LOW;
                else
                  i2c_bit_idx <= i2c_bit_idx + 1;
                  i2c_state <= I2C_READ_LOW;
                end if;

              when I2C_READ_ACK_LOW =>
                scl_out_r <= '0';
                if i2c_rd_remain = 0 then
                  tx_out_r <= '1';
                else
                  tx_out_r <= '0';
                end if;
                i2c_state <= I2C_READ_ACK_HIGH;

              when I2C_READ_ACK_HIGH =>
                scl_out_r <= '1';
                if i2c_rd_remain = 0 then
                  i2c_state <= I2C_STOP_LOW;
                else
                  i2c_bit_idx <= 0;
                  i2c_state <= I2C_READ_LOW;
                end if;

              when I2C_STOP_LOW =>
                scl_out_r <= '0';
                tx_out_r <= '0';
                i2c_state <= I2C_STOP_HIGH;

              when I2C_STOP_HIGH =>
                scl_out_r <= '1';
                i2c_state <= I2C_STOP_DONE;

              when I2C_STOP_DONE =>
                scl_out_r <= '1';
                tx_out_r <= '1';
                tx_active <= '0';
                active_mode <= MODE_NONE;
                i2c_state <= I2C_IDLE;
                done_pulse_i <= '1';

              when others =>
                i2c_state <= I2C_IDLE;
            end case;
          end if;
        end if;
      end if;

      read_issue_q <= issue_read_v;
      read_src_q <= issue_src_v;
      read_addr_q <= issue_addr_v;

      -- Abort/flush takes precedence over same-cycle loads and start requests.
      if Clear = '1' then
        wr_ptr <= (others => '0');
        rd_ptr <= (others => '0');
        used_count <= 0;
        tx_active <= '0';
        active_mode <= MODE_NONE;
        start_d <= Start;
        read_issue_q <= '0';
        read_src_q <= READ_NONE;
        read_valid_q <= '0';
        tx_out_r <= '1';
        scl_out_r <= '1';
        uart_state <= UART_IDLE;
        spi_state <= SPI_IDLE;
        i2c_state <= I2C_IDLE;
        uart_baud_cnt <= 0;
        uart_bit_idx <= 0;
        uart_crc_run <= '0';
        uart_crc_phase <= 0;
        repeat_active <= '0';
        repeat_count <= 0;
        repeat_left <= 0;
        i2c_bit_idx <= 0;
        i2c_rd_remain <= 0;
        i2c_read_phase <= '0';
      end if;
    end if;
  end process;
end rtl;
