import json
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request

try:
    from pywebpush import WebPushException, webpush
except ImportError:
    webpush = None
    WebPushException = Exception

try:
    from plyer import notification as plyer_notification
except ImportError:
    plyer_notification = None

bp = Blueprint("notifications", __name__, url_prefix="/push")


def _subscription_store_path() -> Path:
    path = Path(current_app.config.get("PUSH_SUBSCRIPTIONS_PATH", ""))
    if not path:
        path = Path(current_app.instance_path) / "push_subscriptions.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _load_subscriptions() -> list[dict]:
    store = _subscription_store_path()
    if not store.exists():
        return []

    try:
        return json.loads(store.read_text(encoding="utf-8"))
    except (ValueError, json.JSONDecodeError):
        return []


def _save_subscriptions(subscriptions: list[dict]) -> None:
    store = _subscription_store_path()
    store.write_text(json.dumps(subscriptions, ensure_ascii=False, indent=2), encoding="utf-8")


def _add_subscription(subscription: dict) -> None:
    if not subscription or not subscription.get("endpoint"):
        return

    subscriptions = _load_subscriptions()
    endpoints = [item.get("endpoint") for item in subscriptions]
    if subscription["endpoint"] in endpoints:
        subscriptions = [subscription if item.get("endpoint") == subscription["endpoint"] else item for item in subscriptions]
    else:
        subscriptions.append(subscription)

    _save_subscriptions(subscriptions)


def _send_local_notification(title: str, message: str) -> None:
    if plyer_notification is None:
        current_app.logger.warning("plyer is not installed; local native notifications are disabled.")
        return

    try:
        plyer_notification.notify(
            title=title,
            message=message,
            app_name="Al-Qemma",
            timeout=8,
        )
    except Exception as exc:
        current_app.logger.warning("Failed to send local desktop notification: %s", exc)


def _send_web_push(payload: dict) -> None:
    if webpush is None:
        current_app.logger.warning("pywebpush is not installed; remote push notifications are disabled.")
        return

    private_key = current_app.config.get("VAPID_PRIVATE_KEY")
    if not private_key:
        current_app.logger.warning("VAPID_PRIVATE_KEY is not configured.")
        return

    subscriptions = _load_subscriptions()
    if not subscriptions:
        current_app.logger.info("No push subscriptions available to send remote notification.")
        return

    vapid_claims = {
        "sub": current_app.config.get("VAPID_SUBJECT", "mailto:alerts@alqemma.local")
    }

    for subscription in subscriptions:
        try:
            webpush(
                subscription_info=subscription,
                data=json.dumps(payload, ensure_ascii=False),
                vapid_private_key=private_key,
                vapid_claims=vapid_claims,
            )
        except WebPushException as exc:
            current_app.logger.warning("Web push send failed for endpoint %s: %s", subscription.get("endpoint"), exc)


# Event types that represent something going wrong, not just an update -
# these get the red/warning toast; everything else gets green/success.
_WARNING_EVENTS = {"low_stock", "tailscale_disconnected"}


def _send_inapp_toast(event_type: str, title: str, message: str) -> None:
    """Pushes a themed in-window toast to the desktop app's own webview,
    via the same window.evaluate_js(...) channel already used for the
    connectivity dot (see run.py's ServerController). No-ops harmlessly
    if there's no window to push to (console mode, or a plain browser
    tab over LAN/Tailscale) - window.showAppToast itself lives in
    base.html."""
    window = current_app.config.get("PYWEBVIEW_WINDOW")
    if window is None:
        return
    kind = "warning" if event_type in _WARNING_EVENTS else "success"
    try:
        window.evaluate_js(
            f"window.showAppToast && window.showAppToast({json.dumps(kind)}, {json.dumps(title)}, {json.dumps(message)})"
        )
    except Exception as exc:
        current_app.logger.info("Could not show in-app toast: %s", exc)


def notify_event(event_type: str, title: str, message: str, url: str = "/") -> None:
    _send_local_notification(title, message)
    _send_inapp_toast(event_type, title, message)
    _send_web_push({
        "title": title,
        "body": message,
        "url": url,
        "tag": event_type,
    })


@bp.route("/subscribe", methods=["POST"])
def subscribe() -> tuple[dict, int]:
    subscription = request.get_json(silent=True)
    if not subscription or not subscription.get("endpoint"):
        return {"success": False, "message": "Invalid subscription data."}, 400

    _add_subscription(subscription)
    return {"success": True}, 200


@bp.route("/sale", methods=["POST"])
def sale_notification() -> tuple[dict, int]:
    payload = request.get_json(silent=True) or {}
    product_names = payload.get("product_names") or payload.get("product_name") or "منتجات"
    client_name = payload.get("client_name") or ""
    total_price = payload.get("total_price", 0)
    customer_part = f" للعميل {client_name}" if client_name else ""
    title = "New Sale!"
    message = f"تم بيع {product_names}{customer_part} بمبلغ {total_price} ج.م."
    notify_event("sale", title, message, url="/sales")
    return {"success": True, "title": title, "message": message}, 200


@bp.route("/add-product", methods=["POST"])
def product_added_notification() -> tuple[dict, int]:
    payload = request.get_json(silent=True) or {}
    product_name = payload.get("product_name", "منتج")
    price = payload.get("price", 0)
    title = "Product Added"
    message = f"تم إضافة {product_name} بنجاح. السعر: {price} ج.م."
    notify_event("product_added", title, message, url="/products")
    return {"success": True, "title": title, "message": message}, 200


@bp.route("/low-stock", methods=["POST"])
def low_stock_notification() -> tuple[dict, int]:
    payload = request.get_json(silent=True) or {}
    product_name = payload.get("product_name", "منتج")
    quantity = payload.get("quantity", 0)
    title = f"⚠️ Low Stock: {product_name} is down to {quantity} units!"
    message = f"Inventory alert for {product_name}: only {quantity} units remain."
    notify_event("low_stock", title, message, url="/products")
    return {"success": True, "title": title, "message": message}, 200


@bp.route("/expense", methods=["POST"])
def expense_notification() -> tuple[dict, int]:
    payload = request.get_json(silent=True) or {}
    description = payload.get("description", "unknown expense")
    amount = payload.get("amount", 0)
    title = f"💸 Expense Logged: Paid {amount} ج.م for {description}."
    message = f"An expense of {amount} ج.م was recorded for {description}."
    notify_event("expense", title, message, url="/expenses")
    return {"success": True, "title": title, "message": message}, 200
