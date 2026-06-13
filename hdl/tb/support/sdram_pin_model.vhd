-- Minimal pin-level SDR SDRAM model (single-word access, CAS latency 2),
-- sufficient for SDRAM_Controller_Custom in simulation: LOAD MODE / REFRESH /
-- PRECHARGE are accepted and ignored; ACTIVATE latches the row per bank;
-- WRITE stores on the command cycle; READ drives DQ CL cycles later.
library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;

entity sdram_pin_model is
  generic (
    ROW_WIDTH : natural := 12;
    COL_WIDTH : natural := 8;
    CL        : natural := 2
  );
  port (
    clk   : in    std_logic;
    cke   : in    std_logic;
    cs_n  : in    std_logic;
    ras_n : in    std_logic;
    cas_n : in    std_logic;
    we_n  : in    std_logic;
    ba    : in    std_logic_vector(1 downto 0);
    addr  : in    std_logic_vector(11 downto 0);
    dqm   : in    std_logic_vector(1 downto 0);
    dq    : inout std_logic_vector(15 downto 0)
  );
end sdram_pin_model;

architecture sim of sdram_pin_model is
  -- 4 banks x 4096 rows x 256 cols = 4M words
  type mem_t is array (natural range <>) of std_logic_vector(15 downto 0);
  constant MEM_WORDS : natural := 4 * (2**ROW_WIDTH) * (2**COL_WIDTH);

  type row_arr is array (0 to 3) of natural;
begin

  main : process(clk)
    variable mem : mem_t(0 to MEM_WORDS-1) := (others => x"DEAD");
    variable open_row : row_arr := (others => 0);
    variable cmd : std_logic_vector(2 downto 0);
    variable bank : natural;
    variable col  : natural;
    variable widx : natural;
    -- read pipeline (CL stages)
    type rd_pipe_t is array (0 to CL-1) of std_logic_vector(15 downto 0);
    type rd_val_t  is array (0 to CL-1) of boolean;
    variable rp  : rd_pipe_t := (others => (others => '0'));
    variable rpv : rd_val_t := (others => false);
  begin
    if rising_edge(clk) then
      -- shift the read pipeline and drive/tristate DQ
      if rpv(CL-1) then
        dq <= rp(CL-1);
      else
        dq <= (others => 'Z');
      end if;
      for i in CL-1 downto 1 loop
        rp(i) := rp(i-1);
        rpv(i) := rpv(i-1);
      end loop;
      rpv(0) := false;

      if cke = '1' and cs_n = '0' then
        cmd := ras_n & cas_n & we_n;
        bank := to_integer(unsigned(ba));
        case cmd is
          when "011" =>  -- ACTIVATE
            open_row(bank) := to_integer(unsigned(addr(ROW_WIDTH-1 downto 0)));
          when "101" =>  -- WRITE
            col := to_integer(unsigned(addr(COL_WIDTH-1 downto 0)));
            widx := (bank * (2**ROW_WIDTH) + open_row(bank)) * (2**COL_WIDTH) + col;
            if dqm = "00" then
              mem(widx) := dq;
            end if;
          when "100" =>  -- READ (data after CL)
            col := to_integer(unsigned(addr(COL_WIDTH-1 downto 0)));
            widx := (bank * (2**ROW_WIDTH) + open_row(bank)) * (2**COL_WIDTH) + col;
            rp(0) := mem(widx);
            rpv(0) := true;
          when others =>  -- LOAD MODE / REFRESH / PRECHARGE / NOP: ignore
            null;
        end case;
      end if;
    end if;
  end process;

end sim;
