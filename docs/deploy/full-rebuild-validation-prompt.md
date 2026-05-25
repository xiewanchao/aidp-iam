# Full Rebuild And Validation Prompt

Use this prompt when asking an agent to validate the repository after structural or deployment changes.

```text
请在当前仓库执行一次完整的本地从零验证。

要求：
1. 基于当前分支，不切换分支，不回退用户已有改动。
2. 从零清理本地 Kind 集群 da-cluster。
3. 从源码重新构建所有自研镜像。
4. Keycloak SPI jar 和 Keycloak theme jar 必须强制重建，不允许只复用旧 jar。
5. 使用 Helm install 安装 Gateway 和 IAM。
6. 运行 deploy/scripts/test.sh，记录总数、通过数、失败数。
7. Helm uninstall aidp-iam 和 aidp-gateway，确认 Helm release 清空，Gateway 相关资源清理干净。
8. 使用 --skip-build 重新安装。
9. 再运行 deploy/scripts/test.sh，记录总数、通过数、失败数。
10. 最后总结修改了哪些路径、执行了哪些命令、遇到的问题和解决方式。

推荐命令：

export CLUSTER_NAME=da-cluster
bash deploy/scripts/cleanup.sh

BUILD_KEYCLOAK_SPI=true \
BUILD_KEYCLOAK_THEME=true \
bash deploy/scripts/setup.sh

bash deploy/scripts/test.sh

helm uninstall aidp-iam -n aidp-iam
helm uninstall aidp-gateway -n aidp-gateway

helm list -A
kubectl get gateway,gatewayclass,httproute,referencegrant,securitypolicy,envoyproxy -A

bash deploy/scripts/setup.sh --skip-build
bash deploy/scripts/test.sh
```

## Known Issues And Fixes

### Shell script starts with BOM

Symptom:

```text
deploy/scripts/setup.sh: line 1: 锘?!/usr/bin/env: No such file or directory
```

Cause:

PowerShell `Set-Content -Encoding utf8` may write UTF-8 with BOM on older hosts.

Fix:

Rewrite edited scripts as UTF-8 without BOM, or edit with `apply_patch`.

PowerShell repair example:

```powershell
$files=@(
  'deploy/scripts/setup.sh',
  'deploy/scripts/rebuild.sh',
  'deploy/scripts/test.sh',
  'deploy/scripts/test-install.sh'
)
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
foreach($file in $files){
  $path=(Resolve-Path $file).Path
  $text=[System.IO.File]::ReadAllText($path)
  [System.IO.File]::WriteAllText($path, $text, $utf8NoBom)
}
```

### Keycloak SPI jar rebuild fails on Maven Central

Symptom:

```text
Could not transfer artifact ... from/to central (https://repo.maven.apache.org/maven2)
Remote host terminated the handshake
```

Fix options:

1. Configure Maven to use an internal mirror in `~/.m2/settings.xml`.
2. Retry after network is available.
3. If local Maven is broken but Docker can pull images, temporarily make local `mvn` unavailable so the script uses `maven:3.9-eclipse-temurin-21`.

Because this validation requires forced jar rebuild, do not accept the fallback to existing `data-agent-mapper.jar`.

### Keycloak theme jar rebuild fails

Symptom:

```text
Apache Maven required. Install it with `sudo apt-get install mvn`
```

Fix options:

1. Install/configure Maven and make sure `npm run build-keycloak-theme` can complete.
2. Ensure `node`, `npm`, and package dependencies can be installed.
3. If using the Docker fallback path, make sure Docker can pull `node:24-bookworm`.

Because this validation requires forced jar rebuild, do not accept the fallback to existing `keycloak-theme.jar`.

### Manifest heredoc JSON is invalid

Symptom:

```text
ERROR: manifest heredoc DA_MANIFEST_FILE[0] is invalid JSON
```

Cause:

The shell test file was rewritten with the wrong encoding or corrupted Chinese string content.

Fix:

Restore `deploy/scripts/test.sh` from Git and avoid broad PowerShell text rewrites on UTF-8 documents.

```bash
git checkout -- deploy/scripts/test.sh
bash -n deploy/scripts/test.sh
```

### Old layout directories still appear locally

Cause:

Old top-level directories may contain ignored image tar files, packaged charts, test output, or release artifacts.

Fix:

Do not commit them. Move local-only old contents to `artifacts/old-layout/` if needed.

## Expected Result

The baseline validation should finish with:

```text
Total : 236
Pass  : 236
Fail  : 0
```

Mock backend tests may be skipped if mock charts are not installed.
