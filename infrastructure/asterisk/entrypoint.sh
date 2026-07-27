#!/bin/sh
set -eu

required="SIP_PROVIDER_HOST SIP_AUTH_USERNAME SIP_AUTH_PASSWORD SIP_DID ASTERISK_ARI_USERNAME ASTERISK_ARI_PASSWORD ASTERISK_EXTERNAL_MEDIA_HOST"
for name in $required; do
  eval "value=\${$name:-}"
  if [ -z "$value" ]; then
    echo "Missing required Asterisk environment variable: $name" >&2
    exit 1
  fi
done

for template in /opt/teamora/templates/*.template; do
  target="/etc/asterisk/$(basename "$template" .template)"
  envsubst < "$template" > "$target"
  chmod 0600 "$target"
done

exec "$@"
