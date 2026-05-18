{{- define "aidp-mock-dataagent.labels" -}}
app.kubernetes.io/name: aidp-mock-dataagent
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}
