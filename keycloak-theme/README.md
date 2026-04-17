# AIDP Keycloak 自定义登录主题

基于 [Keycloakify](https://www.keycloakify.dev/) 构建的 Keycloak 登录主题，覆盖登录、注册、忘记密码、修改密码、SAML 跳转等页面。

## 目录结构

```
keycloak-theme/
├── src/
│   ├── login/
│   │   ├── KcPage.tsx              # 主入口，import main.css
│   │   ├── main.css                # 自定义样式（深色主题 + AIDP 品牌）
│   │   ├── KcContext.ts            # Keycloak 上下文类型
│   │   ├── KcPageStory.tsx         # Storybook 辅助
│   │   ├── i18n.ts                 # 国际化
│   │   └── pages/                  # Story 文件（14 个页面预览）
│   │       ├── Login.stories.tsx
│   │       ├── Register.stories.tsx
│   │       ├── LoginResetPassword.stories.tsx
│   │       ├── LoginUpdatePassword.stories.tsx
│   │       └── ...
│   └── kc.gen.tsx
├── dist_keycloak/                  # 构建产物（JAR）
│   ├── keycloak-theme-for-kc-all-other-versions.jar  ← KC 11-21, 26+
│   └── keycloak-theme-for-kc-22-to-25.jar            ← KC 22-25
├── .storybook/
├── vite.config.ts
└── package.json
```

## 快速上手

### 1. 安装依赖

```bash
cd keycloak-theme
npm install
```

### 2. 本地预览（Storybook）

```bash
npm run storybook
# 浏览器打开 http://localhost:6006
```

14 个页面可预览：登录 / 注册 / 忘记密码 / 修改密码 / SAML 跳转 / MFA / 邮箱验证 / 错误页 等。

### 3. 修改样式

编辑 `src/login/main.css`，Storybook 热更新即时预览。

当前主题特征：
- 深色渐变背景（#0f172a → #1e293b）
- 半透明圆角登录卡片
- 紫色渐变按钮（#6366f1）
- 暗色输入框 + 聚焦紫色光晕
- 底部 "Powered by AIDP 统一身份认证平台"

### 4. 深度定制（改组件结构）

```bash
# 弹出某个页面的完整 React 组件
npx keycloakify eject-page
# 选择 login.ftl / register.ftl 等
# 生成到 src/login/pages/Login.tsx，自由修改
```

## 构建

### 方式 A：本地有 Java + Maven

```bash
npm run build-keycloak-theme
# 产物在 dist_keycloak/
```

### 方式 B：用 Docker 构建（推荐，无需装 Java）

```bash
# 1. 构建包含 node + java + maven 的环境镜像（首次）
cat > Dockerfile.env <<'EOF'
FROM maven:3.9-eclipse-temurin-17 AS maven-src
FROM node:20-slim
COPY --from=maven-src /opt/java /opt/java
COPY --from=maven-src /usr/share/maven /usr/share/maven
ENV JAVA_HOME=/opt/java/openjdk
ENV PATH="/usr/share/maven/bin:${JAVA_HOME}/bin:${PATH}"
WORKDIR /build
EOF
docker build -f Dockerfile.env -t theme-env:local .

# 2. 用容器构建 JAR
docker rm -f tb 2>/dev/null
docker create --name tb -w /build theme-env:local \
  bash -c "npm install && npm run build && npx keycloakify build"
docker cp . tb:/build/
docker start -ai tb

# 3. 拷出产物
docker cp tb:/build/dist_keycloak/keycloak-theme-for-kc-all-other-versions.jar dist_keycloak/
docker rm tb
```

## 部署到 Keycloak

### 1. 把 JAR 拷到 Keycloak 镜像

```bash
cp dist_keycloak/keycloak-theme-for-kc-all-other-versions.jar \
   ../da-cluster/images/keycloak-custom/keycloak-theme.jar
```

`da-cluster/images/keycloak-custom/Dockerfile` 已包含：

```dockerfile
COPY keycloak-theme.jar /opt/keycloak/providers/
```

### 2. 重建 Keycloak 镜像并部署

```bash
cd ../da-cluster
docker build -t keycloak-custom:26.5.2 images/keycloak-custom/
# Kind 环境
kind load docker-image keycloak-custom:26.5.2 --name da-cluster
kubectl -n keycloak rollout restart statefulset/keycloak
```

### 3. 启用主题

通过 Admin Console：Realm Settings → Themes → Login Theme → 选 `keycloakify-starter` → Save

或通过 API：
```bash
curl -X PUT "http://<keycloak>/admin/realms/aidp" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"loginTheme":"keycloakify-starter"}'
```

## 覆盖的页面

| 页面 | 文件 ID | 说明 |
|---|---|---|
| 登录 | login.ftl | 用户名密码 + SSO 按钮 |
| 注册 | register.ftl | 用户注册表单 |
| 忘记密码 | login-reset-password.ftl | 重置密码链接发送 |
| 修改密码 | login-update-password.ftl | 首次登录/强制改密 |
| SAML 跳转 | saml-post-form.ftl | SAML POST 中间页 |
| MFA 验证 | login-otp.ftl | 双因素验证码输入 |
| 绑定 MFA | login-config-totp.ftl | 绑定 Authenticator |
| 邮箱验证 | login-verify-email.ftl | 验证邮箱提示 |
| 补全信息 | login-update-profile.ftl | IdP 首次登录补全资料 |
| 关联 IdP | login-idp-link-confirm.ftl | 关联外部账户确认 |
| 退出确认 | logout-confirm.ftl | 退出确认页 |
| 页面过期 | login-page-expired.ftl | 会话过期 |
| 错误页 | error.ftl | 通用错误 |
| 服务条款 | terms.ftl | 条款接受页 |

## 兼容性

- Keycloak 11 ~ 最新版
- 当前项目使用 Keycloak 26.5.2
