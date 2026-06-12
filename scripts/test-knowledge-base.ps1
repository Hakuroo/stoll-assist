$ErrorActionPreference = "Stop"
$Utf8 = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = $Utf8
$OutputEncoding = $Utf8

Write-Host "Importing configured knowledge files:"
$import = Invoke-RestMethod `
    -Uri "http://localhost:8000/operator/knowledge/import-config" `
    -Method Post
$import | Format-List files, created, updated, unchanged

Write-Host "Checking that draft knowledge is not searchable:"
$draftSearchBody = @{ query = "que informacion necesitan para evaluar un galpon"; limit = 5 } |
    ConvertTo-Json
$beforePublish = Invoke-RestMethod `
    -Uri "http://localhost:8000/operator/knowledge/search" `
    -Method Post `
    -ContentType "application/json; charset=utf-8" `
    -Body ([System.Text.Encoding]::UTF8.GetBytes($draftSearchBody))

[PSCustomObject]@{
    hits_before_publish = $beforePublish.hits.Count
} | Format-List

if ($beforePublish.hits.Count -ne 0) {
    throw "Draft knowledge must not appear in search results"
}

Write-Host "Publishing KB-001 and KB-002:"
$publishBody = @{ approved_by = "Elian" } | ConvertTo-Json
$kb1 = Invoke-RestMethod `
    -Uri "http://localhost:8000/operator/knowledge/KB-001/publish" `
    -Method Post `
    -ContentType "application/json; charset=utf-8" `
    -Body ([System.Text.Encoding]::UTF8.GetBytes($publishBody))
$kb2 = Invoke-RestMethod `
    -Uri "http://localhost:8000/operator/knowledge/KB-002/publish" `
    -Method Post `
    -ContentType "application/json; charset=utf-8" `
    -Body ([System.Text.Encoding]::UTF8.GetBytes($publishBody))

$kb1 | Format-List external_key, status, version, approved_by
$kb2 | Format-List external_key, status, version, approved_by

Write-Host "Searching only published knowledge:"
$searchBody = @{ query = "que informacion necesitan para evaluar un galpon"; limit = 5 } |
    ConvertTo-Json
$search = Invoke-RestMethod `
    -Uri "http://localhost:8000/operator/knowledge/search" `
    -Method Post `
    -ContentType "application/json; charset=utf-8" `
    -Body ([System.Text.Encoding]::UTF8.GetBytes($searchBody))

$search.hits | Select-Object external_key, title, risk_class, version, score | Format-Table

if ($search.hits.Count -lt 1) {
    throw "Expected at least one published knowledge result"
}
if ($search.hits[0].external_key -ne "KB-002") {
    throw "Expected KB-002 as the top result"
}

Write-Host "Knowledge safety details from the top result:"
[PSCustomObject]@{
    external_key = $search.hits[0].external_key
    allowed_claims = ($search.hits[0].allowed_claims -join " | ")
    forbidden_claims = ($search.hits[0].forbidden_claims -join " | ")
} | Format-List

Write-Host "Knowledge base test completed successfully."
