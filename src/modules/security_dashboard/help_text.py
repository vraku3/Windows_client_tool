"""Security card explanations composed from catalog evidence, without Qt or reads."""
INTRO = 'Inspect Windows security status and stage settings to change. Review and Apply ask before writing and request administrator rights when needed; History can revert recorded steps, but commands without an undo and unreadable previous values cannot be restored exactly.'
CONTROLS = {
    'Profile…': 'Export this PC\'s readable security settings or import a profile to stage differences. Import does not write Windows settings until Review and Apply are confirmed.',
    'Refresh': 'Read the selected security page again. This may take time and some checks need administrator rights; it does not apply staged changes.',
    'Only staged': 'Show controls with a pending change. Staged means selected for review, not yet written to Windows.',
    'Only changeable': 'Hide controls that this app can only report. This changes the view without changing protection.',
    'Review…': 'Review pending values, restart requirements and steps that cannot be undone. Nothing is applied until you confirm.',
    'Discard': 'Clear the pending changes without writing to Windows. This does not revert a batch already applied.',
    'Apply': 'Review and confirm the staged batch, then write its settings with administrator rights when required. Only steps with recorded undo can be restored exactly; review lists one-way changes.',
    'Apply these changes': 'Confirm the reviewed batch. Windows settings are then written and checked; unreadable previous values and commands without an undo may prevent exact recovery.',
    'Reload': 'Reload locally recorded security batch history. This reads records without applying or reverting settings.',
    'Revert the selected batch': 'Ask before restoring the selected batch\'s recorded settings, then read back the results. Needs administrator rights for system settings; commands without an undo are not restored and restart-dependent changes remain pending.',
    'Copy report': 'Copy the displayed incident findings and evidence to the clipboard. This does not clear logs or change security settings.',
    'Export this machine…': 'Choose a JSON file for the readable current security values. This writes a profile file without changing Windows settings.',
    'Import and stage…': 'Choose a security profile and stage supported differences for review. Windows settings stay unchanged until Apply is confirmed.',
    '__combo__': 'Choose the number of days of retained event evidence to read. Missing or rolled-over logs do not prove that no incident occurred.',
}
PANES = {
    'Overview': 'Read a summary of Windows protection and its supporting components. Nothing in this overview changes the PC; some checks require administrator rights, and Unknown means the app could not establish the state.',
    'History': 'Review security batches recorded by this app and their verification results. Revert asks first and may require administrator rights; only recorded undo steps can restore previous settings, and deleted event evidence is not recovered.',
    'Incident Triage': 'Review grouped signs of account access, new services and security changes from retained Windows event logs. Nothing here changes the PC; administrator rights are needed for Security log evidence, and missing coverage is reported as Unknown.',
}
HEADERS = {
    'When': 'Local timestamp recorded when this app created the security batch.',
    'What': 'Recorded name or description of the security batch, not a live Windows audit event.',
    'Changes': 'Count of settings included in the recorded batch; check results before assuming they all succeeded.',
    'State': 'Recorded batch or revert outcome. Pending restart and unverified do not mean the setting was confirmed active.',
    'Severity': 'Triage engine priority: High needs investigation, Medium needs review, Info is context, and Unknown means evidence was unavailable. This is not proof of intrusion.',
    'Finding': 'Grouped interpretation of Windows events; select a row for evidence and suggested next steps.',
    'Count': 'Number of retained events grouped into this finding within the selected day window.',
    'Last seen': 'Most recent event timestamp in the finding, shown from retained Windows log evidence.',
}
CATEGORY_COST = {
    'Defender': 'Enforcement can block or quarantine legitimate files and scripts; scanning can add CPU and disk work, and Tamper Protection or another antivirus can refuse changes.',
    'Firewall & Network': 'Changing network protection can interrupt incoming connections, name discovery, file sharing or remote management; review the specific protocol before applying.',
    'Accounts & Credentials': 'Changing sign-in or credential policy can block older authentication methods, offline logons or scripts; confirm a working administrator sign-in first.',
    'Device & Boot': 'Changing boot and device protection can require a restart and affect older drivers or hardware; encryption changes require keeping a recovery key.',
    'Services': 'Turning this service on allows its catalogued function; disabling it prevents dependent apps or devices from using that function. This control changes startup configuration, rather than promising an immediate runtime stop.',
    'Windows Features': 'Turning this feature on installs its Windows component and exposes its functions; turning it off can break apps that depend on it. Component installation may require Windows Update and a restart.',
    'Exploit & CVE': 'Enforcing this mitigation can affect older apps, drivers or CPU performance; a policy value is not proof that all required patches and firmware are installed.',
    'Audit & Logging': 'More auditing uses log space and may record sensitive paths, command lines or script contents; circular logs still overwrite their oldest evidence.',
}
COMPATIBILITY = {
    'defender_controlled_folder_access': 'Turning it on can deny legitimate apps permission to save in protected folders; allow trusted apps when needed.',
    'defender_cloud_protection': 'Cloud checks send protection data to Microsoft and depend on network access; policy or Tamper Protection may refuse the write.',
    'defender_cloud_timeout': 'A longer cloud wait can delay running a downloaded file while Defender waits for a verdict.',
    'defender_cpu_usage': 'Higher scan limits can slow other work; lower limits can make scans take longer. This is a performance trade rather than an On/Off protection.',
    'firewall_outbound_blocked': 'Blocking outbound by default stops legitimate apps reaching the network until matching allow rules exist.',
    'firewall_inbound_blocked': 'Blocking inbound by default prevents connections to listening services unless an allow rule exists.',
    'firewall_enabled': 'Turning the firewall on can block apps or sharing without matching allow rules; turning it off exposes listening services.',
    'firewall_logging_dropped': 'Logging records blocked connections without changing which traffic is allowed, but uses disk space.',
    'mdns': 'Disabling multicast name discovery can stop local printer and cast-device discovery.',
    'llmnr': 'Disabling this fallback can stop short-name lookups on networks without working DNS.',
    'netbios_over_tcpip': 'Disabling NetBIOS can break name discovery and shares on older networks.',
    'rdp': 'Turning Remote Desktop on permits remote interactive sign-in when firewall and account policy allow it; turning it off removes that access path.',
    'rdp_nla': 'Requiring authentication before a remote session starts can block old Remote Desktop clients.',
    'winrm_service': 'Disabling Windows Remote Management breaks remote PowerShell and management tools using it.',
    'remote_registry_service': 'Disabling Remote Registry breaks tools that read or write the registry remotely.',
    'smb_signing': 'Requiring signed file-sharing traffic can block older network storage and clients that cannot sign.',
    'smbv1': 'Disabling this old sharing protocol can disconnect legacy network storage and printers; prefer updating those devices.',
    'admin_shares': 'Disabling administrative shares can break remote deployment, backup and support tools.',
    'telnet_client': 'Enabling the Telnet client allows unencrypted terminal connections; disabling it breaks workflows that still call telnet.exe.',
    'wpad_auto_detect': 'Disabling automatic proxy discovery can break internet access on networks that rely on it.',
    'uac': 'Enabling elevation prompts changes administrator workflows and may require a restart; disabling the boundary weakens isolation.',
    'guest_account': 'Enabling Guest makes Guest-based access possible; disabling it breaks workflows that still rely on that account.',
    'autologon': 'Turning automatic sign-in off requires entering credentials after startup; enabling it trades sign-in protection for convenience.',
    'last_username_hidden': 'Users must type their account name instead of choosing the previous signed-in user.',
    'ctrl_alt_del_required': 'Users must press Ctrl+Alt+Del before entering credentials.',
    'account_lockout': 'A threshold can lock out a legitimate user after repeated mistakes or a deliberate password spray.',
    'password_min_length': 'A longer minimum can reject short passwords on the next password change.',
    'cached_logons': 'Reducing cached domain sign-ins can prevent offline sign-in when no domain controller is reachable.',
    'wdigest_credential_caching': 'Disabling clear-text credential caching can affect old WDigest-dependent authentication.',
    'lsass_protection': 'Protected credential processing can block old authentication plugins, security tools or debuggers that inject into LSASS.',
    'credential_guard': 'Credential isolation can break older credential delegation and authentication workflows; restart may be needed.',
    'ntlm_level': 'Stricter authentication can prevent access to old servers and devices that only support weaker protocols.',
    'ntlm_relay_protection': 'Restricting NTLM can break shares and apps that have no working Kerberos authentication path.',
    'anonymous_restrict': 'Restricting anonymous enumeration can break legacy account and share discovery.',
    'ps_script_block_logging': 'Script contents enter event logs and may include secrets; additional events consume retention space.',
    'ps_transcription': 'Transcripts use disk space and can expose secrets typed into PowerShell; secure their storage location.',
    'ps_execution_policy': 'Stricter execution policy can refuse unsigned scripts but is not a security boundary.',
    'ps_constrained_language': 'Restricted PowerShell can break administration scripts using .NET types, Add-Type or native APIs.',
    'screensaver_lock': 'Returning from the screen saver requires authentication, which can interrupt unattended use.',
    'screensaver_configured': 'Configuring an idle screen saver can interrupt presentations or monitoring workflows.',
    'memory_integrity': 'Memory Integrity can prevent older incompatible drivers from loading, breaking dependent hardware.',
    'vbs_policy': 'Virtualisation-based protection can affect older hypervisors and drivers and may reduce performance.',
    'test_signing': 'Allowing test-signed kernel code weakens driver trust; turning it off can stop development drivers loading.',
    'fast_startup': 'Turning it off makes shutdown perform a full kernel shutdown and can increase next boot time.',
    'hibernation': 'Turning it off removes hibernation and Fast Startup support; turning it on uses disk space for saved memory.',
    'autorun': 'Restricting AutoRun prevents automatic media actions, so users must open media manually.',
    'usb_storage': 'Disabling USB storage can stop legitimate external drives, card readers and phone storage from mounting.',
    'pagefile_clear': 'Clearing the page file can substantially lengthen shutdown on systems with a large page file.',
    'smartscreen': 'Enforced reputation checks can warn about or block unfamiliar legitimate installers and prevent changing the setting in Settings.',
    'ntp_sync': 'Changing time synchronisation affects authentication and certificate validity if the resulting clock is wrong.',
    'windows_update_policy': 'Automatic updates may download, install and require restarts; managed policies may override this setting.',
    'process_cmdline_auditing': 'Command lines can include passwords or tokens, which then become visible to readers of the Security log.',
    'taskscheduler_log_enabled': 'Task history consumes event-log space and reveals task execution details; it does not run or disable tasks.',
}


def category_intro(category):
    return (f'Review {category.value} controls and their compatibility costs. Card buttons only stage changes; '
            'Apply asks first and may request administrator rights, while Revert restores only recorded undo steps.')


def compatibility(control):
    if control.id in COMPATIBILITY:
        return COMPATIBILITY[control.id]
    if control.id.startswith('asr_'):
        return (f'Enforcing this rule can block legitimate software that performs the action named above; '
                'test dependent tools before enforcing it. ASR means attack surface reduction: blocking specific techniques used by malware.')
    if control.id.startswith('defender_threat_'):
        return 'A false detection can quarantine a legitimate file; reverting this preference does not recover files already quarantined or removed.'
    return CATEGORY_COST[control.category.value]


def revert_help(control, value=None, target=None):
    if not control.writable:
        return 'Read-only here: this card changes nothing and has no Revert action. ' + (control.read_only_reason or '')
    steps = control.on_steps + control.off_steps
    if control.revert_probe is not None:
        return 'Revert can restore the exact prior setting only if its dedicated read succeeds when staged; a failed capture is identified in Review. It cannot restore overwritten event evidence.'
    if all(step.get('type') in ('registry', 'registry_delete', 'service') for step in steps):
        return 'Revert uses backed-up registry values (including absence) or service startup types. It does not recover data or guarantee restoration of a service\'s previous running state.'
    if target is not None and value is not None:
        from .staging import PendingChange
        if PendingChange(control.id, control, value, target).one_way_steps():
            return 'Revert cannot restore every step exactly from this previous value; Review identifies steps without an undo. Turning a setting off is not the same as restoring its previous value.'
        return 'Revert can restore the captured setting using its recorded undo commands; it cannot recover files or events already removed by that setting.'
    if any(step.get('type') == 'command' for step in steps):
        return 'Revert cannot undo command steps: the app has no recorded exact previous value for them. A separate opposite action is not an exact restore.'
    return 'Revert depends on capturing a supported previous value for every script step; unknown or unmapped values cannot be restored exactly. Review identifies missing undo commands before Apply.'


def control_help(control, value=None, target=None):
    permission = ' Applying requires administrator rights.' if control.requires_admin else ''
    restart = ' A restart is required before the result takes effect.' if control.requires_reboot else ''
    return '\n\n'.join((control.description + ' ' + control.why_it_matters,
                         compatibility(control), revert_help(control, value, target),
                         'Clicking stages a change only; Review and Apply ask before writing.' + permission + restart
                         if control.writable else 'Reading this card changes nothing on the PC.'))
