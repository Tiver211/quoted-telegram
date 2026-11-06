# Quoted Bot

A Telegram bot that renders messages and short conversations as static WebP
stickers. It supports group commands, forwarded messages in private chats, and
Guest Chat Mode for quoting a message without adding the bot to the chat.

Implemented in Python 3.12+ with aiogram 3, asynchronous SQLAlchemy, Pillow, and
uv. SQLite and PostgreSQL are supported. The application uses a database-backed
job queue and runs as a single instance.

## Features

- Quote an individual message with `/q`.
- Quote consecutive messages with `/q N`.
- Assemble a selection of messages with `/qadd` and `/qmake`.
- Process forwarded messages and manually entered text in private chats.
- Collect multiple private messages with `/collect`.
- Respond to guest mentions with a quote sticker.
- Render author names, available avatars, basic formatting, emoji, photos,
  static stickers, and attachment cards.
- Handle classic message text, external replies, and Telegram `rich_message` text.
- Use an optional HTTP CONNECT, SOCKS4, or SOCKS5 proxy for Telegram requests.

A quote contains up to 10 messages. The renderer adjusts the font size within
readability limits and rejects content that cannot fit within a sticker.

## Quick start

### Docker Compose

Requirements: Docker Engine and Docker Compose v2. The supplied Compose
configuration uses host networking and is intended for local Linux deployments.

Create the configuration file:

```bash
cp .env.example .env
```

Set the required values:

```dotenv
BOT_TOKEN=your_botfather_token
STAGING_CHAT_ID=-1001234567890
# Optional:
PROXY_URL=socks5://127.0.0.1:1080
```

Configure the bot in Telegram as described below, then start the service:

```bash
docker compose up -d --build bot
docker compose logs -f bot
```

Compose sets the container paths for SQLite and temporary files. The database
is stored in the `bot-data` named volume and survives container replacement.

```bash
docker compose down       # Stop services and retain the database.
# docker compose down -v  # Also delete the database volume.
```

Run only one application instance per bot token. Stop any local process using
the same token before starting the container. The application uses long polling
and removes an existing webhook at startup without discarding pending updates.

### Local Python environment

Requirements: Python 3.12+ and [uv](https://docs.astral.sh/uv/). Noto fonts are
included in the package.

```bash
uv sync --frozen
cp .env.example .env
# Set BOT_TOKEN and STAGING_CHAT_ID in .env.
mkdir -p data
uv run python -m quoted_bot
```

Skip the copy step if `.env` already exists. For local execution, retain the
relative paths provided in `.env.example`:

```dotenv
DATABASE_URL=sqlite+aiosqlite:///./data/quoted.db
DATA_DIR=./data
```

## Telegram configuration

### Group commands

Add the bot to the group. To receive ordinary messages and support `/q N`,
either grant the bot administrator status or disable **Group Privacy** in
BotFather: `/setprivacy` → select the bot → Disable.

After disabling privacy mode, remove the bot from existing groups and add it
again for the change to take effect. The bot cannot retrieve messages sent
before it was added.

### Guest Chat Mode

Open the Mini App belonging to [BotFather](https://t.me/BotFather), select the
bot, and enable **Bot Settings → Mode Settings → Guest Chat Mode**.

To quote a message in another chat:

1. Select the message and choose Reply.
2. Enter `@your_bot_username` and send the message.
3. The bot responds with a sticker containing the selected message.

Guest Chat Mode does not require adding the bot to the chat. Each invocation
supports one source message and does not provide access to chat history.
Classic Inline Mode, which displays a panel of selectable results, is not
implemented and does not need to be enabled.

See the [Telegram Guest Mode documentation](https://core.telegram.org/bots/features#guest-bots).

### Staging chat

`STAGING_CHAT_ID` identifies a private service group or channel used by the
current guest response implementation. The bot uploads a rendered sticker to
this chat to obtain a Telegram `file_id`, responds to the guest query using
that identifier, and deletes the staging message after a successful response.

Create a separate private group or channel, add the bot, and allow it to send
stickers and delete its own messages. Set `STAGING_CHAT_ID` to its numeric ID.
Members of this chat may see uploaded quotes; restrict membership accordingly.

Ordinary command responses send the sticker directly to the destination chat.
The staging chat setting is nevertheless required at application startup.
Delivery through `uploadStickerFile` without a staging chat has not been
implemented or verified.

## Commands and workflows

| Command or action | Behavior |
| --- | --- |
| Reply with `/q` | Render the selected message. |
| Reply with `/q 3` | Render the selected message and the next three buffered messages. |
| Reply with `/qadd` | Add the selected message to a personal selection. |
| `/qmake` | Render the selection and clear it. |
| `/qclear` | Clear the selection and disable collection mode. |
| Forward a message privately | Render it using the available author information. |
| Send text privately | Render it under the sender's name. |
| `/collect` in a private chat | Collect messages until Create or Cancel is selected. |
| `/status` | Display the source build identifier, bot username, privacy mode, and group role. |
| `/start`, `/help` | Display usage instructions. |

In groups with multiple bots, explicitly address commands when necessary:
`/q@your_bot_username`.

**Consecutive messages:** reply to the first message with `/q 2` to produce a
three-message quote. Bot commands and service messages are excluded from the
sequence.

**Manual selection:** reply to each required message with `/qadd`, then send
`/qmake`. Messages are ordered chronologically. Duplicate additions are ignored.
Selections are isolated by user and chat.

**Multiple forwards:** send `/collect` in a private chat, forward the messages,
and select Create. Outside collection mode, each private message is processed
individually.

Bot responses and button labels currently use Russian. Translating this README
does not change the application interface.

## Configuration reference

Settings are loaded from environment variables and `.env`. The `.env` file is
excluded from Git and Docker image builds.

| Variable | Description | Default |
| --- | --- | --- |
| `BOT_TOKEN` | Telegram bot token. | Required |
| `STAGING_CHAT_ID` | Private service chat for guest sticker uploads. | Required |
| `DATABASE_URL` | Asynchronous SQLAlchemy connection URL. | `sqlite+aiosqlite:////data/quoted.db` |
| `DATA_DIR` | Temporary rendering file directory. | `/data` |
| `PROXY_URL` | Proxy for Telegram connections. | Direct connection |
| `RENDER_TIMEOUT` | Rendering deadline, in seconds. | `30` |
| `MAX_MEDIA_BYTES` | Maximum downloaded media file size, in bytes. | `10485760` |
| `LOG_LEVEL` | Logging level. | `INFO` |

### Database

SQLite for a local Python process:

```dotenv
DATABASE_URL=sqlite+aiosqlite:///./data/quoted.db
DATA_DIR=./data
```

PostgreSQL:

```dotenv
DATABASE_URL=postgresql+asyncpg://quoted:password@localhost:5432/quoted
```

The supplied Compose file explicitly sets a SQLite URL in the service's
`environment` section. To use PostgreSQL, change that value or provide a
Compose override. A PostgreSQL server is not included in the default stack.

### Proxy

HTTP CONNECT, SOCKS4, and SOCKS5 are supported, including authentication:

```dotenv
PROXY_URL=http://127.0.0.1:3128
# Alternatively:
PROXY_URL=socks5://user:password@127.0.0.1:1080
```

Percent-encode special characters in credentials, for example `@` as `%40`.
The proxy applies to Telegram API calls, polling, uploads, and downloads.
PostgreSQL connections do not use it.

The Compose `bot` service uses `network_mode: host`. On Linux, `localhost`
therefore refers to the host and can reach a proxy bound to loopback. Docker
Desktop requires host networking support to be enabled. In Kubernetes,
`127.0.0.1` refers to the Pod; Compose networking settings do not carry over.

## Testing

Run checks in the local environment:

```bash
uv run pytest -q
uv run ruff check src tests
uv run ruff format --check src tests
```

Run tests in a dedicated container:

```bash
docker compose --profile test run --build --rm --no-deps tests
```

The test service does not require a bot token or a running bot. It uses
temporary SQLite databases and a mocked Telegram transport, runs without
network access, and does not mount the bot's data volume. Building the image
still requires access to container and package registries.

Tests cover quoting workflows, external replies, rich text, selections, job
deduplication and recovery, proxy configuration, and WebP output constraints.
They do not replace integration testing against Telegram and PostgreSQL.

## Kubernetes deployment

A [Helm chart](deploy/helm/quoted-bot/README.md) is included:

```bash
helm lint deploy/helm/quoted-bot --set stagingChatId=-1001234567890
helm upgrade --install quoted ./deploy/helm/quoted-bot \
  --namespace quoted --create-namespace \
  --set image.repository=your-registry/quoted-bot \
  --set image.tag=0.1.0 \
  --set-string stagingChatId=-1001234567890
```

Before installation, build and publish the image and create the referenced
Secret containing the token and database URL. The chart supports external
Secrets, an optional SQLite PVC, and an optional Secret for the proxy URL.
It enforces a single application replica. No Service or ingress is required
because the bot receives updates through outbound long polling.

## Architecture

```text
src/quoted_bot/
├── domain/          # Quote models and interfaces; no Telegram or ORM dependencies
├── application/     # Selection, sequence, and job submission use cases
├── presentation/    # aiogram handlers and message/rich-text normalization
├── infrastructure/  # SQLAlchemy, job processing, Pillow, and Telegram HTTP session
├── assets/fonts/    # Bundled Noto fonts and OFL notices
├── config.py        # Environment settings
├── diagnostics.py   # Source build identifier
└── __main__.py      # Dependency composition and application lifecycle
```

Commands, forwarded messages, and guest requests are converted into a common
quote model. The renderer receives this model and local media files without
calling Telegram APIs. Pillow runs in a separate process with a rendering
deadline.

### Persistence

Asynchronous SQLAlchemy supports SQLite through aiosqlite and PostgreSQL
through asyncpg. Messages, selections, and jobs use the same database. Redis
and RabbitMQ are not required.

The initial schema is created with `create_all`. Existing schema upgrades are
not automated; subsequent schema changes require versioned migrations.

### Job processing

Jobs normally transition through `pending → rendering → delivering → done`.
Preparation and rendering failures produce `failed` jobs. Interrupted rendering
jobs return to the queue after a restart.

Interrupted or ambiguous delivery produces an `uncertain` job. Such jobs are
not retried automatically because Telegram delivery is not idempotent. A user
can submit a new command to retry the operation.

## Operational limits

- Quotes are limited to 10 messages and one static WebP sticker. Animation,
  sticker collection management, an appearance editor, and classic inline
  results are outside the current scope.
- `/q N` uses received messages only: up to 1,000 buffered messages per chat,
  retained for 24 hours. Downtime or restricted permissions may leave gaps.
- External replies support individual quotes, but not `/q N` sequences.
- Selections expire after one hour of inactivity. Completed jobs and buffered
  messages are removed according to their retention periods. Temporary media
  is removed after processing.
- Telegram does not report every message deletion. Edits are reflected when
  the bot receives the corresponding update.
- Forwarded messages with hidden authors use only the available attribution.
- Custom emoji use their text alternatives. Composite emoji, complex scripts,
  and advanced rich-message blocks require further rendering work.
- Photos and static stickers are rendered as images. Other attachments use
  cards. Unavailable media falls back to a card or avatar placeholder.
- Multiple polling processes using the same token are not supported.

## Troubleshooting

### Commands can be quoted, but ordinary messages cannot

Run `/status` to check the build identifier, privacy mode, and group role.
Update older builds that do not support `rich_message`. After changing privacy
mode, remove the bot from the group and add it again.

### A deployment still exhibits previous behavior

Rebuild the image and recreate the container. Confirm that no other machine
or local process is polling with the same token:

```bash
docker compose build --no-cache bot
docker compose up -d --force-recreate bot
docker compose logs --tail=100 bot
```

Startup logs include `Starting quoted-bot build=...`. The same identifier is
returned by `/status`. To identify the local source tree:

```bash
uv run python -c 'from quoted_bot.diagnostics import build_id; print(build_id())'
```

### Guest mentions receive no response

Check Guest Chat Mode in the BotFather Mini App, send the mention as a reply
to the source message, and verify access to the staging chat. Enabling classic
Inline Mode does not enable this workflow.

### Inspecting reply metadata

After a failed `/q`, inspect the diagnostic entry:

```bash
docker compose logs --tail=100 bot | grep 'Quote request'
```

This entry records field presence, including `reply_rich` and `reply_fields`,
without recording message text. Do not publish `.env`, bot tokens, or proxy
URLs containing credentials.

## Attribution and license

The project is based on the concept and visual approach of
[ryotairi/quoted](https://github.com/ryotairi/quoted), QuoteD Matrix, by
rustyraven and Vadim-Khristenko. This implementation provides a Python/Pillow
renderer and Telegram integration; pixel-identical output is not claimed.

The project is licensed under **AGPL-3.0-only**; see [LICENSE](LICENSE). This
was selected conservatively for compatibility with the upstream project
because the work began as a renderer port. Translating derivative code into
another language does not remove the original license obligations. A different
license for an independent implementation would require reviewing the actual
reuse; the current license remains unchanged.

AGPL permits commercial use. Section 13 requires modified versions offered to
users over a network to provide an opportunity to obtain the corresponding
source. Distribution is also subject to the other license conditions. Bot
tokens, passwords, and user conversations are not application source code.

- [GNU AGPL v3](https://www.gnu.org/licenses/agpl-3.0.html)
- [GNU guidance on applying licenses](https://www.gnu.org/licenses/gpl-howto.en.html)
- Bundled Noto fonts retain their separate **SIL OFL 1.1** license:
  [license and notices](src/quoted_bot/assets/fonts/OFL.txt).
