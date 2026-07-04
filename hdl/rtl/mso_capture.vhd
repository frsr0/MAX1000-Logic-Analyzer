library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;

-- mso_capture : mixed-signal bit-packing capture front end (parallel mode)
-- ---------------------------------------------------------------------------
-- Self-contained wrapper that ties the compression pipeline together and
-- presents ONE 16-bit valid/ready word stream to be muxed onto the capture
-- write dcfifo. It is an additive, mode-selectable path: it does not touch the
-- existing 128-bit Analog_Frame datapath.
--
--   4x ADC results (adc_clk / sys_clk domain, one valid pulse per conversion)
--        -> CDC into fast_clk -> delta_calc -> analog_packer  (bit15=0 words:
--           4 verbatim anchors + 12 packed deltas per analog frame)
--   16 raw digital pins -> 2-FF sync (fast_clk) -> digital_rle (bit15=1 words)
--        -> mso_stream_mux -> out_data / out_valid  (fast_clk)
--
-- Clock domains
-- -------------
-- The whole pipeline (delta_calc/analog_packer/digital_rle/mso_stream_mux)
-- runs in fast_clk, the same domain that writes the async capture FIFO, so its
-- output needs no further crossing. Only two things cross domains:
--   * ADC samples: adc_clk -> fast_clk. The ADC round-robin emits one sample
--     every conversion (microseconds apart, >> fast_clk period), so a classic
--     toggle handshake is safe: the adc_clk side latches {channel, data} and
--     flips a toggle; the fast_clk side 2-FF-synchronises the toggle and, on
--     its edge, registers the (long-stable) data as one sample_valid pulse.
--     Data is a quasi-static multi-bit bus gated by the synchronised flag --
--     no bus-skew hazard because it settled many cycles before the toggle.
--   * digital pins: asynchronous inputs 2-FF-synchronised into fast_clk (a
--     logic analyser samples the pins at the capture clock).
--
-- Synchronous resets within each domain; adc_clk reset is 2-FF-synchronised in.
entity mso_capture is
  port (
    fast_clk      : in  std_logic;                      -- pipeline + FIFO write clock (200.4 MHz)
    adc_clk       : in  std_logic;                      -- ADC result domain (sys_clk)
    rst           : in  std_logic;                      -- synchronous (fast_clk), active high

    -- ADC results (adc_clk domain), one valid pulse each per conversion
    adc_ch0       : in  std_logic_vector(11 downto 0);
    adc_ch0_valid : in  std_logic;
    adc_ch1       : in  std_logic_vector(11 downto 0);
    adc_ch1_valid : in  std_logic;
    adc_ch2       : in  std_logic_vector(11 downto 0);
    adc_ch2_valid : in  std_logic;
    adc_ch3       : in  std_logic_vector(11 downto 0);
    adc_ch3_valid : in  std_logic;

    -- Raw digital channels (asynchronous pins)
    digital_in    : in  std_logic_vector(15 downto 0);

    -- Unified 16-bit output to the write-FIFO arbiter mux (fast_clk)
    out_data      : out std_logic_vector(15 downto 0);
    out_valid     : out std_logic;
    out_ready     : in  std_logic := '1';               -- '1' when FIFO can accept
    dig_overflow  : out std_logic                       -- digital_rle lost-segment flag
  );
end mso_capture;

architecture rtl of mso_capture is
  -- adc_clk-domain sample capture + crossing toggle
  signal cap_data : std_logic_vector(11 downto 0) := (others => '0');
  signal cap_ch   : std_logic_vector(1 downto 0)  := (others => '0');
  signal adc_tgl  : std_logic := '0';

  -- fast_clk-domain toggle synchroniser + edge -> sample strobe
  signal tgl_meta, tgl_s1, tgl_s2 : std_logic := '0';
  signal smp_data  : std_logic_vector(11 downto 0) := (others => '0');
  signal smp_ch    : std_logic_vector(1 downto 0)  := (others => '0');
  signal smp_valid : std_logic := '0';

  -- digital 2-FF synchroniser
  signal dig_meta, dig_sync : std_logic_vector(15 downto 0) := (others => '0');

  -- pipeline interconnect
  signal d_out  : std_logic_vector(10 downto 0);
  signal d_v    : std_logic;
  signal a0, a1, a2, a3 : std_logic_vector(11 downto 0);
  signal a_v    : std_logic;
  signal b_w    : std_logic_vector(3 downto 0);
  signal b_d    : std_logic;
  signal a_data : std_logic_vector(15 downto 0);
  signal a_valid, a_ready : std_logic;
  signal g_data : std_logic_vector(15 downto 0);
  signal g_valid, g_ready : std_logic;
  signal mux_full : std_logic;
begin

  ----------------------------------------------------------------------------
  -- ADC domain: serialise the 4 channels' valid pulses into one crossing.
  -- The round-robin issues at most one ch_valid per cycle; priority guards a
  -- coincident case defensively. No reset gating: adc_tgl powers up '0' (as do
  -- the fast-side sync FFs) so no spurious startup edge, and the fast-side
  -- reset trick resynchronises the toggle after any mid-run reset. Gating this
  -- on a reset synchronised INTO the adc domain would drop the first sample
  -- after reset release (its 2-cycle recovery window).
  ----------------------------------------------------------------------------
  process(adc_clk)
  begin
    if rising_edge(adc_clk) then
      if adc_ch0_valid = '1' then
        cap_data <= adc_ch0; cap_ch <= "00"; adc_tgl <= not adc_tgl;
      elsif adc_ch1_valid = '1' then
        cap_data <= adc_ch1; cap_ch <= "01"; adc_tgl <= not adc_tgl;
      elsif adc_ch2_valid = '1' then
        cap_data <= adc_ch2; cap_ch <= "10"; adc_tgl <= not adc_tgl;
      elsif adc_ch3_valid = '1' then
        cap_data <= adc_ch3; cap_ch <= "11"; adc_tgl <= not adc_tgl;
      end if;
    end if;
  end process;

  ----------------------------------------------------------------------------
  -- fast_clk domain: synchronise the toggle and digital pins; form the sample
  -- strobe on a toggle edge (cap_data/cap_ch are long-stable by then).
  ----------------------------------------------------------------------------
  process(fast_clk)
  begin
    if rising_edge(fast_clk) then
      -- 2-FF synchronisers
      tgl_meta <= adc_tgl;  tgl_s1 <= tgl_meta;  tgl_s2 <= tgl_s1;
      dig_meta <= digital_in; dig_sync <= dig_meta;

      if rst = '1' then
        smp_valid <= '0';
        tgl_s2    <= tgl_s1;   -- avoid a spurious edge out of reset
      else
        if (tgl_s1 xor tgl_s2) = '1' then
          smp_data  <= cap_data;
          smp_ch    <= cap_ch;
          smp_valid <= '1';
        else
          smp_valid <= '0';
        end if;
      end if;
    end if;
  end process;

  ----------------------------------------------------------------------------
  -- Compression pipeline (all fast_clk)
  ----------------------------------------------------------------------------
  u_delta : entity work.delta_calc
    port map(clk => fast_clk, rst => rst, clk_en => '1',
             sample_in => smp_data, sample_ch => smp_ch, sample_valid => smp_valid,
             anchor_ch0 => a0, anchor_ch1 => a1, anchor_ch2 => a2, anchor_ch3 => a3,
             anchor_valid => a_v,
             delta_out => d_out, delta_valid => d_v,
             block_width => b_w, block_done => b_d);

  u_pack : entity work.analog_packer
    port map(clk => fast_clk, rst => rst, clk_en => '1',
             delta_in => d_out, delta_valid => d_v,
             anchor_ch0 => a0, anchor_ch1 => a1, anchor_ch2 => a2, anchor_ch3 => a3,
             anchor_valid => a_v,
             block_width => b_w, block_done => b_d,
             out_data => a_data, out_valid => a_valid, out_ready => a_ready,
             busy => open, in_ready => open);

  u_drle : entity work.digital_rle
    port map(clk => fast_clk, rst => rst, clk_en => '1',
             digital_in => dig_sync,
             packet_out => g_data, packet_valid => g_valid, packet_ready => g_ready,
             overflow => dig_overflow);

  mux_full <= not out_ready;
  u_mux : entity work.mso_stream_mux
    port map(clk => fast_clk, rst => rst,
             analog_data => a_data, analog_valid => a_valid, analog_ready => a_ready,
             digital_data => g_data, digital_valid => g_valid, digital_ready => g_ready,
             fifo_wdata => out_data, fifo_wreq => out_valid, fifo_wfull => mux_full);

end rtl;
