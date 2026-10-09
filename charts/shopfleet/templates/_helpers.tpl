{{/* Common labels (Kubernetes recommended labels). */}}
{{- define "shopfleet.labels" -}}
app.kubernetes.io/part-of: shopfleet
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/version: {{ .Values.image.tag | default .Chart.AppVersion | quote }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end }}

{{/* Image reference: {{ include "shopfleet.image" (dict "root" $ "name" "catalog") }} */}}
{{- define "shopfleet.image" -}}
{{ .root.Values.image.registry }}/{{ .name }}:{{ .root.Values.image.tag | default .root.Chart.AppVersion }}
{{- end }}

{{- define "shopfleet.podSecurity" -}}
securityContext:
  runAsNonRoot: true
  runAsUser: {{ . }}
  runAsGroup: {{ . }}
  seccompProfile: { type: RuntimeDefault }
{{- end }}

{{- define "shopfleet.containerSecurity" -}}
securityContext:
  allowPrivilegeEscalation: false
  readOnlyRootFilesystem: true
  capabilities: { drop: ["ALL"] }
{{- end }}

{{- define "shopfleet.dbEnv" -}}
- name: DB_USER
  valueFrom: { secretKeyRef: { name: postgres-credentials, key: DB_USER } }
- name: DB_PASSWORD
  valueFrom: { secretKeyRef: { name: postgres-credentials, key: DB_PASSWORD } }
{{- end }}

{{- define "shopfleet.imagePullSecrets" -}}
{{- with .Values.imagePullSecrets }}
imagePullSecrets:
{{ toYaml . }}
{{- end }}
{{- end }}

{{/*
Stable generated secret: use the explicit value, else keep what's already in the cluster
(lookup), else generate. Without lookup, every `helm upgrade` would rotate the password!
Usage: include "shopfleet.secretValue" (dict "root" $ "secret" "admin-credentials" "key" "password" "value" .Values.secrets.adminPassword)
*/}}
{{- define "shopfleet.secretValue" -}}
{{- if .value -}}
{{ .value | b64enc }}
{{- else -}}
{{- $existing := lookup "v1" "Secret" .root.Release.Namespace .secret -}}
{{- if and $existing (index ($existing.data | default dict) .key) -}}
{{ index $existing.data .key }}
{{- else -}}
{{ randAlphaNum 24 | b64enc }}
{{- end -}}
{{- end -}}
{{- end }}
