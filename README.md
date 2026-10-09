# Al-Qemma POS

Al-Qemma POS is a local-first point-of-sale and shop-management application for inventory, sales, purchases, customers, warranties, expenses, reporting, and product compatibility. It is built with Python and Flask, uses SQLite for its database, and provides a web interface that can also be opened in a Windows desktop window through PyWebView. It can be run from source or packaged as a Windows application.

The application is designed to run on a shop's main computer, where its server and business data live. **Tailscale provides private remote access:** when Tailscale is installed and signed in on both the shop computer and the device accessing it, using the same Tailscale account, the interface can be reached from practically anywhere with an internet connection—without exposing the application as a public website. The shop computer must be powered on, connected to Tailscale, and running Al-Qemma.

## Technology stack

- **Python** — application logic and backend
- **Flask** — web server, routes, and request handling
- **SQLite** — local business database
- **HTML, CSS, and JavaScript** — browser-based user interface
- **Jinja2** — server-rendered HTML templates
- **PyWebView** — desktop window for the web interface
- **Tailscale** — private connectivity between the shop computer and authorized devices
- **Playwright + Chromium** — browser-based PDF generation
- **PyInstaller** — packaging the Python application for Windows
- **Inno Setup** — Windows installer creation (for installer builds)

## Remote access with Tailscale

Tailscale is an intentional deployment choice for accessing Al-Qemma remotely while keeping it off the public internet.

1. Install Tailscale on the computer running Al-Qemma and on each device that needs access.
2. Sign in to the **same Tailscale account** on both devices.
3. Keep the shop computer powered on, connected to Tailscale, and running the Al-Qemma server.
4. Open Al-Qemma on the other device using the shop computer's Tailscale IP address or MagicDNS hostname and the configured application port.

This means the owner can access the shop interface while away from the shop, as long as both devices have an internet connection and Tailscale is connected. **Tailscale does not host the application or start its server**; it creates the private network path between devices. The application must be configured to listen on an interface reachable through Tailscale—not only `127.0.0.1`—for remote devices to connect. Keep access limited to trusted devices and protect the account and shop data.

## Capabilities

- Dashboard and owner-level business summaries
- Product catalog, categories, images, stock, pricing, and audit history
- Sales creation, sales history, transaction details, receipts, and returns/adjustments
- Purchase entry and stock receiving
- Customer records, customer purchase history, and customer reports
- Warranty records and warranty lookup
- Product compatibility search
- Expenses, write-offs, and stock adjustments
- Reports, today reports, analytics, exports, and PDF output
- Branding, application settings, notifications, diagnostics, and connectivity checks
- Local backup and restore support
- Arabic-capable UI and PDF text support through bundled fonts and Arabic shaping libraries
- Optional browser notifications through the web-push dependency

## Quick start

### Requirements

- Python 3.10 or newer is recommended for development and packaging.
- A writable project directory. The application creates runtime data under `instance/`.
- Chromium installed for Playwright-based PDF generation.

### Linux and macOS

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
playwright install chromium
python run.py
```

Open the URL printed by the process, normally `http://127.0.0.1:5000` for access on that same computer. For remote access over Tailscale, use the shop computer's Tailscale IP address or MagicDNS hostname with the configured port, and ensure the server listens on a Tailscale-reachable interface.

### Windows from source

```bat
py -m venv .venv
.venv\Scripts\activate
py -m pip install --upgrade pip
pip install -r requirements.txt
playwright install chromium
py run.py
```

The project also contains `desktop_launcher.py` for the desktop-style launch path. See [Operations](docs/OPERATIONS.md) for runtime data, backups, and troubleshooting.

## Windows executable

Build the executable on Windows, not Linux. PyInstaller creates a binary for the operating system on which it runs:

```bat
pip install -r requirements.txt
playwright install chromium
build_exe.bat
```

The output is placed under `dist\AlQemma\`. Copy the complete folder, not only the executable. The packaged application still needs a separately installed Playwright Chromium browser for PDF generation. The existing [build guide](BUILD_EXE.md) contains the detailed Windows procedure and limitations.

## Project map

| Path                               | Responsibility                                                                    |
| ---------------------------------- | --------------------------------------------------------------------------------- |
| `run.py`                           | Main development/server entry point                                               |
| `desktop_launcher.py`              | Starts the local server and opens the interface in a PyWebView desktop window     |
| `config.py`                        | Application configuration and filesystem locations                                |
| `app/__init__.py`                  | Flask application factory, blueprint registration, hooks, and error handling      |
| `app/db.py`                        | Database connection and schema initialization helpers                             |
| `app/routes/`                      | HTTP routes, form handling, page rendering, and access checks                     |
| `app/services/`                    | Business rules, queries, calculations, reports, and file operations               |
| `app/templates/`                   | Jinja HTML templates                                                              |
| `app/static/`                      | CSS, JavaScript, fonts, icons, images, manifest, and service worker               |
| `schema.sql`                       | Database schema and indexes                                                       |
| `instance/`                        | Local database, uploads, temporary files, and launcher settings; do not commit it |
| `alqemma.spec` and `build_exe.bat` | PyInstaller packaging                                                             |

## Documentation

- [Architecture](docs/ARCHITECTURE.md): runtime flow, layers, and extension points
- [Features and module reference](docs/FEATURES.md): route and service responsibilities
- [Data model](docs/DATA_MODEL.md): tables, relationships, and data lifecycle
- [Code reference](docs/CODE_REFERENCE.md): source inventory and change ownership guide
- [Development guide](docs/DEVELOPMENT.md): environment, conventions, verification, and contribution workflow
- [Operations guide](docs/OPERATIONS.md): deployment, backups, PDF support, and troubleshooting
- [Windows build guide](BUILD_EXE.md): executable packaging details

## Testing status

The repository currently has no committed automated test suite. `package.json` contains a placeholder JavaScript test script, but the application is Python/Flask and its behavior should be verified with manual smoke checks until Python tests are added. See [Development](docs/DEVELOPMENT.md) for the current verification checklist.

## Security and data notes

- This is a local shop application, not a hardened multi-tenant internet service or a publicly hosted website.
- Tailscale provides private device-to-device connectivity; it does not replace application security. Only connect trusted devices, and configure the server to listen on the appropriate Tailscale-reachable interface when remote access is needed.
- Do not expose the application port directly to the public internet.
- Treat `instance/` and any generated backups as sensitive: they contain business and customer data.
- Do not commit credentials, databases, uploaded images, generated PDFs, or launcher settings.
- Use the application’s backup workflow and periodically copy backups to a separate, protected location.

## License

See [license.txt](license.txt) for the repository’s license information.
