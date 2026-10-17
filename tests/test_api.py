import json

import azure.functions as func

import function_app as api
from contracts_test import assert_matches
from fakes import ACME, INVOICE, ORDER, FakePlatform


def request(method, url, body=None, route=None, params=None, headers=None):
    return func.HttpRequest(method, url, headers={"Correlation-Id": "ord-4711", **(headers or {})}, route_params=route or {},
                            params=params or {}, body=json.dumps(body).encode() if body is not None else b"")


ORDER_4711 = {"orderId": "4711"}


def test_products_and_customers():
    products = assert_matches(api.retrieve_list_of_products(request("GET", "/products"), FakePlatform()), "retrieveListOfProducts")
    assert products["_embedded"]["products"][0]["unitPrice"]["value"] == 7000.0
    customers = assert_matches(api.retrieve_list_of_customers(request("GET", "/customers"), FakePlatform()), "retrieveListOfCustomers")
    assert customers["_embedded"]["customers"][0]["_links"]["orders"]["href"] == "/orders?customerId=ACME"


def test_an_order_with_everything_about_it():
    platform = FakePlatform()
    order = assert_matches(api.retrieve_order(request("GET", "/orders/4711", route=ORDER_4711), platform), "retrieveOrder")
    assert order["orderLines"][0]["productName"] == "Hydraulic Press HP-1042" and order["orderLines"][0]["lineAmount"]["value"] == 84000.0
    assert order["shipment"] == {"shipmentId": "SPD-004711", "partnerId": "speedy-3pl", "trackingNumber": "SPD4711BE", "status": "dispatched",
                                 "bookedAt": "2026-10-03T09:07:00Z", "dispatchedAt": "2026-10-03T09:08:00Z"}
    assert [p["milestone"] for p in order["progress"] if p["reached"]] == ["placed", "confirmed", "readyForShipment", "shipped"]
    assert order["timeline"][1]["description"] == "SAP accepted the order (credit check passed)"
    assert set(order["_links"]) == {"self", "pay"}  # shipped: no cancel, no address change; the invoice is open
    assert platform.journey.correlation_id == "ord-4711"


def test_unknown_order_is_404():
    response = api.retrieve_order(request("GET", "/orders/9", route={"orderId": "9"}), FakePlatform())
    assert response.status_code == 404 and assert_matches(response, "retrieveOrder")["title"] == "Order Not Found"


def test_place_order_prices_from_the_catalogue_and_delivers_to_the_customer():
    placed = {**ORDER, "status": "placed", "paymentStatus": "unpaid", "references": {}, "timeline": ORDER["timeline"][:1]}
    numbered = lambda body: [{**line, "lineNumber": n} for n, line in enumerate(body["orderLines"], start=1)]  # noqa: E731
    platform = FakePlatform({("POST", "/order/orders"): (201, lambda body, query: {**placed, **body, "orderLines": numbered(body)})})
    response = api.place_order(request("POST", "/orders", {"customerId": "ACME", "orderLines": [{"productId": "SKU-1042", "quantity": 12}]},
                                       headers={"Idempotency-Key": "k-1"}), platform)
    order = assert_matches(response, "placeOrder")
    assert response.status_code == 201 and response.headers["Location"] == "/orders/4711"
    sent = next(c for c in platform.calls if c[0] == "POST")
    assert sent[2]["channel"] == "webshop" and sent[2]["orderLines"][0]["unitPrice"]["value"] == 7000.0
    assert sent[2]["deliveryAddress"] == ACME["shippingAddress"] and sent[4] == {"Idempotency-Key": "k-1"}
    assert set(order["_links"]) == {"self", "cancel", "changeDeliveryAddress"}


def test_place_order_refuses_what_the_catalogue_does_not_have():
    body = {"customerId": "ACME", "orderLines": [{"productId": "SKU-9", "quantity": 1}]}
    response = api.place_order(request("POST", "/orders", body), FakePlatform())
    assert response.status_code == 400 and "SKU-9" in assert_matches(response, "placeOrder")["detail"]
    unknown_customer = api.place_order(request("POST", "/orders", {**body, "customerId": "NOBODY"}), FakePlatform())
    assert unknown_customer.status_code == 400
    assert api.place_order(request("POST", "/orders", {"customerId": "ACME", "orderLines": []}), FakePlatform()).status_code == 400


def test_cancel_passes_the_order_domains_refusal_on():
    conflict = {"title": "Order State Conflict", "detail": "Order 4711 is shipped; cancelOrder is allowed while ..."}
    platform = FakePlatform({("DELETE", "/order/orders/4711"): (409, conflict)})
    response = api.cancel_order(request("DELETE", "/orders/4711", route=ORDER_4711), platform)
    assert response.status_code == 409 and assert_matches(response, "cancelOrder")["title"] == "Order State Conflict"


def test_cancel_answers_the_order_as_it_is_now():
    cancelled = {**ORDER, "status": "cancelled", "cancellationReason": "Ordered twice."}
    platform = FakePlatform({("DELETE", "/order/orders/4711"): (200, cancelled)})
    response = api.cancel_order(request("DELETE", "/orders/4711", route=ORDER_4711, params={"reason": "Ordered twice."}), platform)
    order = assert_matches(response, "cancelOrder")
    assert order["status"] == "cancelled" and "pay" not in order["_links"]
    assert platform.calls[0][3] == {"reason": "Ordered twice."}


def test_pay_the_open_invoice_in_full():
    payment = {"paymentId": "1400000001", "invoiceNumber": "90004711", "orderId": "4711", "amount": {"value": 84000.0, "currency": "EUR"},
               "invoicePaidInFull": True, "receivedAt": "2026-10-10T08:00:00Z"}
    platform = FakePlatform({("POST", "/sap/payments"): (201, payment)})
    response = api.pay_invoice(request("POST", "/orders/4711/payments", {"invoiceNumber": "90004711"}, route=ORDER_4711), platform)
    paid = assert_matches(response, "payInvoice")
    assert response.status_code == 201 and paid["invoicePaidInFull"] and paid["_links"]["order"]["href"] == "/orders/4711"
    assert platform.calls[-1][2] == {"invoiceNumber": "90004711", "amount": {"value": 84000.0, "currency": "EUR"}}


def test_a_credited_or_unknown_invoice_is_not_paid():
    credited = {**INVOICE, "creditNote": {"creditNoteNumber": "70004711", "amount": INVOICE["amount"], "issuedAt": "2026-10-03T10:00:00Z"}}
    platform = FakePlatform({("GET", "/sap/invoices"): (200, {"_embedded": {"invoices": [credited]}})})
    response = api.pay_invoice(request("POST", "/orders/4711/payments", {"invoiceNumber": "90004711"}, route=ORDER_4711), platform)
    assert response.status_code == 409 and assert_matches(response, "payInvoice")["title"] == "Invoice Not Payable"
    other = api.pay_invoice(request("POST", "/orders/4711/payments", {"invoiceNumber": "1"}, route=ORDER_4711), FakePlatform())
    assert other.status_code == 404


def test_a_failure_behind_the_gateway_is_502():
    platform = FakePlatform({("GET", "/salesforce/products"): (500, {"title": "boom"})})
    response = api.retrieve_list_of_products(request("GET", "/products"), platform)
    assert response.status_code == 502 and assert_matches(response, "retrieveListOfProducts")["title"] == "Downstream Failure"


def test_the_journey_is_required():
    response = api.retrieve_list_of_orders(request("GET", "/orders", headers={"Correlation-Id": ""}), FakePlatform())
    assert response.status_code == 400
