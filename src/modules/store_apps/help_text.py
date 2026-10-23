"""Store package inventory help; no Qt dependency."""
INTRO = 'Inspect Windows app packages, their account scope and recent installation failures. Reading needs no administrator rights for your own account; removal asks first, all-user and provisioning changes need administrator rights, and reinstalling does not guarantee app-data recovery.'
PANES = {
    'Installed apps': 'Read package identities, versions and measured folder sizes from Windows. Uninstall asks first and removes packages in the available account scope; administrator rights are needed for all-user changes, and there is no per-package undo here.',
    'Install / update failures': 'Review grouped failures retained in the Windows app deployment log and compare them with installed versions. Nothing here retries an installation or changes the PC; unavailable logs are reported, and older failures may have rotated out.',
}
CONTROLS = {
    'Refresh': 'Read the package inventory and deployment failure log again, then measure package folder sizes. This does not install, repair or remove apps.',
    'Columns': 'Choose additional package identity, architecture and provisioning columns. This changes the display only.',
    'Package Family Name': 'Show or hide the package family identifier, shared across versions. This changes no package registration.',
    'Architecture': 'Show or hide the package architecture such as x86, x64 or ARM64. This changes the display only.',
    'Export': 'Choose a CSV file and write the app inventory and available measured data. This does not change installed packages.',
    'Shortcuts': 'Show keyboard shortcuts for searching, selecting, refreshing and exporting the inventory.',
    'Select Non-System': 'Check removable entries that are not classified as Windows system packages. This is a selection rule, not a guarantee that you do not need these apps.',
    'Clear Selection': 'Uncheck all app rows. This does not undo any completed uninstall.',
    'Uninstall Selected': 'Ask for confirmation, then remove selected removable packages in the available account scope. Reinstalling may need Microsoft Store or an installer and may not recover app data.',
    '__combo__': 'Filter packages by system classification or catalog match. This changes only the displayed rows.',
}
HEADERS = {
    'Name': 'Friendly name resolved from Windows package identity or installation location.',
    'Publisher': 'Publisher distinguished name reported by the package; hover a cell for the full value.',
    'Version': 'Version currently reported installed by Windows; a deployment failure may have attempted another version.',
    'Size': 'Measured package-folder bytes, displayed in scaled units. Shared files and inaccessible folders can make this differ from recoverable disk space.',
    'User-Removable': 'Whether Windows permits user removal of this package; a removable package can still provide a feature you need.',
    'Source': 'Installation source evidence reported by the package scan, with the reason available on the cell.',
    'Package Family Name': 'Stable Windows package family identifier, used for Store links and package matching across versions.',
    'Architecture': 'Processor architecture reported by the package, such as x86, x64 or ARM64.',
    'Provisioned': 'Whether Windows stages this package for new accounts. Checking needs administrator rights; removing provisioning does not uninstall existing accounts.',
    'State': 'Failure status derived by comparing deployment events with later successes and installed versions; an old failure can already be resolved.',
    'App': 'Package or friendly app name associated with a Windows deployment failure event.',
    'Error': 'Windows deployment error code from event 404; hover for the logged message.',
    'What it means': 'Curated explanation of the deployment error, with Windows event details when available.',
    'Failures': 'Number of matching failed deployment events retained in the circular Windows log.',
    'Last failure': 'Most recent retained failure timestamp, shown in local time.',
    'Tried version': 'Package version Windows attempted to install, from the deployment event.',
    'Installed version': 'Version currently found in Windows package inventory; it may differ from the failed update.',
    'Started by': 'Initiator recorded by the deployment log when available; this is Windows event evidence.',
}

CONTROLS.update({
    'Uninstall': CONTROLS['Uninstall Selected'],
    'Copy package name': 'Copy the selected Windows package name to the clipboard. This changes no packages.',
    'Copy uninstall command': 'Copy an all-users PowerShell removal command without running it. Executing it later requires administrator rights and removes the package; it has no undo here.',
    'Copy Package Family Name': 'Copy the stable package family identifier to the clipboard without changing registration.',
    'Copy install location': 'Copy the reported installation path without opening or changing files.',
    'Open install folder': 'Open the package folder in File Explorer without deleting files. Windows may refuse protected folders.',
    'Open in Microsoft Store': 'Open the app page in Microsoft Store. This does not itself install, purchase or update anything.',
    '?? Stop provisioning for new users': 'Ask before removing the package from the image used for new accounts. Needs administrator rights; existing accounts keep their installed copy and this pane has no undo.',
    'Copy deprovision command': 'Copy the PowerShell command that removes new-account provisioning without running it. Executing it later requires administrator rights and has no undo here.',
})
