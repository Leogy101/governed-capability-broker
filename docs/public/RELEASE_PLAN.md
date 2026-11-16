# Public alpha publication and verification record

Publication of `Leogy101/governed-capability-broker` and the `0.3.2a1` GitHub prerelease is authorized. Apache-2.0 and attribution to **2026 Brandon Gordon and GCB contributors** are adopted. The source is public; release assets must satisfy the verification gates below. This scope does not include PyPI, another registry, provider inference or harness changes.

## Completed bootstrap

- The [public repository](https://github.com/Leogy101/governed-capability-broker) was created for the verified owner `Leogy101` without importing private Git history.
- The approved fresh root `4ee9ba7` was pushed to `main`. Original private evidence and development history remain unchanged and private.
- [Private vulnerability reporting](https://github.com/Leogy101/governed-capability-broker/security/advisories/new) was enabled and verified before source publication.
- All nine initial-root [offline CI jobs](https://github.com/Leogy101/governed-capability-broker/actions/runs/43859684029) passed. [CodeQL](https://github.com/Leogy101/governed-capability-broker/actions/runs/43859767757) and [dependency graph](https://github.com/Leogy101/governed-capability-broker/actions/runs/43859776904) also succeeded. Exact platform versions and offline/live boundaries are in [VERIFICATION.md](VERIFICATION.md).

The repository description remains:

> Harness-agnostic AI capability routing and governance. Selects qualified models, tools, and execution paths with policy-aware efficiency, cost controls, and verification. Pairs well with Knowledge Retrieval Engine (KRE).

The README qualifies that wording: task suitability is based on observed profiles, control guarantees have documented limits, and native governed routing remains preview-only. No universal certification or guaranteed spending cap is claimed.

The public snapshot is an export of the existing implementation, not a replacement implementation. Public `git blame` starts at the fresh root. Provenance hashes and the retained private archive preserve the earlier audit trail without exposing private chronology. Never push, mirror, bundle or copy the private repository's Git history.

## Final release gates

Later documentation or asset corrections must pass checks at their own exact commit; the initial-root CI runs do not validate a later untested commit. Before the first prerelease:

1. Verify a clean public `main`, retained root ancestry, reviewed license/export manifest, and final artifact/provenance hashes.
2. Push only the reviewed `main` update. Require successful offline CI and CodeQL for its exact SHA; a failure, cancellation, missing run or skipped required job stops publication. No automatic retries to manufacture a green result.
3. Verify the local annotated `v0.3.2a1` tag identifies that tested SHA. Push that tag only after the main checks pass, then require the tag-triggered offline workflow to pass.
4. Create the GitHub prerelease from the existing verified tag and upload only the named reviewed payload. Download the assets and verify their checksums before reporting completion.

Artifact directory: `../artifacts`, relative to the public checkout. Approved payload:

- `governed-capability-broker-0.3.2a1-source.tar.gz`
- `governed_capability_broker-0.3.2a1.tar.gz`
- `governed_capability_broker-0.3.2a1-py3-none-any.whl`
- `release-provenance.json`
- `SHA256SUMS`

The provenance binds source and tested artifacts. Regenerate and reverify it when the release source or packaging changes. Do not attach private test logs or operational records. The original bootstrap is complete: do not recreate the repository, add a replacement remote or repeat account setup.

## Remaining publication commands

These commands operate only on the reviewed public checkout and the first authorized alpha release. Existing release/tag state must be inspected before any repetition; no force-push, deletion or replacement is included.

```bash
set -euo pipefail
gcb_repo='Leogy101/governed-capability-broker'
gcb_tag='v0.3.2a1'
gcb_commit="$(git rev-parse HEAD)"
gcb_artifacts="$(cd ../artifacts && pwd)"
test "$(gh api user --jq .login)" = 'Leogy101'
test "$(gh api user --jq .id)" = '279771671'
test "$(git branch --show-current)" = 'main'
test -z "$(git status --porcelain)"
git merge-base --is-ancestor 4ee9ba7 "$gcb_commit"
test "$(gh api "repos/$gcb_repo/private-vulnerability-reporting" --jq .enabled)" = 'true'
gcb_verify_artifacts() {
  (cd "$gcb_artifacts" && shasum -a 256 -c SHA256SUMS)
  python3 - "$gcb_artifacts" "$gcb_commit" "$gcb_tag" <<'PY'
from pathlib import Path
import hashlib, json, sys
folder = Path(sys.argv[1])
artifacts = {
    'governed-capability-broker-0.3.2a1-source.tar.gz',
    'governed_capability_broker-0.3.2a1.tar.gz',
    'governed_capability_broker-0.3.2a1-py3-none-any.whl',
}
lines = (folder / 'SHA256SUMS').read_text().splitlines()
listed = {line.split(maxsplit=1)[1].lstrip('*') for line in lines if line.strip()}
assert len(lines) == 4 and listed == artifacts | {'release-provenance.json'}
provenance = json.loads((folder / 'release-provenance.json').read_text())
assert provenance['public_commit'] == sys.argv[2], 'Artifacts belong to a different commit'
assert provenance['tag'] == sys.argv[3] and provenance['version'] == '0.3.2a1'
assert set(provenance['artifacts']) == artifacts
for name in artifacts:
    assert hashlib.sha256((folder / name).read_bytes()).hexdigest() == provenance['artifacts'][name]['sha256']
PY
}
git -c push.followTags=false push origin refs/heads/main:refs/heads/main

gcb_require_ci() {
  local gcb_workflow="$1" gcb_ref="$2" gcb_run='' gcb_wait
  local gcb_event=()
  if test "$gcb_workflow" = 'ci.yml'; then gcb_event=(--event push); fi
  for gcb_wait in {1..30}; do
    gcb_run="$(gh run list --repo "$gcb_repo" --workflow "$gcb_workflow" \
      "${gcb_event[@]}" --branch "$gcb_ref" --commit "$gcb_commit" --limit 1 \
      --json databaseId --jq '.[0].databaseId // empty')"
    test -z "$gcb_run" || break
    sleep 2
  done
  test -n "$gcb_run"
  gh run watch "$gcb_run" --repo "$gcb_repo" --interval 5 --exit-status
  test "$(gh run view "$gcb_run" --repo "$gcb_repo" --json headSha --jq .headSha)" = "$gcb_commit"
  test "$(gh run view "$gcb_run" --repo "$gcb_repo" --json conclusion --jq .conclusion)" = 'success'
  if test "$gcb_workflow" = 'ci.yml'; then
    test "$(gh run view "$gcb_run" --repo "$gcb_repo" --json jobs --jq '.jobs | length')" = '9'
    test "$(gh run view "$gcb_run" --repo "$gcb_repo" --json jobs \
      --jq '[.jobs[] | select(.conclusion != "success")] | length')" = '0'
  else
    # GitHub-managed CodeQL uses dynamic events. All three analyses must pass;
    # its conditional configuration-maintenance job may legitimately be skipped.
    test "$(gh run view "$gcb_run" --repo "$gcb_repo" --json jobs \
      --jq '[.jobs[] | select(.name | startswith("Analyze ("))] | length')" = '3'
    test "$(gh run view "$gcb_run" --repo "$gcb_repo" --json jobs \
      --jq '[.jobs[] | select(.conclusion != "success" and (.name != "Adjust Configuration" or .conclusion != "skipped"))] | length')" = '0'
  fi
  gh run view "$gcb_run" --repo "$gcb_repo" --json url --jq .url
}
gcb_require_ci ci.yml main
gcb_require_ci CodeQL main
test "$(git rev-parse HEAD)" = "$gcb_commit"
test -z "$(git status --porcelain)"
test "$(git cat-file -t "refs/tags/$gcb_tag")" = 'tag'
test "$(git rev-parse "$gcb_tag^{commit}")" = "$gcb_commit"
gcb_verify_artifacts
git -c push.followTags=false push origin "refs/tags/$gcb_tag:refs/tags/$gcb_tag"
gcb_require_ci ci.yml "$gcb_tag"

gcb_verify_artifacts
gh release create "$gcb_tag" --repo "$gcb_repo" --verify-tag \
  --prerelease --latest=false --title 'Governed Capability Broker 0.3.2a1' \
  --notes-file docs/public/RELEASE_NOTES.md \
  "$gcb_artifacts/governed-capability-broker-0.3.2a1-source.tar.gz" \
  "$gcb_artifacts/governed_capability_broker-0.3.2a1.tar.gz" \
  "$gcb_artifacts/governed_capability_broker-0.3.2a1-py3-none-any.whl" \
  "$gcb_artifacts/release-provenance.json" \
  "$gcb_artifacts/SHA256SUMS"
gcb_download="$(mktemp -d "${TMPDIR:-/tmp}/gcb-release-check.XXXXXX")"
gh release download "$gcb_tag" --repo "$gcb_repo" --dir "$gcb_download"
(cd "$gcb_download" && shasum -a 256 -c SHA256SUMS)
gh release view "$gcb_tag" --repo "$gcb_repo"
```

Record final commit/tag, actual CI run URLs and downloaded-asset checks. Stop and report if upload or verification fails; do not silently delete or recreate published state. No provider benchmark, production configuration change or additional development phase follows this publication procedure.
