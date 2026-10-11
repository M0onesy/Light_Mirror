param(
  [string]$DocumentRoot = (Join-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) 'docs\project'),
  [Parameter(Mandatory=$true)][string]$OutputDirectory,
  [switch]$ExportOnly,
  [ValidateSet('ExportAsFixedFormat','SaveAs2','PrintToPdf')][string]$PdfMethod = 'ExportAsFixedFormat'
)
$ErrorActionPreference = 'Stop'
if (Test-Path -LiteralPath $OutputDirectory) { throw 'OutputDirectory must be new; current proposal/PDF are never overwritten.' }
New-Item -ItemType Directory -Path $OutputDirectory | Out-Null
$proposalPath = Join-Path $OutputDirectory '明镜MingJing_项目计划书.docx'
Copy-Item -LiteralPath (Join-Path $DocumentRoot '明镜MingJing_项目计划书.docx') -Destination $proposalPath
$pdfPath = Join-Path $OutputDirectory '明镜MingJing_项目计划书_初赛评审版.pdf'
$ReceiptPath = Join-Path $OutputDirectory 'word.json'
$temporaryPdfPath = Join-Path ([IO.Path]::GetTempPath()) ('MingJing-dev3-' + [Guid]::NewGuid().ToString('N') + '.pdf')
$word = $null
$doc = $null
$receipt = [ordered]@{
  schema = 'mingjing-word-refresh.v2'
  version = '1.0.0.dev3'
  created_at = [DateTimeOffset]::UtcNow.ToString('o')
  proposal = $proposalPath
  pdf = $pdfPath
  status = 'started'
  export_only = [bool]$ExportOnly
  pdf_method = $PdfMethod
  source_sha256_before_fields = (Get-FileHash -LiteralPath $proposalPath -Algorithm SHA256).Hash.ToLowerInvariant()
  member_names = 'awaiting_real_team'
  official_submission = 'not_performed'
}
function Write-TaskProgress([string]$stage) {
  $receipt.stage = $stage
  $receipt.updated_at = [DateTimeOffset]::UtcNow.ToString('o')
  $receiptParent = Split-Path -Parent $ReceiptPath
  New-Item -ItemType Directory -Path $receiptParent -Force | Out-Null
  [IO.File]::WriteAllText($ReceiptPath, ($receipt | ConvertTo-Json -Depth 6), [Text.UTF8Encoding]::new($false))
  Write-Output $stage
}
try {
  Write-TaskProgress 'word_create'
  $word = New-Object -ComObject Word.Application
  $word.Visible = $false
  $word.DisplayAlerts = 0
  Write-TaskProgress 'document_open'
  $doc = $word.Documents.Open($proposalPath, $false, [bool]$ExportOnly, $false)
  if (-not $ExportOnly) {
    Write-TaskProgress 'fields_update'
    $doc.Fields.Update() | Out-Null
    foreach ($toc in $doc.TablesOfContents) {
      Write-TaskProgress 'toc_update'
      $toc.Update()
      $toc.UpdatePageNumbers()
    }
    Write-TaskProgress 'repaginate'
    $doc.Repaginate()
    Write-TaskProgress 'document_save'
    $doc.Save()
    $receipt.fields_refreshed_this_attempt = $true
  } else {
    $receipt.fields_refreshed_this_attempt = $false
    $receipt.field_refresh_evidence = 'word-attempt-01-timeout.json'
  }
  Write-TaskProgress 'page_statistics'
  $receipt.word_version = $word.Version
  $receipt.pages = $doc.ComputeStatistics(2)
  $receipt.tables_of_contents = $doc.TablesOfContents.Count
  $receipt.fields = $doc.Fields.Count
  Write-TaskProgress 'pdf_export'
  if ($PdfMethod -eq 'PrintToPdf') {
    $word.ActivePrinter = 'Microsoft Print to PDF'
    $doc.PrintOut($false, $false, 0, $temporaryPdfPath,
      [Type]::Missing, [Type]::Missing, 0, 1, [Type]::Missing, 0, $true, $true)
  } elseif ($PdfMethod -eq 'SaveAs2') {
    $doc.SaveAs2($temporaryPdfPath, 17)
  } else {
    $doc.ExportAsFixedFormat($temporaryPdfPath, 17)
  }
  Copy-Item -LiteralPath $temporaryPdfPath -Destination $pdfPath -Force
  $receipt.status = 'word_export_passed_visual_review_pending'
  $receipt.pdf_sha256 = (Get-FileHash -LiteralPath $pdfPath -Algorithm SHA256).Hash.ToLowerInvariant()
  $receipt.pdf_bytes = (Get-Item -LiteralPath $pdfPath).Length
} catch {
  $receipt.status = 'failed'
  $receipt.error = $_.Exception.Message
  throw
} finally {
  if ($null -ne $doc) {
    try { $doc.Close(0) } catch { $receipt.document_close_error = $_.Exception.Message }
    try { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($doc) } catch {}
  }
  if ($null -ne $word) {
    try { $word.Quit() } catch { $receipt.word_quit_error = $_.Exception.Message }
    try { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($word) } catch {}
  }
  $receipt.proposal_sha256 = (Get-FileHash -LiteralPath $proposalPath -Algorithm SHA256).Hash.ToLowerInvariant()
  $receiptParent = Split-Path -Parent $ReceiptPath
  New-Item -ItemType Directory -Path $receiptParent -Force | Out-Null
  [IO.File]::WriteAllText($ReceiptPath, ($receipt | ConvertTo-Json -Depth 6), [Text.UTF8Encoding]::new($false))
  [GC]::Collect()
  [GC]::WaitForPendingFinalizers()
  if (Test-Path -LiteralPath $temporaryPdfPath) { Remove-Item -LiteralPath $temporaryPdfPath }
}
$receipt | ConvertTo-Json -Depth 6
