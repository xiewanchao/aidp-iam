{{- define "aidp-mock-rubik.labels" -}}
app.kubernetes.io/name: aidp-mock-rubik
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}
