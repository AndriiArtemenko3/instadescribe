# Licensing InstaDescribe

InstaDescribe uses a deliberate split license. The product core is
source-available; the SDK, CLI and investigation baseline are open source.

| Repository area | License now | Future change |
|---|---|---|
| `App/**` except the shadcn/ui-derived files listed below, plus `services/**`, `modular_pipeline/**`, `packages/contracts/**`, `migrations/**`, `infrastructure/**`, root scripts/configuration, generated OpenAPI and project documentation | [Business Source License 1.1](./LICENSE) | Apache License 2.0 on `2030-08-29`, or the fourth anniversary of the first public distribution of this version under BUSL-1.1, whichever is earlier |
| `App/components.json` and the 13 shadcn/ui-derived component files enumerated in [Third-party notices](./THIRD_PARTY_NOTICES.md#shadcnui-component-sources) | shadcn/ui MIT | No change |
| `packages/sdk/**` | [MIT](./packages/sdk/LICENSE) | No change |
| `packages/cli/**` | [MIT](./packages/cli/LICENSE) | No change |
| `packages/investigation-core/**` | [Apache License 2.0](./packages/investigation-core/LICENSE) | No change |
| Third-party software, media, fonts and model assets | Their respective licenses | Determined by their licensors |

The nested MIT licenses govern the SDK and CLI package directories. The nested
Apache License 2.0 governs the autonomous investigation baseline in
`packages/investigation-core/`. Those nested licenses govern every file inside their
respective package unless a file carries a more specific notice. The root BUSL-1.1
license governs the remaining InstaDescribe-authored material in this version.

## What BUSL-1.1 means for the core

The core may be inspected, copied, modified, redistributed and used for
non-production purposes under BUSL-1.1. The `Additional Use Grant` is `None`, so
production use before the Change Date requires a separate commercial license.
BUSL-1.1 is a source-available license, not an Open Source Initiative-approved
open-source license.

The SDK and CLI are separately licensed under MIT so organizations can write and
distribute integrations without inheriting the core license.

The investigation baseline is separately licensed under Apache License 2.0. It
contains dependency-light contracts, transparent baseline belief fusion, offline
media inspection hooks, trace interchange and evaluation utilities. It is designed
to run independently and does not grant rights to BUSL-licensed application code,
production collectors, private datasets, visual indexes or enterprise deployment.

## Historical releases

Versions that were already distributed under MIT remain available under the MIT
terms that accompanied those versions. This license change applies to the
BUSL-licensed version identified in the root [LICENSE](./LICENSE) and does not
withdraw rights previously granted for earlier versions.

## Commercial use

See [COMMERCIAL_LICENSE.md](./COMMERCIAL_LICENSE.md) for the contact route. That
document is informational only; it does not itself grant production or commercial
rights.

## Third-party material

See [THIRD_PARTY_NOTICES.md](./THIRD_PARTY_NOTICES.md). Third-party terms override
this repository's licenses for the material to which they apply.

This summary is provided for clarity and is not legal advice. If this summary and
an applicable license text conflict, the license text controls.
