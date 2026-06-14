-- Reproduce the mixed-mode (analog) 2-word frame preamble in simulation.
--
-- Drives OLS_SDRAM_Top (FAST_SPEED=true, Sim=true) through the host's analog
-- capture register sequence (REG_FLAGS bit3 = MODE_MIXED) and dumps the first
-- words read back via CMD_READ_CAPTURE. On hardware the aligned 7-word analog
-- frame starts at word index 2 (a fixed 2-sample preamble); this TB prints the
-- raw word stream so we can see whether the sim reproduces that phase.
--
-- In sim the modular-ADC model returns a constant 0xAAA on all 8 channels, so
-- every analog frame is identical: word0 = digital (driven here to a constant),
-- words 1..6 = the 0xAAA-derived ADC bytes. The frame value pattern repeats
-- every 7 words; the offset of that period-7 pattern is the preamble length.
library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;
use work.sim_pkg.all;
use work.spi_protocol_pkg.all;

entity tb_analog_preamble is
  generic (SPI_HALF : time := 100 ns);
end tb_analog_preamble;

architecture bench of tb_analog_preamble is
  constant CLK_PERIOD : time := 1 sec / 12000000;

  signal clk_12 : std_logic := '0';
  signal spi_cs  : std_logic := '1';
  signal sck     : std_logic := '0';
  signal spi_mosi : std_logic := '0';
  signal spi_miso : std_logic;

  -- Drive the LA pins to a recognisable, NON-zero constant so the digital
  -- frame word is distinct from the 0xAAA ADC words and not stripped by any
  -- leading-zero logic.
  signal mkr_d  : std_logic_vector(14 downto 0) := (others => '0');
  signal pmod   : std_logic_vector(7 downto 0) := (others => '0');

  signal sdram_addr : std_logic_vector(11 downto 0);
  signal sdram_ba   : std_logic_vector(1 downto 0);
  signal sdram_cas_n : std_logic;
  signal sdram_cke   : std_logic;
  signal sdram_cs_n  : std_logic;
  signal sdram_dq    : std_logic_vector(15 downto 0);
  signal sdram_dqm   : std_logic_vector(1 downto 0);
  signal sdram_ras_n : std_logic;
  signal sdram_we_n  : std_logic;
  signal sdram_clk   : std_logic;

  signal sen_sdi : std_logic := 'H';
  signal sen_spc : std_logic := 'H';
  signal sen_cs  : std_logic;
  signal sen_sdo : std_logic := '0';
  signal led : std_logic_vector(7 downto 0);

  function flatten(b : byte_array; n : natural) return std_logic_vector is
    variable r : std_logic_vector(n*8-1 downto 0);
  begin
    for i in 0 to n-1 loop
      r(i*8+7 downto i*8) := b(b'low + i);
    end loop;
    return r;
  end function;

  procedure pkt_send(
    signal cs_n : out std_logic; signal sck_o : out std_logic;
    signal mosi : out std_logic; signal miso : in std_logic;
    constant cmd : in std_logic_vector(7 downto 0);
    constant payload : in byte_array; constant plen : in natural) is
    variable tx : byte_array(0 to 300);
    variable rx : byte_array(0 to 300);
    variable len_v : std_logic_vector(15 downto 0);
    variable crc_v : std_logic_vector(15 downto 0);
    variable crc_data : std_logic_vector((4+plen)*8-1 downto 0);
  begin
    tx(0) := x"55"; tx(1) := x"AA";
    tx(2) := cmd; tx(3) := x"00";
    len_v := std_logic_vector(to_unsigned(plen, 16));
    tx(4) := len_v(7 downto 0); tx(5) := len_v(15 downto 8);
    for i in 0 to plen-1 loop tx(6+i) := payload(i); end loop;
    crc_data := flatten(tx(2 to 5+plen), 4+plen);
    crc_v := crc16(crc_data);
    tx(6+plen) := crc_v(7 downto 0); tx(7+plen) := crc_v(15 downto 8);
    spi_xfer(cs_n, sck_o, mosi, miso, SPI_HALF, tx(0 to 7+plen), rx(0 to 7+plen));
  end procedure;

  procedure pkt_read_rsp(
    signal cs_n : out std_logic; signal sck_o : out std_logic;
    signal mosi : out std_logic; signal miso : in std_logic;
    constant rlen : in natural;
    variable status : out std_logic_vector(7 downto 0);
    variable payload : out byte_array;
    variable pay_len : out natural) is
    variable tx : byte_array(0 to 1199);
    variable rx : byte_array(0 to 1199);
    variable plen_v : natural;
    variable found : boolean := false;
  begin
    status := x"FF"; pay_len := 0;
    for i in 0 to rlen-1 loop tx(i) := x"FF"; end loop;
    spi_xfer(cs_n, sck_o, mosi, miso, SPI_HALF, tx(0 to rlen-1), rx(0 to rlen-1));
    for i in 0 to rlen-7 loop
      if not found and rx(i) = x"AA" and rx(i+1) = x"55" then
        status := rx(i+2);
        plen_v := to_integer(unsigned(rx(i+5))) * 256
                + to_integer(unsigned(rx(i+4)));
        if plen_v > 1100 then plen_v := 0; end if;
        for k in 0 to plen_v-1 loop
          if i+6+k <= rlen-1 then
            payload(payload'low + k) := rx(i+6+k);
          end if;
        end loop;
        pay_len := plen_v;
        found := true;
      end if;
    end loop;
  end procedure;

  procedure pkt_cmd(
    signal cs_n : out std_logic; signal sck_o : out std_logic;
    signal mosi : out std_logic; signal miso : in std_logic;
    constant cmd : in std_logic_vector(7 downto 0);
    constant payload : in byte_array; constant plen : in natural;
    variable status : out std_logic_vector(7 downto 0)) is
    variable pay : byte_array(0 to 63);
    variable pl  : natural;
  begin
    pkt_send(cs_n, sck_o, mosi, miso, cmd, payload, plen);
    wait for 6 us;
    pkt_read_rsp(cs_n, sck_o, mosi, miso, 40, status, pay, pl);
  end procedure;

  procedure wreg(
    signal cs_n : out std_logic; signal sck_o : out std_logic;
    signal mosi : out std_logic; signal miso : in std_logic;
    constant reg : in std_logic_vector(7 downto 0);
    constant value : in integer) is
    variable pld : byte_array(0 to 4);
    variable v : std_logic_vector(31 downto 0);
    variable st : std_logic_vector(7 downto 0);
  begin
    v := std_logic_vector(to_unsigned(value, 32));
    pld(0) := reg;
    pld(1) := v(7 downto 0); pld(2) := v(15 downto 8);
    pld(3) := v(23 downto 16); pld(4) := v(31 downto 24);
    pkt_cmd(cs_n, sck_o, mosi, miso, CMD_WRITE_REG, pld, 5, st);
  end procedure;

begin
  gen_clk(clk_12, CLK_PERIOD / 2);

  DUT : entity work.OLS_SDRAM_Top
    generic map (TX_PIN => 3, PLL_MULT => 8, PLL_DIV => 1,
                 Sim => true, FAST_SPEED => true)
    port map (
      CLK => clk_12,
      SPI_CS => spi_cs, SPI_SCK => sck, SPI_MOSI => spi_mosi, SPI_MISO => spi_miso,
      MKR_D => mkr_d, PMOD => pmod,
      sdram_addr => sdram_addr, sdram_ba => sdram_ba, sdram_cas_n => sdram_cas_n,
      sdram_cke => sdram_cke, sdram_cs_n => sdram_cs_n, sdram_dq => sdram_dq,
      sdram_dqm => sdram_dqm, sdram_ras_n => sdram_ras_n, sdram_we_n => sdram_we_n,
      sdram_clk => sdram_clk,
      SEN_SDI => sen_sdi, SEN_SPC => sen_spc, SEN_CS => sen_cs, SEN_SDO => sen_sdo,
      LED => led);

  SDRAM_CHIP : entity work.sdram_pin_model
    port map (
      clk => sdram_clk, cke => sdram_cke, cs_n => sdram_cs_n,
      ras_n => sdram_ras_n, cas_n => sdram_cas_n, we_n => sdram_we_n,
      ba => sdram_ba, addr => sdram_addr, dqm => sdram_dqm, dq => sdram_dq);

  stim : process
    constant FRAMES : natural := 16;
    constant WORDS  : natural := FRAMES * 7;     -- 7 words per mixed frame
    variable st : std_logic_vector(7 downto 0);
    variable pay : byte_array(0 to 1099);
    variable pl : natural;
    variable empty : byte_array(0 to 0);
    variable addr_pld : byte_array(0 to 3);
    variable word : std_logic_vector(15 downto 0);
    variable line_s : string(1 to 120);
    variable deadline : natural := 0;
  begin
    -- drive a recognisable constant on the pins
    mkr_d <= (others => '0');
    pmod  <= (others => '0');
    wait for 30 us;  -- PLL lock + init

    report "=== Analog (MODE_MIXED) capture ===";
    -- driver reset() equivalent
    pkt_cmd(spi_cs, sck, spi_mosi, spi_miso, CMD_ABORT_CAPTURE, empty, 0, st);
    wreg(spi_cs, sck, spi_mosi, spi_miso, REG_DIVIDER, 0);
    wreg(spi_cs, sck, spi_mosi, spi_miso, REG_SAMPLE_COUNT, 2);
    wreg(spi_cs, sck, spi_mosi, spi_miso, REG_FLAGS, 0);
    wreg(spi_cs, sck, spi_mosi, spi_miso, REG_IFACE_MODE, 1);
    wait for 20 us;
    -- analog capture config: word rate via div=99, WORDS words, analog enable
    wreg(spi_cs, sck, spi_mosi, spi_miso, REG_DEBUG_CH0_ENABLE, 0);
    wreg(spi_cs, sck, spi_mosi, spi_miso, REG_DIVIDER, 99);
    wreg(spi_cs, sck, spi_mosi, spi_miso, REG_SAMPLE_COUNT, WORDS);
    wreg(spi_cs, sck, spi_mosi, spi_miso, REG_DELAY_COUNT, WORDS);
    wreg(spi_cs, sck, spi_mosi, spi_miso, REG_TRIGGER_MASK, 0);
    wreg(spi_cs, sck, spi_mosi, spi_miso, REG_TRIGGER_VALUE, 0);
    wreg(spi_cs, sck, spi_mosi, spi_miso, REG_FLAGS, 8);   -- MODE_MIXED (bit 3)
    wreg(spi_cs, sck, spi_mosi, spi_miso, REG_FAST_MODE, 1);

    pkt_cmd(spi_cs, sck, spi_mosi, spi_miso, CMD_ARM_CAPTURE, empty, 0, st);
    loop
      pkt_cmd(spi_cs, sck, spi_mosi, spi_miso, CMD_GET_STATUS, empty, 0, st);
      exit when st = ST_CAPTURE_DONE or deadline > 200;
      deadline := deadline + 1;
      wait for 20 us;
    end loop;
    report "capture status = " & to_hstring(st) & " (deadline=" &
           integer'image(deadline) & ")";

    -- read block 0 and dump the first 24 words
    addr_pld := (x"00", x"00", x"00", x"00");
    pkt_send(spi_cs, sck, spi_mosi, spi_miso, CMD_READ_CAPTURE, addr_pld, 4);
    wait for 30 us;
    pkt_read_rsp(spi_cs, sck, spi_mosi, spi_miso, 1100, st, pay, pl);
    report "block0 payload bytes = " & integer'image(pl);

    for w in 0 to 23 loop
      if w*2 + 1 <= pl - 1 then
        word := pay(w*2 + 1) & pay(w*2);
        report "  word[" & integer'image(w) & "] = " & to_hstring(word);
      end if;
    end loop;

    report "======================================================";
    report "  tb_analog_preamble: done (inspect word dump above)";
    report "======================================================";
    std.env.finish;
  end process;

end bench;
