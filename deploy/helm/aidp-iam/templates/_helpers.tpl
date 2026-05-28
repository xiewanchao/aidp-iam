{{- define "aidp-iam.labels" -}}
app.kubernetes.io/name: aidp-iam
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "aidp-iam.iamNamespace" -}}
{{- $iamApp := default dict (index .Values "iam-app") -}}
{{- default .Release.Namespace (default .Values.iamNamespaceOverride $iamApp.namespace) -}}
{{- end -}}

{{- define "aidp-iam.keycloakNamespace" -}}
{{- $keycloak := default dict (index .Values "keycloak") -}}
{{- default .Release.Namespace (default .Values.keycloakNamespaceOverride $keycloak.namespaceOverride) -}}
{{- end -}}

{{- define "aidp-iam.gatewayNamespace" -}}
{{- default .Release.Namespace .Values.routes.gatewayNamespace -}}
{{- end -}}
