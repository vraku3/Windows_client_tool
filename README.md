# Windows Client Tool

A comprehensive Windows 11 optimization and system diagnostics utility with a modern Qt6-based graphical interface.

## Features

### Disk Space & Logs
- **TreeSize**: A full disk-space analyser. Reads the NTFS `$MFT` directly
  when run elevated on an NTFS volume — a whole `C:` in about 20 seconds —
  and falls back to a directory walk everywhere else. Treemap, pie and bar
  charts; per-extension, per-file-group, per-owner and per-age breakdowns;
  saved scans, snapshots and comparison; duplicate finder; file search;
  scheduled scans; live watching of a scanned folder; exports to CSV, Excel,
  PDF, HTML, XML, JSON, SQLite and text. Besides local drives it can scan
  SSH/SFTP, WebDAV and Outlook mailboxes; S3, Azure Blob, SharePoint and
  Google Drive additionally need their vendor SDK installed (`boto3`,
  `azure-storage-blob`, `google-api-python-client`) and report themselves as
  unavailable until it is.
- **Log Viewer**: A CMTrace-style viewer for ConfigMgr logs and plain text
  alike. Severity colouring, live tailing that survives a log rollover, a
  case-insensitive filter, find with wrap-around, and it opens a
  several-hundred-megabyte log at its tail rather than trying to hold it all.

### System Analysis & Optimization
- **Dashboard**: Live monitoring of CPU, memory, disk, network usage
- **Event Viewer**: Parse Windows Event logs (System, Application, Security)
- **CBS/DISM Logs**: Analyze component store and system repair logs
- **Windows Update**: Check and install pending updates
- **Process Explorer**: View running processes with resource usage
- **Performance Tuner**: Identify and fix performance bottlenecks
- **Cleanup Scanner**: Find and remove unnecessary files

### System Information
- **Hardware Inventory**: Collect detailed hardware specifications
- **Driver Manager**: View and manage installed drivers
- **System Management**: Scheduled tasks, services, and optional Windows
  features — one module with a tab each
- **Network Diagnostics**: Analyze network adapter status and issues
- **Security Dashboard**: Review security settings and vulnerabilities
- **Certificates**: View and manage system certificates
- **Startup & Boot**: Startup programs and services, boot timing, and power
  and boot options — one module with a tab each

### Network
- **Network Diagnostics**: Connectivity checks, Wi-Fi analysis, the HOSTS
  file, DNS/proxy settings, shared folders/sessions/mapped drives, and
  RDP/WinRS/ping-sweep/Wake-on-LAN — one module with a tab each

### Tools
- **Registry Explorer**: Browse and edit registry keys (backup-first)
- **Environment Variables**: View and edit system variables

### Utilities
- **Tweaks**: Apply system optimization recommendations
- **Backup Service**: Config file backup and restore
- **System Report**: Generate comprehensive system health reports

## Requirements

- Windows 10/11 (64-bit)
- Python 3.12+
- PyQt6
- psutil

## Installation

1. Clone or download this repository
2. Create a virtual environment:
   `ash
   python -m venv venv
   venv\Scripts\activate
   `
3. Install dependencies:
   `ash
   pip install -r requirements.txt
   `
4. Run the application:
   `ash
   python src/main.py
   `

## Building an Executable

`ash
pyinstaller --clean WinClientTool.spec
`

The resulting executable will be located in uild/WinClientTool/

## Configuration

The application stores settings in %APPDATA%\WindowsTweaker\config.json:
- Theme (dark/light)
- Log level (DEBUG/INFO/WARNING/ERROR)
- Window size and position
- Start minimized preferences

## License

MIT License - See [LICENSE](LICENSE) for details

## Author

Auto-generated tool for Windows 11 system optimization

## Contributing

This is a system optimization tool. Contributions should focus on:
- Bug fixes
- Performance improvements
- Security hardening
- Documentation enhancements

## Roadmap

- [x] Core modules implementation
- [x] TreeSize disk-space analyser (MFT and walk engines)
- [x] CMTrace-style log viewer
- [ ] Full AI-powered recommendations (Ollama integration)
- [ ] Enhanced restore manager
- [ ] Additional diagnostic modules
- [ ] Plugin system for custom analysis tools

## Support

For issues or questions, create an issue in the repository.
