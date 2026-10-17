# Downstream Failure

`502`, `application/problem+json`

A Process or System API behind the gateway failed. Nothing is half done by this API: it only reads, or passes one command on. Try again later; the trace (Correlation-Id) shows where it failed.
