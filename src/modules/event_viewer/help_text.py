"""Conservative, provider-specific explanations of common Windows events."""
EVENTS = {
    ("microsoft-windows-kernel-power", 41): "An unclean shutdown was detected at the next startup. This records the aftermath, not the cause: a power loss, forced reset or system crash can all produce it.",
    ("eventlog", 6008): "Windows reports that the previous shutdown was unexpected. Read nearby power, disk and crash records; this event alone does not identify faulty hardware.",
    ("user32", 1074): "A process requested a shutdown or restart. The event's own message names the initiating process, account and reason; it does not necessarily mean the restart completed.",
    ("service control manager", 7031): "A service terminated unexpectedly and Windows recorded its configured recovery response. Read the service name and recovery action in the message; this does not explain why it crashed.",
    ("service control manager", 7034): "A service terminated unexpectedly. The message identifies the service and failure count; investigate its own logs before disabling it.",
    ("service control manager", 7000): "A service failed to start. The message includes the service name and reported error; possible causes depend on that error.",
    ("service control manager", 7001): "A service could not start because a dependency failed. Inspect the named dependency rather than assuming the selected service is broken.",
    ("application error", 1000): "Windows recorded an application crash. The named faulting module is where the failure surfaced, not necessarily the component that caused it.",
    ("application hang", 1002): "Windows recorded an application that stopped responding. This is not proof the app is malicious or that its executable is damaged.",
    ("display", 4101): "A display driver stopped responding and Windows recovered it. The event does not by itself distinguish a driver bug from hardware, power or workload problems.",
}


def event_help(provider, event_id):
    """Never assign a meaning from the numeric ID without its provider."""
    return EVENTS.get((str(provider).casefold(), event_id), "")
