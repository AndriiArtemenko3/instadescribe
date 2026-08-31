# Public snapshot policy

This repository is prepared as a curated public snapshot of InstaDescribe. It is not
a mirror of the private development repository or its Git history.

## History boundary

The public delivery branch must start from the existing public default branch and
contain only reviewed public-release commits. Private development commits, private
tags and private remote ancestry must not be pushed, mirrored or made reachable from
the public branch.

Tree transfer uses an explicit path manifest and reviewed diffs. Broad history or tag
pushes are prohibited. This preserves the license boundary: earlier public MIT
versions remain MIT, while the imported core snapshot is first introduced under the
root BUSL-1.1 license.

## Included public evidence

- product source, tests and deterministic fixtures required to review the beta;
- current architecture and architecture-evolution documents;
- ADRs that explain durable decisions;
- sanitized Cloud Core v0.1 release evidence;
- beta runbooks that contain no credentials, account identifiers or private URLs;
- MIT SDK/CLI packages and their nested license files;
- the autonomous Apache-2.0 investigation baseline, its tests and nested license;
- the tenant-scoped investigation domain, Browser API, dedicated worker path and
  deterministic no-model acceptance fixtures;
- public security, contribution and licensing policies.

## Excluded material

- `docs/cloud-migration/**` internal gate-by-gate logs;
- internal implementation handoffs and tranche/progress reports;
- local repository audits, local filesystem paths and sibling-worktree inventories;
- private repository, branch, release or vault locations;
- legacy screenshots containing operator identity, validation titles or real job
  history;
- credentials, signed URLs, account identifiers, customer data or real job history;
- `.env` files, Terraform state/plan files, generated dependency/build directories
  and unreviewed media;
- private Git commits and tags.

These exclusions remove material from the public candidate tree only. They do not
rewrite or delete private Git history.

## Claim boundary

The API-first audio-description beta may be described as **implemented and locally
verified**. The video-investigation foundation may be described as implemented and
verified by a **deterministic, no-model fixture**. It must not be described as an
investigation workspace, validated live model, connected retrieval, persisted
replay, benchmark or deployed service. The public AWS environment is historical
Cloud Core v0.1 evidence, not evidence that either current product line is deployed.
Customer beta and npm publication remain pending separate approval and evidence.

## Pre-push gate

Before any public push, verify the candidate branch with:

1. ancestry and tag reachability checks against the public base;
2. secret and high-entropy scanning over the complete reachable history;
3. scans for local paths, private repository/vault URLs, PII and state files;
4. license-scope checks, Apache investigation-core distribution verification and
   clean SDK/CLI package tarballs containing MIT licenses;
5. dependency audits and the repository's full test/build/typecheck matrix;
6. a reviewed diff showing only the intended public snapshot.
