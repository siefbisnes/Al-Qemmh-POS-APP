# Architecture

## Runtime model

Al-Qemma is a single-process Flask application with a local database and server-rendered HTML. A normal request follows this path:

```text
Browser or desktop webview
        |
        v
Flask route blueprint (app/routes)
        |
        v
Service module (app/services)
        |
        +--> Database connection (app/db.py) --> local database
        +--> Filesystem storage --> product images, backups, temporary files
        +--> Report/PDF helpers --> HTML/PDF output
        |
        v
Jinja template + static CSS/JavaScript
```

`run.py` starts the normal server workflow. `desktop_launcher.py` starts the same local application and presents it through the desktop/browser launcher path. The Windows executable uses the PyInstaller specification and preserves the same local-first runtime model.

## Flask application factory

`app/__init__.py` is the composition root. It creates the Flask app, loads configuration, initializes the database, registers route blueprints, installs request/session behavior, and supplies error handlers. Route modules are intentionally grouped by business area rather than by template.

The route layer should remain thin. It is responsible for:

- Reading path, query, form, and session values
- Enforcing the application’s authentication/access checks
- Calling a service operation
- Selecting a template or redirect
- Translating expected failures into a user-facing response

Business rules and reusable database operations belong in `app/services/`. This makes the same calculations available to pages, reports, receipts, and future integrations without duplicating them in route handlers.

## Main layers

### Routes

The route blueprints cover authentication, dashboard, products, categories, sales, purchases, orders, customers, warranties, expenses, reports, branding, settings, diagnostics, connectivity, notifications, media, and compatibility search. Most endpoints are browser pages or form submissions rather than REST resources.

### Services

Services contain the application’s business vocabulary: stock adjustments, product audit events, receipt generation, customer reporting, owner dashboard calculations, report generation, warranty management, backup/restore, and settings. Services generally work with the database layer and return records or operation results to routes.

### Persistence

The schema is defined in `schema.sql`. The database is local and file-backed. Connections are created through `app/db.py`, which is the correct place to change connection setup, initialization, row handling, or transaction behavior.

### Presentation

Jinja templates in `app/templates/` render the main UI. Shared layout and navigation live in `base.html`; reusable product cards and receipt templates are kept in their respective partial/template directories. `app/static/css/` contains the global, customer, and report-specific styles. JavaScript is split into common application behavior, customer interactions, and report analytics.

### Reports and PDFs

The reporting services calculate business data. Receipt/report templates provide printable HTML, while the PDF utilities and Playwright integration render browser-quality output. Arabic shaping is supported by `arabic-reshaper`, `python-bidi`, and the bundled Noto Naskh Arabic fonts.

## Request and data lifecycle

1. A user signs in through the auth route and receives the application session state.
2. A page route validates the request and delegates to a service.
3. A service reads or writes the local database, applying stock, totals, audit, customer, warranty, and transaction rules.
4. The route renders a template or redirects to the resulting record/page.
5. Receipt and report flows may additionally create temporary files or PDFs.
6. Error handlers return the application’s 404/500 templates for failed requests.

## Extension rules

When adding a feature:

1. Add or update the schema in `schema.sql` if persistence is required.
2. Put reusable queries and business rules in the closest service module.
3. Add a route only for HTTP concerns and authorization/access checks.
4. Add or update a template and static behavior following the existing layout.
5. Update [FEATURES](FEATURES.md) and [DATA_MODEL](DATA_MODEL.md) when the public behavior or schema changes.
6. Run the manual smoke checklist in [DEVELOPMENT](DEVELOPMENT.md).