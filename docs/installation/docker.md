# Docker installation and upgrade (Linux AMD64)

Run the public image `ghcr.io/tech-kuiperbelt/remember-me:latest` without logging
in to GHCR. **BGE-M3 is included and runs on CPU; no Python installation, model
download or separate Embedding service is needed.** For a source install, see
[Source installation and upgrade](source.md).

These Bash examples target a local, rootful Linux Docker Engine on AMD64. Compose
also requires the Docker Compose plugin. Rootless Docker, Docker Desktop, SELinux
and network filesystems need separate permission/platform validation. The image
is about 3.5 GB unpacked; allow space for pulls, updates, backups and model working
memory. A minimum RAM requirement has not been established.

## Prepare one instance

Choose **one** installation method in a dedicated working directory. Do not run
two instances against the same data directory. Paths refer to the Docker daemon's
local host. Both methods use `latest` by default; a fixed version or digest can be
selected when needed. Pulling a new image does not update an existing container.

## Option 1: docker run

### Simplest startup

```bash
mkdir remember-me
cd remember-me
docker run --name remember-me -v "$PWD:/data" -p 127.0.0.1:1314:1314 ghcr.io/tech-kuiperbelt/remember-me:latest
```

The application runs in the foreground and displays logs in the terminal. Data
is saved in the **current host directory**, mounted at `/data` in the container.
Container port `1314` is published on host loopback port `1314`. Open
<http://127.0.0.1:1314/admin/console> in your browser, or connect your MCP client
to `http://127.0.0.1:1314/mcp` (legacy SSE: `/sse`).

The image defaults to container root, so ordinary local directories need no
separate initializer or `chown`. Existing ownership and permissions are not
changed automatically; newly created files usually belong to root. Filesystem
restrictions such as read-only mounts still apply.

### Recommended startup

Choose this **instead of** the simplest command, in the prepared data directory:

```bash
docker run -d --name remember-me \
  --restart unless-stopped --stop-timeout 120 \
  -v "$PWD:/data" \
  -v /etc/localtime:/etc/localtime:ro \
  -p 127.0.0.1:1314:1314 \
  ghcr.io/tech-kuiperbelt/remember-me:latest
```

| Parameter | Meaning |
| --- | --- |
| `-d` | Run in the background and return the terminal. |
| `--name remember-me` | Name the container for later management. |
| `--restart unless-stopped` | Restart after exits and when Docker starts; a manually stopped container stays stopped. Docker itself must start with the host for automatic startup after reboot. |
| `--stop-timeout 120` | Allow up to 120 seconds for graceful cleanup before force-killing. |
| `-v "$PWD:/data"` | Keep configuration, memories, logs and caches in the current host directory. |
| `-v /etc/localtime:/etc/localtime:ro` | Use the host's timezone for application logs through a read-only mount. The simplest command uses the container's default timezone. UTC timestamps in stored records are unchanged. |
| `-p 127.0.0.1:1314:1314` | Format: `host address:host access port:container port`. Open <http://127.0.0.1:1314/admin/console>. To change the host port, change the first port number after the address and keep the final container port `1314`. |
| `ghcr.io/tech-kuiperbelt/remember-me:latest` | Use the latest published stable image. To pin an installation, replace `latest` with a published full version, or use `ghcr.io/tech-kuiperbelt/remember-me@sha256:...`. |

If host port `1314` is already in use, replace `-p 127.0.0.1:1314:1314` with
`-p 127.0.0.1:5263:1314`. Then open <http://127.0.0.1:5263/admin/console> and use
port `5263` in MCP URLs. The container still listens on `1314`.

View background logs with `docker logs -f remember-me`. Allow several minutes
for the first model load. `latest` does not automatically replace a running
container; see [Manual upgrades and rollback](#upgrade-and-rollback).

### Other optional startup parameters

| Parameter | Use | If omitted |
| --- | --- | --- |
| `--platform linux/amd64` | Explicitly select Linux AMD64. | Docker selects its default platform. |
| `--user UID:GID` | Run as a chosen non-root user/group; `/data` must already be writable by that user. | Use the image's default root user. |
| `--read-only` | Make the container root filesystem read-only; the `/data` bind remains writable. | The container filesystem is writable. |
| `--cap-drop ALL` | Remove extra Linux capabilities. | Keep Docker's default capabilities. |
| `--security-opt no-new-privileges:true` | Prevent processes gaining privileges through executable files. | Do not add this restriction. |
| `--pull always` | Check and pull the specified image before creating the container. | `docker run` uses the local image if available. |
| `--mount "type=bind,src=$PWD,dst=/data"` | An alternative spelling of `-v "$PWD:/data"`; choose one. | The examples already mount data using `-v`. |

For a **new** instance, this optional combination uses your host UID/GID and adds
the filesystem/process restrictions above. It is an alternative startup command:

```bash
docker run -d --name remember-me --restart unless-stopped --stop-timeout 120 --user "$(id -u):$(id -g)" --read-only --cap-drop ALL --security-opt no-new-privileges:true -v "$PWD:/data" -v /etc/localtime:/etc/localtime:ro -p 127.0.0.1:1314:1314 ghcr.io/tech-kuiperbelt/remember-me:latest
```

Run this from a non-root host account. Data previously created by root may need
operator-managed permissions before switching users; the image does not
recursively change ownership. Preserve the data before changing permissions.

## Option 2: Docker Compose

In a new working directory, download the [official compose.yaml](../../compose.yaml).
The maintained example uses `latest`, without a source checkout or a version
number to update in this command:

```bash
mkdir remember-me
cd remember-me
curl -fL https://raw.githubusercontent.com/tech-kuiperbelt/remember-me/master/compose.yaml -o compose.yaml
docker compose -f compose.yaml up -d --wait --wait-timeout 300
```

Compose matches recommended Run: root, `latest`, host timezone, automatic restart
and a 120-second stop timeout. Data is stored in **the directory containing
`compose.yaml`** (the current directory when following these examples), and
**host loopback port `1314`** is published. There is no separate data initializer.
Open <http://127.0.0.1:1314/admin/console> when healthy.

Use a local `.env` beside `compose.yaml` to override `REMEMBER_ME_IMAGE`,
`REMEMBER_ME_DATA_DIR` (prefer an absolute path) or `REMEMBER_ME_PORT`, without
`export` prefixes. Exported shell values take precedence. Inspect
`docker compose -f compose.yaml config` before starting. A pinned installation can
instead download Compose from its corresponding Git tag and set a fixed image
version/digest. Stop and remove the previous container before switching methods,
and explicitly select the same data directory if reusing it.

Compose also supports an explicit service `user`, `read_only`, `cap_drop` and
`security_opt` for the optional restrictions described above. Check data access
before enabling a non-root user. The legacy `docker-compose.yml` is unrelated;
explicitly use `-f compose.yaml`.

## Connect, inspect and stop

All three startup examples default to <http://127.0.0.1:1314/admin/console>.
Use `/mcp` for Streamable HTTP and `/sse` for legacy SSE. Connection examples and
client-specific configuration are maintained in Web Help: open `/read-me` on
your published host port, for example <http://127.0.0.1:1314/read-me>.

| Operation | docker run | Docker Compose |
| --- | --- | --- |
| Status | `docker ps -a --filter name=remember-me` | `docker compose -f compose.yaml ps -a` |
| Logs | `docker logs --tail 100 -f remember-me` | `docker compose -f compose.yaml logs --tail 100 -f remember-me` |
| Health | `docker inspect --format '{{.State.Health.Status}}' remember-me` | `docker compose -f compose.yaml ps` |
| Stop | `docker stop --time 120 remember-me` | `docker compose -f compose.yaml stop` |
| Start again | `docker start remember-me` | `docker compose -f compose.yaml up -d --wait --wait-timeout 300` |
| Remove stopped container | `docker rm remember-me` | `docker compose -f compose.yaml down` |

Health observes model readiness without downloading. Check logs, disk space and
data access if startup fails. Configuration, SQLite and its sidecars, Dual JSONL,
logs and caches all live under `/data`. Deleting the container does not delete
the mounted host directory. Do not delete that directory or mount over the
included model directory. Language/Debug and the version-check proxy remain
editable in Configuration; Docker manages model settings and Retry reloads the
included model locally.

Published ports bind only to host loopback. Containers on the same Docker
network can still reach the container directly. Host/Origin protection remains
enabled; the API and Admin UI have no user authentication. Allow active imports
to finish before stopping. An exhausted stop timeout can force-kill the process.

## Permissions, backups and restore

Ordinary startup needs no permission preparation. Non-root operation requires
that the selected UID/GID can read and write existing data. Do not recursively
change shared directory ownership or use `chmod 777`.

Stop gracefully using the table above and verify exit code **0** (`docker inspect
--format '{{.State.ExitCode}}' remember-me`, or `docker compose -f compose.yaml ps -a`).
A successful stop command alone does not prove a clean exit; if force-killed,
start and stop cleanly before backing up. Back up the **entire data directory**,
not just JSONL or the SQLite main file. Record the actual running version/digest,
data path and port; `latest` alone does not identify a rollback image.

Set the actual data path below: both Run and new Compose installations use `$PWD`
when commands are run from the instance directory. For an existing/custom
installation, use its actual data path. Keep the archive **outside** that directory. This example creates a timestamped
archive in a sibling backup directory:

```bash
export REMEMBER_ME_DATA_DIR="$PWD"
export REMEMBER_ME_BACKUP_DIR="$(dirname "$REMEMBER_ME_DATA_DIR")/remember-me-backups"
mkdir -p "$REMEMBER_ME_BACKUP_DIR"
export REMEMBER_ME_BACKUP="$REMEMBER_ME_BACKUP_DIR/remember-me-$(date -u +%Y%m%dT%H%M%SZ).tar"
sudo tar --numeric-owner -C "$REMEMBER_ME_DATA_DIR" -cpf "$REMEMBER_ME_BACKUP" .
```

Restart using the table after the archive is complete. Protect it: the backup
contains private memories. In another terminal, set these variables to the actual
paths again. To restore, stop the instance and extract to a **new empty sibling
directory**, retaining numeric ownership and permissions:

```bash
export REMEMBER_ME_RESTORE_DIR="$(dirname "$REMEMBER_ME_DATA_DIR")/remember-me-restored"
mkdir "$REMEMBER_ME_RESTORE_DIR"
sudo tar --numeric-owner --same-owner -C "$REMEMBER_ME_RESTORE_DIR" -xpf "$REMEMBER_ME_BACKUP"
```

For Run, remove the stopped container and recreate with
`-v "$REMEMBER_ME_RESTORE_DIR:/data"` in place of `-v "$PWD:/data"`. For Compose,
set `REMEMBER_ME_DATA_DIR` to the restored directory, update `.env` if used, and
run `docker compose -f compose.yaml up -d --wait --wait-timeout 300`. Check health,
configuration, memory details and semantic search before retiring the old copy.

<a id="upgrade-and-rollback"></a>
## Manual upgrades and rollback

Work in the original instance directory. Record the actual current version and
image digest before pulling: inspect the running container's `.Image` with
`docker inspect`, then inspect that image's `RepoDigests` with `docker image inspect`.
For Compose, obtain its container ID with `docker compose -f compose.yaml ps -q remember-me`.
Keep that image/reference for rollback. Read all relevant Upgrade Notes / Breaking Changes.

Select `latest` or a published full version/digest; also update the saved value
in `.env` if used. Then pull:

```bash
export REMEMBER_ME_IMAGE=ghcr.io/tech-kuiperbelt/remember-me:latest
docker pull --platform linux/amd64 "$REMEMBER_ME_IMAGE"
```

After pulling, stop gracefully and take a full backup as above. For Run, remove
the stopped container and recreate from the original settings with the selected
image. Preserve its data mount, port, timezone, user and other options. For
Compose, keep the existing `compose.yaml`, data path, port and settings. Older
Compose files defaulted to `./data` and host port `1314`; preserve those values
when upgrading. Do not replace the file with the new template and accidentally
select another data directory. Export the selected `REMEMBER_ME_IMAGE`, and run:

```bash
docker compose -f compose.yaml up -d --pull always --wait --wait-timeout 300
```

Check health, the running version in the Web UI and retrieval of an existing
memory. `docker restart` and `docker start` keep the original image; upgrading
requires recreation. Version notifications do not perform updates/migrations or
prove compatibility.

Rollback uses the recorded old version/digest, not the current `latest`. Reuse
current data only when database schema and Embedding Profile are compatible;
otherwise restore the matching full backup into a new directory and run its
matching image. Post-backup changes are not included. Root-created files may
need permission preparation for an older non-root image. Check health, version
and existing-memory retrieval again.

## Next steps

Open the Admin UI on your published host port → **Help → Getting Started** to
connect AI, choose a namespace and set memory rules. Give your agent the `/read-me`
URL, <http://127.0.0.1:1314/read-me> by default. The container always listens on
`1314`; replace only the host port when using another mapping. Detailed MCP client configuration
and memory rules are maintained in Web Help.

- [Source installation and upgrade](source.md) if you prefer Python.
- [Version numbering](../../README.md#version-numbering) and
  [version checks and networking](../../README.md#version-checks-and-networking).

For backup and update policy context, see the Web UI version dialogs and each
target release's notes.
