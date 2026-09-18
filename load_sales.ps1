$ErrorActionPreference = "Stop"

$salesDir = ".\sales"
$bulkFile = Join-Path (Get-Location) "sales_stage_bulk.tsv"
$containerBulkFile = "/tmp/sales_stage_bulk.tsv"

Write-Host "=============================================="
Write-Host "STEP 1: BUILDING NORMALIZED BULK FILE"
Write-Host "=============================================="

if (Test-Path $bulkFile) { Remove-Item $bulkFile -Force }

$writer = [System.IO.StreamWriter]::new(
    $bulkFile, $false, [System.Text.UTF8Encoding]::new($false)
)

$fileCount = 0
$rowCount = 0

try {
    Get-ChildItem $salesDir -File | ForEach-Object {
        $file = $_
        $header = Get-Content $file.FullName -TotalCount 1

        if ($header -like "ts,bill_no,line_no,*") {
            $format = "timestamp"; $delimiter = ","
        }
        elseif ($header -like "bill_no;line_no;item_code;*") {
            $format = "return"; $delimiter = ";"
        }
        elseif ($header -like "bill_no,line_no,product_code,*") {
            $format = "standard"; $delimiter = ","
        }
        else {
            Write-Warning "UNKNOWN FORMAT: $($file.Name)"
            return
        }

        if ($file.Name -match '^SALES_S\d{2}_(\d{8})(?:__R(\d+))?\.csv$') {
            $businessDate = [datetime]::ParseExact($matches[1], "yyyyMMdd", $null).ToString("yyyy-MM-dd")
            $resendNo = if ($matches[2]) { [int]$matches[2] } else { 0 }
        }
        else {
            Write-Warning "UNKNOWN FILENAME: $($file.Name)"
            return
        }

        $fileCount++
        if (($fileCount % 100) -eq 0) { Write-Host "Processed files: $fileCount" }

        Import-Csv $file.FullName -Delimiter $delimiter | ForEach-Object {
            if ($format -eq "return") {
                $billNo = $_.bill_no; $lineNo = [int]$_.line_no
                $productCode = $_.item_code; $qty = [int]$_.quantity
                $unitPrice = $_.rate; $lineType = $_.type
                $txnTime = [datetime]::ParseExact($_.txn_time, "dd-MM-yyyy HH:mm:ss", $null).ToString("yyyy-MM-dd HH:mm:ss")
            }
            elseif ($format -eq "timestamp") {
                $billNo = $_.bill_no; $lineNo = [int]$_.line_no
                $productCode = $_.product_code; $qty = [int]$_.qty
                $unitPrice = $_.unit_price; $lineType = $_.line_type
                $txnTime = [DateTimeOffset]::FromUnixTimeSeconds([long]$_.ts).UtcDateTime.ToString("yyyy-MM-dd HH:mm:ss")
            }
            else {
                $billNo = $_.bill_no; $lineNo = [int]$_.line_no
                $productCode = $_.product_code; $qty = [int]$_.qty
                $unitPrice = $_.unit_price; $lineType = $_.line_type
                $txnTime = ([datetime]$_.ts).ToString("yyyy-MM-dd HH:mm:ss")
            }

            $billNo = ([string]$billNo).Replace("`t"," ").Replace("`r"," ").Replace("`n"," ")
            $productCode = ([string]$productCode).Replace("`t"," ").Replace("`r"," ").Replace("`n"," ")
            $lineType = ([string]$lineType).Replace("`t"," ").Replace("`r"," ").Replace("`n"," ")
            $sourceFile = $file.Name.Replace("`t"," ")

            $writer.WriteLine("$billNo`t$lineNo`t$productCode`t$qty`t$unitPrice`t$lineType`t$txnTime`t$businessDate`t$sourceFile`t$resendNo")
            $rowCount++
        }
    }
}
finally {
    $writer.Close()
    $writer.Dispose()
}

Write-Host ""
Write-Host "Files processed : $fileCount"
Write-Host "Rows prepared   : $rowCount"

Write-Host ""
Write-Host "=============================================="
Write-Host "STEP 2: BULK LOADING INTO POSTGRESQL"
Write-Host "=============================================="

docker exec postgres psql -U admin -d salesdb -v ON_ERROR_STOP=1 -c @"
DROP TABLE IF EXISTS sales_stage_load;
CREATE TABLE sales_stage_load (
    bill_no TEXT NOT NULL,
    line_no INTEGER NOT NULL,
    product_code TEXT,
    qty INTEGER NOT NULL,
    unit_price NUMERIC(12,2) NOT NULL,
    line_type TEXT NOT NULL,
    txn_time TIMESTAMP,
    business_date DATE NOT NULL,
    source_file TEXT NOT NULL,
    resend_no INTEGER NOT NULL DEFAULT 0
);
"@

docker cp $bulkFile "postgres:$containerBulkFile"
docker exec postgres psql -U admin -d salesdb -v ON_ERROR_STOP=1 -c "\copy sales_stage_load FROM '/tmp/sales_stage_bulk.tsv' WITH (FORMAT text, DELIMITER E'\t')"

docker exec postgres psql -U admin -d salesdb -v ON_ERROR_STOP=1 -c @"
INSERT INTO sales_stage
(bill_no,line_no,product_code,qty,unit_price,line_type,txn_time,business_date,source_file,resend_no)
SELECT bill_no,line_no,product_code,qty,unit_price,line_type,txn_time,business_date,source_file,resend_no
FROM sales_stage_load
ON CONFLICT (bill_no,line_no) DO NOTHING;
"@

Write-Host ""
Write-Host "=============================================="
Write-Host "FINAL VALIDATION"
Write-Host "=============================================="

docker exec postgres psql -U admin -d salesdb -c "SELECT COUNT(*) AS stage_rows FROM sales_stage;"
docker exec postgres psql -U admin -d salesdb -c "SELECT COUNT(DISTINCT source_file) AS source_files FROM sales_stage;"

docker exec postgres psql -U admin -d salesdb -c "DROP TABLE sales_stage_load;"
docker exec postgres rm -f $containerBulkFile

Write-Host ""
Write-Host "=============================================="
Write-Host "BULK LOAD COMPLETE"
Write-Host "=============================================="