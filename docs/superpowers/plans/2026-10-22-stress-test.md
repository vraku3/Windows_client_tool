# Stress Test implementation plan

User specification: CPU and RAM stability, shared ThermalService sensors, automatic safety cutoff.
Execution is authorized in the request. Work in this checkout; preserve the concurrent process job.

- [ ] Test and implement Qt-free safety, CPU known-answer load and bounded-memory RAM patterns.
- [ ] Test and implement sensor adapter, independent watchdog, session orchestration and escaped reports.
- [ ] Add polling Qt UI, shutdown handling, registration and frozen imports.
- [ ] Run short targeted tests, inspect safety/lifecycle code and clean test temporary files.

No new dependencies, GPU load, machine setting writes, builds, commits or pushes. Tests use tiny
allocations and fake sensors. Missing readings remain None. Shared stop Event reaches every load
thread; the watchdog runs independently of sensor reads and Qt. Reports disclose actual RAM coverage.
