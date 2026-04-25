{{- define "aidp-mock-kb.labels" -}}
app.kubernetes.io/name: aidp-mock-kb
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}
