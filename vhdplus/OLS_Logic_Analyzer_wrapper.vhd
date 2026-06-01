library IEEE;
use IEEE.STD_LOGIC_1164.ALL;

entity OLS_Logic_Analyzer is
port (
    CLK       : IN  STD_LOGIC;
    UART_RX   : IN  STD_LOGIC;
    UART_TX   : OUT STD_LOGIC;
    GPIO_0    : INOUT STD_LOGIC;
    GPIO_1    : INOUT STD_LOGIC;
    GPIO_2    : INOUT STD_LOGIC;
    GPIO_3    : INOUT STD_LOGIC;
    GPIO_4    : INOUT STD_LOGIC;
    GPIO_5    : INOUT STD_LOGIC;
    GPIO_6    : INOUT STD_LOGIC;
    GPIO_7    : INOUT STD_LOGIC;
    sdram_dq_0  : INOUT STD_LOGIC;
    sdram_dq_1  : INOUT STD_LOGIC;
    sdram_dq_2  : INOUT STD_LOGIC;
    sdram_dq_3  : INOUT STD_LOGIC;
    sdram_dq_4  : INOUT STD_LOGIC;
    sdram_dq_5  : INOUT STD_LOGIC;
    sdram_dq_6  : INOUT STD_LOGIC;
    sdram_dq_7  : INOUT STD_LOGIC;
    sdram_dq_8  : INOUT STD_LOGIC;
    sdram_dq_9  : INOUT STD_LOGIC;
    sdram_dq_10 : INOUT STD_LOGIC;
    sdram_dq_11 : INOUT STD_LOGIC;
    sdram_dq_12 : INOUT STD_LOGIC;
    sdram_dq_13 : INOUT STD_LOGIC;
    sdram_dq_14 : INOUT STD_LOGIC;
    sdram_dq_15 : INOUT STD_LOGIC;
    sdram_dqm_0 : OUT STD_LOGIC;
    sdram_dqm_1 : OUT STD_LOGIC;
    sdram_addr_0  : OUT STD_LOGIC;
    sdram_addr_1  : OUT STD_LOGIC;
    sdram_addr_2  : OUT STD_LOGIC;
    sdram_addr_3  : OUT STD_LOGIC;
    sdram_addr_4  : OUT STD_LOGIC;
    sdram_addr_5  : OUT STD_LOGIC;
    sdram_addr_6  : OUT STD_LOGIC;
    sdram_addr_7  : OUT STD_LOGIC;
    sdram_addr_8  : OUT STD_LOGIC;
    sdram_addr_9  : OUT STD_LOGIC;
    sdram_addr_10 : OUT STD_LOGIC;
    sdram_addr_11 : OUT STD_LOGIC;
    sdram_ba_0   : OUT STD_LOGIC;
    sdram_ba_1   : OUT STD_LOGIC;
    sdram_cas_n  : OUT STD_LOGIC;
    sdram_cke    : OUT STD_LOGIC;
    sdram_cs_n   : OUT STD_LOGIC;
    sdram_ras_n  : OUT STD_LOGIC;
    sdram_we_n   : OUT STD_LOGIC;
    sdram_clk    : OUT STD_LOGIC;
    SEN_SDI     : INOUT STD_LOGIC;
    SEN_SPC     : INOUT STD_LOGIC;
    SEN_CS      : OUT   STD_LOGIC;
    SEN_SDO     : IN    STD_LOGIC;
    LED_0       : OUT STD_LOGIC;
    LED_1       : OUT STD_LOGIC;
    LED_2       : OUT STD_LOGIC;
    LED_3       : OUT STD_LOGIC;
    LED_4       : OUT STD_LOGIC;
    LED_5       : OUT STD_LOGIC;
    LED_6       : OUT STD_LOGIC;
    LED_7       : OUT STD_LOGIC
);
end OLS_Logic_Analyzer;

architecture rtl of OLS_Logic_Analyzer is
    -- Pin assignments (read by Quartus at compile time)
    attribute chip_pin : string;
    attribute chip_pin of CLK : signal is "H6";
    attribute chip_pin of UART_RX : signal is "A4";
    attribute chip_pin of UART_TX : signal is "B4";
    attribute chip_pin of GPIO_0 : signal is "M3";
    attribute chip_pin of GPIO_1 : signal is "L3";
    attribute chip_pin of GPIO_2 : signal is "M2";
    attribute chip_pin of GPIO_3 : signal is "M1";
    attribute chip_pin of GPIO_4 : signal is "N3";
    attribute chip_pin of GPIO_5 : signal is "N2";
    attribute chip_pin of GPIO_6 : signal is "K2";
    attribute chip_pin of GPIO_7 : signal is "K1";
    attribute chip_pin of sdram_dq_0 : signal is "D11";
    attribute chip_pin of sdram_dq_1 : signal is "G10";
    attribute chip_pin of sdram_dq_2 : signal is "F10";
    attribute chip_pin of sdram_dq_3 : signal is "F9";
    attribute chip_pin of sdram_dq_4 : signal is "E10";
    attribute chip_pin of sdram_dq_5 : signal is "D9";
    attribute chip_pin of sdram_dq_6 : signal is "G9";
    attribute chip_pin of sdram_dq_7 : signal is "F8";
    attribute chip_pin of sdram_dq_8 : signal is "F13";
    attribute chip_pin of sdram_dq_9 : signal is "E12";
    attribute chip_pin of sdram_dq_10 : signal is "E13";
    attribute chip_pin of sdram_dq_11 : signal is "D12";
    attribute chip_pin of sdram_dq_12 : signal is "C12";
    attribute chip_pin of sdram_dq_13 : signal is "B12";
    attribute chip_pin of sdram_dq_14 : signal is "B13";
    attribute chip_pin of sdram_dq_15 : signal is "A12";
    attribute chip_pin of sdram_dqm_0 : signal is "E9";
    attribute chip_pin of sdram_dqm_1 : signal is "F12";
    attribute chip_pin of sdram_addr_0 : signal is "K6";
    attribute chip_pin of sdram_addr_1 : signal is "M5";
    attribute chip_pin of sdram_addr_2 : signal is "N5";
    attribute chip_pin of sdram_addr_3 : signal is "J8";
    attribute chip_pin of sdram_addr_4 : signal is "N10";
    attribute chip_pin of sdram_addr_5 : signal is "M11";
    attribute chip_pin of sdram_addr_6 : signal is "N9";
    attribute chip_pin of sdram_addr_7 : signal is "L10";
    attribute chip_pin of sdram_addr_8 : signal is "M13";
    attribute chip_pin of sdram_addr_9 : signal is "N8";
    attribute chip_pin of sdram_addr_10 : signal is "N4";
    attribute chip_pin of sdram_addr_11 : signal is "M10";
    attribute chip_pin of sdram_ba_0 : signal is "N6";
    attribute chip_pin of sdram_ba_1 : signal is "K8";
    attribute chip_pin of sdram_cas_n : signal is "N7";
    attribute chip_pin of sdram_cke : signal is "M8";
    attribute chip_pin of sdram_cs_n : signal is "M4";
    attribute chip_pin of sdram_ras_n : signal is "M7";
    attribute chip_pin of sdram_we_n : signal is "K7";
    attribute chip_pin of sdram_clk : signal is "M9";
    attribute chip_pin of SEN_SDI : signal is "J7";
    attribute chip_pin of SEN_SPC : signal is "J6";
    attribute chip_pin of SEN_CS : signal is "L5";
    attribute chip_pin of SEN_SDO : signal is "K5";
    attribute chip_pin of LED_0 : signal is "A8";
    attribute chip_pin of LED_1 : signal is "A9";
    attribute chip_pin of LED_2 : signal is "A11";
    attribute chip_pin of LED_3 : signal is "A10";
    attribute chip_pin of LED_4 : signal is "B10";
    attribute chip_pin of LED_5 : signal is "C9";
    attribute chip_pin of LED_6 : signal is "C10";
    attribute chip_pin of LED_7 : signal is "D8";

    -- I/O standard for LEDs (2.5V bank)
    attribute io_standard : string;
    attribute io_standard of LED_0 : signal is "2.5 V";
    attribute io_standard of LED_1 : signal is "2.5 V";
    attribute io_standard of LED_2 : signal is "2.5 V";
    attribute io_standard of LED_3 : signal is "2.5 V";
    attribute io_standard of LED_4 : signal is "2.5 V";
    attribute io_standard of LED_5 : signal is "2.5 V";
    attribute io_standard of LED_6 : signal is "2.5 V";
    attribute io_standard of LED_7 : signal is "2.5 V";

    -- Pull-up resistors for accelerometer I2C lines
    attribute weak_pull_up_resistor : string;
    attribute weak_pull_up_resistor of SEN_SDI : signal is "ON";
    attribute weak_pull_up_resistor of SEN_SPC : signal is "ON";

begin
    core : entity work.OLS_SDRAM_Top
    port map (
        CLK => CLK,
        UART_RX => UART_RX,
        UART_TX => UART_TX,
        GPIO(0) => GPIO_0,
        GPIO(1) => GPIO_1,
        GPIO(2) => GPIO_2,
        GPIO(3) => GPIO_3,
        GPIO(4) => GPIO_4,
        GPIO(5) => GPIO_5,
        GPIO(6) => GPIO_6,
        GPIO(7) => GPIO_7,
        sdram_dq(0) => sdram_dq_0,
        sdram_dq(1) => sdram_dq_1,
        sdram_dq(2) => sdram_dq_2,
        sdram_dq(3) => sdram_dq_3,
        sdram_dq(4) => sdram_dq_4,
        sdram_dq(5) => sdram_dq_5,
        sdram_dq(6) => sdram_dq_6,
        sdram_dq(7) => sdram_dq_7,
        sdram_dq(8) => sdram_dq_8,
        sdram_dq(9) => sdram_dq_9,
        sdram_dq(10) => sdram_dq_10,
        sdram_dq(11) => sdram_dq_11,
        sdram_dq(12) => sdram_dq_12,
        sdram_dq(13) => sdram_dq_13,
        sdram_dq(14) => sdram_dq_14,
        sdram_dq(15) => sdram_dq_15,
        sdram_dqm(0) => sdram_dqm_0,
        sdram_dqm(1) => sdram_dqm_1,
        sdram_addr(0) => sdram_addr_0,
        sdram_addr(1) => sdram_addr_1,
        sdram_addr(2) => sdram_addr_2,
        sdram_addr(3) => sdram_addr_3,
        sdram_addr(4) => sdram_addr_4,
        sdram_addr(5) => sdram_addr_5,
        sdram_addr(6) => sdram_addr_6,
        sdram_addr(7) => sdram_addr_7,
        sdram_addr(8) => sdram_addr_8,
        sdram_addr(9) => sdram_addr_9,
        sdram_addr(10) => sdram_addr_10,
        sdram_addr(11) => sdram_addr_11,
        sdram_ba(0) => sdram_ba_0,
        sdram_ba(1) => sdram_ba_1,
        sdram_cas_n => sdram_cas_n,
        sdram_cke => sdram_cke,
        sdram_cs_n => sdram_cs_n,
        sdram_ras_n => sdram_ras_n,
        sdram_we_n => sdram_we_n,
        sdram_clk => sdram_clk,
        SEN_SDI => SEN_SDI,
        SEN_SPC => SEN_SPC,
        SEN_CS => SEN_CS,
        SEN_SDO => SEN_SDO,
        LED(0) => LED_0,
        LED(1) => LED_1,
        LED(2) => LED_2,
        LED(3) => LED_3,
        LED(4) => LED_4,
        LED(5) => LED_5,
        LED(6) => LED_6,
        LED(7) => LED_7
    );
end rtl;
