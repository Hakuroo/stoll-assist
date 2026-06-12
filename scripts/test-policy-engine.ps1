$ErrorActionPreference = "Stop"

Write-Host "Previewing a safe intake question:"
$safe = Invoke-RestMethod `
    -Uri "http://localhost:8000/operator/policies/preview" `
    -Method Post `
    -ContentType "application/json" `
    -Body (@{ text = "Hola, necesito un galpon de 10 por 20 en Pilar" } | ConvertTo-Json)
$safe | Format-List decision, matched_rule_key, risk_level, reason

if ($safe.decision -ne "ALLOW") {
    throw "Safe message should have been allowed"
}

Write-Host "Previewing a price request:"
$risky = Invoke-RestMethod `
    -Uri "http://localhost:8000/operator/policies/preview" `
    -Method Post `
    -ContentType "application/json" `
    -Body (@{ text = "Cuanto sale un tinglado de 8 por 12?" } | ConvertTo-Json)
$risky | Format-List decision, matched_rule_key, risk_level, reason, matched_evidence

if ($risky.decision -ne "HANDOFF" -or $risky.matched_rule_key -ne "exact_price") {
    throw "Price request should have triggered exact_price handoff"
}

$unique = [guid]::NewGuid().ToString("N")
$messageId = "wamid.POLICY-$unique"
$waId = "54911" + (Get-Random -Minimum 1000000 -Maximum 9999999)
$secret = "replace-me"
$webhookUri = "http://localhost:8000/webhooks/whatsapp"

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
                                profile = @{ name = "Pedro Policy" }
                                wa_id = $waId
                            }
                        )
                        messages = @(
                            @{
                                from = $waId
                                id = $messageId
                                timestamp = "1781237200"
                                text = @{ body = "Cuanto sale un galpon cerrado de 200 metros?" }
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
    -Uri $webhookUri `
    -Method Post `
    -Headers @{"X-Hub-Signature-256" = $signature} `
    -ContentType "application/json; charset=utf-8" `
    -Body $bodyBytes

$eventId = $response.event_id
$finalStatus = $response.event_status
for ($attempt = 1; $attempt -le 20; $attempt++) {
    if (@("PROCESSED", "IGNORED", "FAILED") -contains $finalStatus) { break }
    Start-Sleep -Milliseconds 500
    $finalStatus = (docker compose exec -T postgres psql `
        -U stoll -d stoll_assist -tA `
        -c "SELECT status FROM webhook_events WHERE id = '$eventId';").Trim()
}

if ($finalStatus -ne "PROCESSED") {
    throw "Webhook did not finish successfully. Final status: $finalStatus"
}

$conversationId = (docker compose exec -T postgres psql `
    -U stoll -d stoll_assist -tA `
    -c "SELECT conversation_id FROM messages WHERE provider_message_id = '$messageId';").Trim()

$conversation = Invoke-RestMethod `
    -Uri "http://localhost:8000/operator/conversations/$conversationId" `
    -Method Get

$evaluation = docker compose exec -T postgres psql `
    -P pager=off -U stoll -d stoll_assist `
    -c "SELECT decision, matched_rule_key, risk_level, reason FROM policy_evaluations WHERE message_id = (SELECT id FROM messages WHERE provider_message_id = '$messageId');"

Write-Host "Integrated policy result:"
[PSCustomObject]@{
    event_id = $eventId
    final_status = $finalStatus
    conversation_id = $conversationId
    conversation_state = $conversation.state
    automation_allowed = $conversation.automation_allowed
    handoff_reason = $conversation.active_handoff.reason_code
} | Format-List

$evaluation

if ($conversation.state -ne "HUMAN_REQUIRED") {
    throw "Risky message should have moved conversation to HUMAN_REQUIRED"
}
