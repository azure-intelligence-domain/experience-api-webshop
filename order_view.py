"""One order, everything about it, shaped for a channel: the order (Order Process API) with its
invoices (SAP), its shipment (3PL), its timeline in plain words and what can be done with it next.
Pure functions: the I/O is in function_app.py.

The links say what the order domain allows now, so a screen shows only the buttons that work; the
order domain still decides (409 when the order changed meanwhile).
"""

ACTIONS = {  # HAL link → the order statuses in which the order domain allows it (process-api-order)
    "cancel": {"placed", "confirmed", "onHold", "readyForShipment"},
    "changeDeliveryAddress": {"placed", "confirmed", "onHold"},
    "reschedule": {"placed", "confirmed", "onHold", "readyForShipment"},
    "putOnHold": {"confirmed"},
    "release": {"onHold"},
}
MILESTONES = [("placed", "OrderPlaced"), ("confirmed", "OrderConfirmed"), ("readyForShipment", "OrderReadyForShipment"),
              ("shipped", "OrderShipped"), ("delivered", "OrderDelivered"), ("completed", "OrderCompleted")]
SAID = {
    "OrderPlaced": "Order placed", "OrderConfirmed": "Order confirmed", "OrderRejected": "Order rejected",
    "OrderAmended": "Order lines amended", "OrderDeliveryAddressChanged": "Delivery address changed",
    "OrderDeliveryRescheduled": "Delivery rescheduled", "OrderPutOnHold": "Put on hold", "OrderReleased": "Released from hold",
    "OrderCancelled": "Order cancelled", "OrderInvoiced": "Invoiced", "OrderPaid": "Paid", "OrderReadyForShipment": "Ready for shipment",
    "OrderShipped": "Shipped", "OrderDelivered": "Delivered", "OrderCompleted": "Order completed",
    "SalesOrderAccepted": "SAP accepted the order (credit check passed)", "SalesOrderRefused": "SAP refused the order",
    "InvoiceIssued": "SAP issued the invoice", "PaymentReceived": "Payment received",
    "SalesOrderCancelled": "SAP cancelled the sales order; a credit note reverses the invoice",
    "StockReserved": "Stock reserved in the warehouse", "StockReleased": "Reserved stock released",
    "ShipmentBooked": "Shipment booked with the 3PL partner", "ShipmentDispatched": "The 3PL partner picked it up",
    "ShipmentDelivered": "The 3PL partner delivered it", "ShipmentCancelled": "Shipment booking cancelled",
}
LATER = ["confirmed", "readyForShipment", "shipped", "delivered", "completed"]


def shipment_id(order: dict) -> str | None:
    """The 3PL consignment: recorded on the order, or (orders before 1.2.0) the 3PL's own numbering."""
    known = order.get("references", {}).get("shipmentId")
    if known or order["status"] not in ("shipped", "delivered", "completed"):
        return known
    return f"SPD-{int(order['orderId']):06d}"


def reservation_id(order: dict) -> str | None:
    known = order.get("references", {}).get("reservationId")
    if known or order["status"] not in ("readyForShipment", "shipped", "delivered", "completed"):
        return known
    return f"RSV-{order['orderId']}"


def progress(order: dict) -> list[dict]:
    """Where the order is on its way: each milestone, reached or not, and when."""
    at = {m["fact"]: m["occurredAt"] for m in order.get("timeline", [])}
    status = order["status"]
    reached_by_status = {"placed"} | set(LATER[: LATER.index(status) + 1] if status in LATER else [])
    out = []
    for milestone, fact in MILESTONES:
        step = {"milestone": milestone, "reached": fact in at or milestone in reached_by_status}
        if fact in at:
            step["reachedAt"] = at[fact]
        out.append(step)
    return out


def timeline(order: dict) -> list[dict]:
    return [{**m, "description": SAID.get(m["fact"], m["fact"])} for m in order.get("timeline", [])]


def invoice(doc: dict) -> dict:
    keep = ("invoiceNumber", "amount", "paidAmount", "status", "issuedAt", "dueDate", "creditNote")
    return {k: doc[k] for k in keep if k in doc}


def payable(doc: dict) -> bool:
    return doc["status"] == "open" and "creditNote" not in doc


def shipment(booking: dict | None, shipped: dict | None) -> dict | None:
    """The 3PL's booking and, once picked up, its shipment, as one."""
    if booking is None:
        return None
    out = {"shipmentId": booking["shipmentId"], "partnerId": booking["partnerId"], "trackingNumber": booking["trackingNumber"],
           "status": {"booked": "booked", "cancelled": "cancelled"}.get(booking["status"], (shipped or {}).get("status", "dispatched")),
           "bookedAt": booking.get("bookedAt")}
    for key in ("dispatchedAt", "deliveredAt", "receivedBy"):
        if shipped and key in shipped:
            out[key] = shipped[key]
    if "cancelledAt" in booking:
        out["cancelledAt"] = booking["cancelledAt"]
    return {k: v for k, v in out.items() if v is not None}


def lines(order: dict, products: dict[str, dict]) -> list[dict]:
    out = []
    for line in order["orderLines"]:
        amount = round(line["quantity"] * line["unitPrice"]["value"], 2)
        named = {"productName": products[line["productId"]]["name"]} if line["productId"] in products else {}
        out.append({**line, **named, "lineAmount": {"value": amount, "currency": line["unitPrice"]["currency"]}})
    return out


def links(order: dict, invoices: list[dict], allowed: tuple[str, ...]) -> dict:
    href = f"/orders/{order['orderId']}"
    out = {"self": {"href": href}}
    paths = {"cancel": href, "changeDeliveryAddress": f"{href}/delivery-address", "reschedule": f"{href}/requested-delivery-date",
             "putOnHold": f"{href}/hold", "release": f"{href}/hold", "pay": f"{href}/payments"}
    for action in allowed:
        if action == "pay":
            if order["status"] != "cancelled" and any(payable(i) for i in invoices):
                out["pay"] = {"href": paths["pay"]}
        elif order["status"] in ACTIONS[action]:
            out[action] = {"href": paths[action]}
    return out


def view(order: dict, invoices: list[dict], booking: dict | None, shipped: dict | None, products: dict[str, dict],
         allowed: tuple[str, ...]) -> dict:
    """The order as the channel shows it."""
    keep = ("orderId", "customerId", "channel", "status", "paymentStatus", "totalAmount", "deliveryAddress", "requestedDeliveryDate",
            "holdReason", "cancellationReason", "createdAt", "updatedAt")
    out = {"_links": links(order, invoices, allowed), **{k: order[k] for k in keep if k in order},
           "orderLines": lines(order, products), "progress": progress(order), "timeline": timeline(order),
           "invoices": [invoice(i) for i in invoices]}
    found = shipment(booking, shipped)
    if found:
        out["shipment"] = found
    return out


def summary(order: dict) -> dict:
    """An order in a list."""
    keep = ("orderId", "customerId", "channel", "status", "paymentStatus", "totalAmount", "createdAt", "updatedAt")
    out = {"_links": {"self": {"href": f"/orders/{order['orderId']}"}}, **{k: order[k] for k in keep if k in order},
           "lineCount": len(order["orderLines"])}
    tracking = order.get("references", {}).get("trackingNumber")
    if tracking:
        out["trackingNumber"] = tracking
    return out
