"""A stand-in for the platform behind the gateway: the Process and System APIs as canned answers,
so the Experience API is tested without Azure. Every call is recorded."""

import copy

import kit

ADDRESS = {"street": "Rue de la Loi 16", "postalCode": "1000", "city": "Brussels", "countryCode": "BE"}
EUR = lambda v: {"value": v, "currency": "EUR"}  # noqa: E731
PRODUCTS = {"_embedded": {"products": [
    {"productId": "SKU-1042", "name": "Hydraulic Press HP-1042", "description": "40-tonne press.", "family": "Machinery", "unitPrice": EUR(7000.0)},
    {"productId": "SKU-3310", "name": "Pallet Racking Bay PR-3310", "family": "Storage", "unitPrice": EUR(480.0)}]}}
ACME = {"customerId": "ACME", "name": "ACME Corporation", "industry": "Manufacturing", "shippingAddress": ADDRESS, "currency": "EUR"}
TIMELINE = [{"fact": f, "domain": d, "occurredAt": f"2026-10-03T09:{n:02d}:00Z"} for n, (f, d) in enumerate([
    ("OrderPlaced", "order"), ("SalesOrderAccepted", "finance"), ("OrderConfirmed", "order"), ("InvoiceIssued", "finance"),
    ("OrderInvoiced", "order"), ("StockReserved", "supply"), ("OrderReadyForShipment", "order"), ("ShipmentBooked", "logistics"),
    ("ShipmentDispatched", "logistics"), ("OrderShipped", "order")])]
ORDER = {"_links": {"self": {"href": "/orders/4711"}}, "orderId": "4711", "customerId": "ACME", "channel": "webshop", "status": "shipped",
         "paymentStatus": "invoiced", "orderLines": [{"lineNumber": 1, "productId": "SKU-1042", "quantity": 12, "unitPrice": EUR(7000.0)}],
         "totalAmount": EUR(84000.0), "deliveryAddress": ADDRESS, "createdAt": "2026-10-03T09:00:00Z", "updatedAt": "2026-10-03T09:09:00Z",
         "references": {"erpOrderNumber": "8000043217", "invoiceNumber": "90004711", "reservationId": "RSV-4711", "warehouseId": "BRU-1",
                        "shipmentId": "SPD-004711", "trackingNumber": "SPD4711BE", "partnerId": "speedy-3pl"},
         "timeline": TIMELINE}
INVOICE = {"invoiceNumber": "90004711", "erpOrderNumber": "8000043217", "orderId": "4711", "customerId": "ACME", "amount": EUR(84000.0),
           "paidAmount": EUR(0.0), "status": "open", "issuedAt": "2026-10-03T09:03:00Z", "dueDate": "2026-11-02"}
BOOKING = {"shipmentId": "SPD-004711", "orderId": "4711", "partnerId": "speedy-3pl", "status": "pickedUp", "trackingNumber": "SPD4711BE",
           "bookedAt": "2026-10-03T09:07:00Z", "pickedUpAt": "2026-10-03T09:08:00Z"}
SHIPPED = {"shipmentId": "SPD-004711", "status": "dispatched", "trackingNumber": "SPD4711BE", "dispatchedAt": "2026-10-03T09:08:00Z"}


class FakePlatform:
    """Answers by (method, path); a callable answer gets (body, query)."""

    def __init__(self, answers: dict | None = None):
        self.calls = []
        self.answers = {
            ("GET", "/salesforce/products"): (200, PRODUCTS),
            ("GET", "/salesforce/accounts"): (200, {"_embedded": {"accounts": [ACME]}}),
            ("GET", "/salesforce/accounts/ACME"): (200, ACME),
            ("GET", "/order/orders/4711"): (200, ORDER),
            ("GET", "/order/orders"): (200, {"_links": {"self": {"href": "/orders?offset=0&limit=20"}}, "totalCount": 1, "_embedded": {"orders": [ORDER]}}),
            ("GET", "/sap/invoices"): (200, {"_embedded": {"invoices": [INVOICE]}}),
            ("GET", "/sap/sales-orders/8000043217"): (200, {"erpOrderNumber": "8000043217", "status": "accepted", "netValue": EUR(84000.0)}),
            ("GET", "/wms/stock-reservations/RSV-4711"): (200, {"reservationId": "RSV-4711", "warehouseId": "BRU-1", "status": "reserved",
                                                               "lifecycleStatus": "reserved", "orderLines": [{"productId": "SKU-1042", "quantity": 12}]}),
            ("GET", "/3pl/shipment-bookings/SPD-004711"): (200, BOOKING),
            ("GET", "/3pl/shipments/SPD-004711"): (200, SHIPPED),
            **(answers or {}),
        }

    def __call__(self, journey):  # used as platform_of
        self.journey = journey
        return self

    def call(self, method, path, body=None, query=None, headers=None):
        self.calls.append((method, path, body, query, headers))
        status, answer = self.answers.get((method, path), (404, {"title": "Not Found", "detail": f"{method} {path}"}))
        if callable(answer):
            answer = answer(body, query)
        return kit.Answer(status, copy.deepcopy(answer))

    def all(self, *calls):
        return [self.call(*c) for c in calls]

    def paths(self, method=None):
        return [c[1] for c in self.calls if method is None or c[0] == method]
