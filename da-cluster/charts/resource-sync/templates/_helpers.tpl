{{- define "resource-sync.labels" -}}
app.kubernetes.io/name: resource-sync
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}
