🚀 A2A Swarm: Future Enhancements & Architectural Backlog (v4.1)

This document serves as the long-term roadmap and architectural memory for the A2A Trading Engine. It captures critical optimizations, structural upgrades, and strategy expansions required for institutional scaling.

Epic A: The Pluggable Alpha Layer (Data-Strategy Matching)

Context: Different trading horizons require fundamentally different deterministic rule sets. As the system scales beyond index options, the deterministic matrices must become modular.

[ ] Dynamic Context Truncation ("API Valve"): Update the Orchestrator to prune AgentContext based on the active strategy.

Intraday Options: Pass only VIX, PCR, 5m OHLC, and A/D proxy.

Swing Equity: Pass Relative Strength, Delivery %, and Volume Expansion.

[ ] Strategy-Specific Lookup Matrices: Break the pure Python Agent 2 dictionary into isolated modules: fo_intraday_matrix.py, equity_swing_matrix.py. The pipeline fetches the specific matrix based on the targeted asset.

Epic B: The Serverless Peripheral Architecture (AWS Lambda)

Context: Protect the t3.small 2GB RAM EC2 instance. The EC2 is a Formula 1 engine strictly for live 5-minute Intraday pure-Python execution. All heavy text processing and LLM calls MUST be decoupled via AWS Lambda.

[ ] Agent 0 (The Pre-Market Scout) via Lambda:

Triggered via EventBridge Cron twice daily: 8:00 AM IST (Asian/US overnight context) and 1:00 PM IST (European Open/Mid-day domestic news).

Fetches macro data, queries Claude/OpenAI for sentiment summarization, and writes a static overnight_context.json to the S3 bucket.

[ ] Telegram HITL Gateway (Agent 5) via Lambda URL:

Push webhook traffic away from the EC2. A Lambda Function URL processes incoming Telegram "Approve/Veto" callbacks and updates DynamoDB securely.

[ ] End-of-Day Data Archiver:

Lambda runs at 4:30 PM to convert the day's DynamoDB raw ticks into compressed Parquet/CSV files and stores them in an S3 Data Lake for future RL backtesting.

Epic C: Dynamic Trade Management & Execution

Context: Execution is purely mathematical. The Python Exit Engine manages live positions dynamically while relying on Exchange-level orders for disaster recovery.

[ ] Deterministic Trade Health Score (0-100):

Upgrade ExitEngine to dynamically score an open position every 5 minutes based on live PCR momentum acceleration and VIX divergence.

[ ] Dynamic Stop & Target Trailing:

If Trade Health is accelerating (e.g., >90), cancel the 60% Take Profit order and ride the trend. Drag the Stop Loss up to break-even.

If Trade Health degrades (e.g., <40), aggressively tighten the soft stop to 0.5x ATR to scratch the trade before a full loss.

[ ] Smart Order Routing (SOR) & Slippage Control:

Slice large quantity orders into tranches. Chase limit orders dynamically rather than crossing the bid-ask spread with raw market orders, minimizing the ₹40 options slippage overhead.

Epic D: Swarm Intelligence & Observability

Context: We need absolute visibility into the deterministic decision tree, and we must automate parameter tuning without human emotional interference.

[ ] Serverless Trace Viewer CLI:

Build a Lambda Function URL that queries A2A_Traces by session_id, sorts chronologically, and returns a clean, human-readable terminal output of the 5-minute flow (Feature ➔ Agent 1 ➔ Agent 2 ➔ Gatekeeper).

[ ] Autonomous Parameter Tuning (Agent 6 & 7):

The Task: Agent 6 clusters the day's DynamoDB traces. Agent 7 (LLM) evaluates PnL and proposes tweaks to static parameters (e.g., ATR multipliers, PCR thresholds).

The Guardrails: Agent 7 can only modify risk_config.json in S3. It is strictly bound by hard-coded maximums in absolute_limits.json (e.g., max leverage, max stop distance).

The HITL Gate: Any proposed change triggers a Telegram packet. It requires human approval before Monday 9:00 AM; otherwise, it is discarded.

Epic E: Phase 6 / Northstar (The Auto-Dev Pipeline)

Context: True agency means self-healing code, but with absolute human-gated deployment.

[ ] Asynchronous Self-Healing Code:

If the EC2 catches a Python traceback (e.g., an unhandled Fyers API KeyError), it automatically opens an Issue on the private GitHub repository.

A GitHub Action triggers an OpenHands/Claude agent to analyze the traceback, write a patch, run pytest, and submit a Pull Request.

The human operator solely clicks "Merge" on GitHub to auto-deploy the verified fix to the EC2 via CodeDeploy.