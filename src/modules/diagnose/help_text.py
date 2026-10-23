"""Descriptions of the diagnostic sources and their shared log columns."""
INTRO = (
    "Diagnose brings together Windows events, servicing logs, update reports and crash evidence. "
    "Searching and reading do not repair or clear anything; exports write only the file you choose. "
    "Some logs need administrator rights, including Security events and Crash Dumps."
)
READERS = {
    "Event Viewer": "Windows records events about apps, services, hardware and sign-ins. This pane reads and filters records without clearing logs or changing settings; Security events require administrator rights, and a missing channel or limited history is not proof nothing happened.",
    "CBS Log": "Component-Based Servicing (CBS) records Windows component installation and repair activity. This pane reads CBS.log, or attempts to extract the latest archived CbsPersist CAB into a temporary folder when the text log is absent; it does not repair Windows. No elevation is required by this pane, but restricted log files may need administrator rights.",
    "DISM Log": "Deployment Image Servicing and Management (DISM) records Windows image servicing. Reading does not repair anything; if dism.log is absent, this pane shows installed hotfixes instead. The optional CheckHealth button needs administrator rights and reads an existing health flag rather than scanning or repairing.",
    "Windows Update": "This reads Windows Update's ReportingEvents.log to show reported update outcomes and error codes. Nothing here installs, removes or retries an update; no administrator rights are normally needed, but a missing or refused log is not an empty update history.",
    "Reliability": "Reliability records describe crashes, hangs and other Windows-reported events, alongside the stability index when available. Reading changes no settings and needs no administrator rights under normal permissions; an event near an index drop is a correlation, not proof of its cause.",
    "Crash Dumps": "Crash dumps save memory evidence after system faults; this pane lists kernel dumps, live kernel reports and BugCheck events. It does not delete dumps, repair drivers or identify a culprit with certainty; reading this pane requires administrator rights and may not include every application crash.",
}
CONTROLS = {
    "Everything": "Choose a category of already loaded events, such as service crashes or unexpected shutdowns. This filters the view only; it does not repair faults, clear records or change Windows logging.",
    "Copy row": "Copy the selected log entry to the clipboard, replacing previous clipboard contents. No log or Windows setting changes; copied evidence may include computer names or paths.",
    "Export CSV...": "Choose a CSV file and save the currently shown rows. This writes a report without changing Windows logs; filters can exclude records from the report.",
}
HEADERS = {
    "Time": "Timestamp supplied or parsed by this source's reader. Some fallback records use an inferred timestamp; this is not necessarily the time the underlying fault began.",
    "Source": "Windows event provider, log component or fallback source assigned by the reader. Source identifies where a record came from, not a confirmed cause.",
    "Level": "Severity reported by Windows or assigned by the source parser: Info, Warning, Error or Critical. A severe record alone does not diagnose the root cause.",
    "Message": "Message extracted or formatted from this source's record. The table may shorten it; select the row to read the detail panel.",
    "ID": "Event identifier reported by Windows. Interpret it with its provider: unrelated providers can use the same number for different events.",
    "Count": "Number of loaded events grouped under this provider and event ID. Filters and the read limit affect the count; it is not a lifetime total.",
    "Summary": "Short summary returned by a diagnostic source's search provider. Double-click a result to inspect its details; searching does not change the underlying logs.",
}
