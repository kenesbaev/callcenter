#!/bin/sh
set -eu

SIP_AUTH_MODE="${SIP_AUTH_MODE:-registration}"
SIP_LOCAL_PORT="${SIP_LOCAL_PORT:-5060}"
SIP_CODECS="${SIP_CODECS:-ulaw,alaw}"
SIP_DTMF_MODE="${SIP_DTMF_MODE:-auto}"
ASTERISK_ARI_APPLICATION="${ASTERISK_ARI_APPLICATION:-teamora-voice}"
export SIP_AUTH_MODE SIP_LOCAL_PORT SIP_CODECS SIP_DTMF_MODE ASTERISK_ARI_APPLICATION

required="SIP_PROVIDER_HOST SIP_PROVIDER_PORT SIP_PROVIDER_TRANSPORT SIP_DID SIP_ALLOWED_IPS ASTERISK_ARI_USERNAME ASTERISK_ARI_PASSWORD ASTERISK_EXTERNAL_MEDIA_HOST ASTERISK_PUBLIC_ADDRESS ASTERISK_LOCAL_NET ASTERISK_RTP_PORT_START ASTERISK_RTP_PORT_END"
for name in $required; do
  eval "value=\${$name:-}"
  if [ -z "$value" ]; then
    echo "Missing required Asterisk environment variable: $name" >&2
    exit 1
  fi
done

case "$SIP_AUTH_MODE" in
  registration)
    for name in SIP_AUTH_USERNAME SIP_AUTH_PASSWORD; do
      eval "value=\${$name:-}"
      if [ -z "$value" ]; then
        echo "Missing required registration authentication setting: $name" >&2
        exit 1
      fi
    done
    SIP_AUTH_SECTION="[provider-auth]
type=auth
auth_type=userpass
username=${SIP_AUTH_USERNAME}
password=${SIP_AUTH_PASSWORD}"
    SIP_ENDPOINT_AUTH_LINES="outbound_auth=provider-auth
from_user=${SIP_AUTH_USERNAME}
from_domain=${SIP_PROVIDER_HOST}"
    SIP_REGISTRATION_SECTION="[provider-registration]
type=registration
transport=transport-provider
outbound_auth=provider-auth
server_uri=sip:${SIP_PROVIDER_HOST}:${SIP_PROVIDER_PORT}
client_uri=sip:${SIP_AUTH_USERNAME}@${SIP_PROVIDER_HOST}
contact_user=${SIP_DID}
retry_interval=60
forbidden_retry_interval=600
max_retries=10"
    ;;
  ip)
    SIP_AUTH_SECTION=""
    SIP_ENDPOINT_AUTH_LINES=""
    SIP_REGISTRATION_SECTION=""
    ;;
  *)
    echo "SIP_AUTH_MODE must be registration or ip" >&2
    exit 1
    ;;
esac

SIP_IDENTIFY_MATCHES=""
old_ifs="$IFS"
IFS=','
for network in $SIP_ALLOWED_IPS; do
  network=$(printf '%s' "$network" | tr -d ' ')
  [ -n "$network" ] && SIP_IDENTIFY_MATCHES="${SIP_IDENTIFY_MATCHES}match=${network}
"
done
IFS="$old_ifs"
export SIP_AUTH_SECTION SIP_ENDPOINT_AUTH_LINES SIP_REGISTRATION_SECTION SIP_IDENTIFY_MATCHES

# Restrict substitution to deployment configuration. Asterisk dialplan values
# such as ${EXTEN} and ${CHANNEL(...)} must remain literal until call runtime.
SUBST_VARS='${ASTERISK_ARI_APPLICATION} ${ASTERISK_ARI_PASSWORD} ${ASTERISK_ARI_USERNAME} ${ASTERISK_EXTERNAL_MEDIA_HOST} ${ASTERISK_LOCAL_NET} ${ASTERISK_PUBLIC_ADDRESS} ${ASTERISK_RTP_PORT_END} ${ASTERISK_RTP_PORT_START} ${OPERATOR_QUEUE_TIMEOUT} ${SIP_AUTH_SECTION} ${SIP_CODECS} ${SIP_DTMF_MODE} ${SIP_ENDPOINT_AUTH_LINES} ${SIP_IDENTIFY_MATCHES} ${SIP_LOCAL_PORT} ${SIP_MAX_CHANNELS} ${SIP_PROVIDER_HOST} ${SIP_PROVIDER_PORT} ${SIP_PROVIDER_TRANSPORT} ${SIP_REGISTRATION_SECTION}'

for template in /opt/teamora/templates/*.template; do
  target="/etc/asterisk/$(basename "$template" .template)"
  envsubst "$SUBST_VARS" < "$template" > "$target"
  chmod 0600 "$target"
done

exec "$@"
