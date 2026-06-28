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
    CL        : natural := 2;
    -- STRICT: enforce JEDEC bank-active semantics. A bank must be ACTIVATED and
    -- not since PRECHARGEd / AUTO-REFRESHed for a WRITE or READ to commit. A
    -- command to a closed bank is IGNORED (write lost / read returns idle DQ) and
    -- REPORTED -- this reproduces the real-silicon "never written = 0xFFFF" drop
    -- that the lax model (PRECHARGE/REFRESH ignored, write always commits to the
    -- last-activated row) hides. Default false keeps existing testbenches intact.
    STRICT    : boolean := false
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
    variable active   : boolean_vector(0 to 3) := (others => false);
    variable tact     : row_arr := (others => 0);  -- cycles since ACTIVATE (tRCD)
    variable cmd : std_logic_vector(2 downto 0);
    variable bank : natural;
    variable col  : natural;
    variable widx : natural;
    -- Read data is driven onto DQ for a short window aligned to when the
    -- controller samples. SDRAM_Controller_Custom asserts CAS in ST_RD, waits
    -- one ST_CL_WAIT cycle, then samples sdram_dq in ST_RD_DATA (CAS latency
    -- 2). The model observes the registered CAS one delta-cycle late, so it
    -- drives starting the cycle after it sees the READ and holds two cycles
    -- to robustly cover the sample.
    variable rd_data  : std_logic_vector(15 downto 0) := (others => '0');
    variable rd_drive : natural range 0 to CL + 1 := 0;
  begin
    if rising_edge(clk) then
      -- drive DQ during the read window, tristate otherwise
      if rd_drive > 0 then
        dq <= rd_data;
        rd_drive := rd_drive - 1;
      else
        dq <= (others => 'Z');
      end if;

      if cke = '1' and cs_n = '0' then
        cmd := ras_n & cas_n & we_n;
        bank := to_integer(unsigned(ba));
        -- JEDEC SDR command truth table for cmd = RAS & CAS & WE (CS=0):
        --   "011" ACTIVATE, "100" WRITE, "101" READ, "010" PRECHARGE
        case cmd is
          when "011" =>  -- ACTIVATE
            open_row(bank) := to_integer(unsigned(addr(ROW_WIDTH-1 downto 0)));
            active(bank) := true;
            tact(bank) := 0;
          when "100" =>  -- WRITE
            col := to_integer(unsigned(addr(COL_WIDTH-1 downto 0)));
            widx := (bank * (2**ROW_WIDTH) + open_row(bank)) * (2**COL_WIDTH) + col;
            if STRICT and not active(bank) then
              report "SDRAM STRICT: WRITE to CLOSED bank " & integer'image(bank) &
                     " col " & integer'image(col) & " DROPPED (cell stays idle)"
                     severity warning;
            elsif dqm = "00" then
              mem(widx) := dq;
            end if;
          when "101" =>  -- READ: schedule the DQ drive window
            col := to_integer(unsigned(addr(COL_WIDTH-1 downto 0)));
            widx := (bank * (2**ROW_WIDTH) + open_row(bank)) * (2**COL_WIDTH) + col;
            if STRICT and not active(bank) then
              report "SDRAM STRICT: READ from CLOSED bank " & integer'image(bank) &
                     " col " & integer'image(col) severity warning;
              rd_data := (others => '1');  -- idle/undriven bus reads as 0xFFFF
            else
              rd_data := mem(widx);
            end if;
            rd_drive := CL + 1;  -- drive a window that covers the CL-cycle sample
          when "010" =>  -- PRECHARGE: close the addressed bank (A10=1 -> all)
            if addr(10) = '1' then
              active := (others => false);
            else
              active(bank) := false;
            end if;
          when "001" =>  -- AUTO REFRESH: all banks must be precharged; closes all
            if STRICT and (active(0) or active(1) or active(2) or active(3)) then
              report "SDRAM STRICT: AUTO-REFRESH with an OPEN bank (illegal)"
                     severity warning;
            end if;
            active := (others => false);
          when others =>  -- LOAD MODE / NOP: ignore
            null;
        end case;
      end if;
    end if;
  end process;

end sim;
