param(
    [Parameter(Mandatory=$true)][string]$InputJson,
    [Parameter(Mandatory=$true)][string]$OutputJson
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime

$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
})[0]

function Await-WinRt($WinRtTask, $ResultType) {
    $asTask = $asTaskGeneric.MakeGenericMethod($ResultType)
    $netTask = $asTask.Invoke($null, @($WinRtTask))
    $netTask.Wait(-1) | Out-Null
    return $netTask.Result
}

[Windows.Storage.Streams.IRandomAccessStream, Windows.Storage.Streams, ContentType=WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType=WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.SoftwareBitmap, Windows.Graphics.Imaging, ContentType=WindowsRuntime] | Out-Null
[Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType=WindowsRuntime] | Out-Null
[Windows.Globalization.Language, Windows.Globalization, ContentType=WindowsRuntime] | Out-Null

$lang = [Windows.Globalization.Language]::new('zh-Hans-CN')
$ocr = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($lang)
if ($null -eq $ocr) {
    throw 'Could not initialize Windows OCR engine for zh-Hans-CN.'
}

$paths = Get-Content -LiteralPath $InputJson -Raw -Encoding UTF8 | ConvertFrom-Json
$results = New-Object System.Collections.ArrayList

foreach ($imgPath in $paths) {
    $fs = $null
    $ras = $null
    $bitmap = $null
    try {
        $abs = (Resolve-Path -LiteralPath $imgPath).Path
        $fs = [System.IO.File]::OpenRead($abs)
        $ras = [System.IO.WindowsRuntimeStreamExtensions]::AsRandomAccessStream($fs)
        $decoder = Await-WinRt ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($ras)) ([Windows.Graphics.Imaging.BitmapDecoder])
        $bitmap = Await-WinRt ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
        $res = Await-WinRt ($ocr.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
        $linesOut = New-Object System.Collections.ArrayList
        foreach ($line in $res.Lines) {
            $wordsOut = New-Object System.Collections.ArrayList
            foreach ($w in $line.Words) {
                $r = $w.BoundingRect
                [void]$wordsOut.Add(@{
                    t = $w.Text
                    x = [math]::Round($r.X, 1)
                    y = [math]::Round($r.Y, 1)
                    w = [math]::Round($r.Width, 1)
                    h = [math]::Round($r.Height, 1)
                })
            }
            [void]$linesOut.Add(@{
                text = $line.Text
                words = $wordsOut
            })
        }
        [void]$results.Add(@{
            path = $imgPath
            ok = $true
            width = $decoder.PixelWidth
            height = $decoder.PixelHeight
            lines = $linesOut
        })
    } catch {
        $ex = $_.Exception
        $msgs = New-Object System.Collections.ArrayList
        while ($null -ne $ex) {
            [void]$msgs.Add($ex.GetType().Name + ': ' + $ex.Message)
            $ex = $ex.InnerException
        }
        [void]$results.Add(@{
            path = $imgPath
            ok = $false
            error = ($msgs -join ' -> ')
        })
    } finally {
        if ($null -ne $bitmap) { $bitmap.Dispose() }
        if ($null -ne $ras) { $ras.Dispose() }
        if ($null -ne $fs) { $fs.Dispose() }
    }
}

$jsonOut = ConvertTo-Json -InputObject $results -Depth 6 -Compress
[System.IO.File]::WriteAllText($OutputJson, $jsonOut, (New-Object System.Text.UTF8Encoding($false)))
Write-Output ("OCR_DONE count=" + $results.Count)
