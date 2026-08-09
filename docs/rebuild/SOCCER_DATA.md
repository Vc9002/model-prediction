# Soccer data rights and research boundary

The soccer foundation is collection-only and research/shadow-only. Public
reachability, a free API tier, or an open-source Python client does not grant
the project commercial rights to the underlying sports data.

| Source | Current rights result | Required conditions | Production/economic use |
|---|---|---|---:|
| SportsDataverse-catalogued ESPN Site v2 | unresolved | source terms must be affirmatively cleared | blocked |
| football-data.org API v4 | unresolved | active subscription, one-application scope, visible `Data provided by football-data.org` attribution, and affirmative economic-rights review | blocked |
| StatsBomb Open Data | prohibited | agreement prohibits commercial exploitation of data and derived analysis | policy-blocked; no collection |

ESPN and football-data.org responses may still be captured for isolated
research and future prospective evidence. Every raw observation manifest,
normalized row, dataset manifest, and audit preserves:

- source asset and provider chain;
- license/terms identity and URL;
- attribution and subscription obligations;
- upstream-rights and commercial-use status;
- use scope and `production_allowed`;
- content hash and observation/retrieval timestamps.

Missing, null, empty, or unknown rights metadata fails closed. Normalized rows
must remain `use_scope = research_shadow_only`,
`commercial_use_status = unresolved`, and `production_allowed = false`.
Production or economic-use assertions require affirmative `cleared` upstream
and commercial rights plus `production_allowed = true`; none of the configured
soccer sources currently meets that gate.

The foundation also remains point-in-time conservative. Historical captures
use `availability_basis = capture_time_only`: a provider's event date or update
field does not prove that this repository possessed the row at an earlier
decision horizon. Soccer model stages remain disabled until a draw-aware 1X2
model and replay-safe PIT feature set exist.
