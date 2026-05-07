param(
  [string]$BaseUrl = "http://localhost:30080",
  [string]$Realm = "aidp",
  [string]$ClientId = "cas-test",
  [string]$ServiceUrl = "http://localhost:18080/cas/callback",
  [string]$Username = "normal-user",
  [string]$Password = "NormalUser@123",
  [string]$AdminUsername = "admin",
  [string]$AdminPassword = "admin",
  [switch]$SkipClientSetup
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command curl.exe -ErrorAction SilentlyContinue)) {
  throw "curl.exe is required for cookie-jar based CAS login simulation."
}

function Join-Url([string]$left, [string]$right) {
  return $left.TrimEnd("/") + "/" + $right.TrimStart("/")
}

function Ensure-CasClient {
  $tokenEndpoint = Join-Url $BaseUrl "realms/master/protocol/openid-connect/token"
  $tokenResp = Invoke-RestMethod -Method Post -Uri $tokenEndpoint `
    -ContentType "application/x-www-form-urlencoded" `
    -Body @{
      client_id = "admin-cli"
      username = $AdminUsername
      password = $AdminPassword
      grant_type = "password"
    }

  $headers = @{ Authorization = "Bearer $($tokenResp.access_token)" }
  $clientsUrl = Join-Url $BaseUrl "admin/realms/$Realm/clients"
  $matches = Invoke-RestMethod -Method Get -Uri "${clientsUrl}?clientId=$ClientId" -Headers $headers
  $body = @{
    clientId = $ClientId
    protocol = "cas"
    enabled = $true
    publicClient = $true
    redirectUris = @($ServiceUrl)
  } | ConvertTo-Json -Depth 5

  if ($matches.Count -gt 0) {
    Invoke-RestMethod -Method Put -Uri "$clientsUrl/$($matches[0].id)" `
      -Headers $headers -ContentType "application/json" -Body $body | Out-Null
    Write-Host "Updated CAS client: $ClientId"
  } else {
    Invoke-RestMethod -Method Post -Uri $clientsUrl `
      -Headers $headers -ContentType "application/json" -Body $body | Out-Null
    Write-Host "Created CAS client: $ClientId"
  }
}

if (-not $SkipClientSetup) {
  Ensure-CasClient
}

$workDir = Join-Path $env:TEMP ("aidp-cas-smoke-" + [guid]::NewGuid().ToString("n"))
New-Item -ItemType Directory -Force -Path $workDir | Out-Null

$cookieJar = Join-Path $workDir "cookies.txt"
$loginHtml = Join-Path $workDir "login.html"
$headersFile = Join-Path $workDir "headers.txt"
$bodyFile = Join-Path $workDir "body.html"

$loginUrl = (Join-Url $BaseUrl "realms/$Realm/protocol/cas/login") +
  "?service=$([uri]::EscapeDataString($ServiceUrl))"

curl.exe -sS -L -c $cookieJar -b $cookieJar -o $loginHtml $loginUrl

# Local kind uses plain HTTP. Keycloak sets Secure cookies for browser flows, so
# the cookie jar must be loosened for this local-only smoke test. Production
# should use HTTPS and does not need this workaround.
if ($BaseUrl.StartsWith("http://")) {
  (Get-Content $cookieJar) -replace "`tTRUE`t", "`tFALSE`t" |
    Set-Content $cookieJar -Encoding ascii
}

$html = Get-Content $loginHtml -Raw
if ($html -notmatch 'action="([^"]+)"') {
  throw "Could not find Keycloak login form action."
}

$loginAction = [System.Net.WebUtility]::HtmlDecode($matches[1])

curl.exe -sS -i -c $cookieJar -b $cookieJar -o $bodyFile -D $headersFile `
  -X POST $loginAction `
  -H "Content-Type: application/x-www-form-urlencoded" `
  --data-urlencode "username=$Username" `
  --data-urlencode "password=$Password" `
  --data-urlencode "credentialId=" `
  --max-redirs 0

$headersRaw = Get-Content $headersFile -Raw
if ($headersRaw -notmatch "Location:\s*(\S+)") {
  throw "Login did not return a CAS service redirect. Headers:`n$headersRaw"
}

$redirectLocation = $matches[1].Trim()
if ($redirectLocation -notmatch "ticket=([^&\r\n]+)") {
  throw "Service redirect did not include a CAS ticket: $redirectLocation"
}

$ticket = $matches[1]
$validateUrl = (Join-Url $BaseUrl "realms/$Realm/protocol/cas/serviceValidate") +
  "?service=$([uri]::EscapeDataString($ServiceUrl))&ticket=$([uri]::EscapeDataString($ticket))"

$validation = Invoke-WebRequest -UseBasicParsing -Uri $validateUrl

Write-Host "CAS redirect: $redirectLocation"
Write-Host "CAS ticket: $ticket"
Write-Host "CAS validation status: $($validation.StatusCode)"
Write-Host $validation.Content

if ($validation.Content -notmatch "<cas:authenticationSuccess>") {
  throw "CAS serviceValidate did not return authenticationSuccess."
}
