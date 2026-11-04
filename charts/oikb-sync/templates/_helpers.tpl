{{/* Chart name. */}}
{{- define "oikb-sync.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/* Fully qualified app name (also the daemon Deployment/Service name). */}}
{{- define "oikb-sync.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{- define "oikb-sync.reconcilerName" -}}
{{- printf "%s-reconciler" (include "oikb-sync.fullname" .) | trunc 52 | trimSuffix "-" }}
{{- end }}

{{- define "oikb-sync.configSecretName" -}}
{{- printf "%s-config" (include "oikb-sync.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "oikb-sync.keysSecretName" -}}
{{- if .Values.existingSecret }}
{{- .Values.existingSecret }}
{{- else }}
{{- printf "%s-keys" (include "oikb-sync.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}

{{- define "oikb-sync.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/* Common labels. */}}
{{- define "oikb-sync.labels" -}}
helm.sh/chart: {{ include "oikb-sync.chart" . }}
app.kubernetes.io/name: {{ include "oikb-sync.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- with .Values.commonLabels }}
{{ toYaml . }}
{{- end }}
{{- end }}

{{/* Selector labels for one component (daemon or reconciler). Usage: include "oikb-sync.selectorLabels" (list . "daemon") */}}
{{- define "oikb-sync.selectorLabels" -}}
{{- $ctx := index . 0 -}}
app.kubernetes.io/name: {{ include "oikb-sync.name" $ctx }}
app.kubernetes.io/instance: {{ $ctx.Release.Name }}
app.kubernetes.io/component: {{ index . 1 }}
{{- end }}

{{/* "true" when the pods should let OpenShift's SCC assign UIDs. */}}
{{- define "oikb-sync.isOpenShift" -}}
{{- if eq .Values.platform "openshift" }}true
{{- else if eq .Values.platform "kubernetes" }}
{{- else if .Capabilities.APIVersions.Has "security.openshift.io/v1" }}true
{{- end }}
{{- end }}

{{/* Pod security context. uid: the image's non-root UID on plain Kubernetes. */}}
{{- define "oikb-sync.podSecurityContext" -}}
{{- $ctx := index . 0 -}}
runAsNonRoot: true
{{- if not (include "oikb-sync.isOpenShift" $ctx) }}
runAsUser: {{ index . 1 }}
runAsGroup: {{ index . 1 }}
fsGroup: {{ index . 1 }}
{{- end }}
seccompProfile:
  type: RuntimeDefault
{{- end }}

{{- define "oikb-sync.containerSecurityContext" -}}
allowPrivilegeEscalation: false
capabilities:
  drop: ["ALL"]
{{- end }}

{{/* Required values, with a clear message. */}}
{{- define "oikb-sync.openWebUIURL" -}}
{{- $url := required "openWebUI.url is required, e.g. --set openWebUI.url=http://open-webui.open-webui.svc.cluster.local:8080" .Values.openWebUI.url }}
{{- if not (regexMatch "^https?://[^/]+" $url) }}
{{- fail (printf "openWebUI.url must start with http:// or https:// (got %q)" $url) }}
{{- end }}
{{- trimSuffix "/" $url }}
{{- end }}

{{/* HTTP proxy env vars (upper and lower case), when a proxy is configured. */}}
{{- define "oikb-sync.proxyEnv" -}}
{{- with .Values.proxy }}
{{- if or .httpProxy .httpsProxy }}
{{- $owuiHost := (urlParse (include "oikb-sync.openWebUIURL" $)).hostname }}
{{- $noProxy := list $owuiHost ".svc" ".cluster.local" "localhost" "127.0.0.1" }}
{{- if .noProxy }}{{ $noProxy = append $noProxy .noProxy }}{{ end }}
{{- $np := join "," $noProxy }}
{{- range $name := list "HTTP_PROXY" "http_proxy" }}
- name: {{ $name }}
  value: {{ $.Values.proxy.httpProxy | quote }}
{{- end }}
{{- range $name := list "HTTPS_PROXY" "https_proxy" }}
- name: {{ $name }}
  value: {{ $.Values.proxy.httpsProxy | default $.Values.proxy.httpProxy | quote }}
{{- end }}
{{- range $name := list "NO_PROXY" "no_proxy" }}
- name: {{ $name }}
  value: {{ $np | quote }}
{{- end }}
{{- end }}
{{- end }}
{{- end }}

{{- define "oikb-sync.daemonImage" -}}
{{- printf "%s:%s" .Values.daemon.image.repository (.Values.daemon.image.tag | default .Chart.AppVersion) }}
{{- end }}

{{- define "oikb-sync.reconcilerImage" -}}
{{- printf "%s:%s" .Values.reconciler.image.repository .Values.reconciler.image.tag }}
{{- end }}

{{/* Pod spec shared by the reconciler CronJob and the install/upgrade hook Job. */}}
{{- define "oikb-sync.reconcilerPodSpec" -}}
serviceAccountName: {{ include "oikb-sync.reconcilerName" . }}
restartPolicy: Never
securityContext:
  {{- include "oikb-sync.podSecurityContext" (list . 65534) | nindent 2 }}
{{- with .Values.imagePullSecrets }}
imagePullSecrets:
  {{- toYaml . | nindent 2 }}
{{- end }}
containers:
  - name: reconciler
    image: {{ include "oikb-sync.reconcilerImage" . }}
    imagePullPolicy: {{ .Values.reconciler.image.pullPolicy }}
    command: ["python", "-B", "/app/reconciler.py"]
    env:
      - name: OPEN_WEBUI_URL
        value: {{ include "oikb-sync.openWebUIURL" . | quote }}
      - name: OPEN_WEBUI_API_KEY
        valueFrom:
          secretKeyRef:
            name: {{ include "oikb-sync.keysSecretName" . }}
            key: {{ .Values.secretKeys.openWebUIApiKey }}
      - name: OIKB_DEPLOYMENT
        value: {{ include "oikb-sync.fullname" . | quote }}
      - name: OIKB_CONFIG_SECRET
        value: {{ include "oikb-sync.configSecretName" . | quote }}
      - name: OIKB_DEFAULT_INTERVAL
        value: {{ .Values.reconciler.defaultInterval | quote }}
      - name: OIKB_ALLOW_ANONYMOUS
        value: {{ .Values.reconciler.allowAnonymous | quote }}
      - name: DRY_RUN
        value: {{ .Values.reconciler.dryRun | quote }}
      {{- with include "oikb-sync.proxyEnv" . }}
      {{- . | trim | nindent 6 }}
      {{- end }}
    volumeMounts:
      - name: script
        mountPath: /app
        readOnly: true
    resources:
      {{- toYaml .Values.reconciler.resources | nindent 6 }}
    securityContext:
      {{- include "oikb-sync.containerSecurityContext" . | nindent 6 }}
      readOnlyRootFilesystem: true
volumes:
  - name: script
    configMap:
      name: {{ include "oikb-sync.reconcilerName" . }}
{{- with .Values.reconciler.nodeSelector }}
nodeSelector:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- with .Values.reconciler.tolerations }}
tolerations:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- with .Values.reconciler.affinity }}
affinity:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- end }}
