'Thermal help with motherboard and GPU control kept distinct.'

CONTROLS = {'Identify (20 s at 100%)': 'Run this fan group at full speed for 20 seconds so you can identify '
                            'it by sound; this starts immediately and may be loud. The previous '
                            'control is restored afterwards; motherboard headers require '
                            'administrator rights, GPU control does not.',
 'Drive this header with the curve': 'Choose whether Apply will drive this motherboard fan from '
                                     'the app rather than BIOS automatic control. First enabling '
                                     'asks for confirmation; the curve keeps running with this tab '
                                     'closed and the header returns to BIOS control on normal app '
                                     'exit.',
 'Zero RPM: let the fan stop when the GPU is cool': 'Immediately enable or disable stopping '
                                                    'supported GPU fans when cool, without another '
                                                    'confirmation. This needs no elevation and is '
                                                    'separate from motherboard safety floors; '
                                                    'toggle it again to reverse it, or use the GPU '
                                                    'restore action for the captured state.',
 'Apply': 'Write the edited settings to the selected fan after the initial enable confirmation. '
          'Motherboard control needs administrator rights and enforces 20% minimum duty, 70% for '
          'pumps, and 100% at critical temperature or sensor loss. GPU curves use driver limits, '
          'are read back, and continue after the app closes.',
 'Default curve': 'Load default motherboard curve points, or reload the currently reported GPU '
                  'curve, into the editor. No settings are written until Apply.',
 'Hand back to BIOS': "Disable this app's curve for the selected motherboard header and restore "
                      'its captured BIOS control immediately. This needs administrator rights and '
                      'does not change the other headers.',
 'Back to factory': 'Restore the GPU state captured before the first write; if it was at factory, '
                    'AMD factory reset restores all tuning. This acts immediately and needs no '
                    'administrator rights.',
 'All': 'Show all entries in this list, including unavailable readings; this filter does not '
        'change cooling.',
 'Connected': 'Show fan headers with a reported running fan. Zero RPM does not prove that nothing '
              'is physically connected; this only filters the list.',
 'On a curve': 'Show fans currently controlled by a saved app curve; filtering does not enable or '
               'disable a curve.',
 'BIOS / driver': 'Show fans under BIOS or driver automatic control; this filter does not hand '
                  'control back.',
 'Pumps': 'Show headers identified as pumps by their names. App curves enforce a 70% floor for '
          'pumps; filtering changes no speeds.',
 'GPU': 'Show GPU fan entries only; filtering does not write any GPU settings.',
 'Temperatures': 'Show temperature readings in degrees Celsius. This filters the view without '
                 'changing hardware.',
 'Fans': 'Show reported rotational speeds in revolutions per minute (RPM). This filter changes no '
         'speeds.',
 'Duty': 'Show fan-control duty in percent of the control range; it is not a measurement of actual '
         'fan rotation. This filter changes no speeds.',
 'Hot': "Show sensors classified as hot by the app's display rules. This is a view filter, not a "
        'hardware alarm setting.',
 'Problems': 'Show readings the app flags as missing or problematic. This filter changes no '
             'hardware settings.',
 'Group by chip': 'Group sensor rows by the reporting hardware. This changes presentation only.',
 'Reset min/max': "Clear this panel's collected minimum, maximum and average statistics and start "
                  'collecting again. Hardware readings and cooling settings are unchanged; cleared '
                  'statistics have no undo.',
 'Sensors': 'Read temperature, fan rotation and duty reported by hardware providers. Nothing in '
            "this pane changes cooling; resetting statistics only clears this panel's samples and "
            'export writes a file. Motherboard readings require administrator rights; GPU readings '
            'do not.',
 'Fan curves': 'Edit temperature-to-speed rules and apply them to a selected fan. Motherboard '
               'control requires administrator rights, enforces safety floors and returns to BIOS '
               'on normal exit; a crash can leave the last speed set until recovery. GPU curves '
               "use the card's driver limits, survive app exit, and have a captured-state restore "
               'action.',
 '×': 'Dismiss this message; completed operations and current settings remain unchanged.',
 'Export CSV…': 'Choose where to save the visible sensor readings and statistics as CSV. This '
                'writes a report without changing cooling settings.'}

HEADERS = {'Sensor': 'Sensor name reported by LibreHardwareMonitor or the Windows GPU performance interface; '
           'names are not independent calibration.',
 'Hardware': 'Hardware group reported by the sensor provider.',
 'Now': 'Latest reported value: degrees Celsius for temperature, RPM for rotation, or percent for '
        'control duty. Missing readings are not zero.',
 'Min': 'Lowest observed reading since this panel began collecting or Reset min/max was used; '
        'units match Now.',
 'Max': 'Highest observed reading since this panel began collecting or Reset min/max was used; '
        'units match Now.',
 'Avg': 'Arithmetic average of available readings collected by this panel since the last reset; '
        'units match Now.',
 'Limits': 'Warning or critical temperature limits reported for the hardware, in degrees Celsius. '
           'These are thresholds, not live readings or curve sources.'}

FIELDS = {'_source': 'Temperature sensor used to drive the selected motherboard fan curve. Apply saves it; '
            'if the sensor stops answering, the controller requests 100% duty.',
 '_critical': 'Critical temperature in degrees Celsius for this motherboard curve. At or above it '
              'the app requests 100% duty; Apply saves the edited setting.',
 '_hyst': 'Hysteresis in degrees Celsius: temperature must fall enough before the app lowers fan '
          'duty, reducing speed oscillation. Apply saves it; downward changes are limited to 3 '
          'percentage points per tick.',
 '_fan_sort_box': 'Sort the fan list using the selected reported field. This does not change fan '
                  'speeds.',
 '_search': 'Filter sensor names and hardware by text. This does not change sensor polling or '
            'cooling settings.',
 '_editor': 'Drag points to edit temperature (degrees Celsius) versus duty (percent). Double-click '
            'adds and right-click removes points on motherboard curves; GPU curves have a fixed '
            'point count. Apply writes the edit; speeds may not decrease as temperature rises.',
 '_status': 'Current sensor-provider state. Motherboard readings require elevation and the '
            'supported hardware driver; unavailable readings do not prove a device is absent.'}

PANES = {'Sensors': 'Read temperature, fan rotation and duty reported by hardware providers. Nothing in '
            "this pane changes cooling; resetting statistics only clears this panel's samples and "
            'export writes a file. Motherboard readings require administrator rights; GPU readings '
            'do not.',
 'Fan curves': 'Edit temperature-to-speed rules and apply them to a selected fan. Motherboard '
               'control requires administrator rights, enforces safety floors and returns to BIOS '
               'on normal exit; a crash can leave the last speed set until recovery. GPU curves '
               "use the card's driver limits, survive app exit, and have a captured-state restore "
               'action.'}
