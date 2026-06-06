library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;

entity ADXL345_Model is
  port (
    -- SPI interface
    sclk : in  std_logic := '0';
    mosi : in  std_logic := '0';
    miso : out std_logic := 'Z';
    cs_n : in  std_logic := '1';

    -- I2C interface (open-drain, pull-up)
    scl  : inout std_logic := 'Z';
    sda  : inout std_logic := 'Z';

    -- Sim control: set acceleration values
    accel_x : in std_logic_vector(15 downto 0) := x"0040";  -- ~0.25g
    accel_y : in std_logic_vector(15 downto 0) := x"FFC0";  -- ~-0.25g
    accel_z : in std_logic_vector(15 downto 0) := x"1000"   -- ~1.0g
  );
end ADXL345_Model;

architecture behavioral of ADXL345_Model is
  constant ADXL345_I2C_ADDR : std_logic_vector(6 downto 0) := "1010011";  -- 0x53

  type reg_map_t is array (0 to 63) of std_logic_vector(7 downto 0);
  signal regs : reg_map_t := (
    0   => x"E5",  -- DEVID
    44  => x"00",  -- POWER_CTL: standby
    49  => x"00",  -- FIFO_CTL
    50  => x"00",  -- FIFO_STATUS
    31  => x"00",  -- DATA_FORMAT: full res, +/-2g
    others => x"00"
  );

  signal i2c_state : natural := 0;
  signal i2c_bit_cnt : natural := 0;
  signal i2c_shift : std_logic_vector(7 downto 0) := (others => '0');
  signal i2c_addr_match : std_logic := '0';
  signal i2c_rw : std_logic := '0';
  signal i2c_reg_addr : natural := 0;
  signal i2c_is_write : std_logic := '0';
  signal i2c_byte_cnt : natural := 0;
  signal i2c_expected_bytes : natural := 0;
  signal i2c_done : std_logic := '1';

  signal scl_in : std_logic := '1';
  signal sda_in : std_logic := '1';
  signal scl_prev : std_logic := '1';
  signal sda_prev : std_logic := '1';
  signal scl_fall : std_logic := '0';
  signal sda_fall : std_logic := '0';
  signal sda_rise : std_logic := '0';
  signal i2c_start : std_logic := '0';
  signal i2c_stop : std_logic := '0';

  signal spi_addr : natural := 0;
  signal spi_rw : std_logic := '0';
  signal spi_bit_cnt : natural := 0;
  signal spi_byte_cnt : natural := 0;
  signal spi_shift_in : std_logic_vector(7 downto 0) := (others => '0');
  signal spi_shift_out : std_logic_vector(7 downto 0) := (others => '0');
  signal spi_header : std_logic := '1';
  signal spi_mb : std_logic := '0';  -- multi-byte flag

  -- Update accelerometer data registers on read
  procedure update_data(signal r : inout reg_map_t;
                        x, y, z : in std_logic_vector(15 downto 0)) is
  begin
    r(50) <= x"00";  -- clear FIFO_STATUS
  end procedure;

  signal gen_x : std_logic_vector(15 downto 0);
  signal gen_y : std_logic_vector(15 downto 0);
  signal gen_z : std_logic_vector(15 downto 0);

begin

  gen_x <= accel_x;
  gen_y <= accel_y;
  gen_z <= accel_z;

  -- Fill data registers with current acceleration values
  regs(50) <= x"00";  -- FIFO_STATUS
  regs(32) <= gen_x(7 downto 0);    -- DATAX0
  regs(33) <= gen_x(15 downto 8);   -- DATAX1
  regs(34) <= gen_y(7 downto 0);    -- DATAY0
  regs(35) <= gen_y(15 downto 8);   -- DATAY1
  regs(36) <= gen_z(7 downto 0);    -- DATAZ0
  regs(37) <= gen_z(15 downto 8);   -- DATAZ1

  ------------------------------------------------------------------
  -- SPI interface
  ------------------------------------------------------------------
  process(sclk, cs_n)
  begin
    if cs_n = '1' then
      spi_bit_cnt <= 0;
      spi_byte_cnt <= 0;
      spi_header <= '1';
      miso <= 'Z';
    elsif falling_edge(sclk) then
      if spi_header = '1' then
        spi_shift_in(7 - spi_bit_cnt) <= mosi;
        spi_bit_cnt <= spi_bit_cnt + 1;
        if spi_bit_cnt = 7 then
          spi_rw <= mosi;
          spi_bit_cnt <= 0;
          spi_header <= '0';
        end if;
      else
        if spi_bit_cnt = 0 then
          if spi_rw = '0' then
            -- Write: update reg
            if spi_byte_cnt = 0 then
              regs(spi_addr) <= spi_shift_in;
            end if;
          else
            -- Read: load data
            if spi_byte_cnt = 0 then
              spi_shift_out <= regs(spi_addr);
            elsif spi_mb = '1' then
              spi_shift_out <= regs((spi_addr + spi_byte_cnt) mod 64);
            else
              spi_shift_out <= regs(spi_addr);
            end if;
            miso <= spi_shift_out(7);
          end if;
          spi_bit_cnt <= 1;
          if spi_byte_cnt = 0 and spi_rw = '1' then
            -- read: shift after first read
            null;
          elsif spi_byte_cnt = 0 then
            -- shift in address
            spi_addr <= to_integer(unsigned(spi_shift_in));
            spi_mb <= mosi;
          end if;
          spi_byte_cnt <= spi_byte_cnt + 1;
        else
          spi_shift_in(7 - spi_bit_cnt) <= mosi;
          if spi_rw = '1' then
            miso <= spi_shift_out(7 - spi_bit_cnt);
          end if;
          if spi_bit_cnt = 7 then
            if spi_rw = '0' then
              if spi_byte_cnt = 1 then
                regs(spi_addr) <= spi_shift_in;
              elsif spi_mb = '1' then
                regs((spi_addr + spi_byte_cnt - 1) mod 64) <= spi_shift_in;
              end if;
            end if;
          end if;
          spi_bit_cnt <= spi_bit_cnt + 1;
        end if;
      end if;
    end if;
  end process;

  ------------------------------------------------------------------
  -- I2C interface
  ------------------------------------------------------------------
  -- Pull-ups (open drain with weak pull-up modeled as 'H')
  scl <= 'Z';
  sda <= 'Z';

  process(scl, sda)
  begin
    scl_prev <= scl_in;
    sda_prev <= sda_in;
    scl_in <= To_X01(scl);
    sda_in <= To_X01(sda);

    if scl_in = '0' and scl_prev = '1' then scl_fall <= '1'; else scl_fall <= '0'; end if;
    if sda_in = '0' and sda_prev = '1' then sda_fall <= '1'; else sda_fall <= '0'; end if;
    if sda_in = '1' and sda_prev = '0' then sda_rise <= '1'; else sda_rise <= '0'; end if;

    if scl_in = '1' and sda_fall = '1' then
      i2c_stop <= '0';
      i2c_start <= '1';
    elsif scl_in = '1' and sda_rise = '1' then
      i2c_start <= '0';
      i2c_stop <= '1';
    else
      i2c_start <= '0';
      i2c_stop <= '0';
    end if;
  end process;

  process(scl)
    variable bit_cnt : natural := 0;
    variable byte_cnt : natural := 0;
    variable shift : std_logic_vector(7 downto 0) := (others => '0');
    variable phase : natural := 0;
    variable addr_match : std_logic := '0';
    variable rw : std_logic := '0';
    variable reg_addr : natural := 0;
    variable is_write : std_logic := '0';
    variable expecting_ack : std_logic := '0';
    variable tx_data : std_logic_vector(7 downto 0) := (others => '0');
    variable mb_flag : std_logic := '0';
  begin
    if falling_edge(scl) then
      if i2c_start = '1' then
        bit_cnt := 0;
        byte_cnt := 0;
        phase := 0;
        addr_match := '0';
        rw := '0';
        mb_flag := '0';
        is_write := '1';
        expecting_ack := '0';
      end if;

      if i2c_stop = '1' then
        phase := 0;
        bit_cnt := 0;
        i2c_done <= '1';
      end if;

      if phase = 0 then
        -- Address phase
        if bit_cnt < 8 then
          shift(7 - bit_cnt) := sda_in;
          bit_cnt := bit_cnt + 1;
        else
          if shift(7 downto 1) = ADXL345_I2C_ADDR then
            addr_match := '1';
            rw := shift(0);
          end if;
          bit_cnt := 0;
          phase := 1;
          if addr_match = '1' then
            if rw = '0' then
              sda <= '0';  -- ACK
            else
              sda <= '0';  -- ACK
            end if;
          end if;
        end if;
      elsif phase = 1 and addr_match = '0' then
        null;
      elsif rw = '0' then
        -- Write
        if phase = 1 then
          -- Register address
          if bit_cnt < 8 then
            if bit_cnt = 0 then sda <= 'Z'; end if;
            shift(7 - bit_cnt) := sda_in;
            bit_cnt := bit_cnt + 1;
          else
            reg_addr := to_integer(unsigned(shift));
            bit_cnt := 0;
            phase := 2;
            sda <= '0';  -- ACK
          end if;
        elsif phase = 2 then
          -- Data bytes
          if bit_cnt < 8 then
            if bit_cnt = 0 then sda <= 'Z'; end if;
            shift(7 - bit_cnt) := sda_in;
            bit_cnt := bit_cnt + 1;
          else
            regs(reg_addr) <= shift;
            if reg_addr < 63 then
              reg_addr := reg_addr + 1;
            end if;
            bit_cnt := 0;
            sda <= '0';  -- ACK
          end if;
        end if;
      else
        -- Read
        if phase = 1 then
          -- Register address
          if bit_cnt < 8 then
            if bit_cnt = 0 then sda <= 'Z'; end if;
            shift(7 - bit_cnt) := sda_in;
            bit_cnt := bit_cnt + 1;
          else
            reg_addr := to_integer(unsigned(shift));
            mb_flag := shift(0);  -- unused here, keep simple
            bit_cnt := 0;
            phase := 2;
            sda <= '0';  -- ACK
          end if;
        elsif phase = 2 then
          -- Repeated start: address again
          if i2c_start = '1' then
            bit_cnt := 0;
            phase := 3;
          end if;
        elsif phase = 3 then
          -- Address with read bit
          if bit_cnt < 8 then
            shift(7 - bit_cnt) := sda_in;
            bit_cnt := bit_cnt + 1;
          else
            if shift(7 downto 1) = ADXL345_I2C_ADDR and shift(0) = '1' then
              addr_match := '1';
            end if;
            bit_cnt := 0;
            phase := 4;
            sda <= '0';  -- ACK
          end if;
        elsif phase = 4 then
          -- Read data
          tx_data := regs(reg_addr);
          if bit_cnt < 8 then
            sda <= tx_data(7 - bit_cnt);
            bit_cnt := bit_cnt + 1;
          else
            bit_cnt := 0;
            sda <= 'Z';
            if reg_addr < 63 then
              reg_addr := reg_addr + 1;
            end if;
            -- wait for master ACK/NACK
          end if;
        end if;
      end if;
    end if;
  end process;

end behavioral;
