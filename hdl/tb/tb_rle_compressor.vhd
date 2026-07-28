library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;

-- Standalone lossless-roundtrip test for rle_compressor. A dedicated collector
-- process captures every comp_valid word (decoupled from feeding, so run-
-- boundary emits are never missed); the stim process feeds several 512-word
-- patterns respecting in_ready, flushes, then decodes the (count,value) stream
-- and asserts bit-exact reconstruction.
entity tb_rle_compressor is
end tb_rle_compressor;

architecture sim of tb_rle_compressor is
  constant NSAMP : integer := 512;
  type samp_arr is array(0 to NSAMP-1) of std_logic_vector(15 downto 0);
  type out_arr_t is array(0 to 2*NSAMP+8) of std_logic_vector(15 downto 0);

  signal clk : std_logic := '0';
  signal rst : std_logic := '0';
  signal sample_in : std_logic_vector(15 downto 0) := (others => '0');
  signal sample_valid : std_logic := '0';
  signal compression_enable : std_logic := '1';
  signal flush : std_logic := '0';
  signal comp_data : std_logic_vector(15 downto 0);
  signal comp_valid : std_logic;
  signal busy : std_logic;
  signal in_ready : std_logic;

  signal col_rst : std_logic := '0';
  signal col_mem : out_arr_t := (others => (others => '0'));
  signal col_n   : integer := 0;

  signal done : boolean := false;
begin
  dut : entity work.rle_compressor
    port map (clk => clk, rst => rst, sample_in => sample_in,
              sample_valid => sample_valid, compression_enable => compression_enable,
              flush => flush, comp_data => comp_data, comp_valid => comp_valid,
              busy => busy, in_ready => in_ready);

  clk_proc : process
  begin
    while not done loop
      clk <= '0'; wait for 5 ns;
      clk <= '1'; wait for 5 ns;
    end loop;
    wait;
  end process;

  -- Collector: append every emitted word.
  collector : process(clk)
  begin
    if rising_edge(clk) then
      if col_rst = '1' then
        col_n <= 0;
      elsif comp_valid = '1' and col_n <= 2*NSAMP+8 then
        col_mem(col_n) <= comp_data;
        col_n <= col_n + 1;
      end if;
    end if;
  end process;

  stim : process
    variable dec : samp_arr;
    variable dcount : integer;
    variable errs : integer := 0;

    procedure run_block(pat : in samp_arr; trace : in boolean := false) is
      variable i : integer;
      variable flushing : boolean;
      variable fed : boolean;
      variable trace_cycle : integer;
    begin
      -- reset DUT + collector
      rst <= '1'; sample_valid <= '0'; flush <= '0'; col_rst <= '1';
      wait until rising_edge(clk);
      rst <= '0'; col_rst <= '0';
      wait until falling_edge(clk);  -- first sample follows reset immediately
      i := 0; flushing := false; trace_cycle := 0;
      loop
        fed := false;
        if not flushing then
          if i < NSAMP then
            if in_ready = '1' then
              sample_in <= pat(i);
              sample_valid <= '1';
              fed := true;   -- capture at feed time; in_ready may flip at the edge
            else
              sample_valid <= '0';
            end if;
          else
            sample_valid <= '0';
            flushing := true;
            flush <= '1';
          end if;
        end if;
        wait until rising_edge(clk);
        wait for 0 ns;
        if trace and (i <= 4 or i >= NSAMP-3 or comp_valid = '1' or flush = '1') then
          report "trace cyc=" & integer'image(trace_cycle) &
                 " i=" & integer'image(i) &
                 " fed=" & boolean'image(fed) &
                 " sv=" & std_logic'image(sample_valid) &
                 " ready=" & std_logic'image(in_ready) &
                 " flush=" & std_logic'image(flush) &
                 " busy=" & std_logic'image(busy) &
                 " cvalid=" & std_logic'image(comp_valid) &
                 " cdata=" & integer'image(to_integer(unsigned(comp_data)));
        end if;
        if fed then
          i := i + 1;
        end if;
        trace_cycle := trace_cycle + 1;
        exit when flushing and busy = '0';
        wait until falling_edge(clk);
      end loop;
      flush <= '0';
      -- let the collector capture the final flush words
      wait until rising_edge(clk);
      wait until rising_edge(clk);
    end procedure;

    procedure decode(nw : in integer; ret : out samp_arr; ret_n : out integer) is
      variable k : integer := 0;
      variable si : integer := 0;
      variable c : integer;
    begin
      while k + 1 < nw loop
        c := to_integer(unsigned(col_mem(k)));
        for j in 0 to c - 1 loop
          if si < NSAMP then ret(si) := col_mem(k+1); si := si + 1; end if;
        end loop;
        k := k + 2;
      end loop;
      ret_n := si;
    end procedure;

    procedure check(name : string; pat : in samp_arr) is
      variable nw : integer;
    begin
      run_block(pat);
      nw := col_n;
      decode(nw, dec, dcount);
      if dcount /= NSAMP then
        report name & ": decoded " & integer'image(dcount) &
               " samples (expected " & integer'image(NSAMP) & "), out_words=" &
               integer'image(nw) severity error;
        errs := errs + 1;
      else
        for i in 0 to NSAMP-1 loop
          if dec(i) /= pat(i) then
            report name & ": mismatch at " & integer'image(i) &
                   " got " & integer'image(to_integer(unsigned(dec(i)))) &
                   " expected " & integer'image(to_integer(unsigned(pat(i))))
                   severity error;
            errs := errs + 1;
            exit;
          end if;
        end loop;
      end if;
      report name & ": out_words=" & integer'image(nw) &
             " (raw=" & integer'image(NSAMP) & ")  " &
             integer'image((NSAMP*100)/nw) & "% of raw / 100";
    end procedure;

    variable pat : samp_arr;
  begin
    wait for 20 ns;

    -- micro-test: feed A B B B (then idle) and dump the raw output words
    pat(0) := x"0011";
    pat(1) := x"0022"; pat(2) := x"0022"; pat(3) := x"0022";
    for i in 4 to NSAMP-1 loop pat(i) := x"0022"; end loop;
    run_block(pat, true);
    report "micro AB^: col_n=" & integer'image(col_n) &
      " w0=" & integer'image(to_integer(unsigned(col_mem(0)))) &
      " w1=" & integer'image(to_integer(unsigned(col_mem(1)))) &
      " w2=" & integer'image(to_integer(unsigned(col_mem(2)))) &
      " w3=" & integer'image(to_integer(unsigned(col_mem(3))));

    for i in 0 to NSAMP-1 loop pat(i) := x"1234"; end loop;
    check("all-idle", pat);

    for i in 0 to NSAMP-1 loop pat(i) := std_logic_vector(to_unsigned((i/128), 16)); end loop;
    check("four-runs", pat);

    for i in 0 to NSAMP-1 loop
      if (i mod 2) = 0 then pat(i) := x"AAAA"; else pat(i) := x"5555"; end if;
    end loop;
    check("alternating", pat);

    for i in 0 to NSAMP-1 loop pat(i) := std_logic_vector(to_unsigned(i, 16)); end loop;
    check("incompressible", pat);

    for i in 0 to NSAMP-1 loop
      if (i mod 2) = 0 then pat(i) := x"8001"; else pat(i) := x"7FFE"; end if;
    end loop;
    check("ch15-toggle", pat);

    -- single run of length 1 then idle (boundary case)
    pat(0) := x"DEAD";
    for i in 1 to NSAMP-1 loop pat(i) := x"BEEF"; end loop;
    check("one-then-idle", pat);

    if errs = 0 then
      report "=== TB PASSED ===" severity note;
    else
      report "=== TB FAILED (" & integer'image(errs) & " errors) ===" severity error;
    end if;
    done <= true;
    wait;
  end process;
end sim;
