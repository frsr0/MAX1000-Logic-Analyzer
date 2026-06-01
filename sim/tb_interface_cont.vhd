library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity tb_interface_cont is
  generic (TEST : string := "tc_cont_cmd");
end tb_interface_cont;

architecture sim of tb_interface_cont is
  constant CLK_PERIOD : time := 20.833 ns;
  constant BIT_TIME   : time := 52 * CLK_PERIOD;

  signal clk       : std_logic := '0';
  signal running   : boolean := true;

  signal uart_rx   : std_logic := '1';
  signal uart_tx   : std_logic;

  signal cont_mode_from_dut : std_logic;
  signal run_ols_from_dut   : std_logic;

  type byte_array is array(natural range <>) of std_logic_vector(7 downto 0);

  procedure uart_send_byte(signal rx : out std_logic; data : std_logic_vector(7 downto 0)) is
  begin
    rx <= '0'; wait for BIT_TIME;
    for i in 0 to 7 loop
      rx <= data(i); wait for BIT_TIME;
    end loop;
    rx <= '1'; wait for BIT_TIME;
  end;

  procedure uart_send(signal rx : out std_logic; bytes : byte_array) is
  begin
    for i in bytes'range loop
      uart_send_byte(rx, bytes(i));
    end loop;
  end;

  procedure uart_send_le32(signal rx : out std_logic; val : natural) is
    variable v : std_logic_vector(31 downto 0) := std_logic_vector(to_unsigned(val, 32));
  begin
    uart_send_byte(rx, v(7 downto 0));
    uart_send_byte(rx, v(15 downto 8));
    uart_send_byte(rx, v(23 downto 16));
    uart_send_byte(rx, v(31 downto 24));
  end;

begin
  clk <= not clk after CLK_PERIOD / 2 when running;

  DUT : entity work.OLS_Interface(behavioral)
    generic map (
      Baud_Rate    => 921600,
      CLK_Frequency => 48000000,
      Max_Samples  => 1048576,
      OS_Rate      => 13
    )
    port map (
      CLK => clk,
      UART_RX => uart_rx,
      UART_TX => uart_tx,
      Inputs => (others => '0'),
      Rate_Div => open,
      Samples => open,
      Start_Offset => open,
      Run => open,
      Full => '0',
      Address => open,
      Outputs => (others => '0'),
      Gen_Busy => '0',
      Armed => open,
      Fast_Mode => open,
      Continuous_Mode => open,
      Buffer_Full => "00",
      Buffer_Ack => open
    );

  cont_mode_from_dut <= <<signal .tb_interface_cont.DUT.continuous_mode_i : std_logic>>;

  process
    variable rx_byte : std_logic_vector(7 downto 0);
  begin
    wait for 10 us;

    -- ============================================================
    -- tc_cont_cmd: CMD_CONT_CAPTURE sets Continuous_Mode
    -- ============================================================
    if TEST = "all" or TEST = "tc_cont_cmd" then
      report "--- tc_cont_cmd: CMD_CONT_CAPTURE enables continuous mode ---" severity note;

      uart_send(uart_rx, (0 to 4 => x"00"));
      wait for 50 us;
      assert cont_mode_from_dut = '0' report "tc_cont_cmd: cont_mode should be 0 after reset" severity failure;

      uart_send(uart_rx, (0 => x"AA"));
      wait for 10 us;
      uart_send_le32(uart_rx, 1);
      wait for 50 us;

      assert cont_mode_from_dut = '1' report "tc_cont_cmd: cont_mode should be 1 after CMD_CONT_CAPTURE(1)" severity failure;
      report "tc_cont_cmd: cont_mode went high (OK)" severity note;

      uart_send(uart_rx, (0 => x"00"));
      wait for 50 us;
      assert cont_mode_from_dut = '0' report "tc_cont_cmd: cont_mode should be 0 after reset" severity failure;
      report "tc_cont_cmd: cont_mode cleared by reset (OK)" severity note;

      report "tc_cont_cmd: PASS" severity note;
    end if;

    -- ============================================================
    -- tc_cont_reset: Reset stops continuous mode
    -- ============================================================
    if TEST = "all" or TEST = "tc_cont_reset" then
      report "--- tc_cont_reset: Reset stops continuous mode ---" severity note;

      uart_send(uart_rx, (0 => x"AA")); wait for 10 us;
      uart_send_le32(uart_rx, 1); wait for 50 us;
      assert cont_mode_from_dut = '1' report "tc_cont_reset: expected cont mode ON" severity failure;

      uart_send(uart_rx, (0 => x"00")); wait for 50 us;
      assert cont_mode_from_dut = '0' report "tc_cont_reset: cont mode still ON after reset" severity failure;
      report "tc_cont_reset: Reset properly stops continuous mode (OK)" severity note;

      report "tc_cont_reset: PASS" severity note;
    end if;

    if TEST = "all" then
      report "ALL TESTS: PASS" severity note;
    end if;

    running <= false;
    wait;
  end process;

end sim;
