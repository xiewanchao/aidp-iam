{{- define "aidp-mock-nginx.labels" -}}
app.kubernetes.io/name: aidp-mock-nginx
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}
