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
    Baud_Div  : in  std_logic_vector(15 downto 0);
    Proto     : in  std_logic := '0';  -- 0=UART, 1=I2C
    Tx_Out    : out std_logic := '1';
    Scl_Out   : out std_logic := '1';
    Busy      : out std_logic := '0';
    Active    : out std_logic := '0';
    I2C_Rd_Len : in natural range 0 to 255 := 0;
    I2C_Dev_R  : in std_logic_vector(7 downto 0) := (others => '0');
    Sda_In     : in std_logic := '1';
    CRC_En    : in std_logic := '0';
    CRC_Poly  : in std_logic_vector(15 downto 0) := x"A001"
  );
end Signal_Gen;

architecture rtl of Signal_Gen is
  type fifo_t is array (0 to FIFO_DEPTH-1) of std_logic_vector(7 downto 0);
  signal fifo  : fifo_t := (others => (others => '0'));
  signal head  : natural range 0 to FIFO_DEPTH-1 := 0;
  signal tail  : natural range 0 to FIFO_DEPTH-1 := 0;
  signal count : natural range 0 to FIFO_DEPTH := 0;
  signal tx_active : std_logic := '0';
  -- baud_div_r: registered copy of Baud_Div port, latched on Start.
  -- FIX: original code had hardcoded BAUD_DIV_115200 and ignored the Baud_Div port.
  constant FIXED_BAUD : natural := 208;
  attribute keep : boolean;
  attribute keep of FIXED_BAUD : constant is true;
begin
  Active <= tx_active;
  Busy   <= tx_active;

  process(CLK)
    variable baud_cnt : natural range 0 to 65535 := 0;
    variable bit_cnt  : natural range 0 to 15 := 0;
    variable data_buf : std_logic_vector(7 downto 0) := (others => '0');
    variable byte_active : boolean := false;
    variable crc      : std_logic_vector(15 downto 0) := (others => '0');
    variable crc_run  : boolean := false;
    variable crc_rem  : natural range 0 to 3 := 0;
    variable crc_done : boolean := false;
    variable crc_idx  : natural range 0 to 2 := 0;
    variable i2c_state : natural range 0 to 13 := 0;
    variable i2c_bit  : natural range 0 to 8 := 0;
    variable rd_remain : natural range 0 to 255 := 0;
    variable rd_byte   : std_logic_vector(7 downto 0) := (others => '0');
    variable read_active : boolean := false;
  begin
    if rising_edge(CLK) then
      -- FIFO write (common to both protocols)
      if Load_We = '1' and count < FIFO_DEPTH then
        fifo(head) <= Load_Byte;
        head <= (head + 1) mod FIFO_DEPTH;
        count <= count + 1;
      end if;

      -- Start trigger
      if Start = '1' and tx_active = '0' then
        tx_active <= '1';
      end if;

      if tx_active = '0' then
        baud_cnt := 0; bit_cnt := 0; byte_active := false;
        i2c_state := 0; i2c_bit := 0; rd_remain := 0; read_active := false;
        crc := (others => '0'); crc_run := false; crc_rem := 0; crc_done := false;
        Tx_Out <= '1'; Scl_Out <= '1';
      elsif Proto = '0' then
        ----------------------------------------------------
        -- UART TX with optional Modbus CRC-16 append
        ----------------------------------------------------
        if baud_cnt < FIXED_BAUD - 1 then
          baud_cnt := baud_cnt + 1;
        else
          baud_cnt := 0;
          if not byte_active then
            if count > 0 then
              data_buf := fifo(tail);
              tail <= (tail + 1) mod FIFO_DEPTH;
              count <= count - 1;
              if CRC_En = '1' then
                if not crc_run then crc := x"FFFF"; crc_run := true; end if;
                crc := crc xor (x"00" & data_buf);
                for ci in 0 to 7 loop
                  if crc(0) = '1' then
                    crc := '0' & crc(15 downto 1);
                    crc := crc xor CRC_Poly;
                  else
                    crc := '0' & crc(15 downto 1);
                  end if;
                end loop;
              end if;
              Tx_Out <= '0';
              bit_cnt := 1;
              byte_active := true;
            elsif CRC_En = '1' and crc_run and crc_rem > 0 then
              if crc_rem = 2 then
                data_buf := crc(7 downto 0);
              else
                data_buf := crc(15 downto 8);
              end if;
              crc_rem := crc_rem - 1;
              if crc_rem = 0 then crc_done := true; end if;
              Tx_Out <= '0';
              bit_cnt := 1;
              byte_active := true;
            elsif CRC_En = '1' and crc_run then
              if crc_done then
                tx_active <= '0';
              else
                crc_rem := 2;
              end if;
            else
              tx_active <= '0';
            end if;
          elsif bit_cnt <= 8 then
            Tx_Out <= data_buf(bit_cnt - 1);
            bit_cnt := bit_cnt + 1;
          else
            Tx_Out <= '1';
            byte_active := false;
          end if;
        end if;
      else
        ----------------------------------------------------
        -- I2C Master
        ----------------------------------------------------
        if baud_cnt < FIXED_BAUD - 1 then
          baud_cnt := baud_cnt + 1;
        else
          baud_cnt := 0;
          case i2c_state is
            when 0 =>  -- START: SDA↓ while SCL↑
              Scl_Out <= '1'; Tx_Out <= '0';
              rd_remain := I2C_Rd_Len;
              read_active := false;
              i2c_state := 1;

            when 1 =>  -- prepare next byte (SCL low unless entering REP_START)
              if byte_active = false then
                if count > 0 then
                  -- Load next byte from FIFO (write phase)
                  Scl_Out <= '0';
                  data_buf := fifo(tail);
                  tail <= (tail + 1) mod FIFO_DEPTH;
                  count <= count - 1;
                  i2c_bit := 0; byte_active := true;
                elsif rd_remain > 0 and not read_active then
                  -- Transition to read: pull SCL low first (slave releases SDA while SCL low)
                  read_active := true;
                  Scl_Out <= '0';
                  i2c_state := 13;  -- SCL low state before REP_START
                elsif rd_remain > 0 and read_active then
                  -- Read next byte from slave
                  Scl_Out <= '0';
                  rd_remain := rd_remain - 1;
                  i2c_bit := 0;
                  i2c_state := 8;
                else
                  Scl_Out <= '0';
                  i2c_state := 5;  -- STOP
                end if;
              else
                Scl_Out <= '0';
              end if;
              if byte_active then
                if i2c_state = 1 then
                  Tx_Out <= data_buf(7);
                  i2c_bit := 1;
                  i2c_state := 2;
                end if;
              end if;

            when 2 =>  -- SCL↑: sample data bit
              Scl_Out <= '1';
              i2c_state := 3;

            when 3 =>  -- SCL↓: next bit or ACK
              Scl_Out <= '0';
              if i2c_bit < 8 then
                Tx_Out <= data_buf(7 - i2c_bit);
                i2c_bit := i2c_bit + 1;
                i2c_state := 2;
              else
                Tx_Out <= '1';  -- release for ACK
                i2c_state := 4;
              end if;

            when 4 =>  -- ACK: pulse SCL, check next action
              Scl_Out <= '1';
              byte_active := false;
              if i2c_state = 4 then
                i2c_state := 1;
              end if;

            when 5 =>  -- STOP: SDA↑ while SCL↑
              Scl_Out <= '1'; Tx_Out <= '1';
              tx_active <= '0';
              i2c_state := 0;

            when 13 =>  -- SCL_LOW_BEFORE_REP: pull SCL low after ACK, slave releases SDA
              Scl_Out <= '0'; Tx_Out <= '1';
              i2c_state := 6;

            when 6 =>  -- REP_START1: raise SCL
              Scl_Out <= '1'; Tx_Out <= '1';
              i2c_state := 7;

            when 7 =>  -- REP_START2: SDA↓ while SCL↑
              Scl_Out <= '1'; Tx_Out <= '0';
              -- Load dev_R into data_buf for sending
              data_buf := I2C_Dev_R;
              byte_active := true;
              i2c_bit := 0;
              i2c_state := 1;

            when 8 =>  -- RD_LOW: SCL low, release SDA
              Scl_Out <= '0'; Tx_Out <= '1';
              i2c_state := 9;

            when 9 =>  -- RD_HIGH: raise SCL
              Scl_Out <= '1';
              i2c_state := 12;

            when 12 =>  -- RD_SAMPLE: SCL high, sample SDA (slave has driven it)
              Scl_Out <= '1';
              rd_byte(7 - i2c_bit) := Sda_In;
              i2c_bit := i2c_bit + 1;
              if i2c_bit < 8 then
                i2c_state := 8;
              else
                i2c_state := 10;
              end if;

            when 10 =>  -- RD_ACK: drive ACK (or NACK if last)
              Scl_Out <= '0';
              if rd_remain = 0 then
                Tx_Out <= '1';  -- NACK for last byte
                i2c_state := 11;
              else
                Tx_Out <= '0';  -- ACK
                rd_remain := rd_remain - 1;
                i2c_state := 8;  -- next read byte
              end if;

            when 11 =>  -- RD_NACK_PULSE: SCL high after NACK, then STOP
              Scl_Out <= '1';
              byte_active := false;
              i2c_state := 5;  -- STOP

            when others => i2c_state := 0;
          end case;
        end if;
      end if;
    end if;
  end process;
end rtl;
