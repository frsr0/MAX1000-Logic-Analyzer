library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;

-- analog_packer : anchor+delta bit-packer for the analog stream
-- ---------------------------------------------------------------------------
-- Back end of the analog compression pipeline. It buffers one block of
-- BLOCK_SAMPLES (12) signed 11-bit deltas plus 4 verbatim channel anchors
-- from delta_calc, then serialises them into the fixed 16-bit output word
-- stream using the "Analog Packed Block Frame" format (bit 15 = '0' on every
-- word):
--
--   Word 0   (Header) : bit15='0', bits[14:11] = 4-bit width index W
--                        (bits per packed delta, 0..11),
--                        bit10 = 1 => 4 inline verbatim anchors follow,
--                        bits[9:0] = reserved (0).
--   Word 1..4 (Anchor): bit15='0', bits[11:0] = channel 0..3 first sample.
--   Word 5..N (Payload): bit15='0', bits[14:0] = 12 signed W-bit deltas
--                        packed contiguously LSB-first into 15-bit slots.
--
-- The 4-bit width index spans the full 1..11-bit range delta_calc can request,
-- so every emitted delta round-trips bit-exact. W = 0 (a perfectly flat tail
-- after the anchors) emits header + anchors only.
--
-- Structure (tuned for the 200.4 MHz FAST_CLK domain)
-- ---------------------------------------------------
-- A silicon-accurate fit on the 10M08 (C8) showed the single-cycle pack path
-- overran ~8 ns. Two things caused it and both are removed here:
--   * the sample buffer is captured linearly during FILL and replayed by index
--     during packing, avoiding both a large per-cycle shift network and any
--     synchronous-read RAM latency, and
--   * the per-sample mask (2^W - 1) is precomputed once per block, so only ONE
--     barrel shift (the chunk placement) is ever on a datapath.
-- Packing is a 3-stage micro-sequence per sample:
--   LOAD : chunk = buf(TOP) AND mask
--   SHIFT: chunk_shift = chunk << held
--   ACC  : acc = acc OR chunk_shift; emit low 15 bits when >= 15 queued
-- so each cycle carries only one heavy transform. Three cycles/sample is still
-- immaterial: blocks are spaced far apart at ADC sample rates, and in_ready
-- holds the (slow) feeder off during a pack.
--
-- Output handshake: standard valid/ready. out_valid holds a stable out_data
-- until out_ready; the engine only advances when the slot is free
-- (out_valid='0' or out_ready='1'), so a full FIFO stalls packing without ever
-- dropping a word. in_ready is high only in FILL. Synchronous reset, single
-- clock enable, registered outputs, no combinational loops.
entity analog_packer is
  generic (
    BLOCK_SAMPLES : positive := 12;  -- deltas per block (matches delta_calc)
    MAX_WIDTH     : positive := 11   -- >= 11 for lossless (4-bit header holds 0..15)
  );
  port (
    clk         : in  std_logic;
    rst         : in  std_logic;                     -- synchronous, active high
    clk_en      : in  std_logic := '1';              -- global clock enable

    -- Delta input (from delta_calc)
    delta_in    : in  std_logic_vector(10 downto 0); -- signed 11-bit
    delta_valid : in  std_logic;                     -- delta_in valid
    anchor_ch0  : in  std_logic_vector(11 downto 0);
    anchor_ch1  : in  std_logic_vector(11 downto 0);
    anchor_ch2  : in  std_logic_vector(11 downto 0);
    anchor_ch3  : in  std_logic_vector(11 downto 0);
    anchor_valid : in std_logic;                     -- anchors valid with block_done
    block_width : in  std_logic_vector(3 downto 0);  -- max bits/sample for the block
    block_done  : in  std_logic;                     -- pulses with the block's last delta

    -- 16-bit output word stream (valid/ready)
    out_data    : out std_logic_vector(15 downto 0) := (others => '0');
    out_valid   : out std_logic := '0';
    out_ready   : in  std_logic := '1';

    busy        : out std_logic := '0';              -- packing a block
    in_ready    : out std_logic := '1'               -- can accept a delta (FILL only)
  );
end analog_packer;

architecture rtl of analog_packer is

  -- Accumulator holds up to 14 residual bits + one MAX_WIDTH chunk before emit.
  constant ACC_W : positive := 15 + MAX_WIDTH;

  type state_t is (FILL, EMIT_HEADER, EMIT_ANCHOR_LOAD, EMIT_ANCHOR,
                   PACK_LOAD, PACK_MASK, PACK_SHIFT, PACK_ACC, DRAIN);
  signal state : state_t := FILL;

  type buf_array is array(0 to BLOCK_SAMPLES-1) of std_logic_vector(10 downto 0);
  signal buf   : buf_array := (others => (others => '0'));
  -- FILL and PACK are disjoint states: the buffer is never read and written
  -- on the same cycle.  Tell Quartus it may use the native M9K behaviour
  -- instead of adding read-during-write bypass muxes around this tiny RAM.
  attribute ramstyle : string;
  attribute ramstyle of buf : signal is "M9K, no_rw_check";
  type anchor_array is array(0 to 3) of std_logic_vector(11 downto 0);

  signal w_lat    : unsigned(3 downto 0) := (others => '0');           -- latched block width
  signal mask_lat : unsigned(MAX_WIDTH-1 downto 0) := (others => '0'); -- precomputed 2^W-1
  signal anc_lat  : anchor_array := (others => (others => '0'));
  signal acc      : unsigned(ACC_W-1 downto 0) := (others => '0');
  signal held     : natural range 0 to 14 := 0;                        -- bits queued in acc
  signal pcount   : natural range 0 to BLOCK_SAMPLES := 0;             -- reused across phases

  -- LOAD -> SHIFT -> ACC pipeline registers
  signal sample_r : unsigned(10 downto 0) := (others => '0'); -- registered buffer read
  signal chunk_r  : unsigned(MAX_WIDTH-1 downto 0) := (others => '0'); -- masked delta
  signal hs_r     : natural range 0 to 14 := 0;                       -- shift amount for it
  signal emit_r   : std_logic := '0';                                 -- this sample fills a word
  signal chunk_shift_r : unsigned(ACC_W-1 downto 0) := (others => '0');
  signal anchor_word_r : std_logic_vector(15 downto 0) := (others => '0');

  signal out_valid_r : std_logic := '0';
  signal pending_data_r  : std_logic_vector(15 downto 0) := (others => '0');
  signal pending_valid_r : std_logic := '0';

begin

  busy      <= '0' when state = FILL else '1';
  in_ready  <= '1' when state = FILL else '0';
  out_valid <= out_valid_r;

  process(clk)
    variable m12  : unsigned(11 downto 0);
    variable wi   : integer range 0 to MAX_WIDTH;
    variable nacc : unsigned(ACC_W-1 downto 0);
  begin
    if rising_edge(clk) then
      if rst = '1' then
        state     <= FILL;
        held      <= 0;
        pcount    <= 0;
        acc       <= (others => '0');
        out_valid_r <= '0';
        pending_valid_r <= '0';
      elsif clk_en = '1' then
        -- Move pending data into the external output only when that register
        -- is empty.  Deliberately do not refill on the same edge as a ready
        -- acceptance: this keeps downstream backpressure off the output-data
        -- timing path while the pending slot absorbs the bubble.
        if out_valid_r = '1' and out_ready = '1' then
          out_valid_r <= '0';
        elsif out_valid_r = '0' and pending_valid_r = '1' then
          out_data <= pending_data_r;
          out_valid_r <= '1';
          pending_valid_r <= '0';
        end if;

        case state is

          -- FILL: shift each delta into the buffer. block_done arrives with the
          -- last (BLOCK_SAMPLES-th) delta, which is buffered at pcount.
          when FILL =>
            if delta_valid = '1' then
              buf(pcount) <= delta_in;
              if block_done = '1' and anchor_valid = '1' then
                assert unsigned(block_width) <= MAX_WIDTH
                  report "analog_packer: block_width exceeds MAX_WIDTH"
                  severity error;
                assert pcount = BLOCK_SAMPLES - 1
                  report "analog_packer: block_done before BLOCK_SAMPLES buffered"
                  severity error;
                w_lat  <= unsigned(block_width);
                anc_lat(0) <= anchor_ch0;
                anc_lat(1) <= anchor_ch1;
                anc_lat(2) <= anchor_ch2;
                anc_lat(3) <= anchor_ch3;
                held   <= 0;
                pcount <= 0;
                acc    <= (others => '0');
                state  <= EMIT_HEADER;
              elsif pcount < BLOCK_SAMPLES - 1 then
                pcount <= pcount + 1;
              end if;
            end if;

          -- EMIT_HEADER: present the header and precompute the block mask
          -- (2^W - 1) once, off the per-sample datapath.
          when EMIT_HEADER =>
            if pending_valid_r = '0' then
              pending_data_r <= '0' & std_logic_vector(w_lat) & '1' & "0000000000";
              pending_valid_r <= '1';
              m12 := shift_left(to_unsigned(1, 12), to_integer(w_lat)) - 1;
              mask_lat <= m12(MAX_WIDTH-1 downto 0);
              state <= EMIT_ANCHOR_LOAD;
            end if;

          -- Select the next anchor in its own cycle.  Keeping the variable
          -- anc_lat(pcount) mux off the output-register edge removes the
          -- pcount -> out_data timing path in the 200 MHz FAST_CLK domain.
          when EMIT_ANCHOR_LOAD =>
            anchor_word_r <= "0000" & anc_lat(pcount);
            state <= EMIT_ANCHOR;

          when EMIT_ANCHOR =>
            if pending_valid_r = '0' then
              pending_data_r <= anchor_word_r;
              pending_valid_r <= '1';
              if pcount = 3 then
                pcount <= 0;
                if w_lat = 0 then
                  state <= FILL;             -- flat block: anchors only after header
                else
                  state <= PACK_LOAD;
                end if;
              else
                pcount <= pcount + 1;
                state <= EMIT_ANCHOR_LOAD;
              end if;
            end if;

          -- PACK_LOAD: issue the sample-buffer read and register its result.
          -- Keeping the inferred M9K read on its own edge prevents the RAM
          -- output plus mask cone from landing on chunk_r in one fast_clk cycle.
          when PACK_LOAD =>
            wi      := to_integer(w_lat);
            sample_r <= unsigned(buf(pcount));
            hs_r    <= held;
            if held + wi >= 15 then
              emit_r <= '1';
              held   <= held + wi - 15;
            else
              emit_r <= '0';
              held   <= held + wi;
            end if;
            state <= PACK_MASK;

          -- PACK_MASK: apply the precomputed width mask after the buffered
          -- sample has been captured, leaving only the shift for the next edge.
          when PACK_MASK =>
            chunk_r <= sample_r and resize(mask_lat, sample_r'length);
            state <= PACK_SHIFT;

          -- PACK_SHIFT: perform the variable shift into a staging register so
          -- PACK_ACC only sees an accumulator OR + emit decision.
          when PACK_SHIFT =>
            chunk_shift_r <= shift_left(resize(chunk_r, ACC_W), hs_r);
            state <= PACK_ACC;

          -- PACK_ACC: merge the pre-shifted chunk into the accumulator; emit a
          -- payload word when this sample completed 15 queued bits.
          when PACK_ACC =>
            nacc := acc or chunk_shift_r;
            if emit_r = '1' then
              if pending_valid_r = '0' then
                pending_data_r <= '0' & std_logic_vector(nacc(14 downto 0));
                pending_valid_r <= '1';
                acc <= resize(nacc(ACC_W-1 downto 15), ACC_W);
                if pcount = BLOCK_SAMPLES - 1 then
                  state <= DRAIN;
                else
                  pcount <= pcount + 1;
                  state  <= PACK_LOAD;
                end if;
              end if;
            else
              acc <= nacc;
              if pcount = BLOCK_SAMPLES - 1 then
                state <= DRAIN;
              else
                pcount <= pcount + 1;
                state  <= PACK_LOAD;
              end if;
            end if;

          -- DRAIN: flush any residual partial payload word (< 15 bits held).
          when DRAIN =>
            if held > 0 then
              if pending_valid_r = '0' then
                pending_data_r <= '0' & std_logic_vector(acc(14 downto 0));
                pending_valid_r <= '1';
                held <= 0;
                state <= FILL;
              end if;
            else
              state <= FILL;
            end if;

        end case;
      end if;
    end if;
  end process;

end rtl;
