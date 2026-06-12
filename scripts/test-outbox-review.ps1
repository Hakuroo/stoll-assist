[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding
$ErrorActionPreference = "Stop"

$baseUrl = "http://localhost:8000"
$timestamp = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
$messageId = "wamid.OUTBOX-$timestamp"
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
                                profile = @{ name = "Outbox Test" }
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

Write-Host "Sending a safe inbound message:`n"
$response = Invoke-RestMethod `
    -Method Post `
    -Uri "$baseUrl/webhooks/whatsapp" `
    -Headers @{ "X-Hub-Signature-256" = $signature } `
    -ContentType "application/json; charset=utf-8" `
    -Body ([System.Text.Encoding]::UTF8.GetBytes($json))
$response | Format-List accepted, duplicate, event_id, event_status

$deadline = (Get-Date).AddSeconds(20)
$outbound = $null
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 500
    try {
        $outbound = Invoke-RestMethod `
            -Method Get `
            -Uri "$baseUrl/operator/outbox/by-provider-message/$messageId"
        break
    }
    catch {
        if ($_.Exception.Response.StatusCode.value__ -ne 404) { throw }
    }
}

if (-not $outbound) {
    throw "The outbound draft was not created in time"
}

Write-Host "`nOutbound draft awaiting review:`n"
$outbound | Format-List outbound_id, display_name, recipient, status, requires_review, body_text
if ($outbound.status -ne "PENDING_REVIEW") {
    throw "Expected PENDING_REVIEW but received $($outbound.status)"
}

$approvalBody = @{ operator_name = "Elian" } | ConvertTo-Json
$approved = Invoke-RestMethod `
    -Method Post `
    -Uri "$baseUrl/operator/outbox/$($outbound.outbound_id)/approve" `
    -ContentType "application/json; charset=utf-8" `
    -Body ([System.Text.Encoding]::UTF8.GetBytes($approvalBody))

Write-Host "`nOperator approval:`n"
$approved | Format-List outbound_id, status, approved_by, approved_at, provider_message_id
if ($approved.status -ne "APPROVED") {
    throw "Expected APPROVED but received $($approved.status)"
}
if ($approved.provider_message_id) {
    throw "No real WhatsApp message should be sent in version 0.10"
}

$secondTimestamp = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
$secondMessageId = "wamid.OUTBOX-REJECT-$secondTimestamp"
$secondPhone = "54912$($secondTimestamp.ToString().Substring([Math]::Max(0, $secondTimestamp.ToString().Length - 8)))"
$payload.entry[0].changes[0].value.contacts[0].profile.name = "Outbox Reject Test"
$payload.entry[0].changes[0].value.contacts[0].wa_id = $secondPhone
$payload.entry[0].changes[0].value.messages[0].from = $secondPhone
$payload.entry[0].changes[0].value.messages[0].id = $secondMessageId
$payload.entry[0].changes[0].value.messages[0].timestamp = "$([DateTimeOffset]::UtcNow.ToUnixTimeSeconds())"
$json2 = $payload | ConvertTo-Json -Depth 12 -Compress
$hmac2 = New-Object System.Security.Cryptography.HMACSHA256
$hmac2.Key = [System.Text.Encoding]::UTF8.GetBytes($secret)
$signatureBytes2 = $hmac2.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($json2))
$signature2 = "sha256=" + (($signatureBytes2 | ForEach-Object { $_.ToString("x2") }) -join "")

Invoke-RestMethod `
    -Method Post `
    -Uri "$baseUrl/webhooks/whatsapp" `
    -Headers @{ "X-Hub-Signature-256" = $signature2 } `
    -ContentType "application/json; charset=utf-8" `
    -Body ([System.Text.Encoding]::UTF8.GetBytes($json2)) | Out-Null

$deadline = (Get-Date).AddSeconds(20)
$secondOutbound = $null
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 500
    try {
        $secondOutbound = Invoke-RestMethod `
            -Method Get `
            -Uri "$baseUrl/operator/outbox/by-provider-message/$secondMessageId"
        break
    }
    catch {
        if ($_.Exception.Response.StatusCode.value__ -ne 404) { throw }
    }
}
if (-not $secondOutbound) { throw "The second outbound draft was not created in time" }

$rejectionBody = @{
    operator_name = "Elian"
    reason = "Prefiero que esta consulta la continúe una persona."
} | ConvertTo-Json
$rejected = Invoke-RestMethod `
    -Method Post `
    -Uri "$baseUrl/operator/outbox/$($secondOutbound.outbound_id)/reject" `
    -ContentType "application/json; charset=utf-8" `
    -Body ([System.Text.Encoding]::UTF8.GetBytes($rejectionBody))

Write-Host "`nOperator rejection:`n"
$rejected | Format-List outbound_id, status, rejected_by, rejection_reason
if ($rejected.status -ne "REJECTED") {
    throw "Expected REJECTED but received $($rejected.status)"
}

Write-Host "`nOutbox review test completed successfully. No WhatsApp message was sent."
