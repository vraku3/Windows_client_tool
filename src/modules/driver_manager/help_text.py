"""Driver inventory and action explanations; no Qt dependencies."""
INTRO = ('Drivers let Windows communicate with hardware. This pane reads Windows-reported driver metadata and '
 'can export reports or packages without changing active drivers; protected destinations need '
 'administrator rights. Installs and uninstalls ask first and require elevation; undo is available only '
 'for updates with a usable rollback record, not every driver change.')

CONTROLS = {'All': "Filter the inventory by this tool's driver flags. Flags are investigation hints, not proof a "
        'driver is defective; this does not change drivers.',
 'Select flagged': 'Select displayed drivers with findings. Selection does not uninstall or update '
                   'anything; inspect the flags before using an action.',
 'Hide pseudo-devices': 'Hide software, root-enumerated and other pseudo-device entries using this '
                        "tool's classification. This only changes the view; hidden devices remain "
                        'installed.',
 'Check All for Updates': 'Query supported vendor providers and Windows Update for candidate drivers. '
                          'Checking uses the network; any offered installation asks for confirmation '
                          'and needs administrator rights. An offered update is not a guarantee of '
                          'improvement.',
 'Undo All Updates This Session': 'Ask before rolling back updates this app recorded in the current '
                                  'session using their saved package information. Needs administrator '
                                  'rights; only updates with usable rollback records are covered, and a '
                                  'restart may be needed.',
 'Open Device Manager': "Open Windows Device Manager to inspect hardware or use Windows' driver "
                        'controls. Opening it does not change drivers; actions there may require '
                        'administrator rights.',
 'System Restore Points': 'Read and display available Windows System Restore points. This does not '
                          'create a point or start a restore, and listing points is not a guarantee '
                          'they will recover a driver change.',
 'Snapshots ▾': 'Open inventory snapshot commands. These save or compare metadata; snapshots do not '
                'contain driver binaries and cannot restore a driver.',
 'Save Baseline...': 'Choose a file and save current driver inventory metadata as a comparison '
                     'baseline. This writes a file, not a driver-package backup or restore point.',
 'Diff Against...': 'Choose a saved baseline and compare it with the current inventory. This reads '
                    'metadata and displays differences without installing or removing drivers.',
 'Backup Drivers': 'Choose a folder and confirm export of installed third-party driver packages. This '
                   'writes package files without removing active drivers; protected folders need '
                   'administrator rights, and restoring requires a separate installation step.',
 'Cancel (stops after current file)': 'Request cancellation of the driver backup after the current file '
                                      'finishes. Files already exported remain in the destination; '
                                      'cancellation does not undo completed copies.',
 'Export Inventory': 'Choose a file and save detailed driver inventory data. This writes a report, not '
                     'package files that can reinstall a driver.',
 'Copy device name': "Copy the selected device's Windows-reported name to the clipboard. No driver "
                     'changes.',
 'Details...': 'Open read-only metadata and findings for this device. Driver-store size is calculated '
               'in the background; nothing is installed or removed.',
 'Uninstall driver package…': 'Ask before removing the selected published OEM driver package with '
                              'pnputil. Needs administrator rights and can affect devices sharing it; '
                              'there is no automatic undo, so export the package first.',
 'Export this driver…': 'Choose a destination and export this published driver package. This writes '
                        'files without changing the active driver; protected destinations need '
                        'administrator rights.',
 'Copy Hardware ID': "Copy this device's hardware identifier to the clipboard. This identifies "
                     'compatible hardware, not necessarily one unique physical unit; no driver changes.',
 'Check for Vendor Update...': 'Query the supported vendor provider for a candidate update. Checking '
                               'uses the network; downloading or installing is a separate choice and '
                               'installation requires confirmation and administrator rights.',
 'Check Windows Update for this Device...': "Query Windows Update's driver channel for a candidate "
                                            'matching this device. Checking does not install it; an '
                                            'offered installation asks first and needs administrator '
                                            'rights.',
 'Undo This Update': "Ask before restoring the driver package saved for this app's recorded update. "
                     'Needs administrator rights and a usable rollback record; it cannot undo arbitrary '
                     'changes made outside this app.',
 "Open Cleanup's Superseded Drivers panel": "Navigate to Cleanup's driver-package view. Navigation does "
                                            'not delete anything; review that pane before choosing '
                                            'cleanup.',
 'Why does this matter?': "Explain the selected driver's findings. These are investigation hints, not "
                          'an instruction to uninstall it or proof an update is required.'}

HEADERS = {'Device Name': 'Friendly device name from Win32_PnPSignedDriver or Win32_PnPEntity. Grouped rows '
                'combine matching driver metadata; a name is not a unique device identifier.',
 'Class': 'Device setup class reported by Windows, such as Display or Net. It groups hardware by '
          'function, not manufacturer.',
 'Version': 'Driver version reported by Windows. Different vendors use different version schemes, so '
            'numbers are not comparable across vendors.',
 'Date': 'Driver date reported by Windows, not the date it was installed. Inbox drivers can '
         'deliberately carry old dates; age alone does not establish an update is needed.',
 'Publisher': 'Manufacturer/publisher string reported by Windows driver metadata. This is not an '
              'independent signature verification.',
 'Provider': "This tool's Microsoft/third-party classification from Windows metadata, including "
             'standard inbox provider names. It is not a security verdict.',
 'Signed': 'Signed state reported by Windows driver catalog metadata, not an independent Authenticode '
           'check of every file.',
 'Status': 'Device errors and inventory flags derived from Windows-reported data. No flags does not '
           'prove the device is healthy; read details before acting.',
 'Update Status': "Result of this app's vendor or Windows Update check/install workflow. Not checked is "
                  'different from no update found; availability is not a guarantee of compatibility.',
 'Device ID': 'Windows PnP device-instance identifier distinguishing devices that share the same '
              'friendly name.',
 'Hardware ID': 'Hardware compatibility identifier reported by Windows; multiple physical devices can '
                'share it.',
 'Flags': "This tool's findings about the driver metadata. Read the explanation before deciding whether "
          'to change a driver.'}

TABS = {}

