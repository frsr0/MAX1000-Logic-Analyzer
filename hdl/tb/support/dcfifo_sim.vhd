-- Behavioral simulation model of the Altera dcfifo megafunction, covering
-- the subset used by Fast_Logic_Analyzer_SDRAM (normal mode, lpm_showahead
-- = "OFF": q is registered and updates on the rdclk edge that samples
-- rdreq='1').
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
  -- pointers kept as unconstrained naturals; fill level derived
  signal wptr : natural := 0;
  signal rptr : natural := 0;
begin

  wr_side : process(wrclk)
  begin
    if rising_edge(wrclk) then
      if wrreq = '1' and (wptr - rptr) < lpm_numwords then
        mem(wptr mod lpm_numwords) <= data;
        wptr <= wptr + 1;
      end if;
    end if;
  end process;

  -- showahead = "OFF": q is registered and updates on the rdclk edge that
  -- samples rdreq='1' (the popped word appears one read later).
  gen_sa_off : if lpm_showahead = "OFF" generate
    rd_side : process(rdclk)
    begin
      if rising_edge(rdclk) then
        if rdreq = '1' and rptr < wptr then
          q <= mem(rptr mod lpm_numwords);
          rptr <= rptr + 1;
        end if;
      end if;
    end process;
  end generate;

  -- showahead = "ON" (first-word-fall-through): q always presents the current
  -- head word while the FIFO is non-empty; rdreq merely advances to the next.
  gen_sa_on : if lpm_showahead /= "OFF" generate
    rd_ptr : process(rdclk)
    begin
      if rising_edge(rdclk) then
        if rdreq = '1' and rptr < wptr then
          rptr <= rptr + 1;
        end if;
      end if;
    end process;
    q <= mem(rptr mod lpm_numwords) when rptr < wptr
         else (others => '0');
  end generate;

  -- Flags: ideal (no synchroniser latency). Real dcfifo flags lag by the
  -- delaypipe stages; the design tolerates later flag updates because they
  -- are conservative in the same direction (empty deasserts late, full
  -- asserts early is NOT modelled — acceptable for functional simulation).
  rdempty <= '1' when rptr >= wptr else '0';
  wrfull  <= '1' when (wptr - rptr) >= lpm_numwords else '0';
  wrusedw <= std_logic_vector(to_unsigned((wptr - rptr) mod 2**lpm_widthu, lpm_widthu));
  rdusedw <= std_logic_vector(to_unsigned((wptr - rptr) mod 2**lpm_widthu, lpm_widthu));

end sim;
