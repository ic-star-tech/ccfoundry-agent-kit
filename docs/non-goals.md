# Non-Goals

This repository is intentionally opinionated about what it does not include.

That is not because those concerns are unimportant. It is because mixing them into the public agent-side toolkit would make the project harder to understand, harder to self-host, and harder to maintain.

## Not The Full Foundry Control Plane

This repository does not publish the full Foundry backend or operational environment.

That means no:

- production control-plane APIs
- private deployment topology
- internal orchestration workflows
- internal security and tenant-management subsystems

The purpose of this repo is to expose the agent-side surface cleanly, not to ship a partial internal platform snapshot.

## Not A Multi-Tenant Product Backend

This repository includes agent-facing metadata and developer-facing views for
things like budgets, settlement mandates, earnings, and model gateway policy.
Those surfaces exist so agents can advertise cost expectations, receive
settlement notifications, and let developers inspect what happened locally.

It still does not define the final production answers for:

- tenants
- roles
- audit trails
- billing ledgers
- quotas
- organization structure
- tax, invoicing, refunds, or payout compliance

Those are product-platform concerns, not agent-runtime primitives.

In other words: settlement data models and Dev Board earnings panels are in
scope; a production billing backend is not.

## Not A Payment Processor

The repo can describe agent-facing settlement notifications, preserve
AP2-inspired mandate records, expose opaque payment-provider references such as
Stripe IDs in settlement records, and ship skills that help agents inspect those
records.

It does not run merchant-of-record infrastructure or replace Stripe, treasury,
tax, fraud, compliance, refund, dispute, payout, or revenue-recognition systems.
Real payment authorization, capture, transfer, and reconciliation belong to the
Foundry control plane or another production payment service.

## Not A Production Scheduler Or Sandbox Manager

The local harness is useful for development, and the repository now includes a
Cloud Run deployment path with a Cloud Scheduler wake-up job for a single
deployed agent. That is a reference deployment helper, not a general scheduler.

The repo is still not trying to be:

- a task scheduler
- a durable registry
- a distributed worker system
- a sandbox orchestrator
- a workflow engine

Adding all of that would make the development harness heavier while helping very little with day-one agent development.

## Not A Full Deployment Platform

`Dockerfile.cloudrun`, `scripts/deploy-cloudrun.sh`, and the Dev Board Cloud Run
panel are intentionally narrow. They help package one agent, push an image, set
the agent's public URL, and optionally create a Cloud Scheduler poller.

They are not meant to replace:

- Terraform or other infrastructure-as-code systems
- multi-environment release management
- cloud IAM policy design
- observability, incident response, or SRE runbooks
- cost governance across a fleet of agents

Cloud Run support makes self-hosting easier; it does not make this repository a
cloud operations platform.

## Not An Opinionated Database Framework

The SDK intentionally avoids imposing:

- a relational schema
- a vector-store abstraction
- a queueing model
- a background job contract

Instead, it gives developers a small runtime contract and lets them decide how much persistence and infrastructure they actually need.

## Not A White-Label Chat Product

The bundled web UI is intentionally small.

It exists to exercise the protocol and test local behavior. It is not meant to compete with full-featured AI chat workspaces.

That means if you need:

- advanced chat history UX
- file workspaces
- RAG collections
- tool marketplace flows
- organization-wide operator features

you should treat those as separate product layers or integrations.

## Not Vendor Lock-In

This project does not require:

- one model vendor
- one deployment topology
- one hosting strategy
- one UI strategy

That flexibility is a core design choice. The example uses OpenAI-compatible calls because they are pragmatic and widely supported, not because the repository should hard-code one provider worldview.

## Why These Non-Goals Matter

The boundary protects the project from becoming vague.

Without clear non-goals, the repository would slowly drift into an awkward middle state:

- too platform-heavy for simple self-hosting
- too incomplete for production control-plane use
- too confusing for new contributors

Staying explicit about what belongs elsewhere is one of the reasons this project can stay public, teachable, and portable.
