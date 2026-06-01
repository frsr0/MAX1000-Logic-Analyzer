library IEEE;
use IEEE.STD_LOGIC_1164.ALL;

entity OLS_Logic_Analyzer is
port (
    CLK       : IN  STD_LOGIC;
    UART_RX   : IN  STD_LOGIC;
    UART_TX   : OUT STD_LOGIC;
    GPIO      : INOUT STD_LOGIC_VECTOR(7 downto 0);
    sdram_addr  : OUT STD_LOGIC_VECTOR(11 downto 0);
    sdram_ba    : OUT STD_LOGIC_VECTOR(1 downto 0);
    sdram_cas_n : OUT STD_LOGIC;
    sdram_cke   : OUT STD_LOGIC;
    sdram_cs_n  : OUT STD_LOGIC;
    sdram_dq    : INOUT STD_LOGIC_VECTOR(15 downto 0);
    sdram_dqm   : OUT STD_LOGIC_VECTOR(1 downto 0);
    sdram_ras_n : OUT STD_LOGIC;
    sdram_we_n  : OUT STD_LOGIC;
    sdram_clk   : OUT STD_LOGIC;
    SEN_SDI     : INOUT STD_LOGIC;
    SEN_SPC     : INOUT STD_LOGIC;
    SEN_CS      : OUT   STD_LOGIC;
    SEN_SDO     : IN    STD_LOGIC;
    LED         : OUT STD_LOGIC_VECTOR(7 downto 0)
);
end OLS_Logic_Analyzer;

architecture rtl of OLS_Logic_Analyzer is

    -- Pin assignments (read by Quartus at compile time)
    attribute chip_pin : string;
    attribute chip_pin of CLK : signal is "H6";
    attribute chip_pin of UART_RX : signal is "A4";
    attribute chip_pin of UART_TX : signal is "B4";
    attribute chip_pin of GPIO : signal is "M3,L3,M2,M1,N3,N2,K2,K1";
    attribute chip_pin of sdram_dq : signal is "D11,G10,F10,F9,E10,D9,G9,F8,F13,E12,E13,D12,C12,B12,B13,A12";
    attribute chip_pin of sdram_dqm : signal is "E9,F12";
    attribute chip_pin of sdram_addr : signal is "K6,M5,N5,J8,N10,M11,N9,L10,M13,N8,N4,M10";
    attribute chip_pin of sdram_ba : signal is "N6,K8";
    attribute chip_pin of sdram_cas_n : signal is "N7";
    attribute chip_pin of sdram_cke : signal is "M8";
    attribute chip_pin of sdram_cs_n : signal is "M4";
    attribute chip_pin of sdram_ras_n : signal is "M7";
    attribute chip_pin of sdram_we_n : signal is "K7";
    attribute chip_pin of sdram_clk : signal is "M9";
    attribute chip_pin of LED : signal is "A8,A9,A11,A10,B10,C9,C10,D8";
    attribute chip_pin of SEN_SDI : signal is "J7";
    attribute chip_pin of SEN_SPC : signal is "J6";
    attribute chip_pin of SEN_CS : signal is "L5";
    attribute chip_pin of SEN_SDO : signal is "K5";

    -- I/O standards
    attribute io_standard : string;
    attribute io_standard of LED : signal is "2.5 V";

    -- Pull-up resistors
    attribute weak_pull_up_resistor : string;
    attribute weak_pull_up_resistor of SEN_SDI : signal is "ON";
    attribute weak_pull_up_resistor of SEN_SPC : signal is "ON";

begin
    core : entity work.OLS_SDRAM_Top
    port map (
        CLK => CLK, UART_RX => UART_RX, UART_TX => UART_TX,
        GPIO => GPIO, LED => LED,
        sdram_addr => sdram_addr, sdram_ba => sdram_ba,
        sdram_cas_n => sdram_cas_n, sdram_cke => sdram_cke,
        sdram_cs_n => sdram_cs_n, sdram_dq => sdram_dq,
        sdram_dqm => sdram_dqm, sdram_ras_n => sdram_ras_n,
        sdram_we_n => sdram_we_n, sdram_clk => sdram_clk,
        SEN_SDI => SEN_SDI, SEN_SPC => SEN_SPC,
        SEN_CS => SEN_CS, SEN_SDO => SEN_SDO
    );
end rtl;
