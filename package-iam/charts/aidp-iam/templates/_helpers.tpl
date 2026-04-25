{{- define "aidp-iam.labels" -}}
app.kubernetes.io/name: aidp-iam
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}
