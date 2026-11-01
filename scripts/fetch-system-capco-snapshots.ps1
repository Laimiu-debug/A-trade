$ErrorActionPreference = 'Stop'
$taskRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$intakeRoot = Join-Path $taskRoot 'artifacts/strategy-search/market-sector-stock-system-v1/capco-intake-v1'
$articleRoot = Join-Path $intakeRoot 'articles'
if (Test-Path -LiteralPath $articleRoot) { throw 'Article archive exists; refusing overwrite' }
New-Item -ItemType Directory -Path $articleRoot | Out-Null
$cataloguePath = Join-Path $intakeRoot 'catalogue-records.json'
$catalogue = Get-Content -LiteralPath $cataloguePath -Raw -Encoding utf8 | ConvertFrom-Json
$selected = @($catalogue.records | Where-Object {
    $_.articleUrl.StartsWith('zsxfb/hyfl/hyfljg/') -and $_.publicationDate -ge '2024-04-01' -and $_.publicationDate -le '2026-10-15'
} | Sort-Object publicationDate)
if ($selected.Count -ne 6) { throw 'Expected six observed dated article records' }
$receipts = @()
foreach ($item in $selected) {
    $articleUrl = 'https://www.capco.org.cn/dataFile/' + $item.articleUrl
    $receipt = @{ title=$item.title; publicationDate=$item.publicationDate; url=$articleUrl; requestedAt=[DateTime]::UtcNow.ToString('o'); certificateVerificationDisabled=$false; attempts=1 }
    try {
        $response = Invoke-WebRequest -Uri $articleUrl -TimeoutSec 25
        $raw = $response.RawContentStream.ToArray()
        if ($raw.Length -gt 2000000) { throw 'Article exceeds bounded2MB source intake' }
        $target = Join-Path $articleRoot ($item.publicationDate + '.source.js')
        [IO.File]::WriteAllBytes($target, $raw)
        $receipt.status='archived'
        $receipt.statusCode=[int]$response.StatusCode
        $receipt.bytes=$raw.Length
        $receipt.path=$target.Substring($taskRoot.Length+1).Replace('\','/')
        $receipt.sha256=(Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant()
        Write-Output ('Archived article ' + $item.publicationDate)
    } catch {
        $receipt.status='unavailable'
        $receipt.error=$_.Exception.Message
        Write-Output ('Article unavailable ' + $item.publicationDate)
    }
    $receipts += $receipt
    if ($receipt.status -ne 'archived') { break }
}
@{
    completedAt=[DateTime]::UtcNow.ToString('o')
    cataloguePath=$cataloguePath.Substring($taskRoot.Length+1).Replace('\','/')
    catalogueSha256=(Get-FileHash -LiteralPath $cataloguePath -Algorithm SHA256).Hash.ToLowerInvariant()
    currentSiteSourceSha256=(Get-FileHash -LiteralPath (Join-Path $intakeRoot 'current-site.js') -Algorithm SHA256).Hash.ToLowerInvariant()
    articleLoaderSourceSha256=(Get-FileHash -LiteralPath (Join-Path $intakeRoot 'article-detail-loader.js') -Algorithm SHA256).Hash.ToLowerInvariant()
    selected=$selected
    receipts=$receipts
    allSixArchived=($receipts.Count -eq 6 -and @($receipts | Where-Object {$_.status -ne 'archived'}).Count -eq 0)
    sourceCodeExecuted=$false
    newMarketAccounts=0
} | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $articleRoot 'receipt.json') -Encoding utf8
