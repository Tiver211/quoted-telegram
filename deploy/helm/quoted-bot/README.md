# quoted-bot Helm chart

This chart runs one Telegram bot replica using long polling. It creates no
database or credentials. The token and database URL must be provided in an
existing Kubernetes Secret. Running multiple replicas can cause competing
pollers; this chart enforces `replicaCount: 1`.

## Prerequisites

- Kubernetes 1.25 or newer and Helm 3
- A published bot image, with the repository and immutable tag set at install
- An existing Secret containing the bot token and database URL
- For SQLite, a cluster StorageClass that supports a persistent volume

Create or update the Secret using your normal secret management process. For a
manual setup, the expected keys can be created with:

```sh
kubectl create secret generic quoted-bot \
  --from-literal=bot-token='<telegram-token>' \
  --from-literal=database-url='<database-url>'
```

Avoid putting credentials in shell history or source control. For PostgreSQL,
the application should receive an asyncpg-compatible URL through
`database-url`. For SQLite, use a file URL whose path is under `/data`, such as
`sqlite+aiosqlite:////data/quotes.db`, and enable the PVC below.

## Install

```sh
helm upgrade --install quotes ./deploy/helm/quoted-bot \
  --namespace quotes --create-namespace \
  --set image.repository=ghcr.io/your-org/quoted-bot \
  --set image.tag=0.1.0 \
  --set-string stagingChatId=-1001234567890
```

The defaults reference Secret `quoted-bot`, with keys `bot-token` and
`database-url`. To use a Secret with another name or key, provide overrides:

```yaml
secrets:
  botToken:
    name: production-telegram
    key: token
  databaseUrl:
    name: production-database
    key: url
```

For SQLite persistence, use a values file such as:

```yaml
persistence:
  enabled: true
  size: 5Gi
  storageClassName: standard
```

The PVC is mounted at `/data`. Check that its access mode and reclaim policy
match your cluster's recovery requirements. For PostgreSQL, leave the PVC
disabled and manage backups, access controls, TLS, and database availability
through your database service.

`stagingChatId` is required: use a private service group/channel where the bot can
send and delete stickers. Guest delivery stages a sticker there to obtain its
Telegram file ID, answers the guest query, then removes the staging message.
With persistence disabled, `/data` is an `emptyDir` for render scratch files;
SQLite on that volume would be lost on pod replacement, so enable persistence
when using SQLite. `/tmp` is also writable despite the read-only root filesystem.

## Operations

### Telegram proxy

Optionally store the complete HTTP CONNECT/SOCKS4/SOCKS5 URL in an existing
Secret, then configure:

```yaml
proxy:
  secretName: telegram-proxy
  secretKey: proxy-url
```

The referenced key can contain `socks5://user:password@proxy.example:1080` or
`http://proxy.example:3128`. URL-encode special characters in credentials.
Omit `proxy.secretName` for direct connections. This proxy applies to Telegram
API requests and media transfers, not PostgreSQL.

### Monitoring and updates

- Set `image.tag` to an immutable release version or digest for repeatable
  rollouts. Use `imagePullSecrets` for private registries.
- `helm upgrade` updates the Deployment. `helm rollback` restores a prior
  release revision; it does not roll back database schema or data changes.
- Inspect rollout health with `kubectl rollout status deployment/<release>-quoted-bot -n quotes`
  and logs with `kubectl logs deployment/<release>-quoted-bot -n quotes`.
- The chart intentionally has no Service, ingress, or HTTP probes: the bot
  initiates Telegram long polling and exposes no inbound listener.
- Monitor restarts, memory use, Telegram/API errors, and persistent volume
  capacity. Configure cluster-level alerting and backup policies for the
  selected database.

## Configuration reference

| Value | Default | Purpose |
| --- | --- | --- |
| `replicaCount` | `1` | Bot replicas; keep at one for long polling |
| `stagingChatId` | required | Private staging group/channel ID |
| `renderTimeout` | `30` | Render deadline in seconds |
| `proxy.secretName` / `secretKey` | empty / `proxy-url` | Optional Secret reference for `PROXY_URL` |
| `image.repository` | `ghcr.io/example/quoted-bot` | Container image |
| `image.tag` | chart app version | Image tag |
| `secrets.botToken.name` / `key` | `quoted-bot` / `bot-token` | Existing Secret reference for `BOT_TOKEN` |
| `secrets.databaseUrl.name` / `key` | `quoted-bot` / `database-url` | Existing Secret reference for `DATABASE_URL` |
| `persistence.enabled` | `false` | Create and mount a SQLite data PVC |
| `persistence.mountPath` | `/data` | PVC mount directory |
| `resources` | see `values.yaml` | Container resource requests and limits |

See `values.yaml` for image pull secrets, pod security settings, scheduling,
and PVC options.
