package require ::quartus::flow

project_new OLS_Logic_Analyzer -overwrite

set_global_assignment -name FAMILY "MAX 10"
set_global_assignment -name DEVICE 10M08SAU169C8G
set_global_assignment -name TOP_LEVEL_ENTITY OLS_Logic_Analyzer_wrapper
set_global_assignment -name NUM_PARALLEL_PROCESSORS 10
set_global_assignment -name INTERNAL_FLASH_UPDATE_MODE "SINGLE IMAGE WITH ERAM"

set_global_assignment -name SDC_FILE OLS_Logic_Analyzer.sdc

set_global_assignment -name VHDL_FILE OLS_SDRAM_Top.vhd
set_global_assignment -name VHDL_FILE LED_Controller.vhd
set_global_assignment -name VHDL_FILE OLS_Interface.vhd
set_global_assignment -name VHDL_FILE SPI_Slave.vhd
set_global_assignment -name VHDL_FILE UART_Interface.vhd
set_global_assignment -name VHDL_FILE SDRAM_Interface.vhd
set_global_assignment -name VHDL_FILE SDRAM_Controller_Custom.vhd
set_global_assignment -name VHDL_FILE ADC_Controller.vhd
set_global_assignment -name VHDL_FILE Protocol_Trigger.vhd
set_global_assignment -name VHDL_FILE Signal_Gen.vhd
set_global_assignment -name VHDL_FILE SDRAM_PLL.vhd
set_global_assignment -name VHDL_FILE Fast_Logic_Analyzer_SDRAM.vhd
set_global_assignment -name VHDL_FILE OLS_Logic_Analyzer_SDRAM_Core.vhd
set_global_assignment -name VHDL_FILE OLS_Logic_Analyzer_wrapper.vhd

# Altera Modular ADC II IP
set_global_assignment -name QIP_FILE MAX10_ADC/synthesis/MAX10_ADC.qip

load_package flow
execute_module -tool map
execute_module -tool fit
execute_module -tool asm
project_close
