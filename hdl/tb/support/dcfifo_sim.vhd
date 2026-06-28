-- Behavioral simulation model of the Altera dcfifo megafunction covering the
-- subset used by Fast_Logic_Analyzer_SDRAM (normal mode, lpm_showahead = "OFF").
--
-- Unlike a naive model, this one models the CROSS-DOMAIN POINTER SYNCHRONISER
-- LATENCY: the read side sees the write pointer only after rdsync_delaypipe
-- rdclk edges, and the write side sees the read pointer only after
-- wrsync_delaypipe wrclk edges. That latency is exactly what makes rdempty
-- deassert LATE on real hardware; an idealised (zero-latency) model lets the
-- write-pump producer present a fresh address before the popped data has
-- propagated, duplicating data into consecutive SDRAM addresses in simulation
-- only. Flags are therefore conservative (empty deasserts late, full asserts
-- early), matching silicon.
library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;

entity dcfifo is
  generic (
    lpm_width        : natural;
    lpm_widthu       : natural;
    lpm_numwords     : natural;
    lpm_showahead    : string := "OFF";
    lpm_type         : string := "dcfifo";
    rdsync_delaypipe : natural := 3;
    wrsync_delaypipe : natural := 3;
    intended_device_family : string := "MAX 10"
  );
  port (
    data     : in  std_logic_vector(lpm_width-1 downto 0);
    wrreq    : in  std_logic;
    wrclk    : in  std_logic;
    rdreq    : in  std_logic;
    rdclk    : in  std_logic;
    q        : out std_logic_vector(lpm_width-1 downto 0) := (others => '0');
    rdempty  : out std_logic := '1';
    wrfull   : out std_logic := '0';
    wrusedw  : out std_logic_vector(lpm_widthu-1 downto 0) := (others => '0');
    rdusedw  : out std_logic_vector(lpm_widthu-1 downto 0) := (others => '0')
  );
end dcfifo;

architecture sim of dcfifo is
  type mem_t is array (0 to lpm_numwords-1) of std_logic_vector(lpm_width-1 downto 0);
  signal mem : mem_t := (others => (others => '0'));

  signal wptr : natural := 0;   -- write domain
  signal rptr : natural := 0;   -- read domain

  -- Synchroniser pipelines (binary value through N FFs -> models LATENCY).
  type nat_pipe is array (natural range <>) of natural;
  signal wptr_to_rd : nat_pipe(0 to rdsync_delaypipe) := (others => 0);
  signal rptr_to_wr : nat_pipe(0 to wrsync_delaypipe) := (others => 0);

  signal wptr_seen_by_rd : natural := 0;  -- = wptr_to_rd(rdsync_delaypipe)
  signal rptr_seen_by_wr : natural := 0;  -- = rptr_to_wr(wrsync_delaypipe)
begin

  wptr_seen_by_rd <= wptr_to_rd(rdsync_delaypipe);
  rptr_seen_by_wr <= rptr_to_wr(wrsync_delaypipe);

  -- Write domain: write data, advance wptr; synchronise rptr in from read side.
  wr_side : process(wrclk)
  begin
    if rising_edge(wrclk) then
      if wrreq = '1' and (wptr - rptr_seen_by_wr) < lpm_numwords then
        mem(wptr mod lpm_numwords) <= data;
        wptr <= wptr + 1;
      end if;
      rptr_to_wr(0) <= rptr;
      for i in 1 to wrsync_delaypipe loop
        rptr_to_wr(i) <= rptr_to_wr(i-1);
      end loop;
    end if;
  end process;

  gen_sa_off : if lpm_showahead = "OFF" generate
    -- showahead OFF: q registered, updates on the rdclk edge that samples
    -- rdreq='1'. Only pop data the read side is allowed to see (synchronised
    -- write pointer), so a read never outruns the synchroniser.
    rd_side : process(rdclk)
    begin
      if rising_edge(rdclk) then
        if rdreq = '1' and rptr < wptr_seen_by_rd then
          q <= mem(rptr mod lpm_numwords);
          rptr <= rptr + 1;
        end if;
        wptr_to_rd(0) <= wptr;
        for i in 1 to rdsync_delaypipe loop
          wptr_to_rd(i) <= wptr_to_rd(i-1);
        end loop;
      end if;
    end process;
  end generate;

  gen_sa_on : if lpm_showahead /= "OFF" generate
    rd_ptr : process(rdclk)
    begin
      if rising_edge(rdclk) then
        if rdreq = '1' and rptr < wptr_seen_by_rd then
          rptr <= rptr + 1;
        end if;
        wptr_to_rd(0) <= wptr;
        for i in 1 to rdsync_delaypipe loop
          wptr_to_rd(i) <= wptr_to_rd(i-1);
        end loop;
      end if;
    end process;
    q <= mem(rptr mod lpm_numwords) when rptr < wptr_seen_by_rd
         else (others => '0');
  end generate;

  -- Conservative flags off the SYNCHRONISED pointers (late empty, early full).
  -- rdempty is ANTICIPATORY: a real dcfifo tracks the in-flight read so the flag
  -- reflects the post-pop level. Without this the level-held read-enable of the
  -- write pump advances its address while rdempty still shows data, duplicating
  -- words into consecutive addresses in simulation only.
  rdempty <= '1' when rptr >= wptr_seen_by_rd
                  or (rdreq = '1' and (rptr + 1) >= wptr_seen_by_rd)
             else '0';
  wrfull  <= '1' when (wptr - rptr_seen_by_wr) >= lpm_numwords else '0';
  wrusedw <= std_logic_vector(to_unsigned((wptr - rptr_seen_by_wr) mod 2**lpm_widthu, lpm_widthu));
  rdusedw <= std_logic_vector(to_unsigned((wptr - rptr_seen_by_wr) mod 2**lpm_widthu, lpm_widthu));

end sim;
