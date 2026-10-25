# Docker installation and upgrade (Linux AMD64)

Install and operate the official Remember Me container. For a Python source
install, see [Source installation and upgrade](source.md).

**Pre-release: the official GHCR image is not public yet.** The commands below
are prepared for publication; `1.0.0` is the example version, not an announcement
that it is available. Use the actual published full version when available.
Local acceptance uses `REMEMBER_ME_IMAGE=remember-me:phase3.2-ready-fixed` instead;
it does not prove public downloads. Neither path requires Python or a separate
Embedding service: **BGE-M3 is included and runs on CPU without model downloads.**

These Bash commands target a local, rootful Linux Docker Engine on AMD64. Compose
also requires the Docker Compose plugin. Rootless Docker, Docker Desktop, SELinux
hosts and network filesystems need separate permission/platform validation. The
image is about 3.5 GB unpacked; allow extra disk space for pulls, updates, backups
and model working memory. A minimum RAM requirement has not been established.

## Prepare one instance

Choose **one** installation method. Both use the same image and `./data` directory;
never start two writers against it. Run the following in a new working directory
and keep using that directory. Paths must be on the Docker daemon's local host.

```bash
mkdir -p remember-me
cd remember-me
export REMEMBER_ME_IMAGE=ghcr.io/tech-kuiperbelt/remember-me:1.0.0
export REMEMBER_ME_DATA_DIR="$PWD/data"
export REMEMBER_ME_PORT=1314
```

Keep a record of these settings. In a new terminal, set them again, especially if
you selected another directory or port. Pin a full version or the published
`ghcr.io/tech-kuiperbelt/remember-me@sha256:...` digest; avoid implicit `latest`.
The release's Git tag is `v1.0.0`, while its image tag is `1.0.0`.

## Option 1: docker run

Create a dedicated directory and run the image's existing initializer. Only this
short-lived helper starts as root; it prepares an **empty** directory for
UID/GID `10001:10001`, then drops privileges and checks access. Reusing an existing
directory checks permissions without recursively changing ownership or overwriting
configuration. Do not continue if initialization fails.

```bash
mkdir -p "$REMEMBER_ME_DATA_DIR"
docker run --rm --platform linux/amd64 --network none --read-only \
  --user 0:0 --cap-drop ALL \
  --cap-add CHOWN --cap-add DAC_OVERRIDE --cap-add FOWNER \
  --cap-add SETUID --cap-add SETGID --security-opt no-new-privileges:true \
  --mount "type=bind,src=$REMEMBER_ME_DATA_DIR,dst=/data" \
  --entrypoint python "$REMEMBER_ME_IMAGE" /opt/remember-me/docker/init_data.py
```

After the initializer succeeds, start the application:

```bash
docker run -d --name remember-me --platform linux/amd64 \
  --restart unless-stopped --stop-timeout 120 \
  --user 10001:10001 --read-only --cap-drop ALL \
  --security-opt no-new-privileges:true \
  --mount "type=bind,src=$REMEMBER_ME_DATA_DIR,dst=/data" \
  -p "127.0.0.1:${REMEMBER_ME_PORT}:1314" "$REMEMBER_ME_IMAGE"
```

## Option 2: Docker Compose

Download the [official compose.yaml](../../compose.yaml) from the **same release
tag** into the working directory (Git source checkout is unnecessary):

```bash
curl -fL https://raw.githubusercontent.com/tech-kuiperbelt/remember-me/v1.0.0/compose.yaml -o compose.yaml
docker compose -f compose.yaml up -d --wait --wait-timeout 300
```

The release file and image download require the release to exist. For local
pre-release acceptance, copy `compose.yaml` from this checkout and use the local
image override above. The legacy `docker-compose.yml` is unrelated; explicitly
use `-f compose.yaml`. Compose runs the same permission initializer before the
application. Its defaults match the setup above. For custom settings across
terminal sessions, put the three `REMEMBER_ME_*` settings in a local `.env` file
beside `compose.yaml`, with an absolute data path and without the `export` prefix;
exported shell values take precedence. Check `docker compose -f compose.yaml config`
before starting. Stop/remove the previous method's container before switching.

## Connect, inspect and stop

Open <http://127.0.0.1:1314/admin/console> when healthy. First startup may take
several minutes. If using another host port, replace `1314` in browser and MCP
URLs. Use `/mcp` for Streamable HTTP or `/sse` for legacy SSE; see
[client configuration](../../README.md#connect--enable-agent-memory).

| Operation | docker run | Docker Compose |
| --- | --- | --- |
| Status | `docker ps --filter name=remember-me` | `docker compose -f compose.yaml ps -a` |
| Logs | `docker logs --tail 100 -f remember-me` | `docker compose -f compose.yaml logs --tail 100 -f remember-me` |
| Health | `docker inspect --format '{{.State.Health.Status}}' remember-me` | `docker compose -f compose.yaml ps` |
| Stop | `docker stop --time 120 remember-me` | `docker compose -f compose.yaml stop` |
| Start again | `docker start remember-me` | `docker compose -f compose.yaml up -d --wait --wait-timeout 300` |
| Remove stopped container | `docker rm remember-me` | `docker compose -f compose.yaml down` |

Health observes model readiness and does not initiate a download. If startup
fails, check the logs, disk space and data permissions. Language/Debug and the
version-check proxy remain editable in the configuration page; Docker manages
model settings. Retry reloads the included model locally. The root filesystem and
model stay read-only; the single `/data` bind mount retains configuration, SQLite
and its sidecars, Dual JSONL, logs and caches. Deleting/recreating a container
does not delete this host directory. Do not delete it or mount over the image's
model directory.

The service publishes only `127.0.0.1`; do not change it to a public bind without
a separate access-control design. Containers on the same Docker network can still
access the container directly. Host/Origin protection remains enabled. Neither
API nor Admin UI provides user authentication. `unless-stopped` enables restart
after unexpected exits; stop from the terminal. Allow active imports to finish
before stopping; if 120 seconds expires, Docker can force-kill the process.

## Permissions, backups and restore

A new empty directory is initialized with mode `0750`. For existing nonempty
data, initialization stops on incompatible ownership, unreadable files or
symlinks. After stopping the service, an administrator must prepare **only that
dedicated instance directory** for numeric UID/GID `10001:10001`; do not apply
recursive ownership changes to shared directories, use `chmod 777`, or run the
application as root. Preserve existing data before changing permissions.

Back up the **entire data directory after graceful shutdown**, not just the JSONL
export or SQLite main file. Use your method's Stop command above, then verify
exit code **0** (`docker inspect --format '{{.State.ExitCode}}' remember-me`, or
`docker compose -f compose.yaml ps -a` for Compose). A successful stop command
alone does not prove a clean exit. If force-killed, start and stop cleanly before
backing up. Record the image version/digest, data path and port with the backup.

```bash
sudo tar --numeric-owner -C "$REMEMBER_ME_DATA_DIR" -cpf "$PWD/remember-me-backup.tar" .
```

Restart using the table above. Protect the archive: it contains private memories.
To restore, stop the instance, create a **new empty** directory and preserve
numeric ownership/permissions when extracting:

```bash
mkdir "$PWD/restored-data"
sudo tar --numeric-owner --same-owner -C "$PWD/restored-data" -xpf "$PWD/remember-me-backup.tar"
export REMEMBER_ME_DATA_DIR="$PWD/restored-data"
```

Update any saved `.env` data path too. Recreate the container with the restored
mount: for `docker run`, remove the stopped container, rerun the initializer and
start commands; for Compose, run
`docker compose -f compose.yaml up -d --wait --wait-timeout 300`. Check health,
configuration, memory details and semantic search before retiring the original
copy. Never run both copies as writers to the same directory.

<a id="upgrade-and-rollback"></a>
## Manual upgrades and rollback

Read the target release's compatibility notes and take a stopped full backup.
Keep the old image/version and backup. Set `REMEMBER_ME_IMAGE` to the chosen
published full version or digest (also update `.env` if used), then pull it:

```bash
docker pull --platform linux/amd64 "$REMEMBER_ME_IMAGE"
```

For `docker run`, pulling alone does not upgrade a running container. After
stopping and backing up, remove the old container, then repeat the initialization
and start steps above from your deployment records, changing only the image
reference. Preserve the actual mounts, ports, environment, UID/GID, read-only
filesystem and security options; do not replace custom settings with examples.

For Compose, keep the existing `compose.yaml`, data path, ports and settings in
the original instance directory. Set and export `REMEMBER_ME_IMAGE` to the verified
target full version and **update its saved value in `.env` too**. Exported shell
values override `.env`. After stopping and backing up, run:

```bash
docker compose -f compose.yaml up -d --pull always --wait --wait-timeout 300
```

The changed image setting recreates the service. Check health and retrieve an
existing memory. Read all relevant Upgrade Notes / Breaking Changes first;
version notifications do not prove data compatibility or perform updates or migrations.

Rollback follows the same steps with the previous version/digest. Reuse current
data only when that release's database schema and Embedding Profile are compatible.
Otherwise restore the matching full backup into a new directory and run its
matching image; post-backup changes are not included. Local recreation tests do
not establish compatibility between arbitrary future versions.

## Next steps

- [Source installation and upgrade](source.md) if you prefer Python.
- [Connect & Enable Agent Memory](../../README.md#connect--enable-agent-memory)
  for MCP client configuration and verification.
- [Version numbering](../../README.md#version-numbering) and
  [version checks and networking](../../README.md#version-checks-and-networking).

For backup and update policy context, see the version dialogs in the Web UI and
the release notes for each target version.
