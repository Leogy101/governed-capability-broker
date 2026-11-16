# Alpha verification status

The source implementation at `95174ac` passed the full internal Python suite: **362 tests on each of Python 3.11.15, 3.13.1 and 3.14.6**, running on macOS arm64. The Node 26.10.0 bridge suite passed **151 tests**. These tests use fake backends and synthetic data, not paid/subscription inference.

The public tree retains **247 core/integration Python tests**, including adapters, unknown-completion protections, authorization denial, native configuration identity, usage accounting, citation/context interoperability and negative paths. The remaining 115 internal tests cover private operator tools that are not distributed; they remain in the private repository. Eight additional release-export tests check explicit allowlisting, no overwrite, path traversal, Git metadata exclusion, symlinks, missing files and adopted-license provenance.

Clean wheel installation, `pip check`, offline demos, Gate 0, standalone imports and uninstall passed without KRE on the three installed Python versions. An installed CLI workflow against a fake loopback Ollama service verified metadata before chat, exact native settings, bounded planning, unauthorized-call rejection, partial observations, cancellation/resumption, bios, evidence, routing and completed-cell reuse. Fake HTTP output is not presented as genuine model performance.

The licensed public staging snapshot passed all 247 public Python tests and eight release-export tests on the same three interpreters, plus all 151 Node bridge tests. These tests ran independently of the private development repository. Licensed wheel installation and reconstructed sdist checks passed. A fresh clone of the public repository also passed the README workflow, 247 Python tests, eight release-export tests and 151 bridge tests on macOS 26.3.1 with Python 3.14.6. The release bundle's separate provenance and checksum records identify the exact tested commit and artifacts without circularly embedding their own hashes in the source. Source distributions include public fixtures, the context contract, optional bridge and tests; private operator tests are excluded.

The installed CLI successfully researched an existing local `qwen3.5:9b` deployment through Ollama 0.40.15 using only `/api/version`, `/api/tags` and `/api/show`. It preserved the installed digest, GGUF/Q4_K_M format and boolean thinking settings/default. No chat, load, unload or inference request was made. Live profiling functionality is supported by the separately retained experiments, not relabeled fake transport results.

Portable host configuration/snapshot templates were checked using fake APIs: metadata research, planning and authorization preflight made zero host completions. The documented deterministic extension example also passed offline. No live Gateway or subscription call was performed during release preparation.

## Completed GitHub verification

The initial public root `4ee9ba7` passed all **nine jobs** in [Offline alpha verification](https://github.com/Leogy101/governed-capability-broker/actions/runs/43859684029): eight Python/OS matrix jobs and the separate Node bridge job. Each Python job ran the public regression and release-export tests, public evidence arithmetic, package builds, fresh wheel installation, offline demonstration and uninstall. No live provider inference ran.

| CI runner | Python versions that passed | Execution scope |
|---|---|---|
| Ubuntu 26.04.1, x64 | 3.11.18, 3.12.16, 3.13.18, 3.14.10 | Offline core/profiler and fake adapter paths, package build/install/demo/uninstall |
| macOS 26.7.2 (25G332), arm64 | 3.11.9, 3.12.10, 3.13.17, 3.14.10 | Same offline matrix |

CI used Node 26.13.1. The separate bridge job passed all 151 tests. [CodeQL](https://github.com/Leogy101/governed-capability-broker/actions/runs/43859767757) and [dependency graph](https://github.com/Leogy101/governed-capability-broker/actions/runs/43859776904) workflows also completed successfully. No open CodeQL or secret-scanning alerts were observed at this review; scanning is not proof that every vulnerability or secret format is detectable.

Python 3.12 was unavailable on the local development machine but is now covered by the completed CI matrix. **Linux live Ollama/Codex profiling has not been measured**; passing fake transports and offline execution does not establish every production provider path. Other architectures and Windows execution remain unverified. Windows profiling intentionally fails closed. Later release commits must pass their own CI and artifact checks; the linked initial-root runs are not evidence for an untested future commit.

An initial internal source-tree test command used relative `PYTHONPATH=src`; two subprocess imports failed after changing working directory. The same complete suite passed using an absolute source path. Installed-wheel tests need no PYTHONPATH. The public instructions install the package before testing.
