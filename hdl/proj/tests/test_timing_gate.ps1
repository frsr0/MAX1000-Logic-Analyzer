$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '..\timing_gate.ps1')

function Assert-TimingRejected {
    param([string]$Path, [string]$ExpectedMessage)
    try {
        Assert-QuartusTimingSummary -Path $Path
        throw "Expected timing summary rejection: $Path"
    } catch {
        if ($_.Exception.Message -notlike "*$ExpectedMessage*") {
            throw
        }
    }
}

$fixturePath = Join-Path $env:TEMP ("ols_timing_gate_" + [guid]::NewGuid() + '.sta.summary')
try {
    @'
Timing Analyzer Summary
Type  : Slow Setup 'fast_clk'
Slack : 0.001
TNS   : 0.000
Type  : Slow Hold 'sys_clk'
Slack : 0.250
TNS   : 0.000
'@ | Set-Content -LiteralPath $fixturePath -Encoding ASCII
    Assert-QuartusTimingSummary -Path $fixturePath

    @'
Timing Analyzer Summary
Type  : Slow Setup 'fast_clk'
Slack : -0.001
'@ | Set-Content -LiteralPath $fixturePath -Encoding ASCII
    Assert-TimingRejected -Path $fixturePath -ExpectedMessage 'negative slack'

    @'
Timing Analyzer Summary
Type  : Slow Setup 'fast_clk'
Slack : unknown
'@ | Set-Content -LiteralPath $fixturePath -Encoding ASCII
    Assert-TimingRejected -Path $fixturePath -ExpectedMessage 'invalid Slack value'

    @'
Timing Analyzer Summary
Type  : Slow Setup 'fast_clk'
'@ | Set-Content -LiteralPath $fixturePath -Encoding ASCII
    Assert-TimingRejected -Path $fixturePath -ExpectedMessage 'has no Slack value'

    Assert-TimingRejected -Path ($fixturePath + '.missing') -ExpectedMessage 'is missing'

    $currentSummary = Join-Path $PSScriptRoot '..\output_files\OLS_Logic_Analyzer.sta.summary'
    if (Test-Path -LiteralPath $currentSummary) {
        Assert-TimingRejected -Path $currentSummary -ExpectedMessage 'negative slack'
    }
} finally {
    Remove-Item -LiteralPath $fixturePath -Force -ErrorAction SilentlyContinue
}

Write-Host 'Timing gate tests: PASS'
