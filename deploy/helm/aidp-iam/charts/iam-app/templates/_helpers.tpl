{{/*
Common labels for iam-app resources.
*/}}
{{- define "iam-app.labels" -}}
app.kubernetes.io/name: iam-app
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: aidp-iam
{{- end -}}
