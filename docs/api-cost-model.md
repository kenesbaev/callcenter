# API cost model

Plans are seeded as: Start `$99/month` or `$999/year` with 250 AI minutes; Business `$149/month` or `$1500/year` with 500; Pro `$299/month` or `$2990/year` with 2,000; Enterprise is negotiated. Additional AI usage is modeled at `$0.08/minute`. SIP numbers and carrier minutes are separate.

Each finalized call writes one idempotent `UsageRecord` with metric, quantity, unit, call reference, occurrence time and estimated provider cost. The development simulator rounds a started minute up and labels it demo data. It is not an invoice and does not represent a live provider charge.

Production costing must separate realtime model input/output audio, transcription, synthesis, storage, carrier, integration and taxes using timestamped provider price versions. Pricing changes are platform-admin configuration with audit, never an LLM tool. Usage limits are checked before a call and reconciled after final provider events.

No real payment provider is connected. The billing domain and seeded plans exist; a development fake provider may be added only behind an explicit development flag and UI label.
