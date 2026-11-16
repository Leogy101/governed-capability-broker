# Contributing

GCB is licensed under [Apache-2.0](LICENSE). Copyright 2026 Brandon Gordon and GCB contributors; see [NOTICE](NOTICE). Submit only work you have the right to contribute under these terms, identify compatible third-party material and preserve its notices. No contributor-agreement service is required.

Keep changes small. Preserve independent installation, research before inference, native reasoning identity, descriptive bios, usage provenance and no uncertain replay. Avoid API-key requirements, silent routing, automatic downloads, unbounded retries and universal certification grades.

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
python -m unittest discover -s tests -v
node --test integrations/openclaw-host/*.test.mjs
python examples/public/recompute.py
```

The public tree includes core, adapter, authorization, negative-path and interoperability tests. Private operator tests stay with their undistributed tools. Tests must not call real providers, read live agent settings or require KRE. Source-only testing uses `PYTHONPATH="$PWD/src"`; a relative path does not survive subprocess directory changes.

Explain changed behavior, scope, tests and limitations. Use synthetic reproductions. Version fixture/scorer changes; never silently recompute historical scores. New live evaluations require bounded authorization and complete usage accounting. Keep public examples free of accounts, credentials and private paths. For vulnerabilities see [SECURITY.md](SECURITY.md).
