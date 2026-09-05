# Features and Module Reference

This page is a code-oriented map of the application. The route list describes the HTTP ownership; the service list describes reusable business operations. Exact validation and response details remain in the implementation and templates.

## Route modules

| Module | Responsibility |
| --- | --- |
| `auth.py` | Login, logout, session/authentication flow |
| `branding.py` | Brand/logo and shop identity configuration |
| `categories.py` | Category listing and category create/edit operations |
| `compatibility.py` | Product compatibility search and results |
| `connectivity.py` | Connectivity/status checks used by the UI |
| `customers.py` | Customer list, detail, purchase history, and customer actions |
| `dashboard.py` | Main dashboard and summary views |
| `diagnostics.py` | Health/diagnostic information for local troubleshooting |
| `expenses.py` | Expense entry and expense views |
| `media.py` | Media/product-image serving and related file operations |
| `notifications.py` | Notification subscription and notification-related endpoints |
| `orders.py` | Order listing, details, and order workflow |
| `products.py` | Product catalog, details, forms, images, stock and audit navigation |
| `purchases.py` | Purchase entry, receiving, and purchase history |
| `reports.py` | Report pages, analytics, exports, and printable report flows |
| `sales.py` | New sales, sale history, sale details, receipts, and transaction actions |
| `settings.py` | Application/shop settings |
| `warranties.py` | Warranty creation, lookup, and warranty lifecycle |

## Service modules

| Module | Business responsibility |
| --- | --- |
| `adjustments.py` | Stock corrections and inventory adjustments |
| `backup.py` | Database/data backup and restore operations |
| `categories.py` | Category persistence and category queries |
| `compatibility.py` | Compatibility relationships/search logic |
| `customer_reports.py` | Customer-focused summaries and purchase reporting |
| `customers.py` | Customer persistence and lookup |
| `expenses.py` | Expense persistence and calculations |
| `orders.py` | Order persistence and status/workflow logic |
| `owner_dashboard.py` | Owner dashboard KPIs and aggregated business data |
| `pdf_utils.py` | PDF/browser rendering support and document utilities |
| `product_audit.py` | Product and stock audit trail operations |
| `products.py` | Product persistence, search, stock, pricing, and catalog operations |
| `purchases.py` | Purchase persistence and received-stock handling |
| `receipts.py` | Receipt data preparation and receipt output workflows |
| `reports.py` | Cross-domain reports, totals, exports, and analytics data |
| `sales.py` | Sale creation, line items, totals, stock effects, and sale history |
| `settings.py` | Application settings persistence and retrieval |
| `warranties.py` | Warranty persistence, lookup, and status handling |
| `writeoffs.py` | Written-off stock and loss recording |

## Primary user workflows

### Product and inventory workflow

1. Create categories and products.
2. Attach product images and maintain product metadata/pricing.
3. Receive stock through purchases.
4. Correct stock with adjustments or record losses through write-offs.
5. Review product audit history when investigating a stock or price change.

### Sales workflow

1. Start a new sale and select products/customers as applicable.
2. Calculate line totals and the transaction total.
3. Persist the sale and its items, applying stock changes.
4. View the sale detail or history.
5. Print or generate a receipt and use the transaction/adjustment flow when a correction is required.

### Customer and warranty workflow

Customers can be maintained independently and connected to purchases. Customer detail pages expose purchase history and reports. Warranty records support later lookup against the relevant customer/product transaction and status lifecycle.

### Reporting workflow

Dashboard and report routes aggregate sales, purchases, expenses, inventory, customers, and other operational data. The UI has dedicated report analytics JavaScript and report CSS. Printable report and receipt templates live under `templates/receipts/` and related top-level templates.

## Frontend assets

- `static/js/app.js`: shared browser behavior and application interactions
- `static/js/customers.js`: customer-specific interactions
- `static/js/reports_analytics.js`: charts/report analytics behavior
- `static/js/chart.umd.min.js`: bundled chart runtime
- `static/css/style.css`: global application styles
- `static/css/customers.css`: customer pages
- `static/css/reports_analytics.css`: report and analytics pages
- `static/manifest.json` and `static/sw.js`: installable/PWA-style browser metadata and service worker behavior
- `static/fonts/`: Arabic-capable font assets

## Important distinction

The project is not a documented public REST API. Routes primarily return HTML, redirects, files, or browser-oriented responses. External integrations should call a deliberate service/API boundary rather than relying on template route behavior.