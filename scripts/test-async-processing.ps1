param(
    [string]$MessageId = "wamid.ASYNC-001"
)

$ErrorActionPreference = "Stop"

$secret = "replace-me"
$uri = "http://localhost:8000/webhooks/whatsapp"
$bodyObject = @{
    object = "whatsapp_business_account"
    entry = @(
        @{
            id = "TEST_WABA_ID"
            changes = @(
                @{
                    field = "messages"
                    value = @{
                        messaging_product = "whatsapp"
                        metadata = @{
                            display_phone_number = "5491100000000"
                            phone_number_id = "TEST_PHONE_NUMBER_ID"
                        }
                        contacts = @(
                            @{
                                profile = @{ name = "Maria Async" }
                                wa_id = "5491198765432"
                            }
                        )
                        messages = @(
                            @{
                                from = "5491198765432"
                                id = $MessageId
                                timestamp = "1781236900"
                                text = @{ body = "Hola, necesito un tinglado de 8 por 12 en Escobar" }
                                type = "text"
                            }
                        )
                    }
                }
            )
        }
    )
}

$body = $bodyObject | ConvertTo-Json -Depth 12 -Compress
$bodyBytes = [System.Text.Encoding]::UTF8.GetBytes($body)
$hmac = [System.Security.Cryptography.HMACSHA256]::new(
    [System.Text.Encoding]::UTF8.GetBytes($secret)
)
$signatureBytes = $hmac.ComputeHash($bodyBytes)
$signature = "sha256=" + ([System.BitConverter]::ToString($signatureBytes) -replace "-", "").ToLowerInvariant()

$response = Invoke-RestMethod `
    -Uri $uri `
    -Method Post `
    -Headers @{"X-Hub-Signature-256" = $signature} `
    -ContentType "application/json; charset=utf-8" `
    -Body $bodyBytes

Write-Host "API response:"
$response | Format-List

$eventId = $response.event_id
$terminal = @("PROCESSED", "IGNORED", "FAILED")
$finalStatus = $response.event_status

for ($attempt = 1; $attempt -le 20; $attempt++) {
    if ($terminal -contains $finalStatus) { break }
    Start-Sleep -Milliseconds 500
    $finalStatus = (docker compose exec -T postgres psql `
        -U stoll -d stoll_assist -tA `
        -c "SELECT status FROM webhook_events WHERE id = '$eventId';").Trim()
}

$messageCount = (docker compose exec -T postgres psql `
    -U stoll -d stoll_assist -tA `
    -c "SELECT COUNT(*) FROM messages WHERE provider_message_id = '$MessageId';").Trim()

Write-Host "Final processing result:"
[PSCustomObject]@{
    event_id = $eventId
    final_status = $finalStatus
    persisted_messages = $messageCount
} | Format-List

if ($finalStatus -eq "FAILED") {
    docker compose exec -T postgres psql `
        -U stoll -d stoll_assist `
        -c "SELECT error_message FROM webhook_events WHERE id = '$eventId';"
    exit 1
}
