library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity tb_adc_controller is
  generic (TEST : string := "tc_basic");
end tb_adc_controller;

architecture sim of tb_adc_controller is
  constant CLK_PERIOD : time := 20.833 ns;  -- 48 MHz

  signal sys_clk       : std_logic := '0';
  signal reset         : std_logic := '0';
  signal channel_sel   : natural range 0 to 7 := 0;
  signal start         : std_logic := '0';
  signal busy          : std_logic;
  signal result        : std_logic_vector(11 downto 0);
  signal result_valid  : std_logic;

begin

  sys_clk <= not sys_clk after CLK_PERIOD / 2;

  uut: entity work.ADC_Controller
    port map (
      sys_clk      => sys_clk,
      reset        => reset,
      channel_sel  => channel_sel,
      start        => start,
      busy         => busy,
      result       => result,
      result_valid => result_valid
    );

  stimuli: process
  begin
    reset <= '1';
    wait for 100 ns;
    reset <= '0';
    wait for 200 ns;

    if TEST = "tc_basic" then
      report "=== tc_basic: single conversion channel 0 ===";
      channel_sel <= 0;
      start <= '1';
      wait for CLK_PERIOD;
      start <= '0';
      wait until result_valid = '1' for 200 us;
      if result_valid = '1' then
        report "tc_basic: conversion complete, result=" & to_hstring(result) severity note;
      else
        report "tc_basic: timeout waiting for conversion" severity error;
      end if;
      wait for 10 us;

    elsif TEST = "tc_multi_channel" then
      report "=== tc_multi_channel: convert on all 8 channels ===";
      for ch in 0 to 7 loop
        channel_sel <= ch;
        start <= '1';
        wait for CLK_PERIOD;
        start <= '0';
        wait until result_valid = '1' for 200 us;
        if result_valid = '1' then
          report "Ch" & integer'image(ch) & " = 0x" & to_hstring(result) severity note;
        else
          report "Ch" & integer'image(ch) & " timeout" severity error;
        end if;
        wait for 5 us;
      end loop;

    elsif TEST = "tc_back_to_back" then
      report "=== tc_back_to_back: rapid conversions ===";
      for i in 0 to 15 loop
        channel_sel <= i mod 8;
        start <= '1';
        wait for CLK_PERIOD;
        start <= '0';
        wait until result_valid = '1' for 200 us;
        if result_valid = '0' then
          report "conversion " & integer'image(i) & " timeout" severity error;
        end if;
        wait for 1 us;
      end loop;
      report "tc_back_to_back: all 16 conversions complete" severity note;

    elsif TEST = "tc_busy_timing" then
      report "=== tc_busy_timing: verify busy duration ===";
      channel_sel <= 0;
      start <= '1';
      wait for CLK_PERIOD;
      start <= '0';
      if busy = '1' then
        report "tc_busy_timing: busy asserted after start" severity note;
      end if;
      wait until result_valid = '1' for 200 us;
      if busy = '0' then
        report "tc_busy_timing: busy deasserted after result" severity note;
      end if;
      wait for 10 us;

    else
      report "Unknown test: " & TEST severity failure;
    end if;

    report "Test " & TEST & " complete" severity note;
    wait;
  end process;
end sim;
