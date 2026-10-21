# Docker deployment (Linux AMD64)

Phase 2.2 uses the locally built `remember-me:phase2.2-amd64` image. A public
registry image is not published yet. With that image available and Docker Compose
installed, run from the repository directory:

```bash
docker compose up -d
docker compose ps
docker compose logs -f remember-me
```

Open <http://127.0.0.1:1314>. The image includes the model; no model setup or runtime
download is required. Initialization can take several minutes on slower CPUs.
Health checks only report readiness. Python installation remains available as before.
`compose.yaml` is the deployment definition; the historical `docker-compose.yml`
is an unrelated OpenMemory research artifact.

## Data and permissions

The single bind mount `./data:/data` stores configuration, SQLite (including WAL),
the JSONL replica, audit records, logs, temporary files and writable metadata
caches. Models remain inside the image and are never copied into this directory.
The application always runs as UID/GID `10001:10001`, with a read-only container
filesystem outside `/data`.

The short-lived root `data-init` service initializes only an empty mount root
(owner `10001:10001`, mode `0750`). It drops privileges and checks data access.
It never recursively changes an existing directory. Existing configuration is
preserved, including JSONC comments; invalid configuration fails instead of being
reset. Existing nonempty directories must already be readable/writable by
`10001:10001`. Symbolic links inside the data tree are rejected by the initializer.

On a normal rootful Linux Docker host, a new empty directory needs no preparation.
If initialization reports incompatible permissions, stop the deployment and
prepare **only the intended dedicated data directory**. For example, after
checking that this directory contains only this instance's files and no symlinks:

```bash
docker compose stop
sudo chown -R 10001:10001 ./data
sudo chmod -R u+rwX ./data
docker compose up -d
```

Do not apply these commands to a shared directory. Rootless/user-namespace Docker,
NFS, CIFS and Docker Desktop may map ownership differently; they require suitable
host permissions and are not covered by the current Linux rootful acceptance.
Do not use `chmod 777` or run the application as root to bypass a failed check.

To choose another dedicated directory or an unused loopback port, export variables
before running Compose (keep the same values for subsequent commands):

```bash
export REMEMBER_ME_DATA_DIR=/absolute/path/to/instance-data
export REMEMBER_ME_PORT=1414
docker compose up -d
```

Only `127.0.0.1` is published. Connect Codex using
`http://127.0.0.1:1314/mcp`, or OpenCode using `http://127.0.0.1:1314/sse`.
Use the selected host port when changed. MCP Host/Origin checks remain enabled;
the container's `0.0.0.0` listener makes bridge port forwarding possible.

## Configuration and stopping

Language and Debug remain editable on the existing configuration page. Deployment
and model settings are managed by the image and Compose. Existing stored values
are preserved; Docker's effective policy keeps MCP protection enabled and model
downloads disabled. Retry only reloads the built-in model. A failed model load
keeps the service in maintenance until loading and validation succeed; leaving
maintenance remains a manual action.

Restart Server remains available in maintenance. Stop containers from the terminal:

```bash
docker compose stop
docker compose up -d
```

`restart: unless-stopped` restarts unexpectedly terminated containers; the UI
therefore hides Shut Down Server. Compose allows 120 seconds for graceful stop.
Before backups, avoid active imports or other long operations; if the process is
force-killed after the grace period, wait for a clean startup and stop before
making the backup.

## Backup and restore

JSONL export in the existing UI remains a portable content export. A complete
instance backup must include the whole data directory **after graceful shutdown**.
Keep numeric ownership, permissions, configuration and all SQLite sidecars:

```bash
docker compose stop
sudo tar --numeric-owner -C ./data -cpf remember-me-backup.tar .
docker compose up -d
```

Before copying, check `docker compose ps -a`: the application should have exited
with code 0. A stop command returning successfully alone does not prove that
Docker avoided a timeout and forced kill. Record the image tag/ID (for example
`docker compose images`) and any exported deployment variables with the backup.

To restore into a new empty directory:

```bash
docker compose stop
mkdir ./restored-data
sudo tar --numeric-owner --same-owner -C ./restored-data -xpf remember-me-backup.tar
export REMEMBER_ME_DATA_DIR="$PWD/restored-data"
docker compose up -d
```

Verify records, searches and configuration before retiring the original directory.
Backups contain private memory content; store them accordingly. Never run two
instances writing to the same directory. `docker compose down` removes containers
and their network, while the bind-mounted host data remains.

## Compatible image updates

When a registry release is available, select its published compatible image tag:

```bash
export REMEMBER_ME_IMAGE=REGISTRY/remember-me:COMPATIBLE_TAG
docker compose pull
docker compose up -d
```

Back up first. Keep the same data directory and check health/search afterwards.
There is no automatic updater or new database migration mechanism in this phase.
Reusing the data directory with an older image is supported only when the schemas
and embedding profile are compatible. The Phase 2.1/2.2 pair is tested specifically;
this does not promise arbitrary downgrades.
