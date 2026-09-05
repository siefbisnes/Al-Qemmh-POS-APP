# Development Guide

## Environment setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
playwright install chromium
python run.py
```

On Windows, use `py` and `.venv\Scripts\activate` equivalents. Playwright Chromium is needed by PDF/report flows even when the Flask pages themselves run correctly.

## Dependencies

- Flask and Werkzeug: web framework and request/session utilities
- openpyxl: spreadsheet-oriented import/export support
- Pillow: image processing
- plyer: desktop notification integration
- pywebpush: browser push notification support
- pywebview: desktop webview integration
- waitress: production-style local WSGI serving
- reportlab: PDF/document generation support
- arabic-reshaper and python-bidi: correct Arabic shaping and bidirectional text

The JavaScript package manifest currently provides Playwright test tooling but does not define an application build pipeline. The shipped frontend is served from `app/static/`.

## Local development workflow

1. Create/activate the virtual environment.
2. Install `requirements.txt`.
3. Start `python run.py`.
4. Use the dashboard and the manual smoke checklist below.
5. Keep runtime data and generated files out of commits.
6. Update documentation when routes, services, schema, configuration, or packaging behavior changes.

## Manual smoke checklist

- Start the app and open the login page.
- Log in and confirm the dashboard renders.
- Create or inspect a category and product.
- Create a purchase and verify received stock.
- Create a sale and verify totals, stock effect, history, and receipt output.
- Open a customer detail page and purchase history.
- Open warranty and compatibility workflows.
- Add an expense and verify dashboard/report visibility.
- Open reports and generate a printable/PDF output if Chromium is installed.
- Open settings, branding, diagnostics, and connectivity pages.
- Test a backup operation and confirm the output is stored outside source control.
- Confirm a missing route renders the 404 page and that a server error is logged/rendered through the 500 handler.

## Code conventions

- Keep routes focused on HTTP and presentation concerns.
- Put reusable business logic in `app/services/`.
- Use the existing database helper instead of opening ad hoc connections.
- Keep financial and stock calculations in one service path so reports and UI agree.
- Preserve the existing template/static naming and shared base layout.
- Avoid committing `instance/`, generated reports, uploaded images, caches, or build output.

## Verification commands

Syntax-check the Python tree without changing files:

```bash
python -m compileall -q app run.py config.py desktop_launcher.py playwright_runtime.py
```

Inspect the working tree before committing:

```bash
git status --short
git diff --check
```

There is currently no committed Python test suite or CI workflow. A future test suite should prioritize service-level tests for sale totals, stock effects, purchases, reports, backups, and warranty/customer relationships, followed by route smoke tests.

## Adding a route

1. Choose the existing business-area blueprint or create one only when the responsibility is genuinely new.
2. Add the handler and access check in `app/routes/`.
3. Delegate data changes/calculations to a service.
4. Add/update the template and static behavior.
5. Register the blueprint in the application factory when needed.
6. Add the route to [FEATURES](FEATURES.md) and validate it manually.

## Packaging changes

When imports, templates, static files, fonts, or runtime paths change, review `alqemma.spec` and rebuild on Windows. A successful source run does not prove the PyInstaller bundle includes the changed asset.