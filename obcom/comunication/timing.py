"""Timing constants of the freshness contract — one place, one sentence each.

The temporal model (Staleness Contract): **T0** ``delay`` — a subscriber is not woken more often
than this; **T1** ``time_of_data_tolerance`` — a value fresher than this is served from the cache,
an older one is refreshed first; **T2** ``time_of_data_max_age`` — a value older than this is no
truth any more; **W** — the long-poll window, how long the server may hold a subscription's request
before it renews it with 4004.
"""

#: T1 when neither the call nor the client's config gives one, local cache and wire alike; T0 defaults to T1
DEFAULT_REFRESH_AFTER: float = 0.5

#: the worst client ↔ server round trip (LAN, Tailscale, a scheduler hiccup) the defaults are designed for
RTT_BUDGET: float = 1.0

#: the server answers a long poll this fraction of its window before the window ends
SERVER_MARGIN_FRACTION: float = 0.2

#: the default T2 is never below this: with W = T2 the renewal, sent at (1 − margin)·W, must land before T2
T2_FLOOR: float = RTT_BUDGET / SERVER_MARGIN_FRACTION

#: W of a subscription that declares no value policy (a declaring one has W = T2)
UNDECLARED_WINDOW: float = 30.0

#: W is never shorter than this: no renewal storm, and a window is left after a T0 pause
WINDOW_FLOOR: float = 1.0

#: a 4004 renewal proves contact only when it took at least this fraction of its window to arrive
RENEWAL_CREDIBLE_FRACTION: float = 0.5

#: a timeout waking later than max(FLOOR, FRACTION·W) past its deadline was our own loop frozen, not the router
STARVATION_LAG_FLOOR: float = 0.5
STARVATION_LAG_FRACTION: float = 0.1

#: sleep after an unclassified exception in the poll loop of a retry-forever identity
CATCH_ALL_RETRY_DELAY: float = 60.0

#: router silences in a row before a STOP identity gives up its subscription
DEFAULT_MAX_MISSED_MSG: int = 3

#: a single GET/PUT without an explicit deadline expires after this
SINGLE_REQUEST_TIMEOUT: float = 30.0

#: the shortest period of a periodic cycle query (device protection)
PERIODIC_MIN_DELAY: float = 0.5
