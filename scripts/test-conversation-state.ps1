param(
    [string]$MessageId = "",
    [string]$OperatorName = "Elian"
)

$ErrorActionPreference = "Stop"
if (-not $MessageId) {
    $MessageId = "wamid.STATE-" + [guid]::NewGuid().ToString("N")
}
$secret = "replace-me"
$webhookUri = "http://localhost:8000/webhooks/whatsapp"

function Send-TestWebhook {
    param([string]$ProviderMessageId)

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
                                    profile = @{ name = "Carla State" }
                                    wa_id = "5491199998888"
                                }
                            )
                            messages = @(
                                @{
                                    from = "5491199998888"
                                    id = $ProviderMessageId
                                    timestamp = "1781237000"
                                    text = @{ body = "Quiero hablar con una persona" }
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

    return Invoke-RestMethod `
        -Uri $webhookUri `
        -Method Post `
        -Headers @{"X-Hub-Signature-256" = $signature} `
        -ContentType "application/json; charset=utf-8" `
        -Body $bodyBytes
}

$response = Send-TestWebhook -ProviderMessageId $MessageId
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
    -c "SELECT conversation_id FROM messages WHERE provider_message_id = '$MessageId';").Trim()

if (-not $conversationId) {
    throw "Conversation was not created"
}

$baseUri = "http://localhost:8000/operator/conversations/$conversationId"
Write-Host "Initial conversation:"
$initial = Invoke-RestMethod -Uri $baseUri -Method Get
$initial | Format-List conversation_id, state, automation_allowed, assigned_operator, state_version

Write-Host "Requesting human handoff:"
$handoff = Invoke-RestMethod `
    -Uri "$baseUri/request-handoff" `
    -Method Post `
    -ContentType "application/json" `
    -Body (@{
        reason_code = "customer_requests_human"
        summary = "El cliente pidió hablar con una persona."
        requested_by = "Agustina"
    } | ConvertTo-Json)
$handoff.conversation | Format-List state, automation_allowed, assigned_operator, state_version

Write-Host "Operator takes the conversation:"
$taken = Invoke-RestMethod `
    -Uri "$baseUri/take" `
    -Method Post `
    -ContentType "application/json" `
    -Body (@{
        operator_name = $OperatorName
        note = "Conversación tomada desde la prueba local."
    } | ConvertTo-Json)
$taken.conversation | Format-List state, automation_allowed, assigned_operator, state_version

Write-Host "Returning to automation:"
$returned = Invoke-RestMethod `
    -Uri "$baseUri/return-to-automation" `
    -Method Post `
    -ContentType "application/json" `
    -Body (@{
        operator_name = $OperatorName
        note = "La consulta manual fue resuelta."
    } | ConvertTo-Json)
$returned.conversation | Format-List state, automation_allowed, assigned_operator, state_version

Write-Host "Closing conversation:"
$closed = Invoke-RestMethod `
    -Uri "$baseUri/close" `
    -Method Post `
    -ContentType "application/json" `
    -Body (@{
        operator_name = $OperatorName
        note = "Cierre de prueba."
    } | ConvertTo-Json)
$closed.conversation | Format-List state, automation_allowed, assigned_operator, state_version

Write-Host "State machine summary:"
[PSCustomObject]@{
    conversation_id = $conversationId
    initial_state = $initial.state
    handoff_state = $handoff.conversation.state
    taken_state = $taken.conversation.state
    returned_state = $returned.conversation.state
    closed_state = $closed.conversation.state
    final_version = $closed.conversation.state_version
} | Format-List
