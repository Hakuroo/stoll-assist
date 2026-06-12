[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding
$ErrorActionPreference = "Stop"

$baseUrl = "http://localhost:8000"

function Invoke-PlanPreview {
    param([string]$Text)

    $body = @{
        text = $Text
        conversation_state = "AUTOMATED"
    } | ConvertTo-Json

    return Invoke-RestMethod `
        -Method Post `
        -Uri "$baseUrl/operator/planner/preview" `
        -ContentType "application/json; charset=utf-8" `
        -Body ([System.Text.Encoding]::UTF8.GetBytes($body))
}

Write-Host "Previewing an approved information question:`n"
$answer = Invoke-PlanPreview -Text "Que informacion necesitan para evaluar una obra?"
$answer | Format-List decision, reason_code, risk_level, knowledge_keys, reply_goal, draft_reply
if ($answer.decision -ne "ANSWER") {
    throw "Expected ANSWER but received $($answer.decision)"
}

Write-Host "`nPreviewing a vague project request:`n"
$ask = Invoke-PlanPreview -Text "Hola, quiero hacer un galpon"
$ask | Format-List decision, reason_code, risk_level, knowledge_keys, reply_goal, draft_reply
if ($ask.decision -ne "ASK") {
    throw "Expected ASK but received $($ask.decision)"
}

Write-Host "`nPreviewing a request without approved evidence:`n"
$handoff = Invoke-PlanPreview -Text "Cual es el clima en Japon?"
$handoff | Format-List decision, reason_code, risk_level, knowledge_keys, reply_goal
if ($handoff.decision -ne "HANDOFF") {
    throw "Expected HANDOFF but received $($handoff.decision)"
}

$timestamp = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
$messageId = "wamid.PLANNER-$timestamp"
$phone = "54911$($timestamp.ToString().Substring([Math]::Max(0, $timestamp.ToString().Length - 8)))"

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
                                profile = @{ name = "Planner Test" }
                                wa_id = $phone
                            }
                        )
                        messages = @(
                            @{
                                from = $phone
                                id = $messageId
                                timestamp = "$timestamp"
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
$planRow = $null
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 500
    $query = "SELECT rp.decision, rp.reason_code, rp.risk_level, rp.knowledge_keys, rp.reply_goal, rp.draft_reply FROM response_plans rp JOIN messages m ON m.id = rp.message_id WHERE m.provider_message_id = '$messageId';"
    $raw = docker compose exec -T postgres psql -A -t -F "|" -U stoll -d stoll_assist -c $query
    if ($LASTEXITCODE -ne 0) {
        throw "Could not query response plan"
    }
    if ($raw -and $raw.Trim()) {
        $planRow = $raw.Trim()
        break
    }
}

if (-not $planRow) {
    throw "The integrated response plan was not created in time"
}

$parts = $planRow -split "\|", 6
Write-Host "`nIntegrated response plan:`n"
[pscustomobject]@{
    provider_message_id = $messageId
    decision = $parts[0]
    reason_code = $parts[1]
    risk_level = $parts[2]
    knowledge_keys = $parts[3]
    reply_goal = $parts[4]
    draft_reply = $parts[5]
} | Format-List

if ($parts[0] -ne "ANSWER") {
    throw "Expected integrated ANSWER but received $($parts[0])"
}

Write-Host "`nResponse planner test completed successfully."
