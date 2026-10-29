# Game Mode tuning catalog for this PC

Tailored to: Ryzen 9 9950X3D (dual CCD, 3D V-Cache on one CCD), Radeon RX 7900 XTX (intentionally held on the 2022 driver), Windows 11 26H2 build 26300.9711, 2.5 GbE Ethernet, Bitdefender + Defender, Smart App Control on. VS Code and Codex stay protected during the current test cycle.

Rules: Game Mode changes are journaled and reversible. Anything marked MEASURE is a hypothesis to A/B test, not a default. Anything marked NO is a safety boundary: the app should not do it automatically. "Close" means close for the session and reopen on OFF where possible.

## A. Fix what the first log exposed (1-20)

1. Restore force-close by default (implemented in the settings migration).
2. Treat "still running (force-close disabled)" as WARN, not BAD, when the user disabled force-close.
3. When force-close is on, close Widgets, Snipping Tool, OneDrive, OneDrive co-authoring and Widgets service instead of leaving them.
4. Give UWP apps a UWP-aware close path (AUMID + ApplicationFrameHost), then a documented terminate fallback.
5. Give OneDrive its `/shutdown` path first, then wait longer than 8 s if it is actively syncing.
6. Detect an active OneDrive sync and ask before closing, instead of silently killing a transfer.
7. Detect an active Snipping Tool recording and never close it.
8. Detect an active Teams/Discord call and never close the call owner.
9. Detect unsaved documents in Office/editors and skip force-close unless the user confirms.
10. Re-read PID + create time before every terminate; never kill a recycled PID.
11. Report per-app handles freed, not only the global total.
12. Add a "why it survived" reason to every unmatched close.
13. Retry one failed polite close with a longer grace before forcing.
14. Keep the process tree of a running game out of every close list, including helpers.
15. Keep anti-cheat, controller, audio and VPN services out of every service list.
16. Add a session handle target and warn when the reduction is below it.
17. Do not restart Explorer if a file copy/move dialog is open.
18. Do not restart Explorer if a fullscreen game is foreground.
19. Record the close method used (polite, quit-args, UWP, terminate) in history.
20. Add a one-click "what survived and why" report to the Session tab.

## B. Handle and process reduction (21-55)

21. Close Widgets app and `WidgetService.exe` together, not just one.
22. Stop `MicrosoftWindows.Client.WebExperience` background tasks.
23. Close Snipping Tool when idle; keep it when the capture overlay is open.
24. Close Phone Link / `PhoneExperienceHost` when idle.
25. Stop `CrossDeviceService` and `DevicesFlow` when not pairing a phone.
26. Stop `YourPhone.exe` if present.
27. Stop `Microsoft.Photos` background helpers.
28. Stop `People`, `Mail`, `Calendar` UWP processes.
29. Stop `Teams.exe` background when no call is active (unless Streaming preset).
30. Stop `ms-teamsupdate.exe` while gaming.
31. Stop `Copilot`/`Recall` background processes if installed.
32. Stop `SearchHost` only as OPT (it restarts; cost is CPU not handles).
33. Stop `SearchProtocolHost` and `SearchFilterHost` if a scan is running.
34. Stop `TextInputHost` when no text field is active (MEASURE).
35. Stop `ShellExperienceHost` only as OPT; Windows restarts it.
36. Stop `StartMenuExperienceHost` only if the Start menu is not in use (MEASURE).
37. Stop `RuntimeBroker` instances owned by closed UWP apps.
38. Stop `dllhost` COM surrogates left by closed apps (MEASURE).
39. Stop `WmiPrvSE` idle providers (MEASURE; Windows respawns).
40. Stop `GameBar.exe`, `GameBarFTServer` if Game Bar is not used.
41. Stop `XboxGameCallableUI` when not using Xbox overlay.
42. Stop `XboxPcApp` when not launching via Xbox app.
43. Keep `GamingServices` if a Store/Xbox game needs it.
44. Stop `PhoneExperienceHost` before `CrossDeviceService`.
45. Stop `Adobe` background helpers (Creative Cloud, Acrobat update).
46. Stop `Dropbox`, `GoogleDriveFS`, `iCloudDrive` when not needed.
47. Stop `Steam`/Epic/Battle.net launchers only when idle (already catalogued).
48. Do not close Steam if a game is downloading unless the user confirms.
49. Do not close Battle.net while a Blizzard game is running.
50. Stop `Razer`/`G HUB`/`SteelSeries` services only if the user opts in.
51. Stop `Nahimic`/audio enhancement helpers only if the user opts in.
52. Stop `AdobeIPCBroker`, `Adobe Desktop Service` when idle.
53. Stop `GoogleCrashHandler`/`GoogleUpdate` background helpers.
54. Stop `OfficeClickToRun` when no Office app is open.
55. Empty the working set of large background apps instead of closing them when the app must stay.

## C. CPU, CCD and scheduler (56-80)

56. Keep Balanced on the 9950X3D (already correct; do not force Ultimate).
57. Set the game process to Above Normal first; test High only if 1% lows improve.
58. Pin the game to the V-Cache CCD only when the game benefits; make it per-game.
59. Leave Windows/AMD scheduler alone by default; affinity overrides can hurt.
60. Disable power throttling for the game process (`SetProcessInformation`).
61. Disable EcoQoS for the game process.
62. Raise the game's I/O priority to High.
63. Set GPU priority for the game exe to 8 in UserGpuPreferences (MEASURE).
64. Set CPU priority for the game exe to 6 in UserGpuPreferences (MEASURE).
65. Raise the game main thread priority, not every thread.
66. Test `Win32PrioritySeparation` 26 vs 38 with 1% lows (MEASURE).
67. Test `SystemResponsiveness` 10 vs 20 for MMCSS (MEASURE).
68. Test `NetworkThrottlingIndex` 0xFFFFFFFF only with network measurements.
69. Do not disable core parking on the dual-CCD X3D by default; test.
70. Do not disable CPPC/preferred cores; AMD's plan expects them.
71. Test `CPPC Preferred Cores` on/off in BIOS only with a stability run.
72. Test PBO/Curve Optimizer undervolt in BIOS; validate with OCCT/y-cruncher.
73. Test EXPO profile stability with memtest; do not change during a session.
74. Confirm Resizable BAR is enabled; do not toggle at runtime.
75. Confirm Above 4G decoding; BIOS only.
76. Set the Windows power slider to Best Performance while plugged in.
77. Keep the AMD 3D V-Cache service running (already).
78. Avoid `High performance`/`Ultimate` on this CPU unless benchmarks prove a win.
79. Do not disable SMT; games and background tasks can benefit.
80. Do not disable HVCI/VBS/Core Isolation for a few percent; stability and security win.

## D. GPU, display and driver (81-105)

81. Keep the 2022 AMD display driver intentionally; block WU driver offers (already).
82. Keep `ExcludeWUDriversInQualityUpdate`/driver policy so WU cannot replace it.
83. Do not run DDU or a driver update inside Game Mode.
84. Set Windows graphics preference for the game to High performance.
85. Set AMD Adrenalin per-game profile: no Chill, no Boost unless measured.
86. Disable Radeon Anti-Lag+ if the game uses anti-cheat (ban risk).
87. Test Anti-Lag (classic) separately from Anti-Lag+.
88. Disable Radeon Image Sharpening for competitive titles if it costs frames.
89. Set texture filtering quality per game, not globally.
90. Disable AMD overlay/metrics while benchmarking clean runs.
91. Disable Instant Replay/Record unless streaming.
92. Disable AMD Link.
93. Keep FreeSync on if the monitor supports it; test VRR off for stutter.
94. Set native resolution and highest stable refresh in Monitor Control.
95. Disable HDR if the game is SDR and HDR adds latency (per game).
96. Disable GPU scaling, use display scaling, if the monitor handles it better.
97. Disable Virtual Super Resolution unless the game needs it.
98. Test Hardware-accelerated GPU scheduling on/off (reboot required).
99. Test MPO disable only if flicker/stutter is observed; keep a rollback.
100. Check PCIe link width/gen under load (GPU-Z); reseat if x8/x4.
101. Check GPU hotspot vs edge delta; repaste only if delta is high.
102. Set a sane fan curve; keep hotspot under the card's limit.
103. Check PSU headroom and separate PCIe cables for the 7900 XTX.
104. Disable GPU audio endpoints you do not use (monitor speakers).
105. Do not overclock the GPU inside Game Mode; use a validated Adrenalin profile outside.

## E. Memory, standby and working sets (106-125)

106. Check RAM is running EXPO/XMP at the rated speed and dual-channel.
107. Check committed memory and pagefile headroom before ON.
108. Keep the pagefile system-managed on the fastest NVMe; do not disable it.
109. Do not use RAM cleaners; they increase paging and stutter.
110. Empty working sets only as an experiment; measure 1% lows.
111. Clear the standby list only as an experiment; Windows caches for a reason.
112. Close OneDrive/Teams/Chrome helpers that hold large working sets.
113. Trim the working set of browsers left open, without closing them.
114. Keep VS Code/Codex protected as requested; accept their memory cost.
115. Stop memory-heavy UWP apps (Photos, Widgets) rather than trimming them.
116. Monitor nonpaged pool growth across a long session.
117. Monitor handle growth per process; a leak is a bug, not a tuning target.
118. Check Memory Compression CPU cost on this CPU; usually leave on.
119. Avoid `LargeSystemCache` on a desktop with a pagefile; test only.
120. Check for a runaway `svchost` or `RuntimeBroker` before ON.
121. Close browser tabs/processes that are not needed for the game.
122. Keep Discord/browser only if the user marks them protected.
123. Set a session memory target in the Session tab; warn when exceeded.
124. Record memory before/after in history, not only handles.
125. Test a 32 GB vs 64 GB working set scenario only if the machine has mixed modules.

## F. Storage and I/O (126-145)

126. Keep the game on the fastest NVMe with at least 15-20% free.
127. Confirm TRIM is enabled; never defrag an SSD.
128. Keep the scheduled defrag task paused during sessions (already).
129. Disable NTFS last-access updates (MEASURE; system-wide).
130. Do not enable NTFS compression on game folders.
131. Exclude the game folder from Windows Search indexing.
132. Stop `SysMain` only if NVMe + measured; it can help or hurt.
133. Do not disable Prefetch/ReadyBoot without measurements.
134. Keep Storage Sense from running during a session.
135. Pause OneDrive/Google Drive/Dropbox before a session.
136. Pause Steam/Epic/Battle.net downloads before a session.
137. Set the game process I/O priority to High (see 62).
138. Check NVMe temperature and PCIe ASPM; throttle can cause stutter.
139. Check `storahci`/`stornvme` driver health in Device Manager.
140. Check disk queue length during the game; a queue is a bottleneck.
141. Keep 20% free on the Windows and game drives.
142. Move the pagefile to the fastest NVMe if it is on a slower disk.
143. Check for a failing disk with SMART before blaming the game.
144. Do not run chkdsk/sfc/DISM during a session.
145. Clear %TEMP% after the session, not during it.

## G. Network and latency (146-165)

146. Use the 2.5 GbE wired adapter; disable Wi-Fi while gaming if unused.
147. Confirm link speed is 2.5 Gb/s, full duplex, no errors.
148. Disable Energy Efficient Ethernet/Green Ethernet on the NIC (MEASURE).
149. Test interrupt moderation off/on; off can lower latency, raise CPU.
150. Enable RSS/RSC only if the NIC and driver support it well.
151. Test flow control off/on; do not assume.
152. Test large-send offload off/on for this NIC.
153. Do not disable IPv6 globally; it can break some launchers.
154. Set a low-latency DNS resolver; measure resolution, not just ping.
155. Flush DNS before a session if servers changed.
156. Keep Delivery Optimization/BITS stopped during the session (already).
157. Pause cloud sync and launcher downloads (see F).
158. Test `NetworkThrottlingIndex` only with a latency baseline.
159. Test `SystemResponsiveness` with MMCSS audio/game workloads.
160. Use router SQM/cake if bufferbloat is present; app cannot fix the router.
161. Check packet loss/jitter with ping/pathping before changing Windows.
162. Set the game/server region deliberately; do not trust auto.
163. Disable Network Discovery/UPnP only if not needed for the game.
164. Do not disable LLMNR/NetBIOS blindly; some LAN games need them.
165. Record latency/jitter/loss before and after each network tweak.

## H. Power, thermal and clocks (166-185)

166. Keep Balanced for the 9950X3D; verify effective clocks on both CCDs.
167. Set the Windows power mode to Best Performance while plugged in.
168. Keep the display awake during a controller-only session (already).
169. Disable USB selective suspend for the game controller (MEASURE).
170. Test PCIe ASPM off for the GPU; watch idle power.
171. Set minimum processor state 5% vs 100% and measure 1% lows.
172. Set maximum processor state 100%.
173. Test the cooling policy Active vs Passive.
174. Test processor performance boost mode Enabled/Aggressive.
175. Keep CPPC/preferred cores enabled unless a stability run says otherwise.
176. Do not disable core parking on the X3D by default.
177. Monitor package power, Tctl/Tdie, and per-CCD clocks.
178. Monitor GPU hotspot, VRAM temperature and fan RPM.
179. Validate an undervolt with OCCT/y-cruncher/30-minute game runs.
180. Check PSU transient headroom for the 7900 XTX.
181. Check case airflow and dust before a long session.
182. Check thermal paste/pump speed only if temperatures are abnormal.
183. Keep the GPU fan curve above the zero-RPM stop under load if needed.
184. Record throttling reasons during the session, not just temperatures.
185. Do not apply BIOS/voltage changes automatically from Game Mode.

## I. Services, tasks and telemetry (186-210)

186. Keep the current "stop only, never change start type" rule.
187. Add `SysMain` as an optional MEASURE service, not a default.
188. Add `DiagTrack` (telemetry) to the default stop list.
189. Add `dmwappushservice` to the default stop list.
190. Add `WerSvc` (Error Reporting) to the default stop list.
191. Add `PcaSvc` (Program Compatibility Assistant) to the default stop list.
192. Add `MapsBroker` to the default stop list.
193. Add `RetailDemo` to the default stop list.
194. Add `RemoteRegistry` (already usually disabled) to the guard list.
195. Add `WinRM` to the guard list; never needed for a game.
196. Add `SSDPSRV`/`upnphost` only when the game does not need discovery.
197. Add `WMPNetworkSvc` to the default stop list.
198. Add `PhoneSvc` to the default stop list.
199. Add `WalletService` to the default stop list.
200. Add `Print Spooler` only if no printer is installed.
201. Add `Fax` to the guard list.
202. Keep `CryptSvc`, `RpcSs`, `DcomLaunch`, `Power`, `PlugPlay`, `BFE`, `mpssvc` untouched.
203. Keep Bitdefender/Defender/security services untouched (NO).
204. Add `GameInputSvc`/controller services to the never-stop list.
205. Add `Audiosrv`/`AudioEndpointBuilder` to the never-stop list.
206. Add VPN services to the never-stop list when a game needs the VPN.
207. Add Xbox `GamingServices` to the never-stop list for Store games.
208. Add anti-cheat services (EAC/BattlEye/Vanguard/Faceit) to the never-stop list.
209. Keep the task pause list, but skip tasks owned by TrustedInstaller/Window Manager.
210. Log every skipped service/task with its owner or dependency reason.

## J. UWP, shell, launchers and anti-cheat (211-230)

211. Close Widgets app + service together.
212. Close Snipping Tool only when no capture overlay is visible.
213. Close Phone Link when not in a call.
214. Close Teams when not in a call; keep it in Streaming preset.
215. Disable Widgets via policy for the whole OS (opt-in, persistent).
216. Disable Search highlights and Start recommendations (persistent, opt-in).
217. Disable Taskbar chat/Meet Now/People buttons (persistent, opt-in).
218. Disable Game Bar background capture and Game DVR (persistent, opt-in).
219. Disable Store auto-updates during the session.
220. Keep game launchers (Steam/Epic/Battle.net/Xbox) open when a game is running.
221. Never close anti-cheat processes; detect by signed publisher and known names.
222. Never close controller/input/audio stacks.
223. Keep OBS/Streamlabs/Discord in Streaming preset.
224. Keep VS Code/Codex during the current test cycle (user request).
225. Detect a game via the user-bound exe only; never by title heuristics.
226. If a game crashes, keep ON for a configurable grace period, then auto-OFF.
227. Do not kill a launcher while it is updating a game unless confirmed.
228. Handle Steam Big Picture, Xbox Game Pass and emulators as user-bound games.
229. Detect a VR runtime (SteamVR/Oculus) and do not stop its services.
230. Keep the game's overlay/recording helper when the user protects it.

## K. Measurement and validation (231-245)

231. Benchmark 1% low FPS, not only average FPS.
232. Measure frametime variance and stutter count.
233. Measure effective CPU clocks per CCD, not only package clock.
234. Measure CPU/GPU utilization and power.
235. Measure GPU hotspot, VRAM temperature and fan RPM.
236. Measure RAM/commit and handles per process.
237. Measure DPC/ISR latency and interrupt load.
238. Measure disk queue and NVMe temperature.
239. Measure network latency/jitter/loss before/after.
240. Capture an ETW/WPR trace for any stutter that survives tuning.
241. Use PresentMon/CapFrameX for frame data; do not trust in-game counters alone.
242. Use `powercfg /energy` and `/sleepstudy` for sleep/power issues.
243. A/B one change at a time; keep the previous profile.
244. Record every result in Game Mode history and export CSV/JSON.
245. Re-run the same game scene at the same settings for comparisons.

## L. Safety, UX and recovery (246-260)

246. Show the force-close state prominently; never let it silently default off.
247. Confirm force-close per app when it can lose work.
248. Show "protected by user", "protected publisher", "game", "anti-cheat", "no identity".
249. Show elapsed time, auto-OFF countdown and pause countdown.
250. Show the next scheduled window and armed auto-ON state.
251. Show health drift and a one-click repair.
252. Show last restore issues with retry/copy/export.
253. Journal before every change; never write outside the journal.
254. Atomic journal writes with a retry for transient locks.
255. Recover after crash/reboot; never guess a BEFORE value.
256. Offer a restore point before ON (opt-in, currently off).
257. Offer one-click undo for a single change while ON.
258. Offer "Resume last" with the normal preview, never bypass it.
259. Notify ON/OFF/auto-OFF/restore failure once, with details.
260. Keep tray and Ctrl+Alt+G commands on the same state path as the UI.

## M. Persistent OS hygiene outside Game Mode (261-270)

261. Keep Windows Update and driver policy so the 2022 AMD driver is not replaced.
262. Keep Smart App Control on; sign every build with a trusted cert.
263. Keep Bitdefender and Defender real-time protection on.
264. Keep Core Isolation/HVCI/VBS on unless a measured, accepted tradeoff.
265. Keep Secure Boot, TPM and driver signature enforcement.
266. Keep a recent restore point or image before persistent tuning.
267. Remove stale startup entries and unused services outside Game Mode (reviewed).
268. Keep at least 15-20% free on C: and the game drive.
269. Run SFC/DISM only when there is evidence of corruption, not as routine.
270. Re-test after Windows feature updates; build 26300 changed service/task behavior before.

## Boundaries (NO by default)

- Do not disable Defender, Bitdefender, Smart App Control, HVCI/VBS, Secure Boot, UAC or the firewall.
- Do not disable anti-cheat, controller, audio, VPN or security services.
- Do not kill a running game or its launcher helpers.
- Do not force-close an app with unsaved work, an active call, a recording or a sync without confirmation.
- Do not change service start types, uninstall drivers or apply BIOS/voltage changes from Game Mode.
- Do not enable Anti-Lag+ on anti-cheat titles.
- Do not use RAM cleaners, registry cleaners or "debloat all" scripts as a tuning shortcut.
