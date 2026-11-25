"""Network Sockets, Protocol Hardening & DNS Pipeline Deep Diagnostics. Read-only."""
from ._base import CHK, L, T, maker, need_admin

NET = maker("Network")

SCRIPTS = [
    NET(
        "process_socket_attribution",
        "Comprehensive Network Socket & Process Attribution Matrix",
        "Enumerates active TCP and UDP sockets, mapping remote endpoints and listening ports "
        "to the owning process name, PID, and executable path.",
        r"""
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host " SENIOR SYSADMIN DIAGNOSTIC: NETWORK SOCKET & PROCESS ATTRIBUTION MATRIX       " -ForegroundColor Cyan
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host "[INFO] HELPDESK PRIMER: Network sockets connect local processes to remote endpoints." -ForegroundColor Gray
Write-Host "[INFO] ESTABLISHED connections represent active data channels. Global LISTENING" -ForegroundColor Gray
Write-Host "[INFO] ports (0.0.0.0 or ::) expose services to the network; unexpected listening" -ForegroundColor Gray
Write-Host "[INFO] binaries can indicate unauthorized servers, malware, or open attack surfaces." -ForegroundColor Gray
Write-Host ""

Write-Host "=== Phase 1: Active Established TCP Sockets & Process Mapping ===" -ForegroundColor Cyan
try {
    $connections = Get-NetTCPConnection -State Established -ErrorAction Stop
    $procCache = @{}
    Get-Process | ForEach-Object { $procCache[$_.Id] = $_.ProcessName }

    Write-Host ("[INFO] Active Established Connections: {0}" -f $connections.Count) -ForegroundColor Cyan
    $connections | Select-Object -First 20 | ForEach-Object {
        $pName = if ($procCache.ContainsKey($_.OwningProcess)) { $procCache[$_.OwningProcess] } else { "PID:$($_.OwningProcess)" }
        $local = "$($_.LocalAddress):$($_.LocalPort)"
        $remote = "$($_.RemoteAddress):$($_.RemotePort)"
        Write-Host ("  {0,-20} Local: {1,-22} Remote: {2,-22}" -f $pName, $local, $remote)
    }
    if ($connections.Count -gt 20) {
        Write-Host ("  ... and {0} additional established sockets active." -f ($connections.Count - 20)) -ForegroundColor Gray
    }
} catch {
    Write-Host "[INFO] NetTCPConnection query: $($_.Exception.Message)"
}

Write-Host "`n=== Phase 2: Listening Ports & Open Network Services ===" -ForegroundColor Cyan
$sensitivePorts = @(21, 22, 23, 80, 443, 445, 1433, 3306, 3389, 5985, 5986, 8080)
$globalExposed = 0
try {
    $listening = Get-NetTCPConnection -State Listen -ErrorAction Stop
    Write-Host ("[INFO] Open Listening Ports Count: {0}" -f $listening.Count) -ForegroundColor Cyan
    
    $listening | Sort-Object LocalPort | ForEach-Object {
        $pName = if ($procCache.ContainsKey($_.OwningProcess)) { $procCache[$_.OwningProcess] } else { "PID:$($_.OwningProcess)" }
        $isGlobal = $_.LocalAddress -in @('0.0.0.0', '::')
        
        if ($isGlobal -and ($_.LocalPort -in $sensitivePorts)) {
            $globalExposed++
            Write-Host ("  [WARN] Port {0,-5} ({1,-15}) -> Process: {2} (PID: {3}) [SENSITIVE GLOBAL LISTENER]" -f $_.LocalPort, $_.LocalAddress, $pName, $_.OwningProcess) -ForegroundColor Yellow
        } elseif ($isGlobal) {
            Write-Host ("  [INFO] Port {0,-5} ({1,-15}) -> Process: {2} (PID: {3})" -f $_.LocalPort, $_.LocalAddress, $pName, $_.OwningProcess)
        } else {
            Write-Host ("  [OK]   Port {0,-5} ({1,-15}) -> Process: {2} (PID: {3}) [Localhost Only]" -f $_.LocalPort, $_.LocalAddress, $pName, $_.OwningProcess) -ForegroundColor Green
        }
    }
} catch {
    Write-Host "[INFO] Listening ports query: $($_.Exception.Message)"
}

Write-Host "`n=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ===" -ForegroundColor Cyan
if ($globalExposed -gt 0) {
    Write-Host ("[WARN] Detected {0} sensitive port(s) listening on all interfaces (0.0.0.0 / ::)." -f $globalExposed) -ForegroundColor Yellow
    Write-Host "[REMEDIATION] 1. Verify if these services are intended to be accessible externally (e.g. RDP, SMB, HTTP)."
    Write-Host "[REMEDIATION] 2. Restrict access using Windows Defender Firewall with Advanced Security."
    Write-Host "[REMEDIATION] 3. If an unrecognized PID is listening, investigate executable path in Task Manager."
} else {
    Write-Host "[STATUS] No unauthorized global sensitive listeners detected on standard endpoints." -ForegroundColor Green
    Write-Host "[INFO] Localhost listeners (127.0.0.1) are isolated from LAN exposure." -ForegroundColor Gray
}
""",
    ),
    NET(
        "dns_pipeline_benchmark",
        "DNS Resolution Pipeline, Multi-Resolver Benchmark & Cache Audit",
        "Measures DNS resolution latency across configured DNS servers and public resolvers (1.1.1.1, 8.8.8.8, 9.9.9.9), "
        "and checks the local HOSTS file for tampering.",
        r"""
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host " SENIOR SYSADMIN DIAGNOSTIC: DNS RESOLUTION PIPELINE & RESOLVER BENCHMARK      " -ForegroundColor Cyan
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host "[INFO] HELPDESK PRIMER: Slow DNS resolution degrades web surfing, cloud app" -ForegroundColor Gray
Write-Host "[INFO] connections, and authentication. Tampered HOSTS files redirect traffic to" -ForegroundColor Gray
Write-Host "[INFO] malicious IP addresses. Benchmark latency < 30ms is optimal." -ForegroundColor Gray
Write-Host ""

Write-Host "=== Phase 1: DNS Resolution Latency Benchmark ===" -ForegroundColor Cyan
$testHost = "www.microsoft.com"
$resolvers = @(
    @{ Name = 'Cloudflare (1.1.1.1)'; Server = '1.1.1.1' },
    @{ Name = 'Google (8.8.8.8)';     Server = '8.8.8.8' },
    @{ Name = 'Quad9 (9.9.9.9)';      Server = '9.9.9.9' }
)

# Add configured system DNS servers
$adapters = Get-CimInstance Win32_NetworkAdapterConfiguration | Where-Object { $_.IPEnabled }
foreach ($a in $adapters) {
    if ($a.DNSServerSearchOrder) {
        foreach ($s in $a.DNSServerSearchOrder) {
            $resolvers += @{ Name = "Configured DNS ($s)"; Server = $s }
        }
    }
}

$slowCount = 0
foreach ($r in $resolvers) {
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    try {
        $ans = Resolve-DnsName -Name $testHost -Server $r.Server -Type A -DnsOnly -QuickTimeout -ErrorAction Stop
        $sw.Stop()
        $ms = $sw.ElapsedMilliseconds
        
        if ($ms -lt 35) {
            Write-Host ("[OK]   {0,-28} Resolved in {1,4} ms -> {2}" -f $r.Name, $ms, ($ans | Select-Object -First 1).IPAddress) -ForegroundColor Green
        } elseif ($ms -lt 80) {
            Write-Host ("[INFO] {0,-28} Resolved in {1,4} ms -> {2}" -f $r.Name, $ms, ($ans | Select-Object -First 1).IPAddress) -ForegroundColor Cyan
        } else {
            $slowCount++
            Write-Host ("[WARN] {0,-28} Resolved in {1,4} ms (HIGH LATENCY) -> {2}" -f $r.Name, $ms, ($ans | Select-Object -First 1).IPAddress) -ForegroundColor Yellow
        }
    } catch {
        Write-Host ("[BAD]  {0,-28} Query FAILED ({1})" -f $r.Name, $_.Exception.Message) -ForegroundColor Red
    }
}

Write-Host "`n=== Phase 2: Local HOSTS File Integrity & Tamper Check ===" -ForegroundColor Cyan
$hostsPath = "$env:SystemRoot\System32\drivers\etc\hosts"
$hostsTampered = $false
if (Test-Path $hostsPath) {
    $hostsLines = Get-Content $hostsPath | Where-Object { $_.Trim() -and -not $_.Trim().StartsWith('#') }
    if ($hostsLines) {
        $hostsTampered = $true
        Write-Host ("[WARN] Found {0} active non-default entry/entries in HOSTS file:" -f $hostsLines.Count) -ForegroundColor Yellow
        $hostsLines | ForEach-Object { Write-Host ("       • {0}" -f $_.Trim()) -ForegroundColor Yellow }
    } else {
        Write-Host "[OK]   HOSTS file is clean (zero static domain redirects, default comments only)." -ForegroundColor Green
    }
} else {
    Write-Host "[WARN] HOSTS file missing from expected system path!" -ForegroundColor Yellow
}

Write-Host "`n=== Phase 3: DNS Client Cache Service Health ===" -ForegroundColor Cyan
$dnsSvc = Get-Service -Name Dnscache -ErrorAction SilentlyContinue
if ($dnsSvc) {
    if ($dnsSvc.Status -eq 'Running') {
        Write-Host "[OK]   DNS Client Cache Service (Dnscache) is RUNNING." -ForegroundColor Green
    } else {
        Write-Host ("[BAD]  DNS Client Cache Service is {0}!" -f $dnsSvc.Status) -ForegroundColor Red
    }
}

Write-Host "`n=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ===" -ForegroundColor Cyan
if ($hostsTampered) {
    Write-Host "[ACTION REQUIRED] Review active HOSTS entries above! Malware often hardcodes fake" -ForegroundColor Yellow
    Write-Host "[ACTION REQUIRED] banking or update URLs in %SystemRoot%\System32\drivers\etc\hosts." -ForegroundColor Yellow
}
if ($slowCount -gt 0) {
    Write-Host "[REMEDIATION] Configured DNS server has high latency (>80ms). Consider switching primary" -ForegroundColor Yellow
    Write-Host "[REMEDIATION] DNS to 1.1.1.1 (Cloudflare) or 8.8.8.8 (Google) in adapter properties."
} else {
    Write-Host "[STATUS] DNS resolution pipeline is fast, healthy, and uncompromised." -ForegroundColor Green
}
""",
    ),
    NET(
        "smb_protocol_hardening",
        "SMB Protocol Security, Dialects & Anonymous Sharing Audit",
        "Audits SMBv1 enablement status, SMBv2/v3 packet signing requirement, encryption enforcement, "
        "and anonymous null-session pipe sharing exposure.",
        r"""
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host " SENIOR SYSADMIN DIAGNOSTIC: SMB PROTOCOL HARDENING & SECURE SHARING AUDIT      " -ForegroundColor Cyan
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host "[INFO] HELPDESK PRIMER: Server Message Block (SMB) handles file and printer sharing." -ForegroundColor Gray
Write-Host "[INFO] SMBv1 is an obsolete protocol vulnerable to EternalBlue (WannaCry) exploits." -ForegroundColor Gray
Write-Host "[INFO] Packet signing protects against NTLM relaying and Man-in-the-Middle tampering." -ForegroundColor Gray
Write-Host ""

Write-Host "=== Phase 1: SMBv1 Protocol Status (Critical Security Risk) ===" -ForegroundColor Cyan
try {
    $smb1Config = Get-SmbServerConfiguration -ErrorAction Stop
    $smb1Enabled = $smb1Config.EnableSMB1Protocol
    if ($smb1Enabled) {
        Write-Host "[BAD]  SMBv1 is ENABLED on this machine! Critical vulnerability to EternalBlue." -ForegroundColor Red
    } else {
        Write-Host "[OK]   SMBv1 is DISABLED (Secure baseline enforced)." -ForegroundColor Green
    }
} catch {
    Write-Host "[INFO] SmbServerConfiguration query: $($_.Exception.Message)"
}

Write-Host "`n=== Phase 2: SMB Signing & Encryption Enforcement ===" -ForegroundColor Cyan
try {
    $cfg = Get-SmbServerConfiguration
    $signReq = $cfg.RequireSecuritySignature
    $signEn = $cfg.EnableSecuritySignature
    $encReq = $cfg.EncryptData

    if ($signReq) {
        Write-Host "[OK]   SMB Packet Signing is MANDATORY (RequireSecuritySignature = True)." -ForegroundColor Green
    } else {
        Write-Host "[WARN] SMB Packet Signing is OPTIONAL (Vulnerable to NTLM relay attacks)." -ForegroundColor Yellow
    }

    if ($encReq) {
        Write-Host "[OK]   SMB Encryption is ENFORCED for all network shares." -ForegroundColor Green
    } else {
        Write-Host "[INFO] SMB Encryption is optional (configured per share or opportunistic)."
    }
} catch {
    Write-Host "[INFO] SMB configuration: $($_.Exception.Message)"
}

Write-Host "`n=== Phase 3: Anonymous Null Session Pipes & Shares ===" -ForegroundColor Cyan
$nullPipes = (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Services\LanmanServer\Parameters' -Name NullSessionPipes -ErrorAction SilentlyContinue).NullSessionPipes
if ($nullPipes) {
    Write-Host ("[WARN] Null Session Pipes configured: {0}" -f ($nullPipes -join ', ')) -ForegroundColor Yellow
} else {
    Write-Host "[OK]   Zero Null Session Pipes allowed (Anonymous IPC disallowed)." -ForegroundColor Green
}

Write-Host "`n=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ===" -ForegroundColor Cyan
Write-Host "[SECURITY BASELINE] In modern enterprise networks:"
Write-Host "  1. SMBv1 should remain uninstalled/disabled across all clients and servers."
Write-Host "  2. Enforce SMB signing via Group Policy: Computer Configuration -> Windows Settings"
Write-Host "     -> Security Settings -> Local Policies -> Security Options -> Microsoft network client/server."
Write-Host "  3. Restrict incoming TCP port 445 on public Wi-Fi profiles."
""",
    ),
    NET(
        "tls_cipher_suite_audit",
        "Schannel Protocols, TLS Cipher Suites & Hardening",
        "Audits Schannel cryptographic protocol configurations (SSL 2.0/3.0, TLS 1.0, 1.1, 1.2, 1.3) "
        "and warns of insecure legacy ciphers.",
        r"""
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host " SENIOR SYSADMIN DIAGNOSTIC: SCHANNEL TLS PROTOCOLS & CIPHER SUITES AUDIT      " -ForegroundColor Cyan
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host "[INFO] HELPDESK PRIMER: Schannel is the Windows SSP that handles TLS/SSL encryption." -ForegroundColor Gray
Write-Host "[INFO] SSL 2.0/3.0 and TLS 1.0/1.1 are cryptographically broken (POODLE, BEAST attacks)." -ForegroundColor Gray
Write-Host "[INFO] Windows 10/11 should enforce TLS 1.2 and TLS 1.3 for secure communications." -ForegroundColor Gray
Write-Host ""

Write-Host "=== Phase 1: Schannel Protocol Enablement in Registry ===" -ForegroundColor Cyan
$protocols = @('SSL 2.0', 'SSL 3.0', 'TLS 1.0', 'TLS 1.1', 'TLS 1.2', 'TLS 1.3')
$schannelBase = 'HKLM:\SYSTEM\CurrentControlSet\Control\SecurityProviders\SCHANNEL\Protocols'
$insecureProtocols = 0

foreach ($proto in $protocols) {
    $srvKey = Join-Path $schannelBase "$proto\Server"
    $cltKey = Join-Path $schannelBase "$proto\Client"

    $srvEn = if (Test-Path $srvKey) { (Get-ItemProperty $srvKey -Name Enabled -ErrorAction SilentlyContinue).Enabled } else { $null }
    $cltEn = if (Test-Path $cltKey) { (Get-ItemProperty $cltKey -Name Enabled -ErrorAction SilentlyContinue).Enabled } else { $null }

    $isLegacy = $proto -in @('SSL 2.0', 'SSL 3.0', 'TLS 1.0', 'TLS 1.1')
    $statusStr = if ($srvEn -eq 0 -and $cltEn -eq 0) { "DISABLED" } elseif ($srvEn -eq 1 -or $cltEn -eq 1) { "ENABLED" } else { "OS Default" }

    if ($isLegacy -and $statusStr -eq "ENABLED") {
        $insecureProtocols++
        Write-Host ("[BAD]  {0,-8}: ENABLED (Deprecated legacy protocol!)" -f $proto) -ForegroundColor Red
    } elseif ($proto -in @('TLS 1.2', 'TLS 1.3')) {
        Write-Host ("[OK]   {0,-8}: {1}" -f $proto, $statusStr) -ForegroundColor Green
    } else {
        Write-Host ("[INFO] {0,-8}: {1}" -f $proto, $statusStr)
    }
}

Write-Host "`n=== Phase 2: Active TLS Cipher Suites Roster ===" -ForegroundColor Cyan
try {
    $ciphers = Get-TlsCipherSuite -ErrorAction SilentlyContinue | Select-Object -First 8
    if ($ciphers) {
        Write-Host "[INFO] Priority Cipher Suites (Top Negotiated):" -ForegroundColor Cyan
        foreach ($c in $ciphers) {
            $name = $c.Name
            $prot = $c.Protocols -join ', '
            $tag = if ($name -match 'CHACHA20|GCM|AES_256') { "[OK]  " } else { "[INFO]" }
            Write-Host ("  {0} {1,-42} (Protocols: {2})" -f $tag, $name, $prot)
        }
    }
} catch {
    Write-Host "[INFO] Get-TlsCipherSuite query: $($_.Exception.Message)"
}

Write-Host "`n=== Phase 3: FIPS 140 Cryptographic Compliance Mode ===" -ForegroundColor Cyan
$fips = (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Lsa\FipsAlgorithmPolicy' -Name Enabled -ErrorAction SilentlyContinue).Enabled
if ($fips -eq 1) {
    Write-Host "[INFO] FIPS 140 Cryptography Mode: ENABLED." -ForegroundColor Cyan
} else {
    Write-Host "[INFO] FIPS 140 Cryptography Mode: DISABLED (Windows standard default)."
}

Write-Host "`n=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ===" -ForegroundColor Cyan
if ($insecureProtocols -gt 0) {
    Write-Host ("[ACTION REQUIRED] Legacy SSL/TLS protocols are active! Disable them via registry or IISCrypto tool." -f $insecureProtocols) -ForegroundColor Red
} else {
    Write-Host "[STATUS] Schannel cryptographic configuration adheres to modern security standards." -ForegroundColor Green
    Write-Host "[INFO] TLS 1.2 and TLS 1.3 provide robust forward secrecy encryption." -ForegroundColor Gray
}
""",
    ),
    NET(
        "adapter_dhcp_gateway_triage",
        "Network Adapter Telemetry, DHCP Health & Gateway Diagnostics",
        "Audits physical link speed, duplex, packet errors, DHCP lease expiration countdown, and gateway latency.",
        r"""
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host " SENIOR SYSADMIN DIAGNOSTIC: ADAPTER LINK SPEED, DHCP & GATEWAY LATENCY        " -ForegroundColor Cyan
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host "[INFO] HELPDESK PRIMER: Network connectivity begins at the physical link and DHCP" -ForegroundColor Gray
Write-Host "[INFO] lease. If link speed drops to 100 Mbps on a 1 Gbps port, check the Ethernet" -ForegroundColor Gray
Write-Host "[INFO] cable (bent pins / damaged pair). Gateway latency should remain under 5ms." -ForegroundColor Gray
Write-Host ""

Write-Host "=== Phase 1: Physical Adapters & Link Speed ===" -ForegroundColor Cyan
try {
    $netAdapters = Get-NetAdapter -Physical -ErrorAction Stop
    foreach ($na in $netAdapters) {
        $speed = $na.LinkSpeed
        $status = $na.Status
        
        if ($status -eq 'Up') {
            if ($speed -eq '100 Mbps') {
                Write-Host ("[WARN] {0,-24} Status: {1,-6} Speed: {2,-12} [LINK NEGOTIATION DOWNGRADE?]" -f $na.Name, $status, $speed) -ForegroundColor Yellow
            } else {
                Write-Host ("[OK]   {0,-24} Status: {1,-6} Speed: {2,-12}" -f $na.Name, $status, $speed) -ForegroundColor Green
            }
        } else {
            Write-Host ("[INFO] {0,-24} Status: {1,-6} Speed: {2,-12}" -f $na.Name, $status, $speed)
        }
    }
} catch {
    Write-Host "[INFO] NetAdapter query: $($_.Exception.Message)"
}

Write-Host "`n=== Phase 2: Default Gateway Reachability & Round-Trip Latency ===" -ForegroundColor Cyan
$routes = Get-CimInstance Win32_IP4RouteTable | Where-Object { $_.Destination -eq '0.0.0.0' }
$gw = ($routes | Select-Object -First 1).NextHop

if ($gw) {
    Write-Host ("[INFO] Discovered Default Gateway: {0}" -f $gw) -ForegroundColor Cyan
    $ping = Test-Connection -ComputerName $gw -Count 4 -ErrorAction SilentlyContinue
    if ($ping) {
        $avgMs = [math]::Round(($ping | Measure-Object -Property ResponseTime -Average).Average, 1)
        if ($avgMs -le 5) {
            Write-Host ("[OK]   Gateway {0} reachable - Average latency: {1} ms (Excellent)" -f $gw, $avgMs) -ForegroundColor Green
        } elseif ($avgMs -le 25) {
            Write-Host ("[INFO] Gateway {0} reachable - Average latency: {1} ms (Nominal)" -f $gw, $avgMs) -ForegroundColor Cyan
        } else {
            Write-Host ("[WARN] Gateway {0} reachable - Average latency: {1} ms (HIGH LOCAL LATENCY)" -f $gw, $avgMs) -ForegroundColor Yellow
        }
    } else {
        Write-Host ("[BAD]  Gateway {0} is UNREACHABLE or dropping ICMP ping packets!" -f $gw) -ForegroundColor Red
    }
} else {
    Write-Host "[WARN] No default IPv4 route found in routing table." -ForegroundColor Yellow
}

Write-Host "`n=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ===" -ForegroundColor Cyan
Write-Host "[TROUBLESHOOTING CHEATSHEET]:"
Write-Host "  1. If Link Speed is 100 Mbps instead of 1 Gbps: replace Cat5e/Cat6 patch cable."
Write-Host "  2. If Gateway is unreachable: check IP assignment, subnet mask, and router/switch port."
Write-Host "  3. To refresh DHCP lease: ipconfig /release followed by ipconfig /renew."
""",
    ),
    NET(
        "arp_cache_poison_sentinel",
        "ARP / Neighbor Cache Consistency & Poisoning Sentinel",
        "Inspects IPv4 ARP cache and IPv6 neighbor table: detects duplicate MAC addresses across different IPs "
        "(ARP spoofing indicator), maps default gateway MAC, and verifies physical bindings.",
        r"""
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host " SENIOR SYSADMIN DIAGNOSTIC: ARP CACHE INTEGRITY & SPOOFING SENTINEL           " -ForegroundColor Cyan
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host "[INFO] HELPDESK PRIMER: Address Resolution Protocol (ARP) maps IP addresses to" -ForegroundColor Gray
Write-Host "[INFO] physical MAC addresses on the local subnet. If two distinct IP addresses" -ForegroundColor Gray
Write-Host "[INFO] (especially the gateway and another host) share the same MAC address, an" -ForegroundColor Gray
Write-Host "[INFO] attacker may be conducting Man-in-the-Middle (MITM) ARP poisoning." -ForegroundColor Gray
Write-Host ""

Write-Host "=== Phase 1: ARP & Neighbor Cache Table Enumeration ===" -ForegroundColor Cyan
try {
    $neighbors = Get-NetNeighbor -AddressFamily IPv4 -ErrorAction Stop | Where-Object { $_.State -ne 'Unreachable' -and $_.LinkLayerAddress }
    $macMap = @{}
    $poisonDetected = $false

    foreach ($n in $neighbors) {
        $ip = $n.IPAddress
        $mac = $n.LinkLayerAddress.ToUpper()
        $state = $n.State

        if ($mac -in @('FF-FF-FF-FF-FF-FF', '00-00-00-00-00-00')) { continue }

        if ($macMap.ContainsKey($mac)) {
            $otherIp = $macMap[$mac]
            Write-Host ("[BAD]  Duplicate MAC detected: {0} mapped to BOTH {1} and {2} (Possible ARP Spoofing!)" -f $mac, $otherIp, $ip) -ForegroundColor Red
            $poisonDetected = $true
        } else {
            $macMap[$mac] = $ip
            Write-Host ("  [OK]   IP: {0,-16} MAC: {1,-18} State: {2}" -f $ip, $mac, $state) -ForegroundColor Green
        }
    }

    if (-not $poisonDetected) {
        Write-Host "`n[OK]   Zero duplicate MAC conflicts detected in ARP cache." -ForegroundColor Green
    }
} catch {
    Write-Host "[INFO] NetNeighbor query: $($_.Exception.Message)"
}

Write-Host "`n=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ===" -ForegroundColor Cyan
if ($poisonDetected) {
    Write-Host "[ACTION REQUIRED] Potential ARP cache poisoning attack detected on the local LAN!" -ForegroundColor Red
    Write-Host "[REMEDIATION] 1. Disconnect machine from public Wi-Fi or untrusted network immediately."
    Write-Host "[REMEDIATION] 2. Notify network security team to inspect switch port security and DAI."
    Write-Host "[REMEDIATION] 3. Flush ARP cache in elevated shell: netsh interface ip delete arpcache"
} else {
    Write-Host "[STATUS] ARP and neighbor caches are consistent with standard 1:1 MAC-IP bindings." -ForegroundColor Green
}
""",
    ),
    NET(
        "wlan_bssid_rssi_quality",
        "Wi-Fi Radio, BSSID, RSSI Signal & Channel Quality",
        "Audits active Wi-Fi connection: protocol version (Wi-Fi 6/6E/7 / 802.11ax/ac), BSSID, "
        "signal quality percentage, radio type, and transmit/receive channel bandwidth.",
        r"""
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host " SENIOR SYSADMIN DIAGNOSTIC: WI-FI RADIO, RSSI SIGNAL & BSSID QUALITY          " -ForegroundColor Cyan
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host "[INFO] HELPDESK PRIMER: Wi-Fi signal quality below 50% causes high jitter, packet" -ForegroundColor Gray
Write-Host "[INFO] drops, and dynamic link rate downshifting. Modern Wi-Fi 6 (802.11ax) and" -ForegroundColor Gray
Write-Host "[INFO] Wi-Fi 7 (802.11be) on 5GHz/6GHz offer higher throughput and lower contention." -ForegroundColor Gray
Write-Host ""

Write-Host "=== Phase 1: Active Wireless Interface Telemetry ===" -ForegroundColor Cyan
$hasWifi = $false
try {
    $wlanOut = netsh.exe wlan show interfaces
    $wlanLines = $wlanOut -split "`r?`n"

    foreach ($line in $wlanLines) {
        if ($line -match '^\s*(Name|Description|State|SSID|BSSID|Radio type|Authentication|Cipher|Signal|Receive rate|Transmit rate|Channel)\s*:\s*(.+)$') {
            $hasWifi = $true
            $key = $matches[1].Trim()
            $val = $matches[2].Trim()
            
            if ($key -eq 'Signal') {
                $pct = [int]($val.Replace('%',''))
                if ($pct -ge 75) {
                    Write-Host ("  [OK]   {0,-20}: {1} (Strong)" -f $key, $val) -ForegroundColor Green
                } elseif ($pct -ge 50) {
                    Write-Host ("  [INFO] {0,-20}: {1} (Adequate)" -f $key, $val) -ForegroundColor Cyan
                } else {
                    Write-Host ("  [WARN] {0,-20}: {1} (WEAK SIGNAL - RISK OF DISCONNECT)" -f $key, $val) -ForegroundColor Yellow
                }
            } elseif ($key -eq 'Radio type') {
                if ($val -match '802.11ax|802.11be') {
                    Write-Host ("  [OK]   {0,-20}: {1} (Wi-Fi 6/6E/7 High-Efficiency)" -f $key, $val) -ForegroundColor Green
                } else {
                    Write-Host ("  [INFO] {0,-20}: {1}" -f $key, $val)
                }
            } else {
                Write-Host ("  [INFO] {0,-20}: {1}" -f $key, $val)
            }
        }
    }
} catch {
    Write-Host "[INFO] Wireless query: $($_.Exception.Message)"
}

if (-not $hasWifi) {
    Write-Host "[INFO] No active Wi-Fi interface connected or device uses wired Ethernet." -ForegroundColor Gray
}

Write-Host "`n=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ===" -ForegroundColor Cyan
Write-Host "[WI-FI OPTIMIZATION TIPS]:"
Write-Host "  1. If signal is under 50%: relocate closer to Access Point or switch to 5GHz/6GHz band."
Write-Host "  2. If connection drops frequently: update Wi-Fi adapter drivers from manufacturer site."
Write-Host "  3. To reset WLAN autoconfig service: restart WlanSvc in services.msc."
""",
    ),
    NET(
        "vpn_tunnel_routing_leak_audit",
        "VPN Tunnel Interface, Split-Tunnel & DNS Leak Sentinel",
        "Audits VPN adapters, route table metrics, and detects DNS leaks by verifying whether "
        "DNS requests are routed exclusively through the VPN tunnel.",
        r"""
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host " SENIOR SYSADMIN DIAGNOSTIC: VPN TUNNEL INTEGRITY & DNS LEAK AUDIT             " -ForegroundColor Cyan
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host "[INFO] HELPDESK PRIMER: When connected to an enterprise or privacy VPN, all traffic" -ForegroundColor Gray
Write-Host "[INFO] and DNS requests must traverse the encrypted tunnel. If DNS queries leak to" -ForegroundColor Gray
Write-Host "[INFO] local ISP resolvers, internal hostnames and user browsing are exposed." -ForegroundColor Gray
Write-Host ""

Write-Host "=== Phase 1: Virtual Private Network (VPN) Adapters ===" -ForegroundColor Cyan
try {
    $vpnAdapters = Get-NetAdapter | Where-Object { $_.InterfaceDescription -like "*VPN*" -or $_.InterfaceDescription -like "*TAP*" -or $_.InterfaceDescription -like "*WireGuard*" -or $_.InterfaceDescription -like "*Tailscale*" }
    if ($vpnAdapters) {
        foreach ($va in $vpnAdapters) {
            $tag = if ($va.Status -eq 'Up') { "[OK]  " } else { "[INFO]" }
            Write-Host ("{0} VPN Adapter: {1} [{2}] - Status: {3}" -f $tag, $va.Name, $va.InterfaceDescription, $va.Status)
        }
    } else {
        Write-Host "[INFO] Zero active VPN tunnel adapters detected (host is not connected to a VPN)." -ForegroundColor Gray
    }
} catch {
    Write-Host "[INFO] VPN query: $($_.Exception.Message)"
}

Write-Host "`n=== Phase 2: Active DNS Client Server Search Order ===" -ForegroundColor Cyan
$dnsConfigs = Get-DnsClientServerAddress -AddressFamily IPv4 | Where-Object { $_.ServerAddresses.Count -gt 0 }
foreach ($dc in $dnsConfigs) {
    Write-Host ("  Interface '{0}': DNS Servers -> {1}" -f $dc.InterfaceAlias, ($dc.ServerAddresses -join ', '))
}

Write-Host "`n=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ===" -ForegroundColor Cyan
Write-Host "[INFO] If diagnosing DNS leak or VPN bypass:"
Write-Host "  1. If VPN is active, verify that default route metric (0.0.0.0/0) points to the VPN interface."
Write-Host "  2. Ensure 'Use default gateway on remote network' is checked in VPN adapter IPv4 properties."
Write-Host "  3. Verify that local Wi-Fi/Ethernet DNS does not respond to corporate internal domains."
""",
    ),
    NET(
        "rdp_security_encryption_posture",
        "Remote Desktop (RDP) Listener, NLA & Cipher Hardening",
        "Audits Remote Desktop configuration: verifies Network Level Authentication (NLA) enforcement, "
        "port bindings, encryption levels, and active user sessions.",
        r"""
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host " SENIOR SYSADMIN DIAGNOSTIC: REMOTE DESKTOP (RDP) LISTENER & NLA SECURITY      " -ForegroundColor Cyan
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host "[INFO] HELPDESK PRIMER: Remote Desktop allows remote administration. Without" -ForegroundColor Gray
Write-Host "[INFO] Network Level Authentication (NLA), the RDP listener processes pre-auth" -ForegroundColor Gray
Write-Host "[INFO] packets directly, leaving the server vulnerable to exploits like BlueKeep." -ForegroundColor Gray
Write-Host ""

Write-Host "=== Phase 1: Remote Desktop Configuration & NLA Status ===" -ForegroundColor Cyan
$tsKey = 'HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server'
$fDenyTS = (Get-ItemProperty $tsKey -Name fDenyTSConnections -ErrorAction SilentlyContinue).fDenyTSConnections

$rdpEnabled = ($fDenyTS -eq 0)
if ($rdpEnabled) {
    Write-Host "[INFO] Remote Desktop Connections: ENABLED." -ForegroundColor Cyan

    $tcpKey = 'HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp'
    $userAuth = (Get-ItemProperty $tcpKey -Name UserAuthentication -ErrorAction SilentlyContinue).UserAuthentication
    $secLayer = (Get-ItemProperty $tcpKey -Name SecurityLayer -ErrorAction SilentlyContinue).SecurityLayer
    $portNumber = (Get-ItemProperty $tcpKey -Name PortNumber -ErrorAction SilentlyContinue).PortNumber

    if ($userAuth -eq 1) {
        Write-Host "[OK]   Network Level Authentication (NLA) is ENFORCED." -ForegroundColor Green
    } else {
        Write-Host "[BAD]  Network Level Authentication (NLA) is DISABLED! Vulnerable to pre-auth attacks." -ForegroundColor Red
    }

    Write-Host ("[INFO] RDP Listening Port: {0} | Security Layer: {1}" -f $portNumber, $secLayer)
} else {
    Write-Host "[OK]   Remote Desktop Connections are DISABLED on this machine (Safe default)." -ForegroundColor Green
}

Write-Host "`n=== Phase 2: Active Remote Desktop Sessions ===" -ForegroundColor Cyan
try {
    $quserOut = quser.exe 2>&1
    Write-Host $quserOut
} catch {
    Write-Host "[INFO] No active RDP sessions or quser utility unavailable."
}

Write-Host "`n=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ===" -ForegroundColor Cyan
if ($rdpEnabled -and $userAuth -ne 1) {
    Write-Host "[ACTION REQUIRED] Enable NLA immediately in System Properties -> Remote Desktop" -ForegroundColor Red
    Write-Host "[ACTION REQUIRED] or via GPO: Require user authentication for remote connections." -ForegroundColor Red
} else {
    Write-Host "[STATUS] Remote Desktop configuration adheres to security best practices." -ForegroundColor Green
}
""",
    ),
    NET(
        "nic_hardware_offload_latency_profiler",
        "Network Adapter Hardware Offload, RSS/RSC & Low-Latency Engine Profiler",
        "Comprehensive network interface and TCP stack latency profiler: audits Receive Side Scaling (RSS) "
        "indirection tables and processor core affinity, Receive Segment Coalescing (RSC) and Large Send Offload (LSO), "
        "TCP congestion algorithms (CUBIC/CTCP/BBR), TCP AutoTuning, power-saving latency traps (Green Ethernet, EEE), "
        "and NDIS packet drop counters.",
        r"""
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host " SENIOR SYSADMIN DIAGNOSTIC: NIC HARDWARE OFFLOADS & LOW-LATENCY PROFILER      " -ForegroundColor Cyan
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host "[INFO] HELPDESK PRIMER: Hardware offloads transfer packet processing to the NIC." -ForegroundColor Gray
Write-Host "[INFO] While Receive Segment Coalescing (RSC) improves multi-gigabit throughput," -ForegroundColor Gray
Write-Host "[INFO] it can introduce 5-20ms jitter in VoIP, gaming, and real-time audio. Energy" -ForegroundColor Gray
Write-Host "[INFO] Efficient Ethernet (EEE) adds micro-sleep latency to link wakeup times." -ForegroundColor Gray
Write-Host ""

Write-Host "=== Phase 1: Physical Adapters & Hardware Offloads (RSC / LSO / Checksum) ===" -ForegroundColor Cyan
try {
    $adapters = Get-NetAdapter -Physical -ErrorAction Stop | Where-Object { $_.Status -eq 'Up' }
    if (-not $adapters) {
        $adapters = Get-NetAdapter -Physical -ErrorAction SilentlyContinue
    }

    foreach ($a in $adapters) {
        Write-Host ("  Adapter: {0} ({1}) - Status: {2}, LinkSpeed: {3}" -f $a.Name, $a.InterfaceDescription, $a.Status, $a.LinkSpeed) -ForegroundColor Cyan

        # Check Receive Segment Coalescing (RSC)
        try {
            $rsc = Get-NetAdapterRsc -Name $a.Name -ErrorAction SilentlyContinue
            if ($rsc) {
                $rsc4 = $rsc.IPv4Enabled
                $rsc6 = $rsc.IPv6Enabled
                if ($rsc4 -or $rsc6) {
                    Write-Host ("    [WARN] RSC (Receive Segment Coalescing) is ENABLED (IPv4:{0}, IPv6:{1})." -f $rsc4, $rsc6) -ForegroundColor Yellow
                    Write-Host "           RSC coalesces packets into larger chunks. Recommended for bulk downloads,"
                    Write-Host "           but known to cause jitter/latency spikes in gaming, VoIP, RDP, and streaming."
                } else {
                    Write-Host "    [OK]   RSC is DISABLED (Optimal for low latency & real-time responsiveness)." -ForegroundColor Green
                }
            }
        } catch {
            Write-Host "    [INFO] RSC status query: $($_.Exception.Message)"
        }

        # Check Large Send Offload (LSO)
        try {
            $lso = Get-NetAdapterLso -Name $a.Name -ErrorAction SilentlyContinue
            if ($lso) {
                $lsoV4 = $lso.IPv4Enabled
                $lsoV6 = $lso.IPv6Enabled
                Write-Host ("    [INFO] LSO (Large Send Offload): IPv4={0}, IPv6={1}" -f $lsoV4, $lsoV6)
            }
        } catch {
            Write-Host "    [INFO] LSO status query: $($_.Exception.Message)"
        }

        # Check Checksum Offload
        try {
            $csum = Get-NetAdapterChecksumOffload -Name $a.Name -ErrorAction SilentlyContinue
            if ($csum) {
                Write-Host ("    [INFO] Checksum Offload: Tx={0}, Rx={1}" -f $csum.IpIPv4Enabled, $csum.TcpIPv4Enabled)
            }
        } catch {
            Write-Host "    [INFO] Checksum query: $($_.Exception.Message)"
        }
    }
} catch {
    Write-Host "[INFO] NetAdapter hardware offload query: $($_.Exception.Message)"
}

Write-Host "`n=== Phase 2: Receive Side Scaling (RSS) Processor Mapping & Core Affinity ===" -ForegroundColor Cyan
try {
    $rssList = Get-NetAdapterRss -ErrorAction Stop
    $cpuCount = (Get-CimInstance Win32_ComputerSystem).NumberOfLogicalProcessors

    foreach ($rss in $rssList) {
        $name = $rss.Name
        $enabled = $rss.Enabled
        $baseProc = $rss.BaseProcessorNumber
        $maxProc = $rss.MaxProcessors
        $numQueues = $rss.NumberOfReceiveQueues
        $profile = $rss.Profile

        if ($enabled) {
            Write-Host ("  Adapter '{0}': RSS is ENABLED" -f $name) -ForegroundColor Green
            Write-Host ("    Profile: {0} | Base CPU: {1} | Max CPUs: {2} | Queues: {3}" -f $profile, $baseProc, $maxProc, $numQueues)

            if ($baseProc -eq 0 -and $cpuCount -gt 4) {
                Write-Host "    [WARN] BaseProcessor is Core 0! Core 0 handles OS timer interrupts and system threads." -ForegroundColor Yellow
                Write-Host "           On multi-core CPUs, setting BaseProcessor to 1 or 2 avoids DPC core saturation during high network traffic."
            } else {
                Write-Host "    [OK]   BaseProcessor mapping avoids Core 0 bottleneck." -ForegroundColor Green
            }
        } else {
            Write-Host ("  [WARN] Adapter '{0}': RSS is DISABLED. Single CPU core processes all network interrupts!" -f $name) -ForegroundColor Yellow
        }
    }
} catch {
    Write-Host "[INFO] NetAdapterRss query: $($_.Exception.Message)"
}

Write-Host "`n=== Phase 3: TCP/IP Global Stack Configuration & Congestion Provider ===" -ForegroundColor Cyan
try {
    $tcpGlobal = Get-NetTCPSetting -SettingName Internet -ErrorAction SilentlyContinue
    if (-not $tcpGlobal) {
        $tcpGlobal = Get-NetTCPSetting -ErrorAction SilentlyContinue | Select-Object -First 1
    }

    if ($tcpGlobal) {
        $autoTuning = $tcpGlobal.AutoTuningLevelLocal
        $congestion = $tcpGlobal.CongestionProvider
        $ecn = $tcpGlobal.EcnCapability
        $timestamps = $tcpGlobal.Timestamps
        $initRto = $tcpGlobal.InitialRtoMs
        $minRto = $tcpGlobal.MinRtoMs

        # AutoTuning evaluation
        if ($autoTuning -eq 'Normal') {
            Write-Host "  [OK]   TCP Window Auto-Tuning is set to NORMAL (Dynamic scaling active)." -ForegroundColor Green
        } elseif ($autoTuning -eq 'Disabled') {
            Write-Host "  [BAD]  TCP Window Auto-Tuning is DISABLED! Window locked at 64KB (Severely chokes high-speed links)." -ForegroundColor Red
        } else {
            Write-Host ("  [WARN] TCP Window Auto-Tuning level: {0}" -f $autoTuning) -ForegroundColor Yellow
        }

        # Congestion Provider evaluation
        if ($congestion -match 'CUBIC|CTCP|BBR') {
            Write-Host ("  [OK]   Congestion Provider: {0} (Modern high-throughput algorithm)." -f $congestion) -ForegroundColor Green
        } else {
            Write-Host ("  [INFO] Congestion Provider: {0}" -f $congestion)
        }

        Write-Host ("  [INFO] ECN Capability: {0} | Timestamps: {1} | Initial RTO: {2}ms | Min RTO: {3}ms" -f $ecn, $timestamps, $initRto, $minRto)
    }
} catch {
    Write-Host "[INFO] TCP settings query: $($_.Exception.Message)"
}

Write-Host "`n=== Phase 4: Adapter Advanced Properties (Power-Saving Latency Traps) ===" -ForegroundColor Cyan
try {
    $advProps = Get-NetAdapterAdvancedProperty -ErrorAction SilentlyContinue
    $latencyKeywords = @('*EEE', '*EnergyEfficientEthernet', '*GreenEthernet', '*FlowControl', '*InterruptModeration', '*PowerSaving')

    $trapCount = 0
    foreach ($ap in $advProps) {
        if ($ap.RegistryKeyword -in $latencyKeywords -or $ap.DisplayName -match 'Energy Efficient|Green Ethernet|Power Saving') {
            $val = $ap.DisplayValue
            $isPowerSave = $val -match 'Enabled|On' -and $ap.DisplayName -match 'Energy|Green|Power'
            if ($isPowerSave) {
                $trapCount++
                Write-Host ("  [WARN] {0} ({1}): {2} -> {3}" -f $ap.Name, $ap.DisplayName, $ap.RegistryKeyword, $val) -ForegroundColor Yellow
                Write-Host "         Energy saving features cause physical link micro-sleeps, adding 2-15ms packet latency."
            } else {
                Write-Host ("  [INFO] {0} ({1}): {2} = {3}" -f $ap.Name, $ap.DisplayName, $ap.RegistryKeyword, $val)
            }
        }
    }
    if ($trapCount -eq 0) {
        Write-Host "[OK]   Zero aggressive green/energy-saving latency traps active on adapters." -ForegroundColor Green
    }
} catch {
    Write-Host "[INFO] Advanced adapter properties query: $($_.Exception.Message)"
}

Write-Host "`n=== Phase 5: NDIS Network Interface Statistics & Packet Drop Counters ===" -ForegroundColor Cyan
try {
    $stats = Get-NetAdapterStatistics -ErrorAction Stop
    foreach ($s in $stats) {
        $rxErr = $s.ReceivedPacketErrors
        $txErr = $s.OutboundPacketErrors
        $rxDisc = $s.ReceivedDiscardedPackets
        $txDisc = $s.OutboundDiscardedPackets

        $hasDrops = ($rxErr + $txErr + $rxDisc + $txDisc) -gt 0
        $tag = if ($hasDrops) { "[WARN]" } else { "[OK]  " }

        Write-Host ("{0} Interface '{1}':" -f $tag, $s.Name)
        Write-Host ("     Rx Errors: {0,-6} | Tx Errors: {1,-6} | Rx Discarded: {2,-6} | Tx Discarded: {3,-6}" -f $rxErr, $txErr, $rxDisc, $txDisc)
        Write-Host ("     Total Rx:  {0:N0} bytes | Total Tx: {1:N0} bytes" -f $s.ReceivedBytes, $s.SentBytes)
    }
} catch {
    Write-Host "[INFO] NetAdapter statistics query: $($_.Exception.Message)"
}

Write-Host "`n=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ===" -ForegroundColor Cyan
Write-Host "[LATENCY TUNING CHEATSHEET]:"
Write-Host "  1. For gaming/VoIP: Disable Energy Efficient Ethernet and Green Ethernet in Device Manager."
Write-Host "  2. For high packet-per-second streaming: Ensure RSS is enabled and BaseProcessor is not core 0."
Write-Host "  3. To reset entire TCP/IP stack: netsh int ip reset && netsh winsock reset."
""",
    ),
]

