function Assert-QuartusTimingSummary {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Timing Analyzer summary is missing: $Path"
    }

    $typeCount = 0
    $slackCount = 0
    $currentType = $null
    $currentTypeHasSlack = $false
    $negativeSlacks = @()
    $numberPattern = '[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?'

    foreach ($line in Get-Content -LiteralPath $Path) {
        if ($line -match '^\s*Type\s*:\s*(\S.*)\s*$') {
            if ($null -ne $currentType -and -not $currentTypeHasSlack) {
                throw "Malformed timing summary: '$currentType' has no Slack value"
            }
            $currentType = $matches[1]
            $currentTypeHasSlack = $false
            $typeCount++
            continue
        }

        if ($line -match '^\s*Slack\s*:') {
            if ($null -eq $currentType -or $currentTypeHasSlack) {
                throw "Malformed timing summary: unexpected or duplicate Slack line '$line'"
            }
            if ($line -notmatch ('^\s*Slack\s*:\s*(' + $numberPattern + ')\s*$')) {
                throw "Malformed timing summary: invalid Slack value '$line'"
            }
            $slack = [double]::Parse(
                $matches[1],
                [Globalization.NumberStyles]::Float,
                [Globalization.CultureInfo]::InvariantCulture)
            if ($slack -lt 0) {
                $negativeSlacks += "${currentType}: $($matches[1])"
            }
            $slackCount++
            $currentTypeHasSlack = $true
        }
    }

    if ($null -ne $currentType -and -not $currentTypeHasSlack) {
        throw "Malformed timing summary: '$currentType' has no Slack value"
    }
    if ($typeCount -eq 0 -or $slackCount -eq 0 -or $typeCount -ne $slackCount) {
        throw "Malformed timing summary: expected a Type and Slack value for each timing check"
    }
    if ($negativeSlacks.Count -gt 0) {
        throw "Timing requirements failed; negative slack: $($negativeSlacks -join '; ')"
    }

    Write-Host "Timing summary: VERIFIED ($slackCount checks, all Slack >= 0 ns)"
}
