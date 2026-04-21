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

There is no attempt here to define the final answers for:

- tenants
- roles
- audit trails
- billing
- quotas
- organization structure

Those are product-platform concerns, not agent-runtime primitives.

## Not A Production Scheduler Or Sandbox Manager

The local harness is useful for development, but it is not trying to be:

- a task scheduler
- a durable registry
- a distributed worker system
- a sandbox orchestrator
- a workflow engine

Adding all of that would make the development harness heavier while helping very little with day-one agent development.

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
- organization-wide admin features

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
