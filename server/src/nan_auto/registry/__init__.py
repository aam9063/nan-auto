"""The model registry: live catalog merged with the local card, plus state.

- `catalog`: fetch and parse `/v1/models`, load `config/models.yaml`, merge.
- `state`: per-model health/quota and the `Registry` aggregate with TTL refresh.
- `router`: the read-only `GET /v1/registry` endpoint (no secrets).
"""
