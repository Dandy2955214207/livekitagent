param(
    [Parameter(Mandatory=$true)][string]$ComputerIP,
    [string]$PhoneIP = ''
)
. "$PSScriptRoot/project-env.ps1"
$address = [Net.IPAddress]::Parse($ComputerIP)
if ($address.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork -or [Net.IPAddress]::IsLoopback($address)) {
    throw 'Choose the computer Wi-Fi IPv4 address, not loopback.'
}
if ($PhoneIP) { [void][Net.IPAddress]::Parse($PhoneIP) }
$dir = Join-Path $ProjectRoot '.runtime/mobile'
New-Item -ItemType Directory -Force -Path $dir | Out-Null
$encoding = [Text.UTF8Encoding]::new($false)
$caPem = Join-Path $dir 'ca.pem'
$caKey = Join-Path $dir 'ca-key.pem'
$caDer = Join-Path $dir 'livekit-mobile-test-ca.cer'
$now = [DateTimeOffset]::UtcNow
if ((Test-Path -LiteralPath $caPem) -and (Test-Path -LiteralPath $caKey)) {
    $ca = [Security.Cryptography.X509Certificates.X509Certificate2]::CreateFromPemFile($caPem, $caKey)
    if ($ca.NotAfter.ToUniversalTime() -lt $now.AddDays(8).UtcDateTime) { throw 'Test CA expires soon; review and renew it before preparing another profile.' }
} else {
    $rsa = [Security.Cryptography.RSA]::Create(2048)
    $request = [Security.Cryptography.X509Certificates.CertificateRequest]::new(
        'CN=LiveKitAgent Mobile Test CA', $rsa,
        [Security.Cryptography.HashAlgorithmName]::SHA256, [Security.Cryptography.RSASignaturePadding]::Pkcs1)
    $request.CertificateExtensions.Add([Security.Cryptography.X509Certificates.X509BasicConstraintsExtension]::new($true,$false,0,$true))
    $request.CertificateExtensions.Add([Security.Cryptography.X509Certificates.X509KeyUsageExtension]::new(
        [Security.Cryptography.X509Certificates.X509KeyUsageFlags]::KeyCertSign -bor [Security.Cryptography.X509Certificates.X509KeyUsageFlags]::CrlSign,$true))
    $request.CertificateExtensions.Add([Security.Cryptography.X509Certificates.X509SubjectKeyIdentifierExtension]::new($request.PublicKey,$false))
    $ca = $request.CreateSelfSigned($now.AddMinutes(-5),$now.AddDays(30))
    [IO.File]::WriteAllText($caPem,$ca.ExportCertificatePem(),$encoding)
    [IO.File]::WriteAllText($caKey,$rsa.ExportPkcs8PrivateKeyPem(),$encoding)
    $rsa.Dispose()
}
try {
    [IO.File]::WriteAllBytes($caDer,$ca.Export([Security.Cryptography.X509Certificates.X509ContentType]::Cert))
    $serverRsa = [Security.Cryptography.RSA]::Create(2048)
    try {
        $request = [Security.Cryptography.X509Certificates.CertificateRequest]::new(
            "CN=$ComputerIP",$serverRsa,[Security.Cryptography.HashAlgorithmName]::SHA256,[Security.Cryptography.RSASignaturePadding]::Pkcs1)
        $request.CertificateExtensions.Add([Security.Cryptography.X509Certificates.X509BasicConstraintsExtension]::new($false,$false,0,$true))
        $request.CertificateExtensions.Add([Security.Cryptography.X509Certificates.X509KeyUsageExtension]::new([Security.Cryptography.X509Certificates.X509KeyUsageFlags]::DigitalSignature -bor [Security.Cryptography.X509Certificates.X509KeyUsageFlags]::KeyEncipherment,$true))
        $eku = [Security.Cryptography.OidCollection]::new()
        [void]$eku.Add([Security.Cryptography.Oid]::new('1.3.6.1.5.5.7.3.1'))
        $request.CertificateExtensions.Add([Security.Cryptography.X509Certificates.X509EnhancedKeyUsageExtension]::new($eku,$false))
        $san = [Security.Cryptography.X509Certificates.SubjectAlternativeNameBuilder]::new()
        $san.AddIpAddress($address); $san.AddIpAddress([Net.IPAddress]::Loopback); $san.AddDnsName('localhost')
        $request.CertificateExtensions.Add($san.Build())
        $serial = [Security.Cryptography.RandomNumberGenerator]::GetBytes(16)
        $serial[0] = $serial[0] -band 0x7f
        $serverCert = $request.Create($ca,$now.AddMinutes(-5),$now.AddDays(7),$serial)
        [IO.File]::WriteAllText((Join-Path $dir 'server.pem'),$serverCert.ExportCertificatePem(),$encoding)
        [IO.File]::WriteAllText((Join-Path $dir 'server-key.pem'),$serverRsa.ExportPkcs8PrivateKeyPem(),$encoding)
        $serverCert.Dispose()
    } finally { $serverRsa.Dispose() }
} finally { $ca.Dispose() }
$configPath = Join-Path $ProjectRoot 'config/livekit.local.yaml'
$yaml = [IO.File]::ReadAllText($configPath)
$yaml = $yaml.Replace('node_ip: 127.0.0.1', "node_ip: $ComputerIP")
[IO.File]::WriteAllText((Join-Path $dir 'livekit.yaml'),$yaml,$encoding)
$fingerprint = (Get-FileHash -LiteralPath $caDer -Algorithm SHA256).Hash.ToLowerInvariant()
$profile = [ordered]@{
    computer_ip=$ComputerIP; phone_ip=$PhoneIP; https_port=8443; bootstrap_port=8088
    origin="https://${ComputerIP}:8443"; livekit_public_url="wss://${ComputerIP}:8443/livekit"
    ca_sha256=$fingerprint; created_utc=$now.ToString('o'); certificate_expires_utc=$now.AddDays(7).ToString('o')
}
$profile | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $dir 'profile.json') -Encoding utf8
Write-Output 'Prepared project-local test certificates and separate mobile profile. No OS trust or firewall setting was changed.'
Write-Output "CA SHA256: $fingerprint"
Write-Output "Device check: https://${ComputerIP}:8443/device-check.html"
