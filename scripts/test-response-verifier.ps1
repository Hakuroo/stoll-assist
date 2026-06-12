[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding
$ErrorActionPreference = "Stop"

$baseUrl = "http://localhost:8000"

function Invoke-VerificationPreview {
    param(
        [string]$Decision,
        [string]$Draft,
        [string[]]$KnowledgeKeys,
        [string[]]$ForbiddenClaims = @()
    )

    $body = @{
        decision = $Decision
        draft_reply = $Draft
        knowledge_keys = $KnowledgeKeys
        forbidden_claims = $ForbiddenClaims
    } | ConvertTo-Json -Depth 6

    return Invoke-RestMethod `
        -Method Post `
        -Uri "$baseUrl/operator/verifier/preview" `
        -ContentType "application/json; charset=utf-8" `
        -Body ([System.Text.Encoding]::UTF8.GetBytes($body))
}

Write-Host "Previewing a supported answer:`n"
$safe = Invoke-VerificationPreview `
    -Decision "ANSWER" `
    -Draft "Para evaluar inicialmente un proyecto se solicita, cuando corresponda: ubicación, medidas aproximadas, altura, uso previsto, tipo de cerramiento, alcance solicitado, estado de platea y fundaciones, planos o fotografías disponibles y fecha estimada." `
    -KnowledgeKeys @("KB-002")
$safe | Format-List status, reason_code, unsupported_claims, verifier_version
if ($safe.status -ne "APPROVED") {
    throw "Expected APPROVED but received $($safe.status)"
}

Write-Host "`nPreviewing an unsafe invented answer:`n"
$unsafe = Invoke-VerificationPreview `
    -Decision "ANSWER" `
    -Draft "El precio estimado es de 25.000 dólares y podemos garantizar la entrega en 30 días." `
    -KnowledgeKeys @("KB-002")
$unsafe | Format-List status, reason_code, unsupported_claims, verifier_version
if ($unsafe.status -ne "REJECTED") {
    throw "Expected REJECTED but received $($unsafe.status)"
}

$timestamp = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
$messageId = "wamid.VERIFIER-$timestamp"
$phoneSuffix = $timestamp.ToString().Substring([Math]::Max(0, $timestamp.ToString().Length - 8))
$phone = "54911$phoneSuffix"

$payload = @{
    object = "whatsapp_business_account"
    entry = @(
        @{
            id = "test-entry"
            changes = @(
                @{
                    field = "messages"
                    value = @{
                        messaging_product = "whatsapp"
                        metadata = @{
                            display_phone_number = "5491100000000"
                            phone_number_id = "test-phone-number-id"
                        }
                        contacts = @(
                            @{
                                profile = @{ name = "Verifier Test" }
                                wa_id = $phone
                            }
                        )
                        messages = @(
                            @{
                                from = $phone
                                id = $messageId
                                timestamp = "$([DateTimeOffset]::UtcNow.ToUnixTimeSeconds())"
                                type = "text"
                                text = @{ body = "Que informacion necesitan para evaluar una obra?" }
                            }
                        )
                    }
                }
            )
        }
    )
}

$json = $payload | ConvertTo-Json -Depth 12 -Compress
$secret = "replace-me"
$hmac = New-Object System.Security.Cryptography.HMACSHA256
$hmac.Key = [System.Text.Encoding]::UTF8.GetBytes($secret)
$signatureBytes = $hmac.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($json))
$signature = "sha256=" + (($signatureBytes | ForEach-Object { $_.ToString("x2") }) -join "")

Write-Host "`nSending an integrated webhook:`n"
$response = Invoke-RestMethod `
    -Method Post `
    -Uri "$baseUrl/webhooks/whatsapp" `
    -Headers @{ "X-Hub-Signature-256" = $signature } `
    -ContentType "application/json; charset=utf-8" `
    -Body ([System.Text.Encoding]::UTF8.GetBytes($json))
$response | Format-List accepted, duplicate, event_id, event_status

$deadline = (Get-Date).AddSeconds(20)
$row = $null
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 500
    $query = "SELECT rp.decision, rv.status, rv.reason_code, rv.unsupported_claims FROM response_verifications rv JOIN response_plans rp ON rp.id = rv.plan_id JOIN messages m ON m.id = rv.message_id WHERE m.provider_message_id = '$messageId';"
    $raw = docker compose exec -T postgres psql -A -t -F "|" -U stoll -d stoll_assist -c $query
    if ($LASTEXITCODE -ne 0) {
        throw "Could not query response verification"
    }
    if ($raw -and $raw.Trim()) {
        $row = $raw.Trim()
        break
    }
}

if (-not $row) {
    throw "The integrated response verification was not created in time"
}

$parts = $row -split "\|", 4
Write-Host "`nIntegrated response verification:`n"
[pscustomobject]@{
    provider_message_id = $messageId
    plan_decision = $parts[0]
    verification_status = $parts[1]
    reason_code = $parts[2]
    unsupported_claims = $parts[3]
} | Format-List

if ($parts[0] -ne "ANSWER" -or $parts[1] -ne "APPROVED") {
    throw "Expected integrated ANSWER/APPROVED but received $($parts[0])/$($parts[1])"
}

Write-Host "`nResponse verifier test completed successfully."
