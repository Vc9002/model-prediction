# Polymarket US API boundary

This repository uses the **Polymarket US** product. Polymarket US and the
international Polymarket product are separate products with separate accounts,
credentials, and API surfaces.

## Verified capabilities

The official Polymarket US Python SDK documents these authenticated surfaces:

- event and market discovery;
- order book and BBO reads;
- individual order create, preview, list, retrieve, modify, cancel, and
  cancel-all operations;
- position, activity, balance, and WebSocket access.

The current public US SDK documentation does **not** expose a Combo, bundle,
RFQ, or atomic multi-leg order method. The absence of a documented US endpoint
means the bot must treat native US combo trading as unsupported.

## Combo boundary

The international product documents Combos for supported sports markets and
provides a separate RFQ client. That client is not a Polymarket US client.
Installing the optional `combo-international` dependency does not add combo
support to a US account.

The repository enforces this boundary in
`model_prediction.portfolio.polymarket_combos`: when
`POLYMARKET_PLATFORM=us`, combo-client creation fails closed with an explicit
unsupported-platform error.

Do not emulate a combo by submitting separate US legs from the Auto-Buyer.
Separate orders can fill unevenly, leave unwanted exposure, or fail to fill
one leg after another has executed. Such a strategy would require a distinct
paper-tested execution and unwind design and would still not be a native
combo.

## Credentials

Create Polymarket US API credentials through the
[Polymarket US Developer portal](https://polymarket.us/developer). These are
the credentials used by the US SDK:

```text
POLYMARKET_KEY_ID
POLYMARKET_SECRET_KEY
```

Builder API credentials are not a substitute for US API credentials. If US
combo support is needed, ask Polymarket US support whether a private or partner
endpoint exists; do not infer support from international documentation.

## Sources checked

- [Polymarket US Developer portal](https://polymarket.us/developer)
- [Official Polymarket US Python SDK](https://github.com/Polymarket/polymarket-us-python)
- [International Combo documentation](https://help.polymarket.com/en/articles/15458600-what-are-combos)
