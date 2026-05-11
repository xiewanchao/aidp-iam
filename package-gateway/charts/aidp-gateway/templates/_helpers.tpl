{{/*
Common labels for aidp-gateway resources.
*/}}
{{- define "aidp-gateway.labels" -}}
app.kubernetes.io/name: aidp-gateway
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{/*
Name for the in-cluster certificate adapter service.
*/}}
{{- define "aidp-gateway.certManagerName" -}}
{{ .Release.Name }}-cert-manager
{{- end -}}
