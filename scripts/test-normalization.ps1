param(
    [string]$MessageId = "wamid.NORMALIZE-001"
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
                                profile = @{ name = "Juan Perez" }
                                wa_id = "5491112345678"
                            }
                        )
                        messages = @(
                            @{
                                from = "5491112345678"
                                id = $MessageId
                                timestamp = "1781236800"
                                text = @{ body = "Hola, necesito un galpon de 10 por 20 en Pilar" }
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

$response | Format-List
