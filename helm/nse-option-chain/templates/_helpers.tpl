{{- define "nifty.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- define "nifty.fullname" -}}
{{- default (printf "%s-%s" .Release.Name (include "nifty.name" .)) .Values.fullnameOverride | trunc 45 | trimSuffix "-" -}}
{{- end -}}
{{- define "nifty.labels" -}}
app.kubernetes.io/name: {{ include "nifty.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | quote }}
{{- end -}}
{{- define "nifty.selector" -}}
app.kubernetes.io/name: {{ include "nifty.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}
{{- define "nifty.secret" -}}
{{- default (printf "%s-credentials" (include "nifty.fullname" .)) .Values.credentials.existingSecret -}}
{{- end -}}
{{- define "nifty.dbHost" -}}
{{- if .Values.postgresql.enabled -}}
{{- printf "%s-postgres" (include "nifty.fullname" .) -}}
{{- else -}}
{{- required "postgresql.externalHost is required when postgresql.enabled=false" .Values.postgresql.externalHost -}}
{{- end -}}
{{- end -}}
{{- define "nifty.dbPort" -}}
{{- if .Values.postgresql.enabled -}}5432{{- else -}}{{ .Values.postgresql.externalPort }}{{- end -}}
{{- end -}}
{{- define "nifty.token" -}}
{{ printf "%s-r%d" .Release.Name .Release.Revision }}
{{- end -}}
{{- define "nifty.dbEnv" -}}
- name: PGHOST
  value: {{ include "nifty.dbHost" . | quote }}
- name: PGPORT
  value: {{ include "nifty.dbPort" . | quote }}
- name: PGDATABASE
  value: {{ .Values.postgresql.database | quote }}
{{- end -}}
{{- define "nifty.security" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
capabilities:
  drop: [ALL]
{{- end -}}
{{- define "nifty.podSecurity" -}}
runAsNonRoot: true
runAsUser: 10001
runAsGroup: 10001
fsGroup: 10001
seccompProfile:
  type: RuntimeDefault
{{- end -}}
{{- define "nifty.scheduling" -}}
{{- with .Values.nodeSelector }}
nodeSelector:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- with .Values.tolerations }}
tolerations:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- with .Values.affinity }}
affinity:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- end -}}
{{- define "nifty.backendEnv" -}}
{{- $root := .root -}}
{{- include "nifty.dbEnv" $root }}
- name: PGUSER
  value: {{ if eq .component "api" }}nifty_api{{ else }}nifty_collector{{ end }}
- name: PGPASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ include "nifty.secret" $root }}
      key: {{ if eq .component "api" }}API_PASSWORD{{ else }}COLLECTOR_PASSWORD{{ end }}
- name: REFRESH_SECONDS
  value: {{ $root.Values.collector.refreshSeconds | quote }}
- name: MIGRATION_TOKEN
  valueFrom:
    configMapKeyRef:
      name: {{ include "nifty.fullname" $root }}-runtime
      key: MIGRATION_TOKEN
{{- if eq .component "api" }}
- name: UI_ORIGIN
  value: {{ $root.Values.ui.origin | quote }}
{{- else }}
- name: SPOOL_DIR
  value: /data/spool
{{- if $root.Values.collector.caSecret }}
- name: REQUESTS_CA_BUNDLE
  value: /certs/ca-bundle.pem
{{- end }}
{{- end }}
{{- end -}}
