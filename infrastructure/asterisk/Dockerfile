FROM ubuntu:24.04@sha256:4fbb8e6a8395de5a7550b33509421a2bafbc0aab6c06ba2cef9ebffbc7092d90

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        asterisk \
        asterisk-moh-opsound-wav \
        ca-certificates \
        gettext-base \
    && rm -rf /var/lib/apt/lists/*

COPY templates /opt/teamora/templates
COPY entrypoint.sh /usr/local/bin/teamora-asterisk-entrypoint
RUN chmod 0755 /usr/local/bin/teamora-asterisk-entrypoint \
    && mkdir -p /var/log/asterisk/cdr-csv /var/spool/asterisk/monitor /var/run/asterisk \
    && ln -s monitor /var/spool/asterisk/recording \
    && chown -R asterisk:asterisk /etc/asterisk /var/log/asterisk /var/spool/asterisk /var/run/asterisk

USER asterisk
ENTRYPOINT ["/usr/local/bin/teamora-asterisk-entrypoint"]
CMD ["asterisk", "-f", "-U", "asterisk", "-G", "asterisk"]
