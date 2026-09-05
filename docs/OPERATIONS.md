# Operations Guide

## Running the application

For development or a local shop computer:

```bash
python run.py
```

Keep the process running while the browser is in use. The desktop launcher provides the same application through a desktop-oriented startup path. Closing the server/console stops the application.

The application is local-first. Do not expose it directly to the public internet without adding a complete deployment security boundary, including authentication review, secret management, transport security, network controls, and backup protection.

## Runtime data

The `instance/` directory is operational data, not source code. It can contain the database, product images, temporary files, launcher settings, logs, and backups. Ensure the account running the application has write permission there and that the directory is included in the backup plan.

Do not delete `instance/` casually: doing so can remove the shop’s database and uploaded media.

## Backups

Use the application backup functionality where available through `app/services/backup.py`. A practical backup policy is:

- Keep frequent local backups for recovery from operator mistakes.
- Copy periodic backups to a separate protected disk or managed storage.
- Test restoration on a non-production copy.
- Keep backups access-controlled because they contain customer and financial data.
- Record which application version created each backup when performing upgrades.

Backups should cover both the database and file-based assets such as product images. A database-only backup may not reproduce the complete shop state.

## PDF and receipt support

PDF generation uses Playwright/Chromium and the report/PDF dependencies. If browser pages work but PDF generation fails:

1. Confirm the virtual environment is active.
2. Run `playwright install chromium` for the current user/environment.
3. Confirm the process can write its temporary output directory.
4. Check that bundled fonts and report templates are present.
5. Retry after clearing only stale temporary output, never the database or uploads.

For a packaged Windows build, Chromium is not bundled by default. Install it separately on each target computer as described in [BUILD_EXE.md](../BUILD_EXE.md).

## Windows packaging

Build on Windows using `build_exe.bat` or `pyinstaller alqemma.spec`. Distribute the entire `dist\AlQemma\` folder. The executable is a snapshot, so rebuild after Python, template, static asset, font, or packaging changes.

Unsigned PyInstaller executables may trigger Windows SmartScreen or antivirus warnings. Code signing is the long-term distribution solution.

## Troubleshooting

### The app does not start

- Confirm the selected Python version and virtual environment.
- Reinstall `requirements.txt`.
- Run the source entry point directly to see the traceback.
- Check that the runtime directory is writable.
- Use the diagnostics page once the server is running.

### Pages load but data is missing

- Check that the process is using the intended `instance/` location.
- Confirm the database was initialized from the current `schema.sql`.
- Restore a known-good backup only after preserving the current data.

### Images do not display

- Confirm the image file exists under the configured runtime media directory.
- Check file permissions and the media route.
- Avoid moving uploaded files manually without updating their stored references.

### Reports or receipts fail

- Install Chromium with `playwright install chromium`.
- Check report templates, fonts, temporary-directory permissions, and available disk space.
- Verify Arabic font/shaping dependencies are installed.

### The packaged build behaves differently

- Rebuild the executable after every code or asset change.
- Confirm templates/static/fonts are included by `alqemma.spec`.
- Run the packaged application from a writable folder and install Chromium on that machine.

## Upgrade procedure

1. Stop the running application.
2. Take and verify a backup of the database and media.
3. Review schema and configuration changes.
4. Update Python dependencies in a controlled environment.
5. Start the updated source/build and run the smoke checklist.
6. Verify sales, stock, reports, receipts, and restore capability before normal use.