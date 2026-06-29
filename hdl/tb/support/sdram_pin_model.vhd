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

  -- Command counters (readable via external name) to expose row efficiency:
  -- ACTIVATE-per-WRITE ~1 means the row is NOT staying open (page-mode broken).
  signal n_act : natural := 0;
  signal n_wr  : natural := 0;
  signal n_pre : natural := 0;

  -- JEDEC inter-command minimum timing (cycles @ ~167 MHz / 6 ns, W9864G6-6 grade).
  -- The controller only enforces tRCD/tWR/tRP/tRFC; tRAS (ACT->PRE) and tRC
  -- (ACT->ACT same bank) are NOT enforced in RTL. If page-mode is not effective
  -- (ACT->write->PRE per sample) these get violated, and a real SDRAM stalls to
  -- honour them -- the throughput hit the permissive model hides.
  constant TRCD : natural := 3;   -- ACT -> READ/WRITE
  constant TRP  : natural := 3;   -- PRE -> ACT (same bank)
  constant TRAS : natural := 7;   -- ACT -> PRE (same bank)
  constant TRC  : natural := 10;  -- ACT -> ACT (same bank)
  constant TWR  : natural := 2;   -- WRITE -> PRE (same bank)
  constant TRFC : natural := 10;  -- REFRESH -> ACT
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
    -- Inter-command timing tracking (STRICT only): free-running cycle counter and
    -- the cycle of the last command of each type per bank.
    variable now_cyc  : natural := 0;
    variable t_act    : row_arr := (others => 0);   -- last ACTIVATE per bank
    variable t_pre    : row_arr := (others => 0);   -- last PRECHARGE per bank
    variable t_wr_b   : row_arr := (others => 0);   -- last WRITE per bank
    variable t_ref    : integer := -1000;           -- last REFRESH (any)
    variable t_act_any: integer := -1000;           -- last ACTIVATE (any bank)
    variable viol     : natural := 0;
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
      now_cyc := now_cyc + 1;
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
            if STRICT then
              if now_cyc - t_pre(bank) < TRP then
                report "SDRAM tRP VIOLATION: ACT bank " & integer'image(bank) &
                  " only " & integer'image(now_cyc - t_pre(bank)) & " cyc after PRE (need " &
                  integer'image(TRP) & ")" severity warning; viol := viol + 1;
              end if;
              if now_cyc - t_act(bank) < TRC then
                report "SDRAM tRC VIOLATION: ACT-ACT bank " & integer'image(bank) &
                  " gap " & integer'image(now_cyc - t_act(bank)) & " cyc (need " &
                  integer'image(TRC) & ")" severity warning; viol := viol + 1;
              end if;
              if t_ref >= 0 and now_cyc - t_ref < TRFC then
                report "SDRAM tRFC VIOLATION: ACT only " & integer'image(now_cyc - t_ref) &
                  " cyc after REFRESH (need " & integer'image(TRFC) & ")" severity warning;
                viol := viol + 1;
              end if;
            end if;
            open_row(bank) := to_integer(unsigned(addr(ROW_WIDTH-1 downto 0)));
            active(bank) := true;
            tact(bank) := 0;
            t_act(bank) := now_cyc; t_act_any := now_cyc;
            n_act <= n_act + 1;
          when "100" =>  -- WRITE
            col := to_integer(unsigned(addr(COL_WIDTH-1 downto 0)));
            widx := (bank * (2**ROW_WIDTH) + open_row(bank)) * (2**COL_WIDTH) + col;
            if STRICT and now_cyc - t_act(bank) < TRCD and active(bank) then
              report "SDRAM tRCD VIOLATION: WRITE bank " & integer'image(bank) &
                " only " & integer'image(now_cyc - t_act(bank)) & " cyc after ACT (need " &
                integer'image(TRCD) & ")" severity warning; viol := viol + 1;
            end if;
            t_wr_b(bank) := now_cyc;
            n_wr <= n_wr + 1;
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
            if STRICT then
              for b in 0 to 3 loop
                if (addr(10) = '1' or b = bank) and active(b) then
                  if now_cyc - t_act(b) < TRAS then
                    report "SDRAM tRAS VIOLATION: PRE bank " & integer'image(b) &
                      " only " & integer'image(now_cyc - t_act(b)) & " cyc after ACT (need " &
                      integer'image(TRAS) & ") -- page-mode not held open" severity warning;
                    viol := viol + 1;
                  end if;
                  if now_cyc - t_wr_b(b) < TWR then
                    report "SDRAM tWR VIOLATION: PRE bank " & integer'image(b) &
                      " only " & integer'image(now_cyc - t_wr_b(b)) & " cyc after WRITE (need " &
                      integer'image(TWR) & ")" severity warning; viol := viol + 1;
                  end if;
                  t_pre(b) := now_cyc;
                end if;
              end loop;
            end if;
            n_pre <= n_pre + 1;
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
            t_ref := now_cyc;
            active := (others => false);
          when others =>  -- LOAD MODE / NOP: ignore
            null;
        end case;
      end if;
    end if;
  end process;

end sim;
