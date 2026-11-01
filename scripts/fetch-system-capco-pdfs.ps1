$ErrorActionPreference = 'Stop'
$taskRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$intakeRoot = Join-Path $taskRoot 'artifacts/strategy-search/market-sector-stock-system-v1/capco-intake-v1'
$pdfRoot = Join-Path $intakeRoot 'pdfs'
if (Test-Path -LiteralPath $pdfRoot) { throw 'PDF archive exists; refusing overwrite' }
New-Item -ItemType Directory -Path $pdfRoot | Out-Null
$manifestPath = Join-Path $intakeRoot 'pdf-manifest.json'
$manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding utf8 | ConvertFrom-Json
$receipts = @()
$stop = $false
foreach ($snapshot in $manifest.snapshots) {
    foreach ($attachment in $snapshot.attachments) {
        $receipt = @{publicationDate=$snapshot.publicationDate; sortOrder=$attachment.sortOrder; url=$attachment.url; requestedAt=[DateTime]::UtcNow.ToString('o');certificateVerificationDisabled=$false;attempts=1}
        try {
            $response = Invoke-WebRequest -Uri $attachment.url -TimeoutSec 25
            $raw = $response.RawContentStream.ToArray()
            if ($raw.Length -gt 20000000 -or $raw.Length -lt 1000 -or [Text.Encoding]::ASCII.GetString($raw[0..4]) -ne '%PDF-') { throw 'Expected a bounded original PDF response' }
            $target = Join-Path $pdfRoot ($snapshot.publicationDate + '.' + $attachment.sortOrder + '.pdf')
            [IO.File]::WriteAllBytes($target,$raw)
            $receipt.status='archived'
            $receipt.bytes=$raw.Length
            $receipt.path=$target.Substring($taskRoot.Length+1).Replace('\','/')
            $receipt.sha256=(Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant()
            Write-Output ('Archived PDF ' + $snapshot.publicationDate + ' ' + $attachment.sortOrder)
        } catch {
            $receipt.status='unavailable'
            $receipt.error=$_.Exception.Message
            Write-Output ('PDF unavailable ' + $snapshot.publicationDate + ' ' + $attachment.sortOrder)
            $stop = $true
        }
        $receipts += $receipt
        if ($stop) { break }
    }
    if ($stop) { break }
}
@{completedAt=[DateTime]::UtcNow.ToString('o');manifestSha256=(Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256).Hash.ToLowerInvariant();receipts=$receipts;allTwelveArchived=($receipts.Count -eq 12 -and -not $stop);newMarketAccounts=0} | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $pdfRoot 'receipt.json') -Encoding utf8
