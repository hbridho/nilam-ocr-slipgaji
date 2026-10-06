{{- define "nilam-ocr-slipgaji.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "nilam-ocr-slipgaji.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else if contains (include "nilam-ocr-slipgaji.name" .) .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name (include "nilam-ocr-slipgaji.name" .) | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}

{{/* Label yang dimiliki semua pod release, apa pun service-nya. */}}
{{- define "nilam-ocr-slipgaji.selectorLabels" -}}
app.kubernetes.io/name: {{ include "nilam-ocr-slipgaji.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "nilam-ocr-slipgaji.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{ include "nilam-ocr-slipgaji.selectorLabels" . }}
app.kubernetes.io/part-of: nilam-ocr
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/* Argumen: dict "root" $ "name" <nama service>. Selector Deployment/Service/PDB satu komponen. */}}
{{- define "nilam-ocr-slipgaji.componentSelectorLabels" -}}
{{ include "nilam-ocr-slipgaji.selectorLabels" .root }}
app.kubernetes.io/component: {{ .name }}
{{- end }}

{{/* Argumen: dict "root" $ "name" <nama> "svc" <values service>. */}}
{{- define "nilam-ocr-slipgaji.componentLabels" -}}
{{ include "nilam-ocr-slipgaji.labels" .root }}
app.kubernetes.io/component: {{ .name }}
app.kubernetes.io/version: {{ include "nilam-ocr-slipgaji.imageTag" . | quote }}
{{- end }}

{{- define "nilam-ocr-slipgaji.componentName" -}}
{{- printf "%s-%s" (include "nilam-ocr-slipgaji.fullname" .root) .name | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Nama port container satu service. Argumen: dict "name" <kunci> "svc" <values service>.

Kubernetes membatasi nama port pada IANA_SVC_NAME: maksimal 15 karakter. Nama service lain tidak
dibatasi sekeras itu, jadi kunci sepanjang `guardrail-identity` (18) sah di mana-mana KECUALI di
sini — dan yang menolaknya API server, bukan helm, jadi kesalahannya baru muncul saat apply.

Karena itu dipendekkan di satu tempat ini saja, bukan dengan memendekkan kuncinya: kunci yang
berbeda dari nama folder service akan memaksa pemetaan tambahan di deploy.sh (build pakai nama
folder, `--set` dan `rollout status` pakai kunci) — tiga tempat yang bisa tidak sinkron.

`portName` di values menimpanya, untuk kalau dua kunci kebetulan terpotong menjadi nama yang sama.
*/}}
{{- define "nilam-ocr-slipgaji.portName" -}}
{{- .svc.portName | default (.name | trunc 15 | trimSuffix "-") }}
{{- end }}

{{- define "nilam-ocr-slipgaji.imageTag" -}}
{{- .svc.image.tag | default .root.Values.image.tag | default .root.Chart.AppVersion }}
{{- end }}

{{- define "nilam-ocr-slipgaji.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "nilam-ocr-slipgaji.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Isi ConfigMap satu service. Argumen: dict "root" $ "svc" <values service>.
URL antar service menunjuk ke Service per komponen (<release>-<nama>).
*/}}
{{- define "nilam-ocr-slipgaji.serviceEnv" -}}
{{- $root := .root }}
{{- $svc := .svc }}
{{- $env := dict "PORT" ($svc.port | toString) "ENVIRONMENT" $root.Values.environment }}
{{- if $svc.pipeline }}
{{- $_ := set $env "ORCHESTRATION_URL" $root.Values.orchestration.url }}
{{- $_ := set $env "ORCHESTRATION_CALLBACK_PATH" $root.Values.orchestration.callbackPath }}
{{- $_ := set $env "ORCHESTRATION_TIMEOUT_SECONDS" ($root.Values.orchestration.timeoutSeconds | toString) }}
{{- $_ := set $env "ORCHESTRATION_CALLBACK_FORMAT" ($root.Values.orchestration.callbackFormat | default "stage") }}
{{- $_ := set $env "ORCHESTRATION_CALLBACK_ENABLED" (ternary "false" "true" (eq (toString $root.Values.orchestration.callbackEnabled) "false")) }}
{{- $_ := set $env "ORCHESTRATION_CALLBACK_MAX_AGE_SECONDS" ($root.Values.orchestration.callbackMaxAgeSeconds | default "600" | toString) }}
{{- end }}
{{- range $svc.upstreams }}
{{- $upstream := index $root.Values.services . }}
{{- $host := include "nilam-ocr-slipgaji.componentName" (dict "root" $root "name" .) }}
{{/*
Nama variabel lingkungannya: `urlEnv` kalau service tujuan menyebutkannya, kalau tidak diturunkan
dari kuncinya. `-` HARUS menjadi `_`: `upper` tidak mengubah tanda hubung, dan
`GUARDRAIL-BLANK_SERVICE_URL` bukan nama variabel lingkungan yang sah — setelannya tidak akan
pernah terbaca, tanpa satu pun pesan galat.

`urlEnv` ada karena kunci service juga dipakai sebagai nama port Kubernetes, yang dibatasi 15
karakter. `guardrail-identity` (18) tidak bisa menjadi kunci, jadi kuncinya dipendekkan dan nama
env-nya disebutkan terpisah supaya tetap cocok dengan setelan di kodenya.
*/}}
{{- $urlEnv := $upstream.urlEnv | default (printf "%s_SERVICE_URL" (upper (replace "-" "_" .))) }}
{{/*
Upstream yang dimatikan (`services.<nama>.enabled: false`) tidak diberi URL; bila ia punya `enabledEnv`
(ketiga guardrail), pemanggilnya diberi tahu `<ENABLED_ENV>=false` supaya ia tidak memanggilnya dan
mencatatnya di `skipped`. Jadi mematikan satu guardrail cukup satu `--set`, tanpa menyentuh extraction.
*/}}
{{- if $upstream.enabled }}
{{- $_ := set $env $urlEnv (printf "http://%s:%v" $host $upstream.port) }}
{{- if $upstream.enabledEnv }}
{{- $_ := set $env $upstream.enabledEnv "true" }}
{{- end }}
{{- else if $upstream.enabledEnv }}
{{- $_ := set $env $upstream.enabledEnv "false" }}
{{- end }}
{{- end }}
{{- if and $svc.gcsModels $root.Values.gcpWif.clientId }}
{{- with $root.Values.gcpWif }}
{{- $_ := set $env "AZURE_TENANT_ID" .tenantId }}
{{- $_ := set $env "AZURE_CLIENT_ID" .clientId }}
{{- $_ := set $env "GCP_PROJECT_NUMBER" (.projectNumber | toString) }}
{{- $_ := set $env "GCP_POOL_ID" .poolId }}
{{- $_ := set $env "GCP_PROVIDER_ID" .providerId }}
{{- $_ := set $env "GCP_SERVICE_ACCOUNT_EMAIL" .serviceAccountEmail }}
{{- end }}
{{- end }}
{{- with $root.Values.apm }}
{{- if .serverUrl }}
{{- $_ := set $env "ELASTIC_APM_SERVER_URL" .serverUrl }}
{{- $_ := set $env "ELASTIC_APM_ENVIRONMENT" (.environment | default $root.Values.environment) }}
{{- $_ := set $env "ELASTIC_APM_TRANSACTION_SAMPLE_RATE" (.transactionSampleRate | default "1.0" | toString) }}
{{- $_ := set $env "ELASTIC_APM_VERIFY_SERVER_CERT" (ternary "false" "true" (eq (toString .verifyServerCert) "false")) }}
{{- end }}
{{- end }}
{{- range $key, $value := $root.Values.commonEnv }}
{{- $_ := set $env $key ($value | toString) }}
{{- end }}
{{- range $key, $value := $svc.env }}
{{- $_ := set $env $key ($value | toString) }}
{{- end }}
{{- range $key, $value := $env }}
{{- if ne $value "" }}
{{ $key }}: {{ $value | quote }}
{{- end }}
{{- end }}
{{- end }}
