{{/*
Common labels for aidp-gateway resources.
*/}}
{{- define "aidp-gateway.labels" -}}
app.kubernetes.io/name: aidp-gateway
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{/*
Name for the in-cluster Gateway management service.
*/}}
{{- define "aidp-gateway.gatewayManagerName" -}}
{{ default "gateway-manager" .Values.gatewayManager.service.name }}
{{- end -}}

{{/*
Namespace for the user-facing Gateway, EnvoyProxy, data plane, HTTPRoutes,
policies, and Gateway TLS certificate.
*/}}
{{- define "aidp-gateway.gatewayNamespace" -}}
{{ default .Release.Namespace .Values.gateway.namespace }}
{{- end -}}
