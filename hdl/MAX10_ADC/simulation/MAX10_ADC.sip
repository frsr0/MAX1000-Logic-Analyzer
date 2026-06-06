set_global_assignment -entity "MAX10_ADC" -library "lib_MAX10_ADC" -name IP_TOOL_NAME "Qsys"
set_global_assignment -entity "MAX10_ADC" -library "lib_MAX10_ADC" -name IP_TOOL_VERSION "18.1"
set_global_assignment -entity "MAX10_ADC" -library "lib_MAX10_ADC" -name IP_TOOL_ENV "Qsys"
set_global_assignment -library "lib_MAX10_ADC" -name SPD_FILE [file join $::quartus(sip_path) "../MAX10_ADC.spd"]
set_global_assignment -library "lib_MAX10_ADC" -name MISC_FILE [file join $::quartus(sip_path) "../../MAX10_ADC.qsys"]

set_global_assignment -library "lib_MAX10_ADC" -name MISC_FILE [file join $::quartus(sip_path) "MAX10_ADC.vhd"]
set_global_assignment -library "lib_MAX10_ADC" -name MISC_FILE [file join $::quartus(sip_path) "submodules/MAX10_ADC_modular_adc_0.vhd"]
set_global_assignment -library "lib_MAX10_ADC" -name MISC_FILE [file join $::quartus(sip_path) "submodules/altera_modular_adc_control.v"]
set_global_assignment -library "lib_MAX10_ADC" -name MISC_FILE [file join $::quartus(sip_path) "submodules/altera_modular_adc_control_avrg_fifo.v"]
set_global_assignment -library "lib_MAX10_ADC" -name MISC_FILE [file join $::quartus(sip_path) "submodules/altera_modular_adc_control_fsm.v"]
set_global_assignment -library "lib_MAX10_ADC" -name MISC_FILE [file join $::quartus(sip_path) "submodules/chsel_code_converter_sw_to_hw.v"]
set_global_assignment -library "lib_MAX10_ADC" -name MISC_FILE [file join $::quartus(sip_path) "submodules/fiftyfivenm_adcblock_primitive_wrapper.v"]
set_global_assignment -library "lib_MAX10_ADC" -name MISC_FILE [file join $::quartus(sip_path) "submodules/fiftyfivenm_adcblock_top_wrapper.v"]
