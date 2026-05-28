{{/*
Common labels for iam-app resources.
*/}}
{{- define "iam-app.labels" -}}
app.kubernetes.io/name: iam-app
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: aidp-iam
{{- end -}}

{{- define "iam-app.namespace" -}}
{{- default .Release.Namespace .Values.namespace -}}
{{- end -}}

{{- define "iam-app.keycloakNamespace" -}}
{{- default .Release.Namespace .Values.logCollect.keycloakNamespace -}}
{{- end -}}

{{- define "iam-app.gatewayNamespace" -}}
{{- default .Release.Namespace .Values.logCollect.gatewayNamespace -}}
{{- end -}}

{{- define "iam-app.iamDbUrl" -}}
{{- default (printf "postgresql://keycloak:keycloak@iam-store.%s.svc.cluster.local:5432/iam" (include "iam-app.keycloakNamespace" .)) .Values.db.iam -}}
{{- end -}}

{{- define "iam-app.keycloakUrl" -}}
{{- default (printf "http://keycloak.%s.svc.cluster.local:8080" (include "iam-app.keycloakNamespace" .)) .Values.keycloak.url -}}
{{- end -}}

{{- define "iam-app.keycloakHealthUrl" -}}
{{- default (printf "http://keycloak.%s.svc.cluster.local:9000" (include "iam-app.keycloakNamespace" .)) .Values.keycloak.healthUrl -}}
{{- end -}}
