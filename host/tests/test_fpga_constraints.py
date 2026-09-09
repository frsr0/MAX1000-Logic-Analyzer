from pathlib import Path


def test_every_mkr_d_input_has_the_intended_weak_pullup():
    repo = Path(__file__).resolve().parents[2]
    qsf = (repo / "hdl" / "proj" / "OLS_Logic_Analyzer.qsf").read_text()
    build_script = (repo / "hdl" / "proj" / "compile.ps1").read_text()

    assert "-to GPIO[" not in qsf
    assert "-to GPIO[" not in build_script
    for channel in range(15):
        assignment = (
            "set_instance_assignment -name WEAK_PULL_UP_RESISTOR ON "
            f"-to MKR_D[{channel}]"
        )
        assert assignment in qsf
        assert f"'{assignment}'" in build_script
