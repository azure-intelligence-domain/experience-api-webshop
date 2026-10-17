# CLAUDE.md

The [Channel] Webshop Experience API. Owned by the channel team. Ground rules live in the
[ai-intelligence-domain](https://github.com/azure-intelligence-domain/ai-intelligence-domain)
repository (ADR 0004 API-led, ADR 0014 process layer, ADR 0017 experience layer).

- An Experience API shapes for its channel and decides nothing: the order domain decides. Never
  call a Function directly; only the gateway's Process and System APIs, as the runtime identity.
- `kit.py` is the same in every Experience API repository: change it everywhere or nowhere.
- Contract first: `openapi.yaml` changes before the code, with a higher `info.version`.
- Tests: `uv run --no-project --python 3.12 --with-requirements requirements.txt --with pytest pytest -q`
