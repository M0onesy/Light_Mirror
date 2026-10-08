param(
  [string]$MainRoot = (Split-Path -Parent $PSScriptRoot | Split-Path -Parent),
  [string]$ReceiptPath
)
$ErrorActionPreference = 'Stop'
$proposalPath = Join-Path $MainRoot '明镜MingJing_项目计划书.docx'
$pdfPath = Join-Path $MainRoot '明镜MingJing_项目计划书_初赛评审版.pdf'
$word = $null
$doc = $null
$result = [ordered]@{
  schema_version = 'mingjing-word-refresh.v1'
  created_at = [DateTimeOffset]::UtcNow.ToString('o')
  proposal = $proposalPath
  pdf = $pdfPath
  status = 'started'
}
try {
  $word = New-Object -ComObject Word.Application
  $word.Visible = $false
  $word.DisplayAlerts = 0
  $doc = $word.Documents.Open($proposalPath, $false, $false)
  $doc.Fields.Update() | Out-Null
  foreach ($toc in $doc.TablesOfContents) { $toc.Update(); $toc.UpdatePageNumbers() }
  $doc.Repaginate()
  $doc.Save()
  $doc.ExportAsFixedFormat($pdfPath, 17)
  $result.status = 'passed'
  $result.tables_of_contents = $doc.TablesOfContents.Count
  $result.fields = $doc.Fields.Count
  $result.pages = $doc.ComputeStatistics(2)
  $result.word_version = $word.Version
  $result.proposal_sha256 = (Get-FileHash -LiteralPath $proposalPath -Algorithm SHA256).Hash.ToLowerInvariant()
  $result.pdf_sha256 = (Get-FileHash -LiteralPath $pdfPath -Algorithm SHA256).Hash.ToLowerInvariant()
} catch {
  $result.status = 'failed'
  $result.error = $_.Exception.Message
  throw
} finally {
  if ($null -ne $doc) {
    $doc.Close(0)
    [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($doc)
  }
  if ($null -ne $word) {
    $word.Quit()
    [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($word)
  }
  if ($ReceiptPath) {
    $json = $result | ConvertTo-Json -Depth 5
    [IO.File]::WriteAllText($ReceiptPath, $json, [Text.UTF8Encoding]::new($false))
  }
  [GC]::Collect()
  [GC]::WaitForPendingFinalizers()
}
$result | ConvertTo-Json -Depth 5
