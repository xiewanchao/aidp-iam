{{- define "aidp-mock-kms.labels" -}}
app.kubernetes.io/name: aidp-mock-kms
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}
