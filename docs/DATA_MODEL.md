# Data Model

The authoritative schema is [schema.sql](../schema.sql). This document summarizes the business entities so a developer can understand ownership and relationships before changing queries or migrations.

## Entity groups

### Catalog and inventory

- **Categories** group products for catalog navigation and reporting.
- **Products** hold the sellable item identity, pricing, stock-related values, and catalog metadata.
- **Product images/media** associate filesystem assets with products; the media route controls browser access to those files.
- **Adjustments** record intentional stock corrections.
- **Write-offs** record stock removed because of loss, damage, or another non-sale reason.
- **Product audit records** preserve a history of important product/stock changes for investigation.
- **Compatibility records** connect products or model/device information used by the compatibility search.

### Commercial transactions

- **Sales** are completed or recorded customer transactions.
- **Sale items** are the product lines belonging to a sale and are the source of line-level quantities/totals.
- **Purchases** represent stock acquisition/receiving events.
- **Purchase items** represent the products and quantities received through a purchase.
- **Orders** represent the order workflow separate from completed sales where the application uses that distinction.
- **Expenses** represent shop costs used in operating and report calculations.

### People and after-sales

- **Customers** are optional transaction parties with contact and history data.
- **Warranties** connect after-sales coverage to the relevant product/customer/transaction context and track warranty state.

### Configuration and operations

- **Settings** store application/shop configuration used by pages, receipts, branding, and reports.
- **Users/auth data** support the local login/session workflow when present in the schema/configuration.
- **Notifications/subscriptions** support browser notification behavior when configured.

## Relationship principles

1. Product identity is the shared center of inventory, sales, purchases, compatibility, media, audit, and warranties.
2. Transaction header records own their line items; totals should be derived and persisted according to the existing service implementation.
3. Stock-changing actions must go through their corresponding service so audit, adjustments, and transaction effects remain consistent.
4. Customer data is referenced by sales and warranty workflows but should not be required for anonymous walk-in sales unless the route explicitly requires it.
5. Deletion of records with historical or financial meaning should be treated as a data lifecycle decision, not a UI convenience.

## Storage locations

Runtime data is kept under `instance/` according to the active configuration. This may include:

- The local database
- Product images and uploaded media
- Temporary report/PDF files
- Backups
- Launcher settings and local operational state

The directory is intentionally local and writable. It should be backed up, protected from unauthorized access, and excluded from source control.

## Schema change checklist

Before changing a table or column:

1. Search routes and services for all references to the table/column.
2. Check report, receipt, audit, and backup code, not only CRUD pages.
3. Update `schema.sql` and any initialization/migration path.
4. Preserve existing data or provide a migration strategy.
5. Verify a sale, purchase, report, backup, and restore path if the change touches shared entities.