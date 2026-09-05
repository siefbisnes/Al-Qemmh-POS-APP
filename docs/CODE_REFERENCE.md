# Code Reference

This is the repository index for developers who need to locate behavior quickly. The source files are the authority; the commands below make it possible to refresh an inventory after code changes without manually maintaining a second copy of every function signature.

## Application core

| File | Contents |
| --- | --- |
| `app/__init__.py` | Flask factory, configuration loading, blueprint registration, hooks, and error handlers |
| `app/db.py` | Database connection and initialization helpers |
| `app/branding.py` | Branding-related application helpers |
| `app/utils/errors.py` | Shared error utilities |
| `config.py` | Configuration values and runtime paths |
| `run.py` | Source startup entry point |
| `desktop_launcher.py` | Desktop startup/webview entry point |
| `playwright_runtime.py` | Playwright runtime support |

## HTTP route surface

Every file in `app/routes/` is a Flask blueprint or route group:

`auth.py`, `branding.py`, `categories.py`, `compatibility.py`, `connectivity.py`, `customers.py`, `dashboard.py`, `diagnostics.py`, `expenses.py`, `media.py`, `notifications.py`, `orders.py`, `products.py`, `purchases.py`, `reports.py`, `sales.py`, `settings.py`, and `warranties.py`.

Use this command to list every route decorator and handler definition:

```bash
rg -n "(Blueprint|\.route\(|@.*route|^\s*def )" app/routes
```

## Business service surface

Every file in `app/services/` is a business-area service:

`adjustments.py`, `backup.py`, `categories.py`, `compatibility.py`, `customer_reports.py`, `customers.py`, `expenses.py`, `orders.py`, `owner_dashboard.py`, `pdf_utils.py`, `product_audit.py`, `products.py`, `purchases.py`, `receipts.py`, `reports.py`, `sales.py`, `settings.py`, `warranties.py`, and `writeoffs.py`.

Use this command to list all service functions and classes:

```bash
rg -n "^\s*(def |class )" app/services
```

## Persistence and files

| File/directory | Contents |
| --- | --- |
| `schema.sql` | Tables, constraints, indexes, and initial schema definition |
| `instance/` | Local database and runtime data; machine-specific and sensitive |
| `instance/product_images/` | Uploaded product media |
| `instance/tmp/` | Temporary generated files |
| `app/templates/` | Jinja pages, partials, receipt templates, and error pages |
| `app/static/css/` | Global, customer, and analytics/report styles |
| `app/static/js/` | Shared, customer, and report analytics behavior |
| `app/static/fonts/` | Bundled Arabic-capable fonts |
| `app/static/icons/` and `app/static/images/` | Branding and application image assets |
| `app/static/manifest.json` | Browser/PWA metadata |
| `app/static/sw.js` | Service worker |

Use these commands to inspect the authoritative persistence surface:

```bash
rg -n "^CREATE TABLE|^CREATE INDEX|^CREATE UNIQUE INDEX|^CREATE TRIGGER" schema.sql
rg -n "(SELECT|INSERT|UPDATE|DELETE|CREATE TABLE)" app/db.py app/services app/routes
```

## Packaging and metadata

| File | Contents |
| --- | --- |
| `requirements.txt` | Pinned Python runtime dependencies |
| `package.json` | Node metadata and Playwright test dependency |
| `package-lock.json` | Locked Node dependency resolution |
| `alqemma.spec` | PyInstaller analysis and bundled-resource configuration |
| `build_exe.bat` | Windows executable build command |
| `AlQemma.iss` | Windows installer configuration |
| `BUILD_EXE.md` | Windows build and distribution guide |
| `license.txt` | License text |

## Change ownership guide

| Change | Start here |
| --- | --- |
| Add a page/form | Matching route module, service module, template, and `app/__init__.py` registration if needed |
| Change stock behavior | `app/services/sales.py`, `purchases.py`, `adjustments.py`, `writeoffs.py`, and `product_audit.py` |
| Change receipt/PDF output | `app/services/receipts.py`, `pdf_utils.py`, report/receipt templates, and Playwright runtime support |
| Change reports | `app/services/reports.py`, `owner_dashboard.py`, `customer_reports.py`, report routes/templates, and analytics JavaScript |
| Change stored data | `schema.sql`, `app/db.py`, every affected service, backup behavior, and [DATA_MODEL](DATA_MODEL.md) |
| Change branding | `app/branding.py`, branding route, settings, templates, and static assets |
| Change packaged behavior | `alqemma.spec`, `build_exe.bat`, and [BUILD_EXE](../BUILD_EXE.md) |