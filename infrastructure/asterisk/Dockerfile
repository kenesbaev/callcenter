FROM debian:bookworm-slim

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends asterisk gettext-base ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY templates /opt/teamora/templates
COPY entrypoint.sh /usr/local/bin/teamora-asterisk-entrypoint
RUN chmod 0755 /usr/local/bin/teamora-asterisk-entrypoint \
    && mkdir -p /var/log/asterisk/cdr-csv /var/spool/asterisk/monitor \
    && chown -R asterisk:asterisk /etc/asterisk /var/log/asterisk /var/spool/asterisk

USER asterisk
ENTRYPOINT ["/usr/local/bin/teamora-asterisk-entrypoint"]
CMD ["asterisk", "-f", "-U", "asterisk", "-G", "asterisk"]
