param([switch]$Remove, [switch]$Media)
. "$PSScriptRoot/project-env.ps1"
$profile = Get-Content -LiteralPath (Join-Path $ProjectRoot '.runtime/mobile/profile.json') -Raw | ConvertFrom-Json
if (-not $profile.phone_ip) { throw 'A specific phone IP is required.' }
[void][Net.IPAddress]::Parse($profile.phone_ip)
[void][Net.IPAddress]::Parse($profile.computer_ip)
$name = 'LiveKitAgent-Mobile-Probe-' + $profile.phone_ip
try {
    if ($Remove) {
        Get-NetFirewallRule -Name $name -ErrorAction SilentlyContinue | Remove-NetFirewallRule -ErrorAction Stop
        Get-NetFirewallRule -Name ('LiveKitAgent-Mobile-Media-TCP-' + $profile.phone_ip),('LiveKitAgent-Mobile-Media-UDP-' + $profile.phone_ip) -ErrorAction SilentlyContinue | Remove-NetFirewallRule -ErrorAction Stop
        Write-Output 'Removed the project phone-probe rule, if present.'
        exit
    }
    if (-not (Get-NetFirewallRule -Name $name -ErrorAction SilentlyContinue)) {
        New-NetFirewallRule -Name $name -DisplayName 'LiveKitAgent phone capability test' -Direction Inbound -Action Allow -Protocol TCP -LocalAddress $profile.computer_ip -LocalPort 8443,8088 -RemoteAddress $profile.phone_ip -Profile Any -ErrorAction Stop | Out-Null
    }
    $rule = Get-NetFirewallRule -Name $name -ErrorAction Stop
    if (-not $rule) { throw 'Firewall rule could not be verified.' }
    Write-Output "Allowed TCP 8443/8088 only from $($profile.phone_ip) to $($profile.computer_ip)."
    if ($Media) {
        foreach ($protocol in 'TCP','UDP') {
            $mediaName = 'LiveKitAgent-Mobile-Media-' + $protocol + '-' + $profile.phone_ip
            $port = if ($protocol -eq 'TCP') { 7881 } else { 7882 }
            if (-not (Get-NetFirewallRule -Name $mediaName -ErrorAction SilentlyContinue)) {
                New-NetFirewallRule -Name $mediaName -DisplayName "LiveKitAgent phone media $protocol" -Direction Inbound -Action Allow -Protocol $protocol -LocalAddress $profile.computer_ip -LocalPort $port -RemoteAddress $profile.phone_ip -Profile Any -ErrorAction Stop | Out-Null
            }
            Get-NetFirewallRule -Name $mediaName -ErrorAction Stop | Out-Null
        }
        Write-Output 'Allowed media TCP 7881 and UDP 7882 only for this phone.'
    }
} catch {
    Write-Error ("Windows could not create/verify this scoped rule: " + $_.Exception.Message + ' Run this script in an administrator PowerShell; do not disable the firewall.') -ErrorAction Continue
    exit 1
}
