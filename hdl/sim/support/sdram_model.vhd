library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;

entity SDRAM_Model is
  generic (
    ADDR_WIDTH  : natural := 22;  -- 4M words
    COL_WIDTH   : natural := 8;
    ROW_WIDTH   : natural := 12;
    BA_WIDTH    : natural := 2;
    T_RCD_CYCLES : natural := 2;
    T_RP_CYCLES  : natural := 2;
    T_RFC_CYCLES : natural := 7;
    CL_CYCLES    : natural := 2   -- CAS latency
  );
  port (
    clk        : in  std_logic;
    addr       : in  std_logic_vector(ADDR_WIDTH-1 downto 0);
    wr_en      : in  std_logic;
    wr_data    : in  std_logic_vector(15 downto 0);
    burst      : in  std_logic;
    rd_en      : in  std_logic;
    rd_data    : out std_logic_vector(15 downto 0) := (others => '0');
    rd_valid   : out std_logic := '0';
    busy       : out std_logic := '0';
    idle       : out std_logic := '0'
  );
end SDRAM_Model;

architecture behavioral of SDRAM_Model is
  type mem_t is array (0 to (2**ADDR_WIDTH)-1) of std_logic_vector(15 downto 0);
  signal mem : mem_t := (others => (others => '0'));

  type state_t is (ST_INIT, ST_IDLE, ST_ACT, ST_RD, ST_RD_DATA, ST_WR);
  signal state : state_t := ST_INIT;

  signal init_cnt : natural := 0;
  signal cl_cnt   : natural := 0;
  signal rd_pend  : std_logic := '0';
  signal rd_addr  : natural := 0;

  signal wr_burst_cnt : natural := 0;
  signal burst_active : std_logic := '0';
  signal burst_addr   : natural := 0;
  signal burst_wip    : std_logic := '0';

begin

  process(clk)
    variable waddr : natural;
    variable raddr : natural;
  begin
    if rising_edge(clk) then
      rd_valid <= '0';

      case state is
        when ST_INIT =>
          busy <= '1';
          idle <= '0';
          if init_cnt < 100 then
            init_cnt <= init_cnt + 1;
          else
            init_cnt <= 0;
            state <= ST_IDLE;
          end if;

        when ST_IDLE =>
          busy <= '0';
          idle <= '1';

          if wr_en = '1' then
            busy <= '1';
            idle <= '0';
            if burst = '1' then
              burst_addr <= to_integer(unsigned(addr));
              wr_burst_cnt <= 1;
              burst_active <= '1';
              burst_wip <= '1';
              state <= ST_WR;
            else
              state <= ST_ACT;
            end if;
          elsif rd_en = '1' then
            busy <= '1';
            idle <= '0';
            state <= ST_ACT;
          end if;

        when ST_ACT =>
          if burst_wip = '1' then
            state <= ST_WR;
          else
            if rd_en = '1' or rd_pend = '1' then
              state <= ST_RD;
            elsif wr_en = '1' then
              state <= ST_WR;
            end if;
          end if;

        when ST_WR =>
          if burst_active = '1' and wr_burst_cnt < 4 then
            waddr := burst_addr + wr_burst_cnt;
            mem(waddr) <= wr_data;
            wr_burst_cnt <= wr_burst_cnt + 1;
            state <= ST_WR;
          elsif burst_active = '1' then
            burst_active <= '0';
            burst_wip <= '0';
            wr_burst_cnt <= 0;
            state <= ST_IDLE;
          else
            waddr := to_integer(unsigned(addr));
            mem(waddr) <= wr_data;
            state <= ST_IDLE;
          end if;

        when ST_RD =>
          if cl_cnt < CL_CYCLES then
            cl_cnt <= cl_cnt + 1;
            state <= ST_RD;
          else
            cl_cnt <= 0;
            state <= ST_RD_DATA;
          end if;

        when ST_RD_DATA =>
          raddr := to_integer(unsigned(addr));
          rd_data <= mem(raddr);
          rd_valid <= '1';
          state <= ST_IDLE;

      end case;
    end if;
  end process;

end behavioral;
