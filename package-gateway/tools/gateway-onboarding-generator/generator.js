(function (root) {
  "use strict";

  const METHODS = ["GET", "PUT", "PATCH", "DELETE"];
  const RATE_UNITS = ["Second", "Minute", "Hour", "Day", "Month", "Year"];
  const TIMEOUT_PATTERN = /^([0-9]{1,5}(h|m|s|ms)){1,4}$/;

  function trim(value) {
    return String(value == null ? "" : value).trim();
  }

  function toKebab(value) {
    const raw = trim(value) || "business-app";
    const ascii = raw
      .replace(/([a-z0-9])([A-Z])/g, "$1-$2")
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "");
    return ascii || "business-app";
  }

  function normalizePathPrefix(value, fallback) {
    let path = trim(value) || fallback || "/";
    if (!path.startsWith("/")) {
      path = `/${path}`;
    }
    return path.replace(/\/{2,}/g, "/").replace(/\/$/, "") || "/";
  }

  function splitList(value) {
    return trim(value)
      .split(/[\n,]+/)
      .map((item) => item.trim())
      .filter(Boolean);
  }

  function quote(value) {
    return JSON.stringify(String(value));
  }

  function yamlList(items, indent) {
    const prefix = " ".repeat(indent);
    return items.map((item) => `${prefix}- ${quote(item)}`).join("\n");
  }

  function doc(lines) {
    return lines.join("\n").replace(/\n{3,}/g, "\n\n").trim();
  }

  function normalizeConfig(input) {
    const appName = trim(input.appName) || "KnowledgeBase";
    const resourceName = toKebab(appName);
    const manifestNamespace = trim(input.manifestNamespace) || appName.replace(/\s+/g, "");
    const pathPrefix = normalizePathPrefix(input.pathPrefix, `/${manifestNamespace}`);
    const backendNamespace = toKebab(input.backendNamespace || resourceName);
    const backendService = toKebab(input.backendService || resourceName);
    const gatewayNamespace = toKebab(input.gatewayNamespace || "aidp-gateway");
    const gatewayName = toKebab(input.gatewayName || "eg");
    const port = Number(input.backendPort) || 8080;
    const routeName = toKebab(input.routeName || `${resourceName}-route`);

    return {
      appName,
      displayName: trim(input.displayName) || appName,
      manifestNamespace,
      resourceName,
      routeName,
      gatewayNamespace,
      gatewayName,
      backendNamespace,
      backendService,
      backendPort: Math.min(Math.max(port, 1), 65535),
      pathPrefix,
      hostnames: splitList(input.hostnames),
      includeReferenceGrant: Boolean(input.includeReferenceGrant),
      enableAuth: Boolean(input.enableAuth),
      enableAclSync: Boolean(input.enableAclSync),
      enableRateLimit: Boolean(input.enableRateLimit),
      enableXffPolicy: Boolean(input.enableXffPolicy),
      requestTimeout: trim(input.requestTimeout),
      backendTimeout: trim(input.backendTimeout),
      rateRequests: Math.max(Number(input.rateRequests) || 60, 1),
      rateUnit: RATE_UNITS.includes(input.rateUnit) ? input.rateUnit : "Minute",
      rateScope: input.rateScope || "route",
      rateHeader: trim(input.rateHeader) || "X-User-Id",
      xffTrustedHops: Math.min(Math.max(Number(input.xffTrustedHops) || 1, 1), 10),
      allowCidrs: splitList(input.allowCidrs),
      denyCidrs: splitList(input.denyCidrs),
      resourceType: trim(input.resourceType) || "Resources",
      resourceIdName: trim(input.resourceIdName) || "ResourceId",
      resourceDisplayName: trim(input.resourceDisplayName) || trim(input.resourceType) || "资源",
      defaultRole: trim(input.defaultRole),
      methods: Array.isArray(input.methods) && input.methods.length ? input.methods : METHODS,
    };
  }

  function validateConfig(cfg) {
    const warnings = [];
    const dns = /^[a-z0-9]([-a-z0-9]*[a-z0-9])?$/;
    const cidr = /^([0-9]{1,3}\.){3}[0-9]{1,3}\/([0-9]|[1-2][0-9]|3[0-2])$/;

    [
      ["Gateway namespace", cfg.gatewayNamespace],
      ["Gateway name", cfg.gatewayName],
      ["Backend namespace", cfg.backendNamespace],
      ["Backend service", cfg.backendService],
      ["HTTPRoute name", cfg.routeName],
    ].forEach(([name, value]) => {
      if (!dns.test(value)) {
        warnings.push(`${name} 已按 DNS-1123 名称规则归一化为 ${value}`);
      }
    });

    if (!cfg.pathPrefix.startsWith("/")) {
      warnings.push("路径前缀必须以 / 开头。");
    }
    if (cfg.requestTimeout && !TIMEOUT_PATTERN.test(cfg.requestTimeout)) {
      warnings.push(`请求总超时 ${cfg.requestTimeout} 可能不符合 Gateway API duration 格式。`);
    }
    if (cfg.backendTimeout && !TIMEOUT_PATTERN.test(cfg.backendTimeout)) {
      warnings.push(`后端请求超时 ${cfg.backendTimeout} 可能不符合 Gateway API duration 格式。`);
    }
    cfg.allowCidrs.concat(cfg.denyCidrs).forEach((item) => {
      if (!cidr.test(item)) {
        warnings.push(`${item} 不是标准 IPv4 CIDR，apply 前需要确认。`);
      }
    });
    if ((cfg.allowCidrs.length || cfg.denyCidrs.length) && !cfg.enableXffPolicy) {
      warnings.push("IP 白名单/黑名单依赖 Envoy 识别到真实客户端 IP；如果前面还有 LB 或代理，请由平台统一配置 ClientTrafficPolicy。");
    }
    if (cfg.enableRateLimit && cfg.rateScope === "client-ip" && !cfg.enableXffPolicy) {
      warnings.push("按客户端 IP 限流同样依赖真实客户端 IP 识别，NodePort/LB 场景需要确认 source IP 或 X-Forwarded-For。");
    }
    if (cfg.enableAclSync) {
      warnings.push("ACL 自动同步需要同时注册 Manifest；仅 apply EnvoyExtensionPolicy 不会自动生成 resource_patterns。");
    }
    return warnings;
  }

  function buildHttpRoute(cfg) {
    const lines = [
      "apiVersion: gateway.networking.k8s.io/v1",
      "kind: HTTPRoute",
      "metadata:",
      `  name: ${cfg.routeName}`,
      `  namespace: ${cfg.gatewayNamespace}`,
      "spec:",
    ];
    if (cfg.hostnames.length) {
      lines.push("  hostnames:");
      lines.push(yamlList(cfg.hostnames, 2));
    }
    lines.push(
      "  parentRefs:",
      `  - name: ${cfg.gatewayName}`,
      "  rules:",
      "  - matches:",
      "    - path:",
      "        type: PathPrefix",
      `        value: ${quote(cfg.pathPrefix)}`
    );
    if (cfg.requestTimeout || cfg.backendTimeout) {
      lines.push("    timeouts:");
      if (cfg.requestTimeout) {
        lines.push(`      request: ${quote(cfg.requestTimeout)}`);
      }
      if (cfg.backendTimeout) {
        lines.push(`      backendRequest: ${quote(cfg.backendTimeout)}`);
      }
    }
    lines.push(
      "    backendRefs:",
      `    - name: ${cfg.backendService}`,
      `      namespace: ${cfg.backendNamespace}`,
      `      port: ${cfg.backendPort}`
    );
    return doc(lines);
  }

  function buildReferenceGrant(cfg) {
    return doc([
      "apiVersion: gateway.networking.k8s.io/v1beta1",
      "kind: ReferenceGrant",
      "metadata:",
      `  name: allow-gateway-to-${cfg.backendService}`,
      `  namespace: ${cfg.backendNamespace}`,
      "spec:",
      "  from:",
      "  - group: gateway.networking.k8s.io",
      "    kind: HTTPRoute",
      `    namespace: ${cfg.gatewayNamespace}`,
      "  to:",
      "  - group: \"\"",
      "    kind: Service",
    ]);
  }

  function addAuthorization(lines, cfg) {
    const hasAllow = cfg.allowCidrs.length > 0;
    const hasDeny = cfg.denyCidrs.length > 0;
    if (!hasAllow && !hasDeny) {
      return;
    }

    lines.push("  authorization:");
    lines.push(`    defaultAction: ${hasAllow ? "Deny" : "Allow"}`);
    lines.push("    rules:");

    if (hasDeny) {
      lines.push(
        "    - name: deny-client-cidrs",
        "      action: Deny",
        "      principal:",
        "        clientCIDRs:"
      );
      cfg.denyCidrs.forEach((cidr) => lines.push(`        - ${quote(cidr)}`));
    }

    if (hasAllow) {
      lines.push(
        "    - name: allow-client-cidrs",
        "      action: Allow",
        "      principal:",
        "        clientCIDRs:"
      );
      cfg.allowCidrs.forEach((cidr) => lines.push(`        - ${quote(cidr)}`));
    }
  }

  function buildSecurityPolicy(cfg) {
    if (!cfg.enableAuth && !cfg.allowCidrs.length && !cfg.denyCidrs.length) {
      return "";
    }
    const lines = [
      "apiVersion: gateway.envoyproxy.io/v1alpha1",
      "kind: SecurityPolicy",
      "metadata:",
      `  name: ${cfg.resourceName}-security`,
      `  namespace: ${cfg.gatewayNamespace}`,
      "spec:",
      "  targetRefs:",
      "  - group: gateway.networking.k8s.io",
      "    kind: HTTPRoute",
      `    name: ${cfg.routeName}`,
    ];
    if (cfg.enableAuth) {
      lines.push(
        "  extAuth:",
        "    grpc:",
        "      backendRefs:",
        "      - name: pep-proxy",
        "        namespace: aidp-iam",
        "        port: 9000",
        "    failOpen: false",
        "    bodyToExtAuth:",
        "      maxRequestBytes: 8192"
      );
    }
    addAuthorization(lines, cfg);
    return doc(lines);
  }

  function buildEnvoyExtensionPolicy(cfg) {
    if (!cfg.enableAclSync) {
      return "";
    }
    return doc([
      "apiVersion: gateway.envoyproxy.io/v1alpha1",
      "kind: EnvoyExtensionPolicy",
      "metadata:",
      `  name: ${cfg.resourceName}-extproc`,
      `  namespace: ${cfg.gatewayNamespace}`,
      "spec:",
      "  targetRefs:",
      "  - group: gateway.networking.k8s.io",
      "    kind: HTTPRoute",
      `    name: ${cfg.routeName}`,
      "  extProc:",
      "  - backendRefs:",
      "    - name: resource-sync",
      "      namespace: aidp-iam",
      "      port: 8082",
      "    processingMode:",
      "      request:",
      "        headers: Send",
      "        body: Streamed",
      "      response:",
      "        body: Streamed",
      "    failOpen: true",
    ]);
  }

  function buildBackendTrafficPolicy(cfg) {
    if (!cfg.enableRateLimit) {
      return "";
    }
    const selector = [];
    if (cfg.rateScope === "client-ip") {
      selector.push("        - sourceCIDR:", "            type: Distinct", "            value: 0.0.0.0/0");
    } else if (cfg.rateScope === "path") {
      selector.push("        - path:", "            type: PathPrefix", `            value: ${quote(cfg.pathPrefix)}`);
    } else if (cfg.rateScope === "method") {
      selector.push("        - methods:", "          - value: GET", "          - value: POST", "          - value: PUT", "          - value: DELETE");
    } else if (cfg.rateScope === "header") {
      selector.push("        - headers:", `          - name: ${cfg.rateHeader}`, "            type: Distinct");
    }

    const lines = [
      "apiVersion: gateway.envoyproxy.io/v1alpha1",
      "kind: BackendTrafficPolicy",
      "metadata:",
      `  name: ${cfg.resourceName}-rate-limit`,
      `  namespace: ${cfg.gatewayNamespace}`,
      "spec:",
      "  targetRefs:",
      "  - group: gateway.networking.k8s.io",
      "    kind: HTTPRoute",
      `    name: ${cfg.routeName}`,
      "  rateLimit:",
      "    type: Local",
      "    local:",
      "      rules:",
    ];
    if (selector.length) {
      lines.push("      - clientSelectors:", ...selector, "        limit:");
    } else {
      lines.push("      - limit:");
    }
    lines.push(
      `          requests: ${cfg.rateRequests}`,
      `          unit: ${cfg.rateUnit}`
    );
    return doc(lines);
  }

  function buildClientTrafficPolicy(cfg) {
    if (!cfg.enableXffPolicy) {
      return "";
    }
    return doc([
      "apiVersion: gateway.envoyproxy.io/v1alpha1",
      "kind: ClientTrafficPolicy",
      "metadata:",
      `  name: ${cfg.gatewayName}-client-ip`,
      `  namespace: ${cfg.gatewayNamespace}`,
      "spec:",
      "  targetRef:",
      "    group: gateway.networking.k8s.io",
      "    kind: Gateway",
      `    name: ${cfg.gatewayName}`,
      "  clientIPDetection:",
      "    xForwardedFor:",
      `      numTrustedHops: ${cfg.xffTrustedHops}`,
    ]);
  }

  function buildManifest(cfg) {
    const baseUrl = `http://${cfg.backendService}.${cfg.backendNamespace}.svc.cluster.local:${cfg.backendPort}`;
    const defaultAcl = [];
    if (cfg.defaultRole) {
      defaultAcl.push({
        user_template: "AccessManager/Tenants/{tenantId}/Groups/all-users",
        object_template: `${cfg.manifestNamespace}/Tenants/{tenantId}/${cfg.resourceType}`,
        role_path: `AccessManager/Tenants/System/Roles/${cfg.defaultRole}`,
      });
      defaultAcl.push({
        user_template: "AccessManager/Tenants/{tenantId}/Groups/tenant-admins",
        object_template: `${cfg.manifestNamespace}/Tenants/{tenantId}/${cfg.resourceType}`,
        role_path: "AccessManager/Tenants/System/Roles/Owner",
      });
    }

    return JSON.stringify({
      namespace: cfg.manifestNamespace,
      display_name: cfg.displayName,
      base_url: baseUrl,
      list_filter_mode: "gateway_inject",
      resources: [
        {
          type: cfg.resourceType,
          display_name: cfg.resourceDisplayName,
          path_pattern: `/${cfg.manifestNamespace}/Tenants/{tenantId}/${cfg.resourceType}/{${cfg.resourceIdName}}`,
          methods: cfg.methods,
          actions: [],
          default_acl: defaultAcl,
          children: [],
        },
      ],
      supported_roles: [
        "AccessManager/Tenants/System/Roles/Owner",
        "AccessManager/Tenants/System/Roles/Contributor",
        "AccessManager/Tenants/System/Roles/Viewer",
      ],
      custom_roles: [],
    }, null, 2);
  }

  function buildCommands(cfg) {
    const fileBase = cfg.resourceName;
    const lines = [
      `kubectl apply -f ${fileBase}-gateway.yaml`,
      "",
      `kubectl -n ${cfg.gatewayNamespace} get httproute ${cfg.routeName}`,
      `kubectl -n ${cfg.gatewayNamespace} describe httproute ${cfg.routeName}`,
    ];
    if (cfg.includeReferenceGrant) {
      lines.push(`kubectl -n ${cfg.backendNamespace} get referencegrant allow-gateway-to-${cfg.backendService}`);
    }
    if (cfg.enableAuth || cfg.allowCidrs.length || cfg.denyCidrs.length) {
      lines.push(`kubectl -n ${cfg.gatewayNamespace} get securitypolicy ${cfg.resourceName}-security`);
    }
    if (cfg.enableAclSync) {
      lines.push(`kubectl -n ${cfg.gatewayNamespace} get envoyextensionpolicy ${cfg.resourceName}-extproc`);
      lines.push("");
      lines.push(`curl -X PUT "http://<gateway-host>:30080/AccessManager/Tenants/System/AppManifests/${cfg.manifestNamespace}" \\`);
      lines.push("  -H \"Authorization: Bearer $TOKEN\" \\");
      lines.push("  -H \"Content-Type: application/json\" \\");
      lines.push(`  --data-binary @${fileBase}-manifest.json`);
    }
    if (cfg.enableRateLimit) {
      lines.push(`kubectl -n ${cfg.gatewayNamespace} get backendtrafficpolicy ${cfg.resourceName}-rate-limit`);
    }
    if (cfg.enableXffPolicy) {
      lines.push(`kubectl -n ${cfg.gatewayNamespace} get clienttrafficpolicy ${cfg.gatewayName}-client-ip`);
    }
    lines.push("");
    lines.push(`curl -i "http://<gateway-host>:30080${cfg.pathPrefix}/health"`);
    return lines.join("\n");
  }

  function generateAll(input) {
    const cfg = normalizeConfig(input);
    const resources = [
      buildHttpRoute(cfg),
      cfg.includeReferenceGrant ? buildReferenceGrant(cfg) : "",
      buildSecurityPolicy(cfg),
      buildEnvoyExtensionPolicy(cfg),
      buildBackendTrafficPolicy(cfg),
      buildClientTrafficPolicy(cfg),
    ].filter(Boolean);
    return {
      config: cfg,
      warnings: validateConfig(cfg),
      yaml: resources.join("\n---\n") + "\n",
      manifest: buildManifest(cfg) + "\n",
      commands: buildCommands(cfg) + "\n",
      resourceCount: resources.length,
    };
  }

  function collectForm() {
    const methods = Array.from(document.querySelectorAll("input[name=methods]:checked")).map((item) => item.value);
    return {
      appName: document.getElementById("appName").value,
      displayName: document.getElementById("displayName").value,
      gatewayNamespace: document.getElementById("gatewayNamespace").value,
      gatewayName: document.getElementById("gatewayName").value,
      backendNamespace: document.getElementById("backendNamespace").value,
      backendService: document.getElementById("backendService").value,
      backendPort: document.getElementById("backendPort").value,
      pathPrefix: document.getElementById("pathPrefix").value,
      hostnames: document.getElementById("hostnames").value,
      includeReferenceGrant: document.getElementById("includeReferenceGrant").checked,
      enableAuth: document.getElementById("enableAuth").checked,
      enableAclSync: document.getElementById("enableAclSync").checked,
      enableRateLimit: document.getElementById("enableRateLimit").checked,
      enableXffPolicy: document.getElementById("enableXffPolicy").checked,
      requestTimeout: document.getElementById("requestTimeout").value,
      backendTimeout: document.getElementById("backendTimeout").value,
      rateRequests: document.getElementById("rateRequests").value,
      rateUnit: document.getElementById("rateUnit").value,
      rateScope: document.getElementById("rateScope").value,
      rateHeader: document.getElementById("rateHeader").value,
      xffTrustedHops: document.getElementById("xffTrustedHops").value,
      allowCidrs: document.getElementById("allowCidrs").value,
      denyCidrs: document.getElementById("denyCidrs").value,
      resourceType: document.getElementById("resourceType").value,
      resourceIdName: document.getElementById("resourceIdName").value,
      resourceDisplayName: document.getElementById("resourceDisplayName").value,
      defaultRole: document.getElementById("defaultRole").value,
      methods,
    };
  }

  function renderSummary(result) {
    const cfg = result.config;
    document.getElementById("summary").innerHTML = [
      ["HTTPRoute", `${cfg.gatewayNamespace}/${cfg.routeName}`],
      ["入口路径", cfg.pathPrefix],
      ["后端 Service", `${cfg.backendService}.${cfg.backendNamespace}:${cfg.backendPort}`],
      ["生成资源数", String(result.resourceCount)],
    ].map(([label, value]) => `<div class="summary-item"><span>${label}</span><strong>${value}</strong></div>`).join("");
  }

  function renderWarnings(warnings) {
    const box = document.getElementById("warnings");
    box.hidden = warnings.length === 0;
    box.innerHTML = warnings.length ? `<ul>${warnings.map((item) => `<li>${item}</li>`).join("")}</ul>` : "";
  }

  function updateOutput() {
    const result = generateAll(collectForm());
    document.getElementById("yamlOutput").value = result.yaml;
    document.getElementById("manifestOutput").value = result.manifest;
    document.getElementById("commandsOutput").value = result.commands;
    renderSummary(result);
    renderWarnings(result.warnings);
  }

  function downloadText(filename, content) {
    const blob = new Blob([content], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    link.click();
    URL.revokeObjectURL(url);
  }

  function setupDom() {
    const form = document.getElementById("generatorForm");
    form.addEventListener("input", updateOutput);
    form.addEventListener("change", updateOutput);

    document.querySelectorAll(".tab").forEach((tab) => {
      tab.addEventListener("click", () => {
        document.querySelectorAll(".tab").forEach((item) => item.classList.remove("active"));
        document.querySelectorAll(".tab-panel").forEach((item) => item.classList.remove("active"));
        tab.classList.add("active");
        document.getElementById(tab.dataset.target).classList.add("active");
      });
    });

    document.querySelectorAll("[data-copy]").forEach((button) => {
      button.addEventListener("click", async () => {
        const target = document.getElementById(button.dataset.copy);
        await navigator.clipboard.writeText(target.value);
        const old = button.textContent;
        button.textContent = "已复制";
        window.setTimeout(() => { button.textContent = old; }, 900);
      });
    });

    document.getElementById("downloadYamlBtn").addEventListener("click", () => {
      const result = generateAll(collectForm());
      downloadText(`${result.config.resourceName}-gateway.yaml`, result.yaml);
    });
    document.getElementById("downloadManifestBtn").addEventListener("click", () => {
      const result = generateAll(collectForm());
      downloadText(`${result.config.resourceName}-manifest.json`, result.manifest);
    });
    document.getElementById("resetBtn").addEventListener("click", () => {
      form.reset();
      updateOutput();
    });

    updateOutput();
  }

  if (typeof module !== "undefined" && module.exports) {
    module.exports = { generateAll, normalizeConfig };
  }
  root.gatewayOnboardingGenerator = { generateAll, normalizeConfig };

  if (root.document) {
    root.document.addEventListener("DOMContentLoaded", setupDom);
  }
})(typeof window !== "undefined" ? window : globalThis);
