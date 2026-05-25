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
