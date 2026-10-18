# Feedback on Nebius and NVIDIA technologies

Written from building NearMiss for the Nebius × NVIDIA Global AI Hackathon 2026.

## Nebius Token Factory

**What worked well**

- The OpenAI-compatible `/v1/chat/completions` endpoint meant a plain `httpx` client was
  enough. We kept explicit control of timeouts, retries and error mapping without an SDK.
- `/v1/models` makes it possible to check at startup that a configured model is actually live
  for the key, instead of failing on the first user request. NearMiss does this and falls back
  to another NVIDIA Nemotron model.
- Token usage in each response made it straightforward to report inference cost separately
  from simulation cost.

**What slowed us down**

- **Model retirements.** `nvidia/Cosmos3-Super-Reasoner`, the obvious physical-reasoning model
  for a Physical AI project, was retired from serverless on 2026-08-31, after the hackathon had
  started. We only found this in Nebius' own `nebius-physical-ai` repository notes, not in the
  model catalogue we were reading. A visible "retired / retiring on" status in the catalogue and
  in `/v1/models` would have saved time.
- **Model IDs are hard to confirm without a key.** Third-party pages disagree on exact IDs
  (`Nemotron-3_5-Lightning` vs `NVIDIA-Nemotron-3.5-Lightning-30B-A3B`, casing differences). A
  public, unauthenticated model list with IDs and capabilities (JSON mode, `json_schema`,
  vision, thinking controls) would help teams plan before they have credits.
- **Thinking controls differ per model.** Disabling reasoning needs
  `chat_template_kwargs.enable_thinking=false` for Nemotron 3.5 Lightning; without it, short
  JSON tasks can spend most of the token budget on reasoning and return truncated output.
  Documenting this per model on the model card in Token Factory would prevent a common failure.
- **Structured output support is unclear per model.** We used `response_format: json_object`
  plus strict local validation, because the docs do not say which hosted models honour
  `json_schema` strictly.

## Nebius AI Cloud (Serverless Jobs)

We designed the benchmark sweep (≈310,000 simulation runs) to be a natural Serverless Jobs
workload, but had no AI Cloud project or credits, since the hackathon credits cover Token
Factory only. A small AI Cloud CPU credit for Physical AI entrants would let teams run
simulation sweeps where the track description suggests.

## NVIDIA models and tools

- **Nemotron 3.5 Lightning** was fast enough for interactive use, and did well at the bounded
  extraction task: quoting evidence and separating observed from estimated values when asked
  to. Our validation layer still rejects outputs whose quotes do not appear in the report, and
  we recommend that pattern to anyone using an LLM for incident data.
- **Isaac Sim** was evaluated and not used: it needs an RTX-class GPU we did not have, and
  GPU physics is not bit-reproducible across machines, which matters for an audit trail. A
  documented CPU-only "headless kinematics" mode, or a hosted Isaac Sim endpoint on Nebius,
  would make it reachable for teams like ours.
