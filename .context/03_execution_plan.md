Execution Plan & Roadmap: A2A Trading Engine v4.0

This execution plan breaks down the build into 5 chronological phases. Do not advance to the next phase until the Definition of Done (DoD) for the current phase is fully satisfied.

Phase 1: Observability, Security, & Environment Core

Objective: Build the scaffolding that ensures the agents will not fail silently and cannot be compromised by bad data.

Epic 1.1: AWS & Local Environment Standup

Tasks: Provision EC2 t3.small (ap-south-1). Allocate Elastic IP and whitelist with Fyers. Setup S3 bucket (a2a-prompts). Provision DynamoDB table (A2A_Traces).

DoD: EC2 can successfully ping Fyers API; IAM roles permit EC2 to write to DynamoDB and read from S3.

Epic 1.2: Cross-Cutting Infrastructure Logic

Tasks:

Write AgentContext dataclass in Python.

Implement the @trace_agent decorator using boto3 to log to DynamoDB.

Write the Security Sanitizer data validation functions.

DoD: A dummy function wrapped in @trace_agent successfully writes a complete telemetry record to DynamoDB, and the Sanitizer successfully catches/blocks an engineered out-of-bounds integer.

Epic 1.3: Eval Suite & Prompt Registry

Tasks: Create local evals/ directory. Draft v1.md prompts for Regime and Strategy agents, upload to S3. Write eval_runner.py to parse Pydantic schemas.

DoD: The offline eval_runner.py can invoke OpenAI/Anthropic APIs, read the S3 prompt, and output a Pass/Fail report based on schema validation.

Phase 2: Deterministic State Engine (The Muscle)

Objective: Build the data ingestion, risk management, and exit pipelines that protect capital from the AI.

Epic 2.1: The Feature Engine

Tasks: Connect to TrueData websocket via asyncio. Write Pandas logic for NIFTY 500 A/D ratio, Expiry-Weighted PCR Momentum, and VIX/ATR divergence.

DoD: Script runs for 4 hours without memory leaks, outputting accurate JSON metrics every 5 minutes.

Epic 2.2: Risk Gatekeeper & Exit Engine

Tasks: Implement the Python classes containing the hard mathematical rules (VIX Ceiling > 18, DTE <= 1, Max Daily Loss -₹8000, 2x ATR trailing stop, 60% Theta capture).

DoD: Unit tests confirm that the Gatekeeper rejects engineered bad inputs (e.g., trying to place an Iron Condor on expiry day) 100% of the time.

Phase 3: The Pre-Market & Intraday Swarm (The Brain)

Objective: Bring the first 6 agents (Agents 0 through 5) online and connect them via the Invocation Router.

Epic 3.1: Pre-Market Scout (Agent 0)

Tasks: Write the async scraper for SGX/GIFT Nifty and FII/DII data. Set cron to run at 8:00 AM.

DoD: Automatically generates overnight_context.json daily before 8:45 AM.

Epic 3.2: Intraday Core (Agents 1, 2, & 3)

Tasks:

Build Regime Classifier (Agent 1).

Integrate local Vector Store (FAISS/Pinecone) for semantic memory and build Strategy Selector (Agent 2).

Build the Adversarial Critic (Agent 3) with absolute veto logic.

DoD: The entire sequence (Agent 1 -> Agent 2 -> Vector Query -> Agent 3) executes successfully in the Eval Suite offline, with Agent 3 correctly vetoing contradictory trades.

Epic 3.3: Position Advisor (Agent 4) & HITL Gateway (Agent 5)

Tasks: Build Agent 4 for roll logic. Integrate Telegram API for Agent 5, formatting structured message packets with interactive buttons.

DoD: Sending an UNCERTAIN payload to the Router successfully halts execution and pings Telegram with formatted decision options.

Phase 4: Execution & Full Integration

Objective: Connect the intelligence to the broker and deploy capital.

Epic 4.1: Invocation Router & Fyers Execution

Tasks: Build the master state machine that receives AgentContext. Route approved contexts through the Gatekeeper to the Fyers_Client.

DoD: System can run autonomously in Paper Trading mode, handling full lifecycles from Agent 0 Pre-Market priming to Exit Engine flattening.

Capital Deployment: Once paper-trading DoD is met, pledge ₹2.5L in Liquid BeES and deploy live on 1-lot constraints.

Phase 5: Post-Market & Compilation (The Moat)

Objective: Activate the self-improvement and compilation pipeline (Agents 6 and 7).

Epic 5.1: Nightly Analyzer (Agent 6)

Tasks: Write AWS Lambda function triggered via EventBridge at 6:00 PM IST. Implement embedding clustering on DynamoDB traces.

DoD: Sends an accurate daily summary of clustered trade contexts and PnL to Telegram.

Epic 5.2: The Compiler (Agent 7)

Tasks: Implement weekend logic to check the Golden Goose threshold (10 occ, 65% win, 1.5x expectancy). Generate Shadow Mode proposals.

DoD: Agent successfully identifies a historical pattern from the traces and outputs a formatted compilation proposal document.