param([Parameter(Mandatory=$true)][string]$Source, [Parameter(Mandatory=$true)][string]$Output,
      [string]$OwnerRecord)
$ErrorActionPreference = 'Stop'
$word = $null
$document = $null
$bootstrap = $null
$phase = 'initialization'
try {
    $previousWordIds = @(Get-Process WINWORD -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id)
    $word = New-Object -ComObject Word.Application
    $word.Visible = $false
    $word.DisplayAlerts = 0
    $word.AutomationSecurity = 3
    if ($OwnerRecord) {
        Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class ReportWordProcess {
    [DllImport("user32.dll")]
    public static extern uint GetWindowThreadProcessId(IntPtr window, out uint processId);
}
'@
        $wordProcessId = [uint32]0
        # Word exposes Hwnd on a document Window, not on Application. A blank
        # helper document identifies our process before the large report opens.
        $bootstrap = $word.Documents.Add()
        [void][ReportWordProcess]::GetWindowThreadProcessId([IntPtr]$bootstrap.ActiveWindow.Hwnd, [ref]$wordProcessId)
        $wordProcess = Get-Process -Id $wordProcessId -ErrorAction Stop
        [pscustomobject]@{pid=$wordProcessId; startedTicks=$wordProcess.StartTime.ToUniversalTime().Ticks;
            owned=($previousWordIds -notcontains $wordProcessId)} | ConvertTo-Json -Compress |
            Set-Content -LiteralPath $OwnerRecord -Encoding utf8
        if ($PSVersionTable.PSVersion.Major -lt 7) {
            $discardBootstrap = 0; $bootstrap.Close([ref]$discardBootstrap)
        } else { $bootstrap.Close(0) }
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($bootstrap)
        $bootstrap = $null
    }
    $phase = 'open'
    if ($PSVersionTable.PSVersion.Major -lt 7) {
        $confirmConversions = $false; $readOnly = $true; $addToRecent = $false
        $document = $word.Documents.Open([ref]$Source, [ref]$confirmConversions, [ref]$readOnly, [ref]$addToRecent)
    } else { $document = $word.Documents.Open($Source, $false, $true, $false) }
    $phase = 'update'
    $document.Fields.Update() | Out-Null
    foreach ($toc in $document.TablesOfContents) { $toc.Update() }
    $document.Repaginate()
    foreach ($toc in $document.TablesOfContents) { $toc.UpdatePageNumbers() }
    $phase = 'save'
    if ($PSVersionTable.PSVersion.Major -lt 7) {
        $fileFormat = 16
        $document.SaveAs2([ref]$Output, [ref]$fileFormat)
    } else { $document.SaveAs2($Output, 16) }
    [pscustomobject]@{pages=$document.ComputeStatistics(2)} | ConvertTo-Json -Compress
} catch {
    throw "Word field update failed at phase: $phase"
} finally {
    $discard = 0
    if ($bootstrap) {
        if ($PSVersionTable.PSVersion.Major -lt 7) { $bootstrap.Close([ref]$discard) } else { $bootstrap.Close(0) }
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($bootstrap)
    }
    if ($document) {
        if ($PSVersionTable.PSVersion.Major -lt 7) { $document.Close([ref]$discard) } else { $document.Close(0) }
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($document)
    }
    if ($word) {
        if ($PSVersionTable.PSVersion.Major -lt 7) { $word.Quit([ref]$discard) } else { $word.Quit(0) }
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($word)
    }
}
