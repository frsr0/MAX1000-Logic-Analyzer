-- Decode several FIFO replays. Samples mid-bit like host UART decoder.
library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.numeric_std.all;
use work.sim_pkg.all;

entity tb_gen_uart_repeat_decode is
end tb_gen_uart_repeat_decode;

architecture bench of tb_gen_uart_repeat_decode is
  constant CLK_PERIOD : time := 10 ns;
  constant BAUD_DIV   : natural := 20;
  constant BIT_TIME   : time := BAUD_DIV * CLK_PERIOD;
  constant N          : natural := 3;
  constant REPEATS    : natural := 5;
  type pl_t is array (0 to N-1) of std_logic_vector(7 downto 0);
  constant PAYLOAD : pl_t := (x"A5", x"3C", x"7E");
  signal clk : std_logic := '0';
  signal load_byte : std_logic_vector(7 downto 0) := (others => '0');
  signal load_we, start, tx_out, scl_out, busy, active : std_logic := '0';
begin
  gen_clk(clk, CLK_PERIOD / 2);
  DUT : entity work.Signal_Gen
    port map (
      CLK => clk, Load_Byte => load_byte, Load_We => load_we, Start => start,
      Baud_Div => std_logic_vector(to_unsigned(BAUD_DIV, 16)), Proto => '0',
      SPI_Mode => '0', Repeat => '1', Tx_Out => tx_out, Scl_Out => scl_out,
      Busy => busy, Active => active, I2C_Rd_Len => 0,
      I2C_Dev_R => (others => '0'), Sda_In => '1', CRC_En => '0', CRC_Poly => x"A001");

  process
    variable rxb : std_logic_vector(7 downto 0);
  begin
    for i in 0 to N-1 loop
      wait until rising_edge(clk);
      load_byte <= PAYLOAD(i); load_we <= '1';
      wait until rising_edge(clk);
      load_we <= '0';
    end loop;
    wait_cycles(clk, 4);
    start <= '1'; wait_cycles(clk, 1); start <= '0';
    wait_until(clk, busy, '1', 10 us, "repeat generator should start");

    for r in 0 to REPEATS-1 loop
      for i in 0 to N-1 loop
        wait until falling_edge(tx_out);
        wait for BIT_TIME * 1.5;
        for b in 0 to 7 loop
          rxb(b) := tx_out;
          wait for BIT_TIME;
        end loop;
        assert rxb = PAYLOAD(i)
          report "repeat=" & integer'image(r) & " byte=" & integer'image(i) &
                 " got=0x" & to_hstring(rxb) severity error;
        assert tx_out = '1' report "stop bit missing" severity error;
      end loop;
    end loop;
    assert busy = '1' report "repeat generator stopped" severity error;
    report "PASS: UART repeat decodes five complete FIFO replays" severity note;
    wait;
  end process;
end bench;
