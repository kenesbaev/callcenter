FROM python:3.12-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de

RUN addgroup --system emulator && adduser --system --ingroup emulator emulator
WORKDIR /opt/emulator
COPY sip_emulator.py ./sip_emulator.py
USER emulator
EXPOSE 5060/udp 16000/udp
CMD ["python", "sip_emulator.py", "server"]
