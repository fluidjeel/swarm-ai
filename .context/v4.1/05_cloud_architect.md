System Architecture & Cloud Infrastructure Specification: A2A v4.0

1. Architectural Overview & Design Philosophy

The A2A Trading Engine is an event-driven, multi-agent orchestration middleware deployed on Amazon Web Services (AWS). It is designed to solve the three core failures of standard agentic systems: LLM latency, silent hallucination, and lack of semantic memory.

The architecture is partitioned into three distinct execution tiers, bridged by 5 cross-cutting observability and context management systems. The execution pipeline is strictly decoupled from the intelligence pipeline, ensuring that broker APIs are only triggered by deterministic Python code, never directly by an LLM output.

2. Cloud Infrastructure Topology (AWS)

architecture-beta
    group aws(cloud)[AWS Cloud - ap-south-1]

    service vpc(internet)[VPC / Security Group] in aws
    service ec2(server)[EC2 t3.small\nDeterministic Engine & Router] in aws
    service ddb(database)[DynamoDB\nTelemetry & Traces] in aws
    service s3(disk)[S3 Bucket\nPrompt Registry] in aws
    service lambda(server)[AWS Lambda\nAgents 6 & 7] in aws
    service eventbridge(internet)[EventBridge\nCron Triggers] in aws

    vpc:R --> L:ec2
    ec2:T --> B:ddb
    ec2:R --> L:s3
    eventbridge:R --> L:lambda
    lambda:T --> B:ddb


2.1 Component Specification

Compute (EC2): A single t3.small (Ubuntu 24.04) in ap-south-1 (Mumbai). This hosts the TrueData websocket connection via asyncio, the Feature Engine, the Risk Gatekeeper, and the Invocation Router. It requires an Elastic IP whitelisted by the Fyers Broker API to comply with SEBI static IP mandates.

State & Observability (DynamoDB): A single table (A2A_Traces). Partition key: session_id. Sort key: timestamp. Operates on on-demand capacity. Used to store granular @trace_agent logs (input hashes, token counts, JSON payloads, latencies).

Prompt Management (S3): Bucket a2a-prompts. Stores markdown files representing agent personas and instructions (e.g., regime_classifier_v2.md). Decouples prompt engineering from application code.

Async Analysis (Lambda & EventBridge): Agent 6 (Nightly Analyzer) and Agent 7 (Weekend Compiler) run on serverless AWS Lambda functions, triggered by EventBridge cron schedules, to prevent heavy clustering algorithms from blocking the EC2 trading threads.

Semantic Memory (Vector Store): A lightweight local FAISS index or serverless Pinecone instance. Stores historical AgentContext embeddings to provide Agent 2 with RAG-based historical precedent.

3. The 5 Cross-Cutting Systems (Middleware)

3.1 The AgentContext Contract

A strict Python dataclass that represents the absolute state of the system at any given microsecond. It is passed linearly through the swarm.

Pre-Market State: overnight_context, opening_regime.

Dynamic State: regime_decision, similar_regimes (from Vector DB), strategy_decision, critic_decision.

Risk State: open_position, daily_pnl, circuit_status, dte.

3.2 Observability Layer (@trace_agent)

No LLM API call occurs outside this decorator. It captures the exact prompt version fetched from S3, the raw TrueData input payload, the LLM's raw output, and the Pydantic validation status. It pushes this record asynchronously to DynamoDB. This provides complete forensic rebuild capability for any failed trade.

3.3 Security Sanitizer

A middleware interceptor sitting between the Feature Engine and the LLM API client. It validates all numerical inputs from the websocket (preventing NaN or boundary overflow injections) and truncates string fields to protect against prompt injection or context-window overflow.

3.4 Eval Suite (Offline)

A local CI/CD pipeline for prompts. Before a new prompt version is uploaded to S3, eval_runner.py executes it against 20+ historical JSON fixtures to verify:

Pydantic Schema compliance (no malformed JSON).

Behavioral compliance (does Agent 3, the Critic, successfully veto engineered bad trades?).

4. The 8-Agent Swarm Workflow

4.1 Tier 0 (Pre-Market Async)

Agent 0 (Scout): Runs at 8:00 AM. Scrapes global macros and institutional flows. Outputs JSON bias to prime the state before market open.

4.2 Tier 1 (Intraday Event-Driven)

The Invocation Router only triggers this swarm if open_positions == 0 and a momentum anomaly is detected.

Agent 1 (Regime Classifier): Ingests Feature Engine JSON. Outputs TREND_UP, TREND_DOWN, RANGE, CHOPPY, or UNCERTAIN.

Agent 2 (Strategy Selector): Queries Vector Store for 3 similar past contexts. Selects strategy. MUST output ΓëÑ2 supporting_signals from the data payload via Pydantic schema.

Agent 3 (The Critic - Adversarial): Replaces debate loops. One LLM call. Analyzes Agent 1 and Agent 2. Holds absolute veto power (APPROVE or REJECT).

Agent 4 (Position Advisor): Runs only during open trades to manage rollover logic.

Agent 5 (HITL Gateway): Triggers Telegram webhooks on Critic rejections, UNCERTAIN outputs, or schema failures. Awaits human input for 10 minutes before defaulting to CASH state.

4.3 Tier 2 (Post-Market Async)

Agent 6 (Nightly Analyzer): Lambda function (6:00 PM). Uses embeddings to cluster the day's traces in DynamoDB to detect strategy decay.

Agent 7 (Compiler): Lambda function (Weekend). Evaluates trace clusters against hard thresholds. Proposes shadow-mode deployment for successful patterns, outputting Python code logic via Telegram for human review.

5. Security & IAM Topology

EC2 Role: A2A-Trading-EC2-Role. Permissions: dynamodb:PutItem, s3:GetObject (restricted to a2a-prompts bucket). NO permissions to modify or delete S3 data.

Lambda Role: A2A-Analysis-Lambda-Role. Permissions: dynamodb:Query, dynamodb:Scan.

API Keys: Fyers API, TrueData, and OpenAI/Anthropic keys are stored securely in AWS Secrets Manager, fetched at runtime startup by the EC2 instance. They are never hardcoded.

6. Estimated Cloud Operating Costs

The architecture is designed to be highly cost-efficient by minimizing LLM invocations and utilizing serverless/on-demand resources where possible.

AWS EC2 (t3.small) + Elastic IP: ~$16.00 / month.

AWS DynamoDB (On-Demand): ~$2.00 / month (low write frequency).

AWS S3, Lambda, EventBridge: < $1.00 / month (well within free tier).

LLM API (gpt-4o-mini / Claude 3.5 Haiku): ~$15.00 / month (Assuming 20-30 routing exceptions per day; bypass logic saves 95% of standard agentic polling costs).

Total Cloud Infra Overhead: ~$35.00 / month.
