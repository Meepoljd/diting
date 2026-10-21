# shop example

One rule — "member discount is rounded half-up to 2 decimals for orders >= 200" — is implemented
three times on purpose: settlement (`svc/pricing`), refund (`svc/refund`) and display (`web`, with a
registered variant: rounds to whole yuan). diting indexes them as one concept with three occurrences (see `tests/test_cli.py`).
