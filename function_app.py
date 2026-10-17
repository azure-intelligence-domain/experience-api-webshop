"""[Channel] Webshop Experience API (openapi.yaml): the webshop, shaped for the customer portal.
Products and customers come from Salesforce, orders from the Order Process API, invoices and
payments from SAP, the shipment from the 3PL: all through the gateway, as this API's runtime
identity (ADR 0017). It decides nothing about an order: the order domain does.

Only the people gateway reaches it (Easy Auth), for staff signed in with Google (product webshop).
"""

import azure.functions as func

import kit
import order_view

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)  # Easy Auth guards the door

ALLOWED = ("cancel", "changeDeliveryAddress", "pay")  # what the webshop offers on an order


def gateway(journey: kit.Journey) -> kit.Platform:
    return kit.Platform.from_environment(journey)


def ok(answer: kit.Answer, req: func.HttpRequest) -> dict:
    if not answer.ok:
        raise kit.passed_on(answer, req)
    return answer.body


def product(p: dict) -> dict:
    keep = ("productId", "name", "description", "family", "unitPrice")
    return {"_links": {"self": {"href": f"/products/{p['productId']}"}}, **{k: p[k] for k in keep if p.get(k) is not None}}


def customer(a: dict) -> dict:
    keep = ("customerId", "name", "industry", "shippingAddress", "currency")
    return {"_links": {"self": {"href": f"/customers/{a['customerId']}"}, "orders": {"href": f"/orders?customerId={a['customerId']}"}},
            **{k: a[k] for k in keep if a.get(k) is not None}}


def catalogue(platform: kit.Platform, req: func.HttpRequest) -> dict[str, dict]:
    found = ok(platform.call("GET", "/salesforce/products"), req)
    return {p["productId"]: p for p in found["_embedded"]["products"]}


def the_order(platform: kit.Platform, order: dict, req: func.HttpRequest) -> dict:
    """An order with its invoices, shipment and product names, read at once."""
    shipment_id = order_view.shipment_id(order)
    calls = [("GET", "/sap/invoices", None, {"orderId": order["orderId"]}), ("GET", "/salesforce/products")]
    if shipment_id:
        calls += [("GET", f"/3pl/shipment-bookings/{shipment_id}"), ("GET", f"/3pl/shipments/{shipment_id}")]
    answers = platform.all(*calls)
    invoices = ok(answers[0], req)["_embedded"]["invoices"]
    products = {p["productId"]: p for p in ok(answers[1], req)["_embedded"]["products"]}
    booking = answers[2].body if shipment_id and answers[2].ok else None
    shipped = answers[3].body if shipment_id and answers[3].ok else None
    return order_view.view(order, invoices, booking, shipped, products, ALLOWED)


def order_of(platform: kit.Platform, req: func.HttpRequest) -> dict:
    answer = platform.call("GET", f"/order/orders/{req.route_params['orderId']}")
    if answer.status == 404:
        raise kit.refused(404, "Order Not Found", f"No order {req.route_params['orderId']} exists.", "not-found", req)
    return ok(answer, req)


# --- Queries ------------------------------------------------------------------------------------


@kit.refusals
def retrieve_list_of_products(req: func.HttpRequest, platform_of=gateway) -> func.HttpResponse:
    platform = platform_of(kit.journey(req))
    products = [product(p) for p in catalogue(platform, req).values()]
    return kit.hal(200, {"_links": {"self": {"href": "/products"}}, "totalCount": len(products), "_embedded": {"products": products}})


@kit.refusals
def retrieve_list_of_customers(req: func.HttpRequest, platform_of=gateway) -> func.HttpResponse:
    platform = platform_of(kit.journey(req))
    accounts = ok(platform.call("GET", "/salesforce/accounts"), req)["_embedded"]["accounts"]
    customers = [customer(a) for a in accounts]
    return kit.hal(200, {"_links": {"self": {"href": "/customers"}}, "totalCount": len(customers), "_embedded": {"customers": customers}})


@kit.refusals
def retrieve_list_of_orders(req: func.HttpRequest, platform_of=gateway) -> func.HttpResponse:
    platform = platform_of(kit.journey(req))
    query = {name: req.params.get(name) for name in ("customerId", "status", "offset", "limit")}
    found = ok(platform.call("GET", "/order/orders", query=query), req)
    orders = [order_view.summary(o) for o in found["_embedded"]["orders"]]
    return kit.hal(200, {"_links": found["_links"], "totalCount": found["totalCount"], "_embedded": {"orders": orders}})


@kit.refusals
def retrieve_order(req: func.HttpRequest, platform_of=gateway) -> func.HttpResponse:
    platform = platform_of(kit.journey(req))
    return kit.hal(200, the_order(platform, order_of(platform, req), req))


# --- Commands -----------------------------------------------------------------------------------


@kit.refusals
def place_order(req: func.HttpRequest, platform_of=gateway) -> func.HttpResponse:
    """The webshop prices the lines from Salesforce's catalogue and delivers to the customer's
    shipping address unless another is given; the order domain places the order."""
    platform = platform_of(kit.journey(req))
    request = kit.body_of(req, "placeOrderRequest")
    account_answer, products_answer = platform.all(("GET", f"/salesforce/accounts/{request['customerId']}"), ("GET", "/salesforce/products"))
    if account_answer.status == 404:
        raise kit.invalid(f"customerId: no customer {request['customerId']}.", req)
    account = ok(account_answer, req)
    products = {p["productId"]: p for p in ok(products_answer, req)["_embedded"]["products"]}
    unknown = [line["productId"] for line in request["orderLines"] if line["productId"] not in products]
    if unknown:
        raise kit.invalid(f"orderLines: no product {', '.join(unknown)} in the catalogue.", req)
    order = {"customerId": request["customerId"], "channel": "webshop",
             "orderLines": [{"productId": line["productId"], "quantity": line["quantity"],
                             "unitPrice": products[line["productId"]]["unitPrice"]} for line in request["orderLines"]],
             "deliveryAddress": request.get("deliveryAddress") or account["shippingAddress"]}
    if request.get("requestedDeliveryDate"):
        order["requestedDeliveryDate"] = request["requestedDeliveryDate"]
    key = req.headers.get("Idempotency-Key")
    placed = ok(platform.call("POST", "/order/orders", order, headers={"Idempotency-Key": key} if key else None), req)
    shown = order_view.view(placed, [], None, None, products, ALLOWED)
    return kit.hal(201, shown, {"Location": f"/orders/{placed['orderId']}"})


@kit.refusals
def cancel_order(req: func.HttpRequest, platform_of=gateway) -> func.HttpResponse:
    platform = platform_of(kit.journey(req))
    reason = req.params.get("reason") or "Cancelled by the customer in the webshop."
    answer = platform.call("DELETE", f"/order/orders/{req.route_params['orderId']}", query={"reason": reason})
    return kit.hal(200, the_order(platform, ok(answer, req), req))


@kit.refusals
def change_delivery_address(req: func.HttpRequest, platform_of=gateway) -> func.HttpResponse:
    platform = platform_of(kit.journey(req))
    address = kit.body_of(req, "deliveryAddress")
    answer = platform.call("PUT", f"/order/orders/{req.route_params['orderId']}/delivery-address", address)
    return kit.hal(200, the_order(platform, ok(answer, req), req))


@kit.refusals
def pay_invoice(req: func.HttpRequest, platform_of=gateway) -> func.HttpResponse:
    """The customer pays an open invoice of the order in full (the payment provider, simulated):
    SAP records the payment, and the order domain hears PaymentReceived."""
    platform = platform_of(kit.journey(req))
    wanted = kit.body_of(req, "paymentRequest")["invoiceNumber"]
    order = order_of(platform, req)
    invoices = ok(platform.call("GET", "/sap/invoices", query={"orderId": order["orderId"]}), req)["_embedded"]["invoices"]
    invoice = next((i for i in invoices if i["invoiceNumber"] == wanted), None)
    if invoice is None:
        raise kit.refused(404, "Invoice Not Found", f"Order {order['orderId']} has no invoice {wanted}.", "not-found", req)
    if not order_view.payable(invoice) or order["status"] == "cancelled":
        raise kit.refused(409, "Invoice Not Payable", f"Invoice {wanted} is paid already, or credited.", "conflict", req)
    left = round(invoice["amount"]["value"] - invoice["paidAmount"]["value"], 2)
    paid = ok(platform.call("POST", "/sap/payments", {"invoiceNumber": wanted, "amount": {"value": left, "currency": invoice["amount"]["currency"]}}), req)
    keep = ("paymentId", "invoiceNumber", "amount", "invoicePaidInFull", "receivedAt")
    payment = {"_links": {"self": {"href": f"/orders/{order['orderId']}/payments/{paid['paymentId']}"},
                          "order": {"href": f"/orders/{order['orderId']}"}}, **{k: paid[k] for k in keep}}
    return kit.hal(201, payment, {"Location": payment["_links"]["self"]["href"]})


# --- Routes (host.json: no route prefix, so the paths are the contract's) -----------------------


@app.route(route="products", methods=["GET"])
def retrieveListOfProducts(req: func.HttpRequest) -> func.HttpResponse:
    return retrieve_list_of_products(req)


@app.route(route="customers", methods=["GET"])
def retrieveListOfCustomers(req: func.HttpRequest) -> func.HttpResponse:
    return retrieve_list_of_customers(req)


@app.route(route="orders", methods=["GET"])
def retrieveListOfOrders(req: func.HttpRequest) -> func.HttpResponse:
    return retrieve_list_of_orders(req)


@app.route(route="orders", methods=["POST"])
def placeOrder(req: func.HttpRequest) -> func.HttpResponse:
    return place_order(req)


@app.route(route="orders/{orderId}", methods=["GET"])
def retrieveOrder(req: func.HttpRequest) -> func.HttpResponse:
    return retrieve_order(req)


@app.route(route="orders/{orderId}", methods=["DELETE"])
def cancelOrder(req: func.HttpRequest) -> func.HttpResponse:
    return cancel_order(req)


@app.route(route="orders/{orderId}/delivery-address", methods=["PUT"])
def changeDeliveryAddress(req: func.HttpRequest) -> func.HttpResponse:
    return change_delivery_address(req)


@app.route(route="orders/{orderId}/payments", methods=["POST"])
def payInvoice(req: func.HttpRequest) -> func.HttpResponse:
    return pay_invoice(req)
