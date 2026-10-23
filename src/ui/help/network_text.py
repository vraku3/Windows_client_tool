"""Beginner explanations shared by the network panes; no Qt dependencies.

Command semantics checked against Microsoft Learn: windows-commands/ipconfig,
windows-commands/netsh-winsock, windows-commands/netsh-interface and winrs.
The descriptions of verification and confirmation refer to this app's code.
"""

READ_ONLY = "Nothing here changes your PC's settings; no undo is needed. Administrator rights are not normally needed, but Windows may refuse some reads."
DNS = "DNS (Domain Name System) turns names such as example.com into IP addresses."
WIFI = "SSID is the Wi-Fi network name; BSSID identifies an individual access point radio. Signal % is Windows' signal-quality estimate, not an internet speed test; RSSI is received signal power, usually measured in dBm (less negative is stronger)."
APIPA = "APIPA is an automatic local-only 169.254.x.x address, usually assigned when Windows cannot obtain an address from DHCP (the network's automatic address service)."
MTU = "MTU (maximum transmission unit) is the largest IP packet, in bytes, a link or path can carry without splitting it. This app probes the path with ping; blocked replies leave it unmeasured, and it never changes the adapter MTU."
BAND = "2.4, 5 and 6 GHz are Wi-Fi frequency bands. 2.4 GHz often reaches farther but is more crowded; 5/6 GHz offer more channel choices with compatible equipment. A channel is a numbered slice of a band; sharing or overlapping one can cause contention."
SMB = "SMB (Server Message Block) shares folders and files over a network. A share exposes a local path to permitted remote users; share permissions and file-system permissions both matter."
RDP = "RDP (Remote Desktop Protocol) opens another computer's desktop using mstsc. The target must allow Remote Desktop and your account must have permission; this launcher does not enable it."
WINRM = "WinRM (Windows Remote Management) lets authorized users manage another PC. WinRS runs the entered command through WinRM; that command can change or delete things on the remote PC."
HOSTS = "The hosts file supplies local name-to-address overrides before ordinary DNS lookups in apps that use the Windows resolver. It is not a firewall, and apps using their own resolver may ignore it."

INTRO = {
    "hub": "Choose a pane to inspect connectivity, Wi-Fi, file sharing or remote access. Diagnostics read settings and send test traffic; repairs, DNS/proxy changes and hosts-file saves can change this PC, while remote commands can change another PC. Individual panes explain confirmation, undo and administrator requirements.",
    "tools": "These cards inspect network traffic, configuration and connectivity using Windows commands and local counters. They send probes or capture packets but do not change network settings; ordinary reads need no administrator rights, while password visibility and packet capture depend on Windows permissions and installed capture support.",
    "Health": "Check adapters, gateway reachability, DNS and packet size to find likely connectivity problems. Checks only read settings and send probes; repair buttons ask first, and DHCP renewal or Winsock reset need administrator rights. Repairs have no automatic undo; Winsock reset requires a restart.",
    "DNS compare": DNS + " Ask the system and additional DNS servers the same question to compare answers and response times. This sends the name to those servers without changing your configured DNS; no administrator rights or undo are needed.",
    "DNS cache": DNS + " Inspect the answers Windows has already remembered; Refresh and filtering only read data. Flush Cache asks before clearing cached answers, which refill as names are used; Windows permissions may restrict it and there is no restore of the old cache.",
    "Ping and jitter": "Send repeated ping probes to measure response time, loss and variation between replies (jitter). This does not change settings and needs no administrator rights; Start/Stop controls the probes and Reset clears only this pane's measurements.",
    "Routes": "Routes tell Windows where to send packets; the gateway is the next router used to reach other networks. ARP (IPv4) and NDP (IPv6) remember nearby IP-to-hardware-address mappings. This pane only reads Windows routes and neighbour state, normally without administrator rights; it changes nothing.",
    "wifi": "Inspect nearby Wi-Fi radios, your current link, saved profiles and connection events. Scanning and refreshing only read Windows data; they do not connect, forget networks or change router channels. No administrator rights are normally needed, but Windows location permission or log permissions may limit results.",
    "Networks": WIFI + " This list comes from netsh WLAN reads and changes nothing; no administrator rights are normally needed, though Windows may require location permission.",
    "Current Connection": "Read the current Wi-Fi interface properties reported by netsh, including connection state and link rate. This does not connect or disconnect anything; no administrator rights or undo are normally needed.",
    "Channel Map": BAND + " The map only suggests channels from the visible scan; it does not change your router and needs no administrator rights.",
    "Connection History": "Read the WLAN AutoConfig event log for connection and disconnection reasons recorded by Windows. Counts cover the events retained and read, not your lifetime history; this pane changes nothing and restricted logs may need administrator rights.",
    "Saved Profiles": "Saved profiles are Wi-Fi networks Windows remembers and may rejoin automatically. Refresh reads profile security and connection preferences without displaying keys, deleting profiles or changing settings; no administrator rights or undo are normally needed.",
    "hosts": HOSTS + " Edits stay in the table until Save; saves and restores ask first, back up the existing file and check the written file. Administrator rights are needed to write, and Restore can recover a saved backup.",
    "DNS Switcher": DNS + " Apply DNS writes IPv4 DNS servers for the selected adapter; Restore DHCP switches that adapter's DNS back to automatic. These act without confirmation and need administrator rights; the previous static addresses are not backed up.",
    "Proxy Settings": "A proxy forwards requests for applications that use the current user's Windows Internet Settings. Save writes these registry values without confirmation or backup; re-enter previous values to change them again. This module requires administrator rights, although these values belong to your user account.",
    "Quick Actions": "Run ipconfig or netsh repairs immediately, without confirmation; these need appropriate Windows permissions and this module requires administrator rights. Release can disconnect you, while Winsock and TCP/IP resets need a restart and have no undo here. The output is command output, not proof that connectivity is repaired.",
    "shares": SMB + " These panes only read shares, permissions, sessions and mapped drives, and never close files or disconnect users. Windows may require administrator rights for some SMB reads; no undo is needed.",
    "Exposure": "Inspect the reported configuration and findings for network access on this PC. Refresh only reads Windows state and does not enable, disable or test access from another computer; some reads may need administrator rights. No undo is needed.",
    "Network Shares": SMB + " This pane reads Get-SmbShare and Get-SmbShareAccess and changes nothing; Windows may require administrator rights for these reads.",
    "Sessions": "An SMB session is a remote user's connection to this PC's file-sharing service. Read Get-SmbSession for clients, idle time and open counts; this does not disconnect anyone, and restricted reads may require administrator rights.",
    "Open Files": "Read Get-SmbOpenFile to see files remote SMB clients currently have open on this PC. Local application handles are not included; nothing is closed or changed, and Windows may require administrator rights for the read.",
    "Mapped Drives": "A mapped drive gives a remote shared folder a drive letter in this Windows account. This reads HKCU Network and connected Windows network resources without mapping or disconnecting anything; no administrator rights or undo are normally needed.",
    "remote": "Inspect local remote-access exposure or launch connections and network probes. RDP opens a separate client, WinRS can change a remote PC according to your command, and Wake-on-LAN can wake hardware; none has an automatic undo here. Local administrator rights are not required to launch these tools, but target permissions still apply.",
    "RDP": RDP + " No local administrator rights are needed to launch the client; actions inside the remote session may change that PC and are not undone by this app.",
    "WinRS": WINRM + " Run executes immediately without confirmation or automatic undo, using your available credentials; remote authorization is required and local elevation alone does not grant it.",
    "Ping Sweep": "Run ping against addresses from .1 through the selected final number in a subnet prefix. This sends network probes without changing settings and needs no administrator rights; a host that blocks ping can be labelled Offline even when it is running.",
    "Wake-on-LAN": "Send a UDP magic packet to request that compatible, configured hardware wakes up. Sending needs no administrator rights and has no confirmation or undo; success means the packet was sent, not that the computer woke up.",
}

CARDS = {
    "Ping": "Runs ping -n COUNT HOST to measure reachability and round-trip time in milliseconds. Sends probes without changing settings; a missing reply can mean filtering, not a broken connection.",
    "Traceroute": "Runs tracert -d -w 1000 HOST to list routers (hops) along a route, without reverse name lookups. Sends probes without changing routes; can take up to two minutes, and missing hops may simply block replies.",
    "DNS Lookup": DNS + " Runs nslookup -type=TYPE HOST and displays its answer; it does not change DNS settings or the hosts file.",
    "Port Scanner": "Attempts TCP connections to the target ports using Python sockets, with a 0.5-second timeout per attempt. Sends connection traffic without changing firewall rules or opening ports; service names are guesses from port numbers, not identification of the responding application.",
    "Active Connections": "Reads psutil.net_connections and process names for current local/remote endpoints. Does not close connections or stop processes; protected process details may be unavailable without administrator rights.",
    "Wi-Fi Profiles": "Runs netsh wlan show profiles, then netsh wlan show profile name=NAME key=clear when you select a profile. This changes nothing but may display the saved Wi-Fi password if Windows permits it; take care when sharing the output.",
    "Adapter Info": "Reads psutil adapter addresses/link state, the Windows route table and Get-DnsClientServerAddress for IPv4 DNS. No adapter, address, gateway or DNS settings are changed; reported link speed is not measured internet speed.",
    "Network Errors": "Runs netstat -s to read protocol counters such as retransmissions and discarded packets. Does not reset counters or repair anything; cumulative nonzero values alone do not prove a current fault.",
    "Winsock Catalog": "Runs netsh winsock show catalog and checks whether listed provider DLL paths exist. Winsock connects applications to Windows networking; this card does not reset the catalog, and an existing DLL is not proof that the provider works.",
    "Live Traffic": "Reads psutil byte counters once a second and adapter error/drop counters every five seconds. Chart values are byte-counter differences divided by 1024 (shown as KB/s); this neither captures packet contents nor changes settings.",
    "Packet Capture (requires Npcap + scapy)": "Uses scapy sniff with Npcap to observe packets for the selected duration and display summaries. Does not change network configuration or save a capture file; capture support and Windows permissions are required, and addresses or packet details may be sensitive.",
}

FIX_HELP = {
    "flush_dns": "Runs ipconfig /flushdns after confirmation to discard remembered DNS answers. The cache refills on use; the app compares counts to verify clearing where readable, but cannot restore old entries. Windows may require administrator rights.",
    "renew_dhcp": "Runs ipconfig /renew after confirmation for all DHCP adapters, without running /release first. The address may change and remote sessions may drop; administrator rights are required. Lease times are read back, and there is no automatic undo.",
    "reset_winsock": "Runs netsh winsock reset after confirmation, restoring the catalog and removing custom layered service providers used by some networking software. Requires administrator rights and a restart; the result cannot be verified immediately and this app has no undo.",
}

QUICK_HELP = {
    "Flush DNS": "Runs ipconfig /flushdns immediately without confirmation, discarding cached DNS answers. Entries refill on use; there is no restore or read-back verification here. Requires appropriate Windows permissions.",
    "Reset Winsock": "Runs netsh winsock reset immediately without confirmation; removes custom Winsock layered service providers. Requires administrator rights and a restart; cannot be verified immediately and has no undo here.",
    "IP Release": "Runs ipconfig /release immediately without confirmation on all IPv4 DHCP adapters. Discards leased addresses and can cut off internet and remote sessions; IP Renew can request a lease again but cannot guarantee the same address. Requires appropriate Windows permissions; there is no state verification here.",
    "IP Renew": "Runs ipconfig /renew immediately without confirmation for all IPv4 DHCP adapters. Requests renewed leases and may change addresses; requires appropriate Windows permissions and has no automatic undo or read-back verification here.",
    "Reset TCP/IP": "Runs netsh int ip reset immediately without confirmation, resetting TCP/IP configuration. Requires administrator rights and a restart; custom network settings may need to be entered again. No backup, undo or read-back verification is provided here.",
}

CONTROL_HELP = {
    "host_edit": "Target computer name or IP address to probe; 127.0.0.1 means this PC. Typing only edits the target; the adjacent action sends traffic without changing network settings.",
    "count_spin": "Number of ping echo requests (1-10). Changes only the next probe, not Windows settings.",
    "type_combo": "DNS record to request: A = IPv4 address, AAAA = IPv6 address, MX = mail server, NS = name server, PTR = reverse lookup, TXT = text, ANY = server-selected records. Selecting it changes only the next query.",
    "start_spin": "First TCP port to test (1-65535), included in the scan. Changes only this scan's range, not firewall rules.",
    "end_spin": "Last TCP port to test (1-65535), included in the scan. A larger range sends more connection attempts and takes longer; it does not open ports.",
    "duration_spin": "Capture duration in seconds (1-60). Changes only how long this capture observes packets; it does not change network settings.",
    "self._name": "DNS name sent to every selected resolver when Query is clicked; queries may disclose internal names to public servers. Typing does not change Windows settings.",
    "self._type": "DNS record: A = IPv4, AAAA = IPv6, MX = mail server, TXT = text, NS = name server, CNAME = alias. Changes the next query only.",
    "self._extra": "Additional DNS server IP addresses, separated by commas or semicolons. Query sends the chosen name to these and the system DNS servers; editing this list does not replace Windows DNS settings.",
    "self._host": "Computer name or IP address for repeated ping probes. Editing changes only this pane's target, not Windows settings.",
    "self._filter": "Show cached DNS rows whose name contains this text. Filters the view only; it does not remove cache entries or change DNS settings.",
    "adapter_combo": "Network adapter whose IPv4 DNS settings Apply DNS or Restore DHCP will change. Listed adapters are those psutil reports as up; selection alone changes nothing.",
    "preset_combo": "Fill the two DNS address fields with a bundled provider preset, or choose Custom to type your own. Selection does not change Windows until Apply DNS.",
    "primary_edit": "Primary IPv4 DNS server to write for the selected adapter. Editing alone changes nothing; Apply DNS replaces the setting without saving the previous addresses.",
    "secondary_edit": "Optional second IPv4 DNS server, added at index 2 when Apply DNS runs. Editing alone changes nothing; this is not a guarantee that every lookup uses the first server.",
    "server_edit": "Proxy address, such as host:port, stored in the current user's ProxyServer registry value when Save is clicked. Editing alone changes nothing; this does not configure the separate WinHTTP proxy.",
    "override_edit": "Semicolon-separated proxy bypass patterns such as localhost;127.*;*.local. Save writes ProxyOverride for the current user; editing alone changes nothing.",
    "self._winrs_cmd": "Command sent to the remote host by winrs. Run or Enter executes immediately without confirmation; it may modify or delete remote data according to the command and your permissions, with no app undo.",
    "self._sweep_prefix": "First three IPv4 numbers, such as 192.168.1; Sweep probes prefix.1 through the chosen final number. Editing only changes the probe range, not your PC's address or subnet.",
    "self._sweep_end": "Last address number included in the ping sweep (2-254). Sweep starts at .1; changing this only affects the probe range.",
    "self._wol_mac": "Hardware (MAC) address of the device to wake, for example AA:BB:CC:DD:EE:FF. This identifies the target in the magic packet; typing does not change any adapter address.",
    "self._wol_broadcast": "Destination IPv4 broadcast address for the UDP port-9 magic packet. 255.255.255.255 normally stays on the local network; a subnet-directed broadcast needs network support and routing permission. Editing this changes only the packet destination.",
    "le": "Remote computer name or IP address; recent targets are suggested from this app's history. Typing changes only the target, not remote-access permissions.",
    "cb": "Enable or comment out this hosts-file mapping when saved. Changes stay in the table until the confirmed administrator Save; Reload can discard unsaved edits.",
}

BUTTON_HELP = {
    "Run checks": "Read Windows adapter/address/DNS settings and send gateway, DNS, path-MTU, IPv6 and time probes. Checks change no settings and normally need no administrator rights; they can take a minute. Findings explain APIPA addresses (169.254.x.x) and MTU (maximum packet size in bytes) where relevant; an unanswered probe is not proof of a fault.",
    "Copy report": "Copy the current findings, adapter data, routes and DNS timings to the clipboard for a support ticket. Replaces clipboard contents without confirmation; does not change networking and may include private addresses.",
    "Hide passing checks": "Hide findings marked OK in this pane. Error means a detected problem, Warning needs attention, Info adds context, and Unknown means the app could not establish a result; this filter changes no settings.",
    "Query": "Send the selected DNS query directly to system and extra resolvers and compare their answers and milliseconds taken. Does not change your DNS settings; public resolvers receive the queried name.",
    "Start": "Start or stop repeated ping probes about once a second. Changes no network settings; loss means unanswered probes, jitter is variation between reply times, and the chart plots lost replies as zero rather than zero latency.",
    "Reset": "Clear this pane's ping samples, chart and loss/jitter statistics without confirmation. Does not reset adapters or Windows networking; discarded samples have no undo.",
    "Flush Cache": FIX_HELP["flush_dns"],
    "Apply DNS": "Run netsh interface ip set dns with the primary IPv4 server, then add dns index=2 for an optional secondary, on the selected adapter. Applies immediately without confirmation and requires administrator rights; old settings are not backed up and command completion is not verified by reading settings back.",
    "Restore DHCP": "Run netsh interface ip set dns ADAPTER dhcp immediately without confirmation. Returns IPv4 DNS selection to automatic, not the adapter's IP address; requires administrator rights and does not restore previous static DNS servers. There is no read-back verification here.",
    "Enable Proxy": "Choose whether Save writes ProxyEnable=1 or 0 for this Windows user. Toggling alone changes nothing; this does not configure every application's proxy.",
    "Load": "Read ProxyEnable, ProxyServer and ProxyOverride from the current user's Internet Settings registry key. Replaces unsaved field edits without confirmation and changes no Windows settings.",
    "Save proxy": "Write ProxyEnable, ProxyServer and ProxyOverride for the current user immediately without confirmation. Affects applications using these Windows Internet Settings; no backup or undo is provided, so keep old values before replacing them.",
    "Open related page": "Navigate to the app page associated with the selected exposure finding. Does not enable remote access or change settings; any changes on the destination page are separate actions.",
    "Connect (mstsc)": "Launch mstsc /v:HOST to open Remote Desktop to the entered target without an app confirmation. The target must allow your account; this does not enable RDP or change firewall rules. Actions in the remote session can change that PC; no app undo is provided.",
    "Run": "Run winrs -r:HOST COMMAND immediately without confirmation using available credentials. The command can change the remote PC and has no automatic undo; WinRM and remote authorization must already be configured.",
    "Sweep": "Run ping -n 1 -w 500 against each address from prefix.1 through the final number, with up to 50 concurrent probes. Sends traffic without changing settings or needing administrator rights; Offline can mean ping was blocked.",
    "Send Magic Packet": "Send a Wake-on-LAN magic packet by UDP to port 9 at the chosen broadcast address immediately, without confirmation. Can wake compatible configured hardware; no undo is provided, and sent does not confirm the target woke.",
    "Save hosts": "Ask before writing the edited hosts file with administrator rights. Back up the existing file, read the write back and attempt rollback if verification fails; Restore can recover a previous backup. A newly created file has no prior-file backup.",
    "Backup": "Copy the current on-disk hosts file to the app's hosts_backups folder without confirmation. Unsaved table edits are not included; the active hosts file is unchanged.",
    "Restore...": "Choose a backup, then confirm replacing the active hosts file with administrator rights. The current file is backed up first when present and the replacement is read back; use Restore again to recover that backup.",
    "Reload": "Re-read the active hosts file; asks before discarding unsaved edits. Changes only the table, not the file; discarded edits are not recoverable here.",
    "Add Entry": "Add an editable example row to the table without confirmation. Nothing is written until the confirmed Save; Reload can discard the unsaved row.",
    "Delete Selected": "Ask before removing selected rows from the table. The hosts file changes only after a separate confirmed Save; Reload can recover unsaved deletions, or Restore can recover a saved backup.",
    "Import Blocklist": "Ask before adding bundled hostname blocks to the table; Save is still needed to apply them. Some entries can affect sign-in or other services, and this list does not block every telemetry route; Reload can discard unsaved additions.",
    "Check against DNS": "Run Resolve-DnsName -NoHostsFile for listed hostnames to compare DNS answers with local overrides. Sends lookup traffic without saving or changing the hosts file; no administrator rights are normally needed.",
    "Auto-refresh": "Periodically repeat this pane's reads while active. Changes only this app's refresh behavior, not Windows settings; turn it off to stop scheduled reads.",
    "Show error counters only": "Filter netstat -s rows by error-style words such as failure, reset or retransmit. Changes only the displayed rows; a cumulative nonzero counter does not prove a present fault.",
    "Stop": "Request cancellation of this card's current scan or capture. An in-flight operation may finish first; changes no network settings and does not remove already collected results.",
}

HEADERS = {
    "Hop": "Router position along the tracert path, starting at 1; a missing reply does not prove the router is down.",
    "IP": "IP address reported by Windows; on an adapter this identifies an interface, and in traceroute it identifies a responding hop. " + APIPA,
    "IP Address": "IP address reported in this pane; in Hosts Editor it is the local override destination, and in Ping Sweep it is the tested target.",
    "IP address": "Neighbour's IPv4 or IPv6 address read from Get-NetNeighbor; not proof that the device is currently reachable.",
    "Time": "In traceroute: first parsed round-trip result in milliseconds, or * for no reply. In packet capture: elapsed seconds since capture started, from the packet timestamp.",
    "Port": "TCP port number that accepted the Python socket connection, in the selected range 1-65535; not a firewall rule.",
    "Status": "State reported by this pane's source. Ping Sweep's Offline means no successful ping, not proof of power-off; exposure status is configuration evidence, not a remote reachability test.",
    "Service": "Conventional service name guessed from a port-number dictionary (SMB shares files; RDP provides Remote Desktop). Does not identify or verify the actual listening application.",
    "Local Address": "This PC's IP address and port from psutil.net_connections; a wildcard address means the socket is not bound to just one local IP.",
    "Remote Address": "Peer IP address and port from psutil.net_connections; a blank value can mean a listening or unconnected socket.",
    "PID": "Windows process identifier reported by psutil for the socket owner; it may be unavailable for protected processes.",
    "Process": "Socket owner's process name resolved by psutil from the PID; missing or exited processes may have no name.",
    "Name": "Adapter, DNS cache record or SMB share name, as reported by the source described above this table; a name is a label, not independent verification.",
    "MAC": "Network adapter hardware address reported by psutil. Identifies an interface on a local link and is different from its IP address.",
    "Netmask": "IPv4 subnet mask reported by psutil; specifies which address bits identify the local network.",
    "Gateway": "Next router used to reach other networks, read from the Windows IPv4 route table; it is not necessarily your DNS server.",
    "DNS": DNS + " IPv4 resolver addresses read using Get-DnsClientServerAddress for this adapter.",
    "Speed": "Adapter's reported link speed in Mbps (megabits per second), from psutil; not a measured download speed.",
    "Up": "Whether psutil reports the adapter link as up; an up link does not prove internet access.",
    "Category": "Protocol section heading parsed from netstat -s, such as IPv4, TCP or UDP.",
    "Counter": "Counter name parsed from netstat -s; highlighted names match the app's error-word filter, not a new failure detected by a probe.",
    "Value": "Value reported by the source for the named property or counter. Wi-Fi values come from netsh; protocol counters come from netstat -s and are cumulative, not rates.",
    "Description": "Provider description read from netsh winsock show catalog; it does not independently verify the provider's purpose or trust.",
    "Entry type": "Winsock catalog entry type reported by netsh, such as a base or layered provider; layered providers intercept socket operations.",
    "Provider path": "Provider DLL path reported by netsh. Red indicates that the app could not find the DLL at the resolved path; existence alone does not prove correct operation.",
    "Catalog ID": "Windows Winsock catalog identifier from netsh, used to distinguish provider entries; it is not a risk score.",
    "Adapter": "Network interface name returned by psutil for the byte, error and drop counters in this row.",
    "Bytes Sent": "Cumulative bytes sent on this adapter, from psutil.net_io_counters(pernic=True); not bytes per second.",
    "Bytes Recv": "Cumulative bytes received on this adapter, from psutil.net_io_counters(pernic=True); not bytes per second.",
    "Errors In": "Cumulative receive errors reported by psutil for this adapter, in packets; nonzero alone does not prove an active fault.",
    "Errors Out": "Cumulative transmit errors reported by psutil for this adapter, in packets.",
    "Drops In": "Cumulative incoming packets discarded, from psutil; not a percentage or application-level loss test.",
    "Drops Out": "Cumulative outgoing packets discarded, from psutil; zero can also reflect limited platform counter support.",
    "Proto": "Protocol classified from captured scapy packet layers, such as TCP, UDP or ICMP.",
    "Source": "In packet capture: sending address from the captured packet. In DNS compare: which adapter or custom entry supplied the DNS server address.",
    "Destination": "In capture: recipient address from the packet. In Routes: network prefix read from Get-NetRoute; 0.0.0.0/0 or ::/0 is a default route.",
    "Length": "Captured packet length in bytes as reported by scapy, not an application download size.",
    "Info": "Packet summary produced from captured scapy layers; may include network endpoints and other sensitive details.",
    "Result": "Health severity inferred by this app, or DNS response/error returned by the queried server. Unknown means a result could not be established, not that the check passed.",
    "Finding": "App-generated explanation based on the collected Windows data and probe results; select a row for supporting details.",
    "Server": "DNS resolver IP address queried directly by the app, drawn from Windows adapter settings or the extra-server field.",
    "ms": "DNS request round-trip time in milliseconds, measured by the app; a dash means no timing was available.",
    "Answers": "DNS records returned by the queried server, including any CNAME alias chain; answers can legitimately vary by server, cache and location.",
    "Next hop": "Router address for this route, from Get-NetRoute; an on-link route may use an unspecified address rather than a separate router.",
    "Interface": "Windows interface identifier/name from Get-NetRoute or Get-NetNeighbor; identifies which adapter carries the route or neighbour.",
    "Metric": "Combined route and interface cost from the Get-NetRoute fields; lower costs are normally preferred among equally specific routes, not a speed measurement.",
    "Protocol": "Windows route-origin protocol from Get-NetRoute, describing how the route was learned or configured.",
    "Store": "Policy store reported by Get-NetRoute, such as ActiveStore or PersistentStore; this pane does not modify either.",
    "MAC address": "Neighbour's local-link hardware address from Get-NetNeighbor; a blank value can mean address resolution is incomplete.",
    "State": "Neighbour reachability state from Get-NetNeighbor: Reachable was recently confirmed, Stale needs revalidation, and Incomplete/Unreachable indicates unresolved or failed reachability.",
    "Type": "DNS cache record type parsed from ipconfig /displaydns, or mapped-drive persistence type from Windows registry/network resources, according to this table.",
    "TTL": "Time to live: remaining cache lifetime in seconds, parsed from ipconfig /displaydns; this is not the age of the record.",
    "Section": "DNS record section reported by ipconfig /displaydns, such as Answer; describes the cached response section.",
    "Data": "Cached record contents parsed from ipconfig /displaydns, such as an address or target name; no new lookup is performed by Refresh.",
    "SSID": "Wi-Fi network name advertised by an access point, as reported by netsh; multiple radios can share the same name.",
    "Signal %": WIFI,
    "Channel": "Numbered frequency slice used by the access point, reported by netsh. " + BAND,
    "Band": BAND + " Reported or inferred from the netsh scan; this pane cannot change it.",
    "Radio": "Wi-Fi radio standard, such as 802.11n/ac/ax, reported by netsh; not a guarantee of internet speed.",
    "Security": "Wi-Fi security summary derived from the netsh authentication/encryption fields; it is not a penetration test.",
    "Authentication": "Access-point or saved-profile authentication method from netsh, such as WPA2-Personal or enterprise authentication; not proof of a trusted network.",
    "Utilization %": "Access point's advertised channel busy percentage from the BSS Load information element, when available. A blank value means not advertised/read, not zero congestion.",
    "BSSID": "Access point radio's hardware address reported by netsh; distinguishes radios sharing an SSID, but does not prove identity or trust.",
    "Property": "Current Wi-Fi interface property name parsed from netsh wlan show interfaces; select the associated Value for Windows' report.",
    "Profile": "Saved Wi-Fi profile name read by netsh, which can differ from currently visible network names.",
    "Rating": "App assessment of saved-profile authentication and preferences; Unknown means insufficient data, not a secure profile.",
    "Cipher": "Wi-Fi encryption method reported in the saved profile by netsh; it is not independently tested here.",
    "Auto-connect": "Whether the saved profile tells Windows to connect automatically, read from netsh; this pane does not toggle it.",
    "Hidden SSID": "Whether the saved profile permits connection to a network that does not broadcast its name; hiding a name does not encrypt or secure the network.",
    "Has key": "Whether netsh reports a stored Wi-Fi key; this pane checks its presence without requesting or showing the password.",
    "Event": "WLAN AutoConfig event ID/type from the retained Windows event log; identifies connection-related events.",
    "Network": "Wi-Fi network name associated with a WLAN AutoConfig event, when Windows recorded one.",
    "Reason (Windows' words)": "Connection/disconnection reason text retained in the WLAN AutoConfig event, not a cause independently verified by this app.",
    "Count": "Number of matching events in the log sample read by this app, not a lifetime total.",
    "Last seen": "Most recent matching WLAN event timestamp in the retained log sample, displayed as recorded by the reader.",
    "What it means": "App explanation of a known WLAN event/reason; an unfamiliar event is described only using its available data.",
    "Feature": "Remote-access feature inspected using local registry, service, listener and firewall configuration; not a test from outside this PC.",
    "Details": "Supporting Windows configuration evidence gathered for the remote-access feature; unreadable data is not treated as disabled.",
    "Findings": "App assessment of the remote-access configuration evidence; select a row before opening a related page.",
    "Severity": "App assessment of SMB configuration: warning/error needs attention, Unknown means unavailable evidence, and Setting reports a value rather than a finding.",
    "Detail": "Supporting explanation from the SMB audit and Windows settings; it does not test whether an external client can reach the share.",
    "Kind": "SMB share classification derived from its name and Windows properties; administrative or hidden shares are not automatically public.",
    "Path": "Local folder exposed by an SMB share, or a file opened by a remote SMB client, as reported by Get-SmbShare/Get-SmbOpenFile.",
    "Access": "Allow/deny entries read from Get-SmbShareAccess. These are share permissions only; file-system permissions also restrict access.",
    "Encrypted": "Share EncryptData setting from Get-SmbShare; this is configured SMB encryption policy, not proof of a particular session's negotiated encryption.",
    "Users": "Current share users reported by Get-SmbShare, a count rather than a configured access limit.",
    "Comment": "SMB share description reported by Windows, or the hosts-file row's comment. A description is supplied text, not a verified purpose.",
    "Client": "Remote SMB client address/name read from Get-SmbSession or Get-SmbOpenFile.",
    "User": "Remote account reported by Get-SmbSession/Get-SmbOpenFile; does not list every account permitted access.",
    "Opens": "Number of opens reported for this SMB session by Get-SmbSession; not a count of all local process handles.",
    "Connected (sec)": "Seconds this SMB session has been connected, reported by Get-SmbSession.",
    "Idle (sec)": "Seconds this SMB session has been idle, reported by Get-SmbSession; not the remote user's general inactivity.",
    "Dialect": "SMB protocol version negotiated for this session, reported by Get-SmbSession; SMB1 is an older protocol with weaker protections.",
    "Drive": "Mapped drive letter read from HKCU Network or Windows connected network resources for this account.",
    "Remote Path": "Network share path associated with the mapped drive, read from Windows, typically \\\\server\\share.",
    "Enabled": "Whether this hosts-file mapping is active rather than commented out. Editing takes effect only after the confirmed Save.",
    "Hostname(s)": "One or more names mapped to the row's IP address by the local hosts file; entries match names, not URL paths or wildcard domains.",
}

# Display labels differ slightly between the two diagnostic views.
INTRO["Connected Sessions"] = INTRO["Sessions"]
INTRO["Network Diagnostics"] = INTRO["tools"]
INTRO["Wi-Fi Analyzer"] = INTRO["wifi"]
INTRO["Hosts Editor"] = INTRO["hosts"]
INTRO["Network Extras"] = "Change adapter IPv4 DNS, current-user proxy settings or run network repairs. Administrator rights are required for this module; these actions have no automatic undo and several can disconnect you or need a restart. Each tab explains what is written and whether confirmation is offered."
INTRO["Shared Resources"] = INTRO["shares"]
INTRO["Remote Tools"] = INTRO["remote"]
CARDS["WiFi Profiles"] = CARDS["Wi-Fi Profiles"]
CARDS["Packet Capture"] = CARDS["Packet Capture (requires Npcap + scapy)"]
HEADERS["Resolver"] = HEADERS["Server"]
HEADERS["Time (ms)"] = HEADERS["ms"]

CONTEXT_HEADERS = {
    ("Adapter Info", "Name"): "Adapter name reported by psutil.net_if_addrs, matched to its reported link state and IPv4 DNS settings.",
    ("Adapter Info", "IP"): "Adapter IPv4 address from psutil.net_if_addrs. " + APIPA,
    ("Traceroute", "IP"): "Responding router address parsed from tracert -d; a timeout is displayed when the hop does not answer.",
    ("Traceroute", "Time"): "First round-trip result parsed from tracert, in milliseconds; * means no reply, not zero latency.",
    ("Packet Capture", "Time"): "Seconds elapsed between capture start and the packet timestamp, calculated from scapy's packet time.",
    ("DNS cache", "Name"): "Cached DNS record name parsed from ipconfig /displaydns; Refresh reads the cache without contacting a DNS server.",
    ("DNS cache", "Type"): "DNS cache record type parsed from ipconfig /displaydns, such as A (IPv4), AAAA (IPv6) or CNAME (alias).",
    ("Mapped Drives", "Type"): "Mapping classification from HKCU Network or Windows connected network resources, such as Persistent, Session or Network; this does not check reachability.",
    ("Mapped Drives", "Status"): "Connected is the label this app assigns to registry mappings and connected Windows network resources; it does not independently test the share.",
    ("Ping Sweep", "Status"): "Online means ping returned success; Offline means it did not. Filtering, timeouts or a sleeping device can all cause no reply, so this is not a power-state check.",
    ("hosts", "IP Address"): "Destination IPv4 or IPv6 address in this hosts-file mapping; 0.0.0.0 and loopback addresses are often used to block or redirect a hostname locally. Edits are applied only after Save.",
    ("hosts", "Comment"): "Optional text after the hosts entry's # comment marker, read from or saved to the hosts file. Comments do not participate in name lookup.",
    ("Network Shares", "Name"): "SMB share name read from Get-SmbShare; a name ending in $ is hidden from ordinary browsing, which does not itself restrict access.",
}
