library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;

-- analog_packer : barrel-shift bit-packer for the analog delta stream
-- ---------------------------------------------------------------------------
-- Back end of the analog compression pipeline. It buffers one block of
-- BLOCK_SAMPLES (16) signed 11-bit deltas from delta_calc, then serialises
-- them into the fixed 16-bit output word stream using the "Analog Packed Block
-- Frame" format (bit 15 = '0' on every word):
--
--   Word 0  (Header) : bit15='0', bits[14:11] = 4-bit width index W
--                       (bits per packed sample, 0..11),
--                       bits[10:0] = reserved (0).
--   Word 1..N (Payload): bit15='0', bits[14:0] = deltas packed W bits each,
--                       laid down contiguously (LSB-first) into consecutive
--                       15-bit slots.
--
-- The 4-bit width index spans the full 1..11-bit range delta_calc can request,
-- so every delta round-trips bit-exact (no clamp, no saturation). W = 0 (a
-- perfectly flat block) emits the header alone.
--
-- Structure (tuned for the 200.4 MHz FAST_CLK domain)
-- ---------------------------------------------------
-- A silicon-accurate fit on the 10M08 (C8) showed the single-cycle pack path
-- overran ~8 ns. Two things caused it and both are removed here:
--   * the sample buffer is an explicit 16-deep SHIFT REGISTER read at a fixed
--     tap (buf(TOP)) -- a plain register, not an indexed mux and not an
--     inferred synchronous-read M9K (which would also have mismatched the
--     zero-latency simulation read), and
--   * the per-sample mask (2^W - 1) is precomputed once per block, so only ONE
--     barrel shift (the chunk placement) is ever on a datapath.
-- Packing is a 2-stage micro-sequence per sample:
--   LOAD: chunk = buf(TOP) AND mask   (register it; shift the buffer on)
--   ACC : acc  = acc OR (chunk << held);  emit low 15 bits when >= 15 queued
-- so each cycle carries either a mask-AND or a single barrel-shift+accumulate,
-- never both. Two cycles/sample is immaterial: blocks are spaced far apart at
-- ADC sample rates, and in_ready holds the (slow) feeder off during a pack.
--
-- Output handshake: standard valid/ready. out_valid holds a stable out_data
-- until out_ready; the engine only advances when the slot is free
-- (out_valid='0' or out_ready='1'), so a full FIFO stalls packing without ever
-- dropping a word. in_ready is high only in FILL. Synchronous reset, single
-- clock enable, registered outputs, no combinational loops.
entity analog_packer is
  generic (
    BLOCK_SAMPLES : positive := 16;  -- deltas per block (matches delta_calc)
    MAX_WIDTH     : positive := 11   -- >= 11 for lossless (4-bit header holds 0..15)
  );
  port (
    clk         : in  std_logic;
    rst         : in  std_logic;                     -- synchronous, active high
    clk_en      : in  std_logic := '1';              -- global clock enable

    -- Delta input (from delta_calc)
    delta_in    : in  std_logic_vector(10 downto 0); -- signed 11-bit
    delta_valid : in  std_logic;                     -- delta_in valid
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
  constant TOP   : natural  := BLOCK_SAMPLES - 1;  -- fixed shift-register read tap

  type state_t is (FILL, EMIT_HEADER, PACK_LOAD, PACK_ACC, DRAIN);
  signal state : state_t := FILL;

  -- Sample buffer as a shift register (async read at the fixed TOP tap).
  type buf_array is array(0 to BLOCK_SAMPLES-1) of std_logic_vector(10 downto 0);
  signal buf   : buf_array := (others => (others => '0'));

  signal w_lat    : unsigned(3 downto 0) := (others => '0');           -- latched block width
  signal mask_lat : unsigned(MAX_WIDTH-1 downto 0) := (others => '0'); -- precomputed 2^W-1
  signal acc      : unsigned(ACC_W-1 downto 0) := (others => '0');
  signal held     : natural range 0 to 14 := 0;                        -- bits queued in acc
  signal pcount   : natural range 0 to BLOCK_SAMPLES := 0;             -- samples packed

  -- LOAD -> ACC pipeline registers
  signal chunk_r  : unsigned(MAX_WIDTH-1 downto 0) := (others => '0'); -- masked delta
  signal hs_r     : natural range 0 to 14 := 0;                       -- shift amount for it
  signal emit_r   : std_logic := '0';                                 -- this sample fills a word

  signal slot_free : std_logic;

begin

  busy      <= '0' when state = FILL else '1';
  in_ready  <= '1' when state = FILL else '0';
  slot_free <= '1' when (out_valid = '0' or out_ready = '1') else '0';

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
        out_valid <= '0';
      elsif clk_en = '1' then

        -- Clear an accepted output word (unless a state below re-loads it).
        if out_valid = '1' and out_ready = '1' then
          out_valid <= '0';
        end if;

        case state is

          -- FILL: shift each delta into the buffer. block_done arrives with the
          -- last (BLOCK_SAMPLES-th) delta, leaving buf(TOP) = sample 0.
          when FILL =>
            if delta_valid = '1' then
              buf(0) <= delta_in;
              for i in 1 to BLOCK_SAMPLES-1 loop
                buf(i) <= buf(i-1);
              end loop;
              if block_done = '1' then
                assert unsigned(block_width) <= MAX_WIDTH
                  report "analog_packer: block_width exceeds MAX_WIDTH"
                  severity error;
                w_lat  <= unsigned(block_width);
                held   <= 0;
                pcount <= 0;
                acc    <= (others => '0');
                state  <= EMIT_HEADER;
              end if;
            end if;

          -- EMIT_HEADER: present the header and precompute the block mask
          -- (2^W - 1) once, off the per-sample datapath.
          when EMIT_HEADER =>
            if slot_free = '1' then
              out_data  <= '0' & std_logic_vector(w_lat) & "00000000000";
              out_valid <= '1';
              m12 := shift_left(to_unsigned(1, 12), to_integer(w_lat)) - 1;
              mask_lat <= m12(MAX_WIDTH-1 downto 0);
              if w_lat = 0 then
                state <= FILL;             -- flat block: header only
              else
                state <= PACK_LOAD;
              end if;
            end if;

          -- PACK_LOAD: read the TOP sample, mask to W bits, register the chunk
          -- and its target shift; advance the fill count and the buffer.
          when PACK_LOAD =>
            wi      := to_integer(w_lat);
            chunk_r <= unsigned(buf(TOP)) and mask_lat;
            hs_r    <= held;
            if held + wi >= 15 then
              emit_r <= '1';
              held   <= held + wi - 15;
            else
              emit_r <= '0';
              held   <= held + wi;
            end if;
            -- Shift buffer up so the next sample appears at buf(TOP).
            for i in BLOCK_SAMPLES-1 downto 1 loop
              buf(i) <= buf(i-1);
            end loop;
            state <= PACK_ACC;

          -- PACK_ACC: single barrel shift into the accumulator; emit a payload
          -- word when this sample completed 15 queued bits.
          when PACK_ACC =>
            if slot_free = '1' then
              nacc := acc or shift_left(resize(chunk_r, ACC_W), hs_r);
              if emit_r = '1' then
                out_data  <= '0' & std_logic_vector(nacc(14 downto 0));
                out_valid <= '1';
                acc       <= resize(nacc(ACC_W-1 downto 15), ACC_W);
              else
                acc <= nacc;
              end if;
              if pcount = BLOCK_SAMPLES - 1 then
                state <= DRAIN;
              else
                pcount <= pcount + 1;
                state  <= PACK_LOAD;
              end if;
            end if;

          -- DRAIN: flush any residual partial payload word (< 15 bits held).
          when DRAIN =>
            if slot_free = '1' then
              if held > 0 then
                out_data  <= '0' & std_logic_vector(acc(14 downto 0));
                out_valid <= '1';
                held      <= 0;
              end if;
              state <= FILL;
            end if;

        end case;
      end if;
    end if;
  end process;

end rtl;
