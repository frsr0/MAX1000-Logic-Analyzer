-- Stub library for Altera megafunctions (GHDL simulation).
-- SDRAM_PLL declares LIBRARY altera_mf but does not USE any types
-- from it; GHDL still requires the library to exist at analysis time.
library ieee;
use ieee.std_logic_1164.all;

package altera_mf_components is
  -- Minimal altpll stub
  component altpll
    generic (
      bandwidth_type          : string := "AUTO";
      clk0_divide_by          : natural := 1;
      clk0_duty_cycle         : natural := 50;
      clk0_multiply_by        : natural := 1;
      clk0_phase_shift        : string := "0";
      clk1_divide_by          : natural := 1;
      clk1_duty_cycle         : natural := 50;
      clk1_multiply_by        : natural := 1;
      clk1_phase_shift        : string := "0";
      clk2_divide_by          : natural := 1;
      clk2_duty_cycle         : natural := 50;
      clk2_multiply_by        : natural := 1;
      clk2_phase_shift        : string := "0";
      clk3_divide_by          : natural := 1;
      clk3_duty_cycle         : natural := 50;
      clk3_multiply_by        : natural := 1;
      clk3_phase_shift        : string := "0";
      clk4_divide_by          : natural := 1;
      clk4_duty_cycle         : natural := 50;
      clk4_multiply_by        : natural := 1;
      clk4_phase_shift        : string := "0";
      compensate_clock        : string := "CLK0";
      inclk0_input_frequency  : natural := 0;
      intended_device_family  : string := "MAX 10";
      lpm_hint                : string := "UNUSED";
      lpm_type                : string := "altpll";
      operation_mode          : string := "NORMAL";
      pll_type                : string := "AUTO";
      port_activeclock        : string := "PORT_UNUSED";
      port_areset             : string := "PORT_UNUSED";
      port_clkbad0            : string := "PORT_UNUSED";
      port_clkbad1            : string := "PORT_UNUSED";
      port_clkloss            : string := "PORT_UNUSED";
      port_clkswitch          : string := "PORT_UNUSED";
      port_configupdate       : string := "PORT_UNUSED";
      port_fbin               : string := "PORT_UNUSED";
      port_inclk0             : string := "PORT_USED";
      port_inclk1             : string := "PORT_UNUSED";
      port_locked             : string := "PORT_USED";
      port_pfdena             : string := "PORT_UNUSED";
      port_phasecounterselect : string := "PORT_UNUSED";
      port_phasedone          : string := "PORT_UNUSED";
      port_phasestep          : string := "PORT_UNUSED";
      port_phaseupdown        : string := "PORT_UNUSED";
      port_pllena             : string := "PORT_UNUSED";
      port_scanaclr           : string := "PORT_UNUSED";
      port_scanclk            : string := "PORT_UNUSED";
      port_scanclkena         : string := "PORT_UNUSED";
      port_scandata           : string := "PORT_UNUSED";
      port_scandataout        : string := "PORT_UNUSED";
      port_scandone           : string := "PORT_UNUSED";
      port_scanread           : string := "PORT_UNUSED";
      port_scanwrite          : string := "PORT_UNUSED";
      port_clk0               : string := "PORT_USED";
      port_clk1               : string := "PORT_USED";
      port_clk2               : string := "PORT_USED";
      port_clk3               : string := "PORT_USED";
      port_clk4               : string := "PORT_USED";
      port_clkena0            : string := "PORT_UNUSED";
      port_clkena1            : string := "PORT_UNUSED";
      port_clkena2            : string := "PORT_UNUSED";
      port_clkena3            : string := "PORT_UNUSED";
      port_clkena4            : string := "PORT_UNUSED";
      port_extclk0            : string := "PORT_UNUSED";
      port_extclk1            : string := "PORT_UNUSED";
      port_extclk2            : string := "PORT_UNUSED";
      port_extclk3            : string := "PORT_UNUSED";
      self_reset_on_loss_lock : string := "OFF";
      width_clock             : natural := 5
    );
    port (
      areset   : in  std_logic := '0';
      clk      : out std_logic_vector(4 downto 0);
      clkbad   : out std_logic_vector(1 downto 0);
      activeclock : out std_logic;
      clkena   : out std_logic_vector(5 downto 0);
      clkloss  : out std_logic;
      clkswitch : in std_logic := '0';
      configupdate : in std_logic := '0';
      enable0  : out std_logic;
      enable1  : out std_logic;
      extclk   : out std_logic_vector(3 downto 0);
      extclkena : out std_logic_vector(3 downto 0);
      fbin     : in std_logic := '0';
      fbmimicbidir : inout std_logic := '0';
      fbout    : out std_logic;
      inclk    : in std_logic_vector(1 downto 0) := (others => '0');
      locked   : out std_logic;
      pfdena   : in std_logic := '1';
      phasecounterselect : in std_logic_vector(3 downto 0) := (others => '0');
      phasedone : out std_logic;
      phasestep : in std_logic := '0';
      phaseupdown : in std_logic := '0';
      pllena   : in std_logic := '1';
      scanaclr : in std_logic := '0';
      scanclk  : in std_logic := '0';
      scanclkena : in std_logic := '1';
      scandata : in std_logic := '0';
      scandataout : out std_logic;
      scandone : out std_logic;
      scanread : in std_logic := '0';
      scanwrite : in std_logic := '0';
      sclkout0 : out std_logic;
      sclkout1 : out std_logic;
      vcooverrange : out std_logic;
      vcounderrange : out std_logic
    );
  end component;
end package altera_mf_components;

package body altera_mf_components is
end package body altera_mf_components;
