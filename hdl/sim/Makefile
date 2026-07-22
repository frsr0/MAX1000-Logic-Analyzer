.PHONY: analyze compile run clean help

# Testbench targets for ACK pad timing analysis

GHDL := ghdl
GHDL_FLAGS := --std=08 -fsynopsys

PKG_FILES := ../rtl/spi_protocol_pkg.vhd
TB_FILES := tb_stream_protocol_timing.vhd

# Derived output names
WORK_DIR := work
TB_NAME := tb_stream_protocol_timing

help:
	@echo "ACK Pad Timing Testbench"
	@echo "make analyze  - Check VHDL syntax"
	@echo "make compile  - Compile testbench"
	@echo "make run      - Run simulation"
	@echo "make clean    - Remove work files"

analyze:
	@echo "[1/3] Analyzing package..."
	$(GHDL) -a $(GHDL_FLAGS) $(PKG_FILES)
	@echo "[2/3] Analyzing testbench..."
	$(GHDL) -a $(GHDL_FLAGS) $(TB_FILES)
	@echo "✓ Syntax check passed"

compile: analyze
	@echo "[3/3] Elaborating design..."
	$(GHDL) -e $(GHDL_FLAGS) $(TB_NAME)
	@echo "✓ Compile successful"

run: compile
	@echo "Running simulation..."
	$(GHDL) -r $(GHDL_FLAGS) $(TB_NAME) --stop-time=1us 2>&1 | tee tb_output.log
	@echo ""
	@echo "=== RESULTS ==="
	@grep -E "ACK|Results|Recommendations|byte|safe" tb_output.log || echo "(Check tb_output.log for details)"

clean:
	rm -rf work *.cf *.o tb_output.log
	$(GHDL) --remove $(GHDL_FLAGS) 2>/dev/null || true

.SILENT: help
