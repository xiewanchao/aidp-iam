{{- define "aidp-mock-memory.labels" -}}
app.kubernetes.io/name: aidp-mock-memory
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}
