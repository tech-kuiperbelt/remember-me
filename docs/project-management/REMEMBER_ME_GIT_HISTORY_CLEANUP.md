# Remember Me — Git 历史清理执行计划

状态：两路径隔离演练已完成；四路径范围已确认，尚未演练或正式实施。

本文件只记录待执行步骤，不代表已经授权正式重写。待当前开发完成、需要保留的内容提交后，重新演练并取得用户明确确认，再执行正式清理。全流程不执行 Push。

## 1. 目标与最新审查结论

精确删除以下根目录路径的所有历史版本：

```text
memories.db
.venv/
.idea/
__pycache__/
```

保留其他文件、文件模式、提交作者、提交时间、提交消息和父子关系。提交 ID 会改变；不主动裁剪清理后变空的提交，不修改业务代码或接口。

最新审查记忆 `801ec8b6a2674c98` 将历史数据库描述为 4 条开发期记录，未发现凭据、邮箱或内网 IP。因此不能将其描述为已确认的敏感数据泄露；仍按用户要求移除数据库历史。禁止读取、打印或向外部服务发送记忆正文。

根目录 `.idea/` 与 `__pycache__/` 在旧演练结果中仍然存在，用户已确认一并清理。历史作者邮箱默认保留，公开前由用户确认是否接受。README、LICENSE 和 `.gitignore` 改进另行处理，不混入历史重写。

## 2. 已有演练证据与当前状态

旧演练目录：`/tmp/remember-me-history-audit.zdrhFO/rehearsal`。

原始历史备份：`/tmp/remember-me-history-audit.zdrhFO/original.bundle`。

旧演练基于 `de09865`，使用 `git filter-branch` 删除 `memories.db` 和 `.venv/`。提交数保持 157，HEAD 文件树一致，目标路径为零，`git fsck` 通过，无超过 50 MiB 的可达对象。此结论不覆盖后来确认的另外两个路径。

文档编写时 HEAD 已为 `d7f38ca`，仍有正在进行的修改。另有 detached worktree 登记在 `/path/to/codex-worktree`。正式执行必须重新读取实时状态，不能直接将旧演练副本的 `.git` 覆盖回来。

临时 bundle 含原始历史数据，只能本地受限保存，不能上传；它不包含完整工作区和 Git 配置，也不能作为正式操作的唯一备份。

## 3. 执行前置条件

以下条件不满足时停止，不自动 stash、提交、reset、checkout 或 clean：

- [ ] 用户已完成当前开发并提交希望保留的文件，包括决定是否提交本计划和审查文档。
- [ ] 主工作区和索引干净，没有未跟踪文件。
- [ ] 额外 worktree 的提交及文件已保存；由用户确认结束使用后再解除关联，不自动删除。
- [ ] 只剩主工作区及 `refs/heads/master`。如出现新分支、标签、stash、notes 或 replace refs，先补充保留方案，不能悄悄丢弃。
- [ ] 当前 HEAD 已不跟踪四个目标路径；过滤只改变历史。若不符合，先报告当前树影响。
- [ ] 执行期间暂停其他 Agent、IDE 自动 Git 操作和提交；Git 状态变化则重新备份和演练。
- [ ] 备份目录位于仓库外的持久本地磁盘，权限受限、空间充足，不在云同步目录。

以下命令块按顺序在同一个 Bash 会话运行；任何步骤失败都停止调查，不跳过失败继续执行。路径变量只在该会话使用。

```bash
set -euo pipefail
umask 077
export CLEAN_SOURCE=/path/to/remember-me
cd "$CLEAN_SOURCE"
test "$(git branch --show-current)" = master
test -z "$(git status --porcelain --untracked-files=all)"
test "$(git worktree list --porcelain | awk '/^worktree / {n++} END {print n}')" = 1
test "$(git for-each-ref --format='%(refname)')" = refs/heads/master
test -z "$(git ls-tree -r --name-only HEAD -- memories.db .venv .idea __pycache__)"
test "$(git remote get-url origin)" = git@github.com:tech-kuiperbelt/remember-me.git
test "$(git remote get-url --push origin)" = git@github.com:tech-kuiperbelt/remember-me.git
test ! -e .git/objects/info/alternates
test ! -e .git/shallow
git fsck --full
```

## 4. 持久备份与工具准备

下面使用用户目录下的专用备份目录。确认其为非同步的持久本地目录后执行；否则先改为合适的绝对路径。

```bash
mkdir -p "$HOME/remember-me-git-backups"
chmod 700 "$HOME/remember-me-git-backups"
export CLEAN_RUN
CLEAN_RUN=$(mktemp -d "$HOME/remember-me-git-backups/cleanup.XXXXXX")
git rev-parse HEAD > "$CLEAN_RUN/head.before"
git rev-parse HEAD^{tree} > "$CLEAN_RUN/tree.before"
git for-each-ref --format='%(refname) %(objectname)' > "$CLEAN_RUN/refs.before"
git status --porcelain --untracked-files=all > "$CLEAN_RUN/status.before"
git remote get-url origin > "$CLEAN_RUN/origin.before"
git remote get-url --push origin > "$CLEAN_RUN/origin-push.before"
cp -a .git "$CLEAN_RUN/git.before"
git bundle create "$CLEAN_RUN/history.before.bundle" --all
git bundle verify "$CLEAN_RUN/history.before.bundle"
git archive --format=tar HEAD > "$CLEAN_RUN/tracked.before.tar"
sha256sum "$CLEAN_RUN/history.before.bundle" "$CLEAN_RUN/tracked.before.tar" > "$CLEAN_RUN/SHA256SUMS"
```

`.git` 副本保存索引、配置、引用、对象和 reflog；tar 保存干净 HEAD 的跟踪文件。备份范围是 Git 状态和跟踪文件，忽略的运行数据库、模型及本地环境不参与本任务，不宣称已为运行数据制作一致性备份。

在独立目录验证恢复，避免只验证 bundle 就认为整个备份可用：

```bash
mkdir "$CLEAN_RUN/restore-check"
cp -a "$CLEAN_RUN/git.before" "$CLEAN_RUN/restore-check/.git"
tar -xf "$CLEAN_RUN/tracked.before.tar" -C "$CLEAN_RUN/restore-check"
git -C "$CLEAN_RUN/restore-check" fsck --full
test "$(git -C "$CLEAN_RUN/restore-check" rev-parse HEAD)" = "$(cat "$CLEAN_RUN/head.before")"
test -z "$(git -C "$CLEAN_RUN/restore-check" status --porcelain --untracked-files=all)"
```

正式使用 `git-filter-repo`，不重复采用 `filter-branch`。如已安装可直接使用；否则在备份目录内创建独立工具环境：

```bash
python3 -m venv "$CLEAN_RUN/tooling"
"$CLEAN_RUN/tooling/bin/python" -m pip install git-filter-repo
export CLEAN_FILTER="$CLEAN_RUN/tooling/bin/git-filter-repo"
"$CLEAN_FILTER" --version > "$CLEAN_RUN/filter-repo.version"
"$CLEAN_RUN/tooling/bin/python" -m pip freeze > "$CLEAN_RUN/tooling.requirements.txt"
```

下载只获取工具，不上传仓库内容；网络或安装权限不足时按环境要求申请，失败则停止。重新演练与正式实施必须使用同一工具文件和版本。

## 5. 基于最新提交重新演练

从已验证 bundle 克隆，确保演练与备份使用完全相同的历史。独立副本不使用原仓库对象的硬链接或 alternates。

```bash
export CLEAN_TRIAL="$CLEAN_RUN/rehearsal"
git clone "$CLEAN_RUN/history.before.bundle" "$CLEAN_TRIAL"
test "$(git -C "$CLEAN_TRIAL" rev-parse HEAD)" = "$(cat "$CLEAN_RUN/head.before")"
cd "$CLEAN_TRIAL"
"$CLEAN_FILTER" --force \
  --path memories.db --path .venv/ --path .idea/ --path __pycache__/ \
  --invert-paths --prune-empty never --prune-degenerate never \
  --preserve-commit-hashes --preserve-commit-encoding
git remote add origin git@github.com:tech-kuiperbelt/remember-me.git
git fsck --full --no-reflogs
```

`--force` 仅允许在已核对路径的副本中使用，以及用户确认后的正式步骤中使用。`--preserve-commit-hashes` 保留提交消息中的旧哈希文本，不表示提交 ID 不变。工具会移除 origin，随后恢复；若工具行为不同、origin 仍存在，则先检查原因再恢复 URL，不忽略错误。

### 历史与对象验收脚本

将以下脚本保存为 `$CLEAN_RUN/verify-history.py`，不放入项目。它不读取目标数据库正文，按 Git blob ID、模式及路径逐提交比较文件树。签名提交无法在重写后保留有效签名；脚本遇到无法保留的头字段差异会失败，应单独报告而非跳过。

```python
import os
import subprocess
import sys

before, after = sys.argv[1:3]

def git(repo, *args):
    return subprocess.check_output(['git', '-C', repo, *args])

def excluded(path):
    return (path == b'memories.db' or
            path.startswith((b'.venv/', b'.idea/', b'__pycache__/')))

def tree(repo, commit, filter_paths=False):
    entries = {}
    for item in git(repo, 'ls-tree', '-r', '-z', commit).split(b'\0'):
        if item:
            metadata, path = item.split(b'\t', 1)
            if not filter_paths or not excluded(path):
                entries[path] = metadata
    return entries

def commit_parts(repo, commit):
    raw = git(repo, 'cat-file', 'commit', commit)
    headers, message = raw.split(b'\n\n', 1)
    parents, retained = [], []
    for line in headers.splitlines():
        if line.startswith(b'parent '):
            parents.append(line[7:].decode())
        elif not line.startswith(b'tree '):
            retained.append(line)
    return parents, retained, message

map_path = os.path.join(after, '.git', 'filter-repo', 'commit-map')
with open(map_path, encoding='ascii') as stream:
    next(stream)
    mapping = dict(line.split() for line in stream if line.strip())
old = git(before, 'rev-list', 'master').decode().splitlines()
new = git(after, 'rev-list', 'master').decode().splitlines()
assert len(old) == len(new), 'commit count changed'
assert all(c in mapping and set(mapping[c]) != {'0'} for c in old), 'missing mapping'
assert {mapping[c] for c in old} == set(new), 'unexpected commit set'
for commit in old:
    rewritten = mapping[commit]
    assert tree(before, commit, True) == tree(after, rewritten), 'tree mismatch: ' + commit
    parents, headers, message = commit_parts(before, commit)
    new_parents, new_headers, new_message = commit_parts(after, rewritten)
    assert [mapping[p] for p in parents] == new_parents, 'parent mismatch: ' + commit
    assert (headers, message) == (new_headers, new_message), 'metadata mismatch: ' + commit
assert git(before, 'rev-parse', 'master^{tree}') == git(after, 'rev-parse', 'master^{tree}'), 'HEAD tree changed'
for commit in git(after, 'rev-list', '--all').decode().splitlines():
    assert not any(excluded(p) for p in tree(after, commit)), 'target path remains: ' + commit
objects = git(after, 'rev-list', '--objects', '--all').splitlines()
ids = [line.split(b' ', 1)[0] for line in objects]
sizes = subprocess.check_output(
    ['git', '-C', after, 'cat-file', '--batch-check=%(objectname) %(objecttype) %(objectsize)'],
    input=b'\n'.join(ids) + b'\n')
large = []
for row in sizes.splitlines():
    oid, kind, size = row.split()
    if kind == b'blob' and int(size) > 50 * 1024 * 1024:
        large.append((oid.decode(), int(size)))
assert not large, 'review large objects (object IDs / bytes): ' + repr(large)
print('PASS: commits, metadata, parent graph, trees, target paths, object sizes; commits:', len(old))
```

执行：

```bash
python3 "$CLEAN_RUN/verify-history.py" "$CLEAN_RUN/restore-check" "$CLEAN_TRIAL"
git -C "$CLEAN_TRIAL" fsck --full --no-reflogs
git -C "$CLEAN_TRIAL" for-each-ref --format='%(refname) %(objectname)'
```

补充验收：检查 `refs/original`、replace refs 和 reflog 是否残留旧历史；在清理后的全部可达文本对象中扫描常见 Token、私钥、凭据赋值和本机路径，只输出文件路径、对象 ID、类别和数量。任何疑似真实秘密由人工确认；零命中不代表完整隐私保证。图片内容和作者邮箱单独列为公开前复核项，不声称已被文本扫描覆盖。

输出演练报告，包含工具版本、原/新 HEAD、提交数量、逐提交对比结果、对象完整性、扫描范围与未覆盖项、备份位置。到此暂停，等待正式重写授权。

## 6. 正式实施（必须另获用户确认）

重新执行第 3 节前置检查，确认没有新增提交、引用或改动；变化则重新备份并演练。不得将旧演练 `.git` 直接替换到正式仓库。

```bash
cd "$CLEAN_SOURCE"
test "$(git rev-parse HEAD)" = "$(cat "$CLEAN_RUN/head.before")"
test -z "$(git status --porcelain --untracked-files=all)"
git for-each-ref --format='%(refname) %(objectname)' > "$CLEAN_RUN/refs.pre-apply"
cmp "$CLEAN_RUN/refs.before" "$CLEAN_RUN/refs.pre-apply"
test "$(git worktree list --porcelain | awk '/^worktree / {n++} END {print n}')" = 1
"$CLEAN_FILTER" --force \
  --path memories.db --path .venv/ --path .idea/ --path __pycache__/ \
  --invert-paths --prune-empty never --prune-degenerate never \
  --preserve-commit-hashes --preserve-commit-encoding
git remote add origin "$(cat "$CLEAN_RUN/origin.before")"
git remote set-url --push origin "$(cat "$CLEAN_RUN/origin-push.before")"
python3 "$CLEAN_RUN/verify-history.py" "$CLEAN_RUN/restore-check" "$CLEAN_SOURCE"
test "$(git rev-parse HEAD)" = "$(git -C "$CLEAN_TRIAL" rev-parse HEAD)"
git fsck --full --no-reflogs
git status --short
git remote -v
git worktree list --porcelain
git for-each-ref --format='%(refname) %(objectname)'
```

工作区必须仍干净；复核配置除预期远程调整外未丢失，并重复第 5 节补充验收。不得自行删除大对象或新增路径来“让验收通过”。

保存正式提交映射和验收结果到仓库外的执行目录。停止于本地验收完成；远端是否为空与权限状态需在将来的 Push 任务中重新检查。本计划不包含 Push 命令，也不授权 force push。

## 7. 回滚与风险

提交 ID 必然改变，旧哈希引用、旧开发副本、签名和外部链接可能需要后续处理。根目录源码中的本机路径和提交作者邮箱不会被本次四路径过滤自动匿名化。

验收失败时停止所有 Git 写入。下列回滚保留失败后的 Git 元数据供诊断，不删除工作区文件；前提是仍只有主 worktree，期间没有新增用户工作。若出现新修改，先另行保存并报告，不能覆盖。

```bash
cd "$CLEAN_SOURCE"
test ! -e "$CLEAN_RUN/git.failed"
mv .git "$CLEAN_RUN/git.failed"
cp -a "$CLEAN_RUN/git.before" .git
test "$(git rev-parse HEAD)" = "$(cat "$CLEAN_RUN/head.before")"
git fsck --full
git status --short
git remote -v
```

正常情况下过滤前后 HEAD 文件树相同，恢复 `.git` 即可恢复 Git 状态。如果工具中途失败导致跟踪文件缺失或改变，先把故障工作区另行备份；确认没有新用户修改后，才用 `tracked.before.tar` 恢复跟踪文件并再次验收。不要使用 `git clean` 清理故障现场，也不要覆盖忽略的运行数据或环境。

备份与恢复副本都含旧历史，必须留在仓库外，不能成为可推送分支或打包到 Release。验收后由用户决定保留期限及清理时间。
