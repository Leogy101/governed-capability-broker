# Public alpha publication plan — execution not yet authorized

Apache-2.0, attribution to **2026 Brandon Gordon and GCB contributors**, and the clean public snapshot are approved. This document prepares executable commands; **do not run the publication commands until the owner separately authorizes repository creation, remote pushes and the prerelease.** No GitHub CI pass or public release is claimed by this preparation.

## Fixed destination and scope

- GitHub owner: `Leogy101`, verified authenticated account ID `279771671`.
- Repository: `Leogy101/governed-capability-broker`.
- Version: `0.3.2a1`; local annotated tag: `v0.3.2a1`, created only after local verification.
- Public branch: `main`, containing the reviewed fresh root snapshot.
- Artifacts: sibling `../artifacts` directory relative to the public checkout.
- Description:

> Harness-agnostic AI capability routing and governance. Selects qualified models, tools, and execution paths with policy-aware efficiency, cost controls, and verification. Pairs well with Knowledge Retrieval Engine (KRE).

The README accompanies that description: “qualified” means task-specific observed profiles, cost controls have the documented bounds, and native governed routing remains preview-only. No universal certification or guaranteed spending cap is claimed.

The private development repository and operational history stay private and unchanged. Do not push, mirror, bundle or copy that history. The public root is an allowlist export of the existing implementation, not a replacement implementation. Public `git blame` begins at this snapshot; the retained private archive and release provenance preserve the earlier audit trail without exposing it.

## Preconditions

Before authorizing execution, review the exact root commit, annotated tag, clean tree, export manifest, license files, local verification and checksums. The approved payload is:

- `governed-capability-broker-0.3.2a1-source.tar.gz`
- `governed_capability_broker-0.3.2a1.tar.gz`
- `governed_capability_broker-0.3.2a1-py3-none-any.whl`
- `release-provenance.json`
- `SHA256SUMS`

The provenance must bind the reviewed public commit and artifact hashes. Artifacts and checksums must be regenerated and reverified if source, packaging or attribution changes. No private test logs, account snapshots or operational records belong in the payload.

## Commands after separate publication approval

Run this Bash sequence from the reviewed **public** checkout. It verifies the authenticated account again, requires the locally verified root/tag, creates an empty repository **without pushing**, enables private vulnerability reporting, and pushes only `main` first. Any failed command stops the sequence. If a repository or remote unexpectedly already exists, stop and inspect; do not delete, overwrite or reuse it automatically.

```bash
set -euo pipefail

gcb_repo='Leogy101/governed-capability-broker'
gcb_tag='v0.3.2a1'
gcb_artifacts="$(cd ../artifacts && pwd)"
gcb_commit="$(git rev-parse HEAD)"

test "$(gh api user --jq .login)" = 'Leogy101'
test "$(gh api user --jq .id)" = '279771671'
test "$(git branch --show-current)" = 'main'
test -z "$(git status --porcelain)"
test -z "$(git remote)"
test "$(git rev-list --all --count)" = '1'
test "$(git cat-file -t "refs/tags/$gcb_tag")" = 'tag'
test "$(git rev-parse "$gcb_tag^{commit}")" = "$gcb_commit"

(cd "$gcb_artifacts" && shasum -a 256 -c SHA256SUMS)
python3 - "$gcb_artifacts" "$gcb_commit" "$gcb_tag" <<'PY'
from pathlib import Path
import json, sys
folder = Path(sys.argv[1])
expected = {
    'governed-capability-broker-0.3.2a1-source.tar.gz',
    'governed_capability_broker-0.3.2a1.tar.gz',
    'governed_capability_broker-0.3.2a1-py3-none-any.whl',
    'release-provenance.json',
}
listed = {line.split(maxsplit=1)[1].lstrip('*')
          for line in (folder / 'SHA256SUMS').read_text().splitlines() if line.strip()}
assert listed == expected, 'Unexpected or unbound release payload'
assert all((folder / name).is_file() for name in expected)
provenance = json.loads((folder / 'release-provenance.json').read_text())
assert provenance['public_commit'] == sys.argv[2], 'Artifact commit differs from reviewed checkout'
assert provenance['tag'] == sys.argv[3], 'Artifact tag differs from reviewed tag'
assert provenance['version'] == '0.3.2a1'
PY

gh repo create "$gcb_repo" --public --description \
  'Harness-agnostic AI capability routing and governance. Selects qualified models, tools, and execution paths with policy-aware efficiency, cost controls, and verification. Pairs well with Knowledge Retrieval Engine (KRE).'
gh api --method PUT "repos/$gcb_repo/private-vulnerability-reporting"
test "$(gh api "repos/$gcb_repo/private-vulnerability-reporting" --jq .enabled)" = 'true'
git remote add origin "https://github.com/$gcb_repo.git"
git -c push.followTags=false push --set-upstream origin refs/heads/main:refs/heads/main
```

The prepared workflow has eight Python/OS jobs and one bridge job. The next function waits for the actual push-triggered run for the exact commit and ref, then requires a successful workflow and all nine jobs. A missing run, failure, cancellation, skipped job, authentication error or timeout stops release preparation. Do not count local checks or a different commit's run as this result; do not rerun failed jobs automatically to obtain a green status.

```bash
gcb_verify_ci() {
  local gcb_ref="$1"
  local gcb_run=''
  local gcb_wait
  for gcb_wait in {1..30}; do
    gcb_run="$(gh run list --repo "$gcb_repo" --workflow ci.yml \
      --event push --branch "$gcb_ref" --commit "$gcb_commit" --limit 1 \
      --json databaseId --jq '.[0].databaseId // empty')"
    test -z "$gcb_run" || break
    sleep 2
  done
  test -n "$gcb_run"
  gh run watch "$gcb_run" --repo "$gcb_repo" --interval 5 --exit-status
  test "$(gh run view "$gcb_run" --repo "$gcb_repo" --json headSha --jq .headSha)" = "$gcb_commit"
  test "$(gh run view "$gcb_run" --repo "$gcb_repo" --json conclusion --jq .conclusion)" = 'success'
  test "$(gh run view "$gcb_run" --repo "$gcb_repo" --json jobs --jq '.jobs | length')" = '9'
  test "$(gh run view "$gcb_run" --repo "$gcb_repo" --json jobs \
    --jq '[.jobs[] | select(.conclusion != "success")] | length')" = '0'
  gh run view "$gcb_run" --repo "$gcb_repo" --json url --jq .url
}

gcb_verify_ci main
test "$(git rev-parse HEAD)" = "$gcb_commit"
test -z "$(git status --porcelain)"
git -c push.followTags=false push origin "refs/tags/$gcb_tag:refs/tags/$gcb_tag"
gcb_verify_ci "$gcb_tag"
```

Only after both actual CI checks pass, recheck the reviewed artifact bytes and create the prerelease using the already pushed tag. `--verify-tag` prevents GitHub from inventing a tag on a different commit. The named assets below deliberately avoid wildcard uploads.

```bash
(cd "$gcb_artifacts" && shasum -a 256 -c SHA256SUMS)
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

Record the public commit/tag, CI run URLs and downloaded-asset verification. If upload or verification fails, stop and report the actual remote state; do not silently delete/recreate the repository, tag or release. The temporary download directory may be removed after review.

This plan does not authorize PyPI publication, other package registries, provider inference, harness changes or any additional implementation. GitHub CI has not run during local preparation. Remote creation, private-reporting enablement, pushes and release commands above remain unexecuted until separately approved.
